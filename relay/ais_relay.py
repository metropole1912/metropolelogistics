"""Metropole AIS relay: keeps a websocket open to aisstream.io, classifies ships, screens them
against sanctions lists, and writes positions to the Supabase `vessels` table that the website reads.

Why a relay: aisstream.io only allows server-side websocket clients (an API key must stay secret and
browsers cannot hold the connection open). This small process runs anywhere Python runs: your Mac,
a US$5 VPS, Railway, Render or Fly.io. It uses about 50 MB of memory.

Run:
  python relay/ais_relay.py                 live (needs AISSTREAM_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY in .env)
  python relay/ais_relay.py --sample        replay relay/fixtures/ais_sample.jsonl into the local test database
  python relay/ais_relay.py --prune         delete vessels not seen for RELAY_PRUNE_DAYS (default 14) and exit
  python relay/ais_relay.py --boxes my.json use another bounding-box file (default relay/boxes.json)

Settings (environment or .env):
  AISSTREAM_API_KEY      free key from https://aisstream.io (sign in with GitHub)
  RELAY_FLUSH_SECONDS    how often positions are written to the database (default 60)
  RELAY_TYPES            AIS type codes to keep, default "70-89" (cargo and tankers); "all" keeps everything
  RELAY_MIN_LENGTH       ignore ships shorter than this many metres once their size is known (default 90)
  RELAY_PRUNE_DAYS       forget vessels silent for this long (default 14)
  RELAY_SANCTIONS        "ofac" (default: download the OFAC SDN list on start and every 24 h) or "off"
"""
import argparse
import asyncio
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector import config, db as dbmod  # noqa: E402  (loads .env, gives the Supabase client)

API_KEY = config.env("AISSTREAM_API_KEY")
FLUSH_SECONDS = int(config.env("RELAY_FLUSH_SECONDS", "60") or 60)
MIN_LENGTH = int(config.env("RELAY_MIN_LENGTH", "90") or 90)
PRUNE_DAYS = int(config.env("RELAY_PRUNE_DAYS", "14") or 14)
SANCTIONS_MODE = config.env("RELAY_SANCTIONS", "ofac")
WS_URL = "wss://stream.aisstream.io/v0/stream"

# ---------- AIS reference tables ----------
TYPE_NAMES = {70: "Cargo", 71: "Cargo (hazardous A)", 72: "Cargo (hazardous B)", 73: "Cargo (hazardous C)", 74: "Cargo (hazardous D)", 79: "Cargo",
              80: "Tanker", 81: "Tanker (hazardous A)", 82: "Tanker (hazardous B)", 83: "Tanker (hazardous C)", 84: "Tanker (hazardous D)", 89: "Tanker",
              30: "Fishing", 31: "Towing", 32: "Towing (large)", 33: "Dredging", 35: "Military", 36: "Sailing", 37: "Pleasure", 50: "Pilot",
              51: "Search and rescue", 52: "Tug", 55: "Law enforcement", 60: "Passenger", 90: "Other", 0: "Unknown"}
