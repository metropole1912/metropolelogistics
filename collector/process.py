"""Turns tagged items into events: validates tags, places them on the map, merges duplicates, decides review status."""
import re
from datetime import date, datetime, timezone

from . import config
from .tagger import ENERGY_CODES, METAL_WORDS, TYPES

STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "as", "at", "by", "with", "from", "after", "over", "its", "is"}


def words(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 2}


def similar(a, b):
    wa, wb = words(a), words(b)
    return len(wa & wb) / max(1, len(wa | wb))


def clean(tags, ref):
    """Drop anything the model invented; keep only ids that exist in the register."""
    ids = lambda rows: {r["id"] for r in rows}
    isos = {c["iso"] for c in ref["countries"]} | {"XX"}
    t = dict(tags)
    t["country_iso3"] = (t.get("country_iso3") or "").upper()
    if t["country_iso3"] not in isos:
        return None
    t["asset_ids"] = [a for a in t.get("asset_ids") or [] if a in ids(ref["assets"])]
    t["corridor_ids"] = [c for c in t.get("corridor_ids") or [] if c in ids(ref["corridors"])]
    t["chokepoint_id"] = t.get("chokepoint_id") if t.get("chokepoint_id") in ids(ref["chokepoints"]) else None
    t["port_id"] = t.get("port_id") if t.get("port_id") in ids(ref["ports"]) else None
    t["metals"] = [m for m in t.get("metals") or [] if m in METAL_WORDS]
    t["type"] = t.get("type") if t.get("type") in TYPES else "political"
    t["severity"] = min(4, max(1, int(t.get("severity") or 2)))
    t["confidence"] = float(t.get("confidence") or 0.5)
    try:
        d = date.fromisoformat((t.get("event_date") or "")[:10])
        if d > date.today():
            d = date.today()
    except ValueError:
        d = date.today()
    t["event_date"] = d.isoformat()
    return t


def locate(t, ref):
    """Coordinates come from the register, never from the AI."""
    by = lambda rows, i: next((r for r in rows if r["id"] == i), None)
    for row in ([by(ref["assets"], a) for a in t["asset_ids"]] + [by(ref["chokepoints"], t["chokepoint_id"]),
                                                                  by(ref["ports"], t["port_id"])]):
        if row:
            return row["lon"], row["lat"]
    c = next((c for c in ref["countries"] if c["iso"] == t["country_iso3"]), None)
    return (c["lon"], c["lat"]) if c else (20.0, 0.0)


def find_duplicate(t, recent, loc=None):
    for e in recent:
        same_place = loc is not None and (round(e["lon"], 3), round(e["lat"], 3)) == (round(loc[0], 3), round(loc[1], 3))
        if e["iso"] != t["country_iso3"] and not same_place:
            continue
        days = abs((date.fromisoformat(str(e["date"])[:10]) - date.fromisoformat(t["event_date"])).days)
        if days > 4:
            continue
        same_asset = set(e.get("asset_ids") or []) & set(t["asset_ids"]) and e["type"] == t["type"]
        if same_asset or (same_place and e["type"] == t["type"]) or similar(e["title"], t["title"]) >= 0.45:
            return e
    return None


def review_status(t):
    if t["severity"] >= config.REVIEW_FROM_SEVERITY or t["confidence"] < config.REVIEW_BELOW_CONFIDENCE:
        return "pending"
    return "auto"


def to_event(t, raw, ref):
    lon, lat = locate(t, ref)
    return {
        "date": t["event_date"], "iso": t["country_iso3"], "lon": lon, "lat": lat, "type": t["type"],
        "severity": t["severity"], "metals": t["metals"], "title": t["title"][:200], "summary": t["summary"][:1200],
        "corridors": t["corridor_ids"], "asset_ids": t["asset_ids"], "status": review_status(t),
        "confidence": round(t["confidence"], 2), "source_urls": [raw["url"]] if raw.get("url") else [],
        "sector": sector_of(t["metals"]),
        "region": region_of(t["country_iso3"], ref),
    }


def region_of(iso, ref):
    if iso == "XX":
        return "all"
    c = next((c for c in ref["countries"] if c["iso"] == iso), None)
    return (c or {}).get("region") or "africa"


def sector_of(codes):
    e = any(c in ENERGY_CODES for c in codes)
    m = any(c not in ENERGY_CODES for c in codes)
    return "all" if (e and m) else ("energy" if e else "metals")
