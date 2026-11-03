"""Draw a corridor's real path from OpenStreetMap, replacing the desk's hand-drawn waypoints.

Examples:
  python tools/fetch_osm_corridors.py --id eacop --name "EACOP" --type pipeline
  python tools/fetch_osm_corridors.py --id lobito --name "Benguela" --type railway --bbox 12,-13.5,26,-9.5
  python tools/fetch_osm_corridors.py --id transmed --name "Transmed" --type pipeline --push

It queries the free Overpass API (no key), joins the mapped segments into one line, simplifies it,
writes register/osm_<id>.json, and with --push updates that corridor's `path` in the database.
Coverage varies: if OSM has only part of a line you will see a short path; keep the desk waypoints in that case.
Data © OpenStreetMap contributors, ODbL.
"""
import argparse
import json
import math

import requests

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from registry import push, save

OVERPASS = "https://overpass-api.de/api/interpreter"
TAGS = {"pipeline": '["man_made"="pipeline"]', "railway": '["railway"~"rail|narrow_gauge"]', "road": '["highway"~"trunk|primary|motorway"]'}


def fetch_ways(name, kind, bbox):
    area = f"({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]})" if bbox else ""
    q = f'[out:json][timeout:120];way{TAGS[kind]}["name"~"{name}",i]{area};(._;>;);out body;'
    r = requests.post(OVERPASS, data={"data": q}, timeout=180, headers={"User-Agent": "MetropoleLogistics-Importer/0.1"})
    r.raise_for_status()
    els = r.json()["elements"]
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in els if e["type"] == "node"}
    ways = [[nodes[n] for n in e["nodes"] if n in nodes] for e in els if e["type"] == "way"]
    return [w for w in ways if len(w) >= 2]


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def chain(ways):
    """Greedy join of segments end-to-end into one polyline."""
    ways = [list(w) for w in ways]
    line = ways.pop(0)
    while ways:
        best, best_d, flip, at_end = None, 1e9, False, True
        for i, w in enumerate(ways):
            for f in (False, True):
                ww = w[::-1] if f else w
                d1, d2 = dist(line[-1], ww[0]), dist(line[0], ww[-1])
                if d1 < best_d:
                    best, best_d, flip, at_end = i, d1, f, True
                if d2 < best_d:
                    best, best_d, flip, at_end = i, d2, f, False
        w = ways.pop(best)
        w = w[::-1] if flip else w
        if best_d > 1.0:  # more than ~100 km gap: probably a different line with the same name
            break
        line = line + w if at_end else w + line
    return line


def simplify(pts, tol=0.02):
    """Douglas–Peucker so the path stays small enough for the page."""
    if len(pts) < 3:
        return pts

    def perp(p, a, b):
        if a == b:
            return dist(p, a)
        t = max(0, min(1, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / (dist(a, b) ** 2)))
        return dist(p, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))

    dmax, idx = 0, 0
    for i in range(1, len(pts) - 1):
        d = perp(pts[i], pts[0], pts[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > tol:
        return simplify(pts[:idx + 1], tol)[:-1] + simplify(pts[idx:], tol)
    return [pts[0], pts[-1]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="corridor id in the register, e.g. eacop")
    ap.add_argument("--name", required=True, help="name as it appears on OpenStreetMap (regex, case-insensitive)")
    ap.add_argument("--type", default="pipeline", choices=list(TAGS))
    ap.add_argument("--bbox", help="lon_min,lat_min,lon_max,lat_max to limit the search")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    bbox = [float(x) for x in a.bbox.split(",")] if a.bbox else None
    ways = fetch_ways(a.name, a.type, bbox)
    if not ways:
        raise SystemExit("OpenStreetMap returned no mapped segments for that name; try a different spelling or add --bbox")
    line = simplify(chain(ways))
    length_km = sum(dist(line[i], line[i + 1]) for i in range(len(line) - 1)) * 111
    print(f"{len(ways)} segments joined into a {len(line)}-point path, about {length_km:.0f} km")
    row = {"id": a.id, "path": [[round(x, 4), round(y, 4)] for x, y in line]}
    save(f"osm_{a.id}", [row])
    if a.push:
        push("corridors", "id", [row])


if __name__ == "__main__":
    main()
