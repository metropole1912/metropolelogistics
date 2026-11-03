"""Import JODI World Oil Database consumption data into the demand table.

1. Download the CSV (free): https://www.jodidata.org/oil/database/data-downloads.aspx
   Pick the "World Oil Database (primary + secondary)" CSV. Columns look like:
     REF_AREA, TIME_PERIOD, ENERGY_PRODUCT, FLOW_BREAKDOWN, UNIT_MEASURE, OBS_VALUE, ASSESSMENT_CODE
   (older files use lower-case or spaced names; both are handled)
2. Run:   python tools/import_jodi.py path/to/world_Oil_Database.csv
   Writes register/jodi_demand.json with yearly averages (kb/d) of total products demand per country.
3. Push:  python tools/import_jodi.py path/to/world_Oil_Database.csv --push
   Upserts into the demand table (replacing the desk snapshot rows for those countries and years).

Options:
  --product TOTPRODS   JODI product code (TOTPRODS total products, CRUDEOIL, GASOLINE, GASDIES, JETKERO, LPG, RESFUEL)
  --flow TOTDEMO       flow code (TOTDEMO = total demand / consumption)
  --min-months 9       keep a year only when at least this many months are present (the current year is kept as a partial)
  --since 2015         first year to keep
"""
import argparse
import csv
import sys as _sys
from collections import defaultdict
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from registry import iso3, push, save

PRODUCT_LABEL = {"TOTPRODS": "total", "CRUDEOIL": "crude", "GASOLINE": "gasoline", "GASDIES": "diesel", "JETKERO": "jet",
                 "LPG": "lpg", "RESFUEL": "fuel_oil", "NAPHTHA": "naphtha", "OTHERKERO": "kerosene"}
# JODI uses a few non-ISO area codes
AREA_FIX = {"UK": "GB", "EL": "GR", "KO": "KR", "VE": "VE", "TW": "TW", "XK": "XK"}


def find(headers, *names):
    low = {h.strip().lower().replace(" ", "_"): h for h in headers}
    for n in names:
        if n in low:
            return low[n]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--product", default="TOTPRODS")
    ap.add_argument("--flow", default="TOTDEMO")
    ap.add_argument("--min-months", type=int, default=9)
    ap.add_argument("--since", type=int, default=2015)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    months = defaultdict(list)   # (iso3, year) -> [kbd values]
    units = set()
    with open(a.file, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        h = rd.fieldnames or []
        c_area, c_time = find(h, "ref_area", "country"), find(h, "time_period", "date", "time")
        c_prod, c_flow = find(h, "energy_product", "product"), find(h, "flow_breakdown", "flow")
        c_unit, c_val = find(h, "unit_measure", "unit"), find(h, "obs_value", "value", "quantity")
        if not all([c_area, c_time, c_prod, c_flow, c_val]):
            raise SystemExit(f"Could not find the JODI columns in {h}")
        print(f"Columns: area={c_area!r} time={c_time!r} product={c_prod!r} flow={c_flow!r} value={c_val!r}")
        for r in rd:
            if r[c_prod].strip().upper() != a.product.upper() or r[c_flow].strip().upper() != a.flow.upper():
                continue
            unit = (r[c_unit] or "").strip().upper() if c_unit else "KBD"
            if unit not in ("KBD", "KBBL/D", "KB/D", "THOUSAND_BARRELS_DAILY", "TBD"):
                units.add(unit)
                continue
            try:
                v = float(r[c_val])
            except (TypeError, ValueError):
                continue
            t = r[c_time].strip()
            year = int(t[:4])
            if year < a.since:
                continue
            code = AREA_FIX.get(r[c_area].strip().upper(), r[c_area].strip().upper())
            iso = iso3(code)
            if not iso:
                continue
            months[(iso, year)].append(v)
    if units:
        print(f"Skipped rows in other units: {sorted(units)} (re-run with the KBD file; JODI offers kb/d and kt)")

    from datetime import date
    this_year = date.today().year
    out = []
    for (iso, year), vals in sorted(months.items()):
        if len(vals) < a.min_months and year != this_year:
            continue
        out.append({"iso": iso, "year": year, "product": PRODUCT_LABEL.get(a.product.upper(), a.product.lower()),
                    "flow": "demand", "kbd": round(sum(vals) / len(vals), 1), "months": len(vals), "source": "JODI"})
    print(f"Yearly averages: {len(out)} rows for {len({r['iso'] for r in out})} countries, {min(r['year'] for r in out) if out else '-'}–{max(r['year'] for r in out) if out else '-'}")
    top = sorted((r for r in out if r["year"] == max(x["year"] for x in out if x["months"] >= a.min_months)), key=lambda r: -r["kbd"])[:10] if out else []
    for r in top:
        print(f"  {r['iso']} {r['year']} {r['kbd']:>9,.0f} kb/d ({r['months']} months)")
    save("jodi_demand", out)
    if a.push:
        push("demand", "iso,year,product,flow", out)


if __name__ == "__main__":
    main()
