"""US EIA open data — daily benchmark prices (free API key from https://www.eia.gov/opendata/).
Put the key in .env as EIA_API_KEY. Run with:  python -m collector prices
"""
import json

import requests

from .. import config

BASE = "https://api.eia.gov/v2"
# series id -> (route, our name, unit)
SERIES = {
    "RBRTE": ("petroleum/pri/spt/data/", "brent", "USD/bbl"),
    "RWTC": ("petroleum/pri/spt/data/", "wti", "USD/bbl"),
    "EER_EPD2F_PF4_Y35NY_DPG": ("petroleum/pri/spt/data/", "diesel_nyh", "USD/gal"),
    "EER_EPMRU_PF4_Y35NY_DPG": ("petroleum/pri/spt/data/", "gasoline_nyh", "USD/gal"),
    "RNGWHHD": ("natural-gas/pri/fut/data/", "henry_hub", "USD/MMBtu"),
}


def parse(payload, series_id):
    route, name, unit = SERIES[series_id]
    out = []
    for row in (payload.get("response") or {}).get("data", []):
        if row.get("series") != series_id or row.get("value") in (None, ""):
            continue
        out.append({"series": name, "date": row["period"][:10], "value": float(row["value"]), "unit": unit, "source": "EIA"})
    return out


def fetch(sample=False, days=30):
    if sample:
        return parse(json.loads((config.ROOT / "collector" / "fixtures" / "eia.json").read_text()), "RBRTE")
    key = config.env("EIA_API_KEY")
    if not key:
        print("  eia: skipped (set EIA_API_KEY from eia.gov/opendata)")
        return []
    rows = []
    for sid, (route, name, unit) in SERIES.items():
        params = {"api_key": key, "frequency": "daily", "data[0]": "value", "facets[series][]": sid,
                  "sort[0][column]": "period", "sort[0][direction]": "desc", "length": days}
        try:
            r = requests.get(f"{BASE}/{route}", params=params, timeout=40, headers={"User-Agent": config.USER_AGENT})
            r.raise_for_status()
            rows += parse(r.json(), sid)
        except Exception as e:
            print(f"  eia: {name} failed: {e}")
    return rows