# Maritime Identification Digits (first three digits of an MMSI) -> ISO2 flag. Common registries; others fall back to '??'.
MID = {
    201: "AL", 202: "AD", 203: "AT", 204: "PT", 205: "BE", 206: "BY", 207: "BG", 208: "VA", 209: "CY", 210: "CY", 211: "DE", 212: "CY", 213: "GE", 214: "MD",
    215: "MT", 216: "AM", 218: "DE", 219: "DK", 220: "DK", 224: "ES", 225: "ES", 226: "FR", 227: "FR", 228: "FR", 229: "MT", 230: "FI", 231: "FO", 232: "GB",
    233: "GB", 234: "GB", 235: "GB", 236: "GI", 237: "GR", 238: "HR", 239: "GR", 240: "GR", 241: "GR", 242: "MA", 243: "HU", 244: "NL", 245: "NL", 246: "NL",
    247: "IT", 248: "MT", 249: "MT", 250: "IE", 251: "IS", 252: "LI", 253: "LU", 254: "MC", 255: "PT", 256: "MT", 257: "NO", 258: "NO", 259: "NO", 261: "PL",
    262: "ME", 263: "PT", 264: "RO", 265: "SE", 266: "SE", 267: "SK", 268: "SM", 269: "CH", 270: "CZ", 271: "TR", 272: "UA", 273: "RU", 274: "MK", 275: "LV",
    276: "EE", 277: "LT", 278: "SI", 279: "RS", 301: "AI", 303: "US", 304: "AG", 305: "AG", 306: "CW", 307: "AW", 308: "BS", 309: "BS", 310: "BM", 311: "BS",
    312: "BZ", 314: "BB", 316: "CA", 319: "KY", 321: "CR", 323: "CU", 325: "DM", 327: "DO", 329: "GP", 330: "GD", 331: "GL", 332: "GT", 334: "HN", 336: "HT",
    338: "US", 339: "JM", 341: "KN", 343: "LC", 345: "MX", 347: "MQ", 348: "MS", 350: "NI", 351: "PA", 352: "PA", 353: "PA", 354: "PA", 355: "PA", 356: "PA",
    357: "PA", 358: "PR", 359: "SV", 361: "PM", 362: "TT", 364: "TC", 366: "US", 367: "US", 368: "US", 369: "US", 370: "PA", 371: "PA", 372: "PA", 373: "PA",
    374: "PA", 375: "VC", 376: "VC", 377: "VC", 378: "VG", 379: "VI", 401: "AF", 403: "SA", 405: "BD", 408: "BH", 410: "BT", 412: "CN", 413: "CN", 414: "CN",
    416: "TW", 417: "LK", 419: "IN", 422: "IR", 423: "AZ", 425: "IQ", 428: "IL", 431: "JP", 432: "JP", 434: "TM", 436: "KZ", 437: "UZ", 438: "JO", 440: "KR",
    441: "KR", 443: "PS", 445: "KP", 447: "KW", 450: "LB", 451: "KG", 453: "MO", 455: "MV", 457: "MN", 459: "NP", 461: "OM", 463: "PK", 466: "QA", 468: "SY",
    470: "AE", 471: "AE", 472: "TJ", 473: "YE", 475: "YE", 477: "HK", 478: "BA", 501: "AQ", 503: "AU", 506: "MM", 508: "BN", 510: "FM", 511: "PW", 512: "NZ",
    514: "KH", 515: "KH", 516: "CX", 518: "CK", 520: "FJ", 523: "CC", 525: "ID", 529: "KI", 531: "LA", 533: "MY", 536: "MP", 538: "MH", 540: "NC", 542: "NU",
    544: "NR", 546: "PF", 548: "PH", 553: "PG", 555: "PN", 557: "SB", 559: "AS", 561: "WS", 563: "SG", 564: "SG", 565: "SG", 566: "SG", 567: "TH", 570: "TO",
    572: "TV", 574: "VN", 576: "VU", 577: "VU", 578: "WF", 601: "ZA", 603: "AO", 605: "DZ", 607: "TF", 608: "IO", 609: "BI", 610: "BJ", 611: "BW", 612: "CF",
    613: "CM", 615: "CG", 616: "KM", 617: "CV", 618: "AQ", 619: "CI", 620: "KM", 621: "DJ", 622: "EG", 624: "ET", 625: "ER", 626: "GA", 627: "GH", 629: "GM",
    630: "GW", 631: "GQ", 632: "GN", 633: "BF", 634: "KE", 635: "AQ", 636: "LR", 637: "LR", 638: "SS", 642: "LY", 644: "LS", 645: "MU", 647: "MG", 649: "ML",
    650: "MZ", 654: "MR", 655: "MW", 656: "NE", 657: "NG", 659: "NA", 660: "RE", 661: "RW", 662: "SD", 663: "SN", 664: "SC", 665: "SH", 666: "SO", 667: "SL",
    668: "ST", 669: "SZ", 670: "TD", 671: "TG", 672: "TN", 674: "TZ", 675: "UG", 676: "CD", 677: "TZ", 678: "ZM", 679: "ZW", 701: "AR", 710: "BR", 720: "BO",
    725: "CL", 730: "CO", 735: "EC", 740: "FK", 745: "GF", 750: "GY", 755: "PY", 760: "PE", 765: "SR", 770: "UY", 775: "VE",
}
CONTAINER_WORDS = ("MAERSK", "MSC ", "CMA CGM", "COSCO", "EVER ", "HAPAG", "ONE ", "HMM ", "YANG MING", "ZIM ", "EXPRESS", "OOCL", "WAN HAI", "SEALAND")


def classify(type_code, name, length, width):
    """Rough class and size from AIS type, name and dimensions. Heuristic: AIS has no cargo field."""
    n = (name or "").upper()
    if type_code is None:
        return "other", None, None
    if 80 <= type_code <= 89:
        if "LNG" in n or ("GAS" in n and length and length >= 270):
            return "lng", "LNG carrier", 85000
        if "LPG" in n or "GAS" in n:
            return "lpg", "VLGC" if length and length >= 220 else "LPG carrier", 54000 if length and length >= 220 else 25000
        if not length:
            return "product", None, None
        if length >= 320: return "crude", "VLCC", 300000
        if length >= 265: return "crude", "Suezmax", 157000
        if length >= 235: return "crude", "Aframax / LR2", 110000
        if length >= 200: return "product", "Panamax / LR1", 74000
        if length >= 170: return "product", "MR", 48000
        return "product", "Handy", 22000
    if 70 <= type_code <= 79:
        if any(w in n for w in CONTAINER_WORDS) or (length and length >= 330 and width and width <= 52):
            teu = int(length * 42) if length else None
            return "container", f"{teu:,} TEU (est.)" if teu else None, int(length * 400) if length else None
        if not length:
            return "general", None, None
        if length >= 350: return "bulk", "Valemax", 400000
        if length >= 280: return "bulk", "Capesize", 180000
        if length >= 215: return "bulk", "Panamax", 80000
        if length >= 185: return "bulk", "Supramax", 57000
        if length >= 150: return "bulk", "Handysize", 35000
        return "general", "Handysize", 15000
    return "other", None, None


