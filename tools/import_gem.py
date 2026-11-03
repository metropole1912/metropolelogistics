"""Import Global Energy Monitor tracker spreadsheets into the asset register.

1. Download a tracker from https://globalenergymonitor.org/projects/ (free; a short form). Useful ones:
     Global Oil & Gas Extraction Tracker  -> fields            (--kind field)
     Global Oil Infrastructure Tracker    -> pipelines, refineries, terminals (--kind auto)
     Global Gas Infrastructure Tracker    -> LNG terminals, gas pipelines     (--kind auto)
2. Run:
     python tools/import_gem.py path/to/tracker.xlsx --kind auto
   It writes register/gem_assets.json (and register/gem_corridors.json for pipelines) for you to look over.
3. Push to the database when happy:
     python tools/import_gem.py path/to/tracker.xlsx --kind auto --push

Only rows in the countries the engine covers are kept. Attribution: data © Global Energy Monitor, CC BY 4.0.
"""
import argparse
import re

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from registry import find_col, iso3, num, push, read_table, save, slug

KIND_WORDS = [("lng", "lng"), ("refiner", "refinery"), ("terminal", "terminal"), ("storage", "storage"), ("pipeline", "pipeline"),
              ("field", "field"), ("extraction", "field"), ("gas", "field"), ("oil", "field")]
STATUS_MAP = {"operating": "op", "operational": "op", "construction": "proj", "proposed": "proj", "announced": "proj", "pre-construction": "proj",
              "shelved": "proj", "mothballed": "dis", "idle": "dis", "cancelled": "dis", "retired": "dis", "discovered": "proj", "in development": "proj"}


def guess_kind(row, headers, forced):
    if forced != "auto":
        return forced
    text = " ".join(str(row.get(h, "")) for h in headers if any(w in str(h).lower() for w in ("type", "facility", "infrastructure", "unit"))).lower()
    for w, k in KIND_WORDS:
        if w in text:
            return k
    return "field"


def commodities(row, headers):
    text = " ".join(str(row.get(h, "")) for h in headers if any(w in str(h).lower() for w in ("fuel", "product", "commodity", "type"))).lower()
    out = []
    if "lng" in text:
        out.append("LNG")
    if "gas" in text and "LNG" not in out:
        out.append("Gas")
    if "oil" in text or "crude" in text:
        out.append("Crude")
    if "product" in text or "refin" in text:
        out.append("Prod")
    if "lpg" in text or "ngl" in text:
        out.append("LPG")
    return out or ["Crude"]


def parse_route(text):
    """GEM routes look like 'lat, lon: lat, lon; lat, lon' — pull every numeric pair."""
    pairs = re.findall(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", str(text or ""))
    return [[float(lon), float(lat)] for lat, lon in pairs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--kind", default="auto", choices=["auto", "field", "refinery", "lng", "terminal", "storage", "pipeline"])
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    headers, rows = read_table(a.file)
    if not headers:
        raise SystemExit("Could not find a header row in that file")
    c_name = find_col(headers, "unit name", "project name", "pipeline name", "terminal name", "name", "project")
    c_country = find_col(headers, "country", "countries", "country/area")
    c_lat, c_lon = find_col(headers, "latitude", "lat"), find_col(headers, "longitude", "lon", "lng")
    c_owner = find_col(headers, "owner", "operator", "parent")
    c_status = find_col(headers, "status")
    c_route = find_col(headers, "route", "wkt")
    c_cap = find_col(headers, "capacity", "production")
    print(f"Columns used: name={c_name!r} country={c_country!r} lat={c_lat!r} lon={c_lon!r} owner={c_owner!r} status={c_status!r} route={c_route!r}")

    assets, corridors, skipped = [], [], 0
    for r in rows:
        iso = iso3(str(r.get(c_country, "")).split(",")[0].split("-")[0].strip())
        name = str(r.get(c_name) or "").strip()
        if not iso or not name:
            skipped += 1
            continue
        kind = guess_kind(r, headers, a.kind)
        st = STATUS_MAP.get(str(r.get(c_status, "")).strip().lower(), "op")
        owner = str(r.get(c_owner) or "").strip()[:200]
        cap = str(r.get(c_cap) or "").strip()
        note = f"Source: Global Energy Monitor. Status: {r.get(c_status, '')}." + (f" Capacity: {cap}." if cap else "")
        if kind == "pipeline":
            pts = parse_route(r.get(c_route)) if c_route else []
            if len(pts) < 2:
                skipped += 1
                continue
            corridors.append({"id": "gem-" + slug(name), "name": name, "mode": "pipeline", "sector": "energy", "risk": 2,
                              "origin": "", "destination": "", "km": 0, "days": "—", "status": str(r.get(c_status, "")),
                              "path": pts, "note": note, "metals": commodities(r, headers), "serves": []})
            continue
        lat, lon = num(r.get(c_lat)), num(r.get(c_lon))
        if lat is None or lon is None:
            skipped += 1
            continue
        assets.append({"id": "gem-" + slug(name), "name": name, "iso": iso, "kind": kind, "status": st, "sector": "energy",
                       "metals": commodities(r, headers), "operator": owner, "lon": lon, "lat": lat, "note": note})
    print(f"Kept {len(assets)} assets and {len(corridors)} pipelines; skipped {skipped} rows (outside coverage or no coordinates)")
    if assets:
        save("gem_assets", assets)
    if corridors:
        save("gem_corridors", corridors)
    if a.push:
        if assets:
            push("assets", "id", assets)
        if corridors:
            push("corridors", "id", corridors)


if __name__ == "__main__":
    main()
