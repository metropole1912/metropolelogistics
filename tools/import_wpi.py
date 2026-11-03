"""Import the World Port Index (US National Geospatial-Intelligence Agency) into the ports register.

1. Download the CSV from https://msi.nga.mil/Publications/WPI  (look for the "Pub 150 ... CSV" download).
2. Run:   python tools/import_wpi.py path/to/UpdatedPub150.csv
   Writes register/wpi_ports.json with every port in the engine's countries.
3. Push:  python tools/import_wpi.py path/to/UpdatedPub150.csv --push

Ports are added alongside the desk's own ports; existing desk ports are not changed.
"""
import argparse

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from registry import find_col, iso3, num, push, read_table, save, slug


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--min-size", default="S", help="smallest harbor size to keep: L, M, S or V (very small). Default S.")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    keep = {"L": ["L"], "M": ["L", "M"], "S": ["L", "M", "S"], "V": ["L", "M", "S", "V"]}[a.min_size.upper()]

    headers, rows = read_table(a.file)
    c_name = find_col(headers, "main port name", "port name", "name")
    c_country = find_col(headers, "country code", "country")
    c_lat, c_lon = find_col(headers, "latitude", "lat"), find_col(headers, "longitude", "lon")
    c_size = find_col(headers, "harbor size", "harbour size", "size")
    c_oil = find_col(headers, "oil terminal depth", "petroleum")
    c_locode = find_col(headers, "un/locode", "locode")
    print(f"Columns used: name={c_name!r} country={c_country!r} lat={c_lat!r} lon={c_lon!r} size={c_size!r}")

    out, skipped = [], 0
    for r in rows:
        iso = iso3(r.get(c_country))
        lat, lon = num(r.get(c_lat)), num(r.get(c_lon))
        if not iso or lat is None or lon is None:
            skipped += 1
            continue
        size = str(r.get(c_size) or "").strip().upper()[:1]
        if c_size and size and size not in keep:
            skipped += 1
            continue
        name = str(r.get(c_name) or "").strip().title()
        oil = str(r.get(c_oil) or "").strip() if c_oil else ""
        note = "Source: World Port Index (NGA)." + (f" Harbor size {size}." if size else "") + (f" Oil terminal depth {oil} m." if oil and oil not in ("0", "0.0") else "")
        if c_locode and r.get(c_locode):
            note += f" UN/LOCODE {r.get(c_locode)}."
        out.append({"id": "wpi-" + slug(f"{iso}-{name}"), "name": name, "iso": iso, "lon": lon, "lat": lat, "note": note})
    print(f"Kept {len(out)} ports; skipped {skipped}")
    save("wpi_ports", out)
    if a.push:
        push("ports", "id", out)


if __name__ == "__main__":
    main()