LADEN_DRAUGHT = {"VLCC": 20.0, "Suezmax": 15.5, "Aframax / LR2": 13.5, "Panamax / LR1": 12.5, "MR": 11.0, "Handy": 8.5, "Valemax": 22.0,
                 "Capesize": 17.0, "Panamax": 13.5, "Supramax": 12.5, "Handysize": 9.5, "LNG carrier": 11.5, "VLGC": 11.0, "LPG carrier": 9.0}


def laden_guess(size_class, draught):
    ref = LADEN_DRAUGHT.get(size_class or "")
    if not ref or not draught:
        return None
    return draught >= 0.82 * ref


def norm(name):
    return " ".join(re.sub(r"[^A-Z0-9 ]+", " ", (name or "").upper()).split())


# ---------- sanctions ----------
class Sanctions:
    def __init__(self, sample=False):
        self.names = {}
        self.loaded = 0
        self.sample = sample

    def load(self):
        try:
            from collector.sources import sanctions as sx
            rows = sx.load_sdn(sx.fetch_sdn(self.sample)) if SANCTIONS_MODE != "off" or self.sample else []
            self.names = {norm(r["name"]): r["program"] for r in rows if r["type"].lower() == "vessel" and len(norm(r["name"])) >= 4}
            for line in sx.vessel_names():
                self.names.setdefault(norm(line), "watch list")
            self.loaded = time.time()
            print(f"[sanctions] {len(self.names)} vessel names loaded")
        except Exception as e:
            print(f"[sanctions] could not load list: {e}")

    def check(self, name):
        n = norm(name)
        if not n:
            return None
        if n in self.names:
            return self.names[n]
        if len(n) >= 8:
            import difflib
            m = difflib.get_close_matches(n, list(self.names), n=1, cutoff=0.93)
            if m:
                return self.names[m[0]] + " (fuzzy)"
        return None


# ---------- state ----------
class Fleet:
    def __init__(self, types, sanctions):
        self.rows = {}        # mmsi -> row dict
        self.dirty = set()
        self.types = types
        self.sanctions = sanctions
        self.seen_msgs = 0

    def keep_type(self, t):
        return self.types is None or (t is not None and t in self.types)

    def handle(self, msg):
        self.seen_msgs += 1
        meta, kind = msg.get("MetaData", {}), msg.get("MessageType")
        mmsi = meta.get("MMSI")
        if not mmsi:
            return
        row = self.rows.setdefault(mmsi, {"mmsi": mmsi, "flag": MID.get(int(str(mmsi)[:3]), None), "first_seen": now_iso()})
        name = (meta.get("ShipName") or "").strip()
        if name and name != row.get("name"):
            row["name"] = name
            row["sanctions_program"] = self.sanctions.check(name)
            row["sanctioned"] = bool(row["sanctions_program"])
        t = meta.get("time_utc")
        row["last_seen"] = parse_time(t)
        if kind == "PositionReport":
            p = msg["Message"]["PositionReport"]
            row.update(lon=p.get("Longitude"), lat=p.get("Latitude"), sog=p.get("Sog"), cog=p.get("Cog"),
                       heading=p.get("TrueHeading") if p.get("TrueHeading", 511) != 511 else None, nav_status=p.get("NavigationalStatus"))
        elif kind == "ShipStaticData":
            s = msg["Message"]["ShipStaticData"]
            d = s.get("Dimension") or {}
            length = (d.get("A") or 0) + (d.get("B") or 0) or None
            width = (d.get("C") or 0) + (d.get("D") or 0) or None
            eta = s.get("Eta") or {}
            row.update(imo=s.get("ImoNumber") or None, callsign=(s.get("CallSign") or "").strip() or None, type_code=s.get("Type"),
                       type_name=TYPE_NAMES.get(s.get("Type"), "Other"), length=length, width=width,
                       draught=s.get("MaximumStaticDraught") or None, destination=(s.get("Destination") or "").strip() or None,
                       eta=f"{eta.get('Month', 0):02d}-{eta.get('Day', 0):02d} {eta.get('Hour', 0):02d}:{eta.get('Minute', 0):02d}" if eta.get("Month") else None)
            if s.get("Name"):
                row["name"] = s["Name"].strip()
            vclass, size_class, dwt = classify(row.get("type_code"), row.get("name"), length, width)
            row.update(vclass=vclass, size_class=size_class, est_dwt=dwt, laden=laden_guess(size_class, row.get("draught")))
        self.dirty.add(mmsi)

    def flush_rows(self):
        out = []
        for m in list(self.dirty):
            r = self.rows.get(m)
            if not r or r.get("lon") is None or "type_code" not in r:
                continue                      # wait until both a position and static data have arrived
            if not self.keep_type(r.get("type_code")):
                self.dirty.discard(m); self.rows.pop(m, None); continue
            if r.get("length") and r["length"] < MIN_LENGTH:
                self.dirty.discard(m); self.rows.pop(m, None); continue
            out.append({k: v for k, v in r.items() if k in COLS})
            self.dirty.discard(m)
        return out


COLS = {"mmsi", "imo", "name", "callsign", "type_code", "type_name", "vclass", "size_class", "est_dwt", "length", "width", "draught", "laden",
        "flag", "destination", "eta", "lon", "lat", "sog", "cog", "heading", "nav_status", "last_seen", "first_seen", "sanctioned", "sanctions_program"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_time(t):
    """aisstream times look like '2024-05-01 12:34:56.789 +0000 UTC'."""
    if not t:
        return now_iso()
    try:
        return datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return now_iso()


def parse_types(spec):
    spec = (spec or "70-89").strip().lower()
    if spec == "all":
        return None
    out = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


# ---------- run loops ----------
async def live(fleet, database, boxes):
    import websockets
    backoff = 5
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, max_size=2**22) as ws:
                await ws.send(json.dumps({"APIKey": API_KEY, "BoundingBoxes": boxes, "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}))
                print(f"[relay] connected; {len(boxes)} boxes; flushing every {FLUSH_SECONDS}s")
                backoff = 5
                last = time.time()
                async for raw in ws:
                    try:
                        fleet.handle(json.loads(raw))
                    except Exception as e:  # one bad message must not kill the stream
                        print(f"[relay] message skipped: {e}")
                    if time.time() - last >= FLUSH_SECONDS:
                        flush(fleet, database)
                        last = time.time()
                    if fleet.sanctions.loaded and time.time() - fleet.sanctions.loaded > 86400:
                        fleet.sanctions.load()
        except Exception as e:
            print(f"[relay] connection lost ({e}); reconnecting in {backoff}s")
            flush(fleet, database)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


def flush(fleet, database):
    rows = fleet.flush_rows()
    if rows:
        try:
            database.upsert_rows("vessels", rows, "mmsi")
            print(f"[relay] {now_iso()[:19]} wrote {len(rows)} vessels ({len(fleet.rows)} tracked, {fleet.seen_msgs} messages)")
        except Exception as e:
            print(f"[relay] write failed: {e}")
            fleet.dirty.update(r["mmsi"] for r in rows)


def replay_sample(fleet, database):
    path = ROOT / "relay" / "fixtures" / "ais_sample.jsonl"
    n = 0
    for line in path.read_text().splitlines():
        if line.strip():
            fleet.handle(json.loads(line)); n += 1
    flush(fleet, database)
    print(f"Replayed {n} messages → {len([r for r in fleet.rows.values() if r.get('vclass')])} classified vessels")
    for r in fleet.rows.values():
        if r.get("vclass"):
            print(f"  {r.get('name', '?'):<22} {r.get('flag')} {r.get('vclass'):<9} {r.get('size_class') or '':<14} {r.get('est_dwt') or '':>7} "
                  f"laden={r.get('laden')} sanctioned={r.get('sanctioned', False)} → {r.get('destination')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--boxes", default=str(ROOT / "relay" / "boxes.json"))
    a = ap.parse_args()
    database = dbmod.connect() if not a.sample else dbmod.LocalDB(config.LOCAL_DB_DIR)
    if a.prune:
        if isinstance(database, dbmod.SupabaseDB):
            n = database.rpc("prune_vessels", {"days": PRUNE_DAYS})
            print(f"Pruned {n} vessels not seen for {PRUNE_DAYS} days")
        else:
            print("Prune only applies to the Supabase database.")
        return
    sanctions = Sanctions(sample=a.sample)
    sanctions.load()
    fleet = Fleet(parse_types(config.env("RELAY_TYPES", "70-89")), sanctions)
    if a.sample:
        replay_sample(fleet, database)
        return
    if not API_KEY:
        raise SystemExit("AISSTREAM_API_KEY is missing. Get a free key at https://aisstream.io and add it to .env")
    boxes = json.loads(Path(a.boxes).read_text())["boxes"]
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: (flush(fleet, database), loop.stop()))
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(live(fleet, database, boxes))
    except RuntimeError:
        pass
    finally:
        flush(fleet, database)


if __name__ == "__main__":
    main()
