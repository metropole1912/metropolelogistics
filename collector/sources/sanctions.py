"""Screens operators in your asset register (and names in collector/watch_names.txt)
against the US OFAC SDN list. Every hit is a *possible* match for you to check by hand.
"""
import csv
import difflib
import io
import re

import requests

from .. import config

SUFFIXES = {"ltd", "limited", "sa", "sarl", "sàrl", "plc", "inc", "llc", "co", "company", "group", "corp",
            "corporation", "holding", "holdings", "the", "pty", "ag", "gmbh", "bv", "nv", "spa", "srl", "fze", "dmcc"}
WATCH_FILE = config.ROOT / "collector" / "watch_names.txt"
VESSEL_FILE = config.ROOT / "collector" / "watch_vessels.txt"


def norm(name):
    words = re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
    return " ".join(w for w in words if w not in SUFFIXES)


def load_sdn(text):
    rows = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 4:
            continue
        clean = [c.strip() for c in r]
        clean = ["" if c == "-0-" else c for c in clean]
        rows.append({"name": clean[1], "type": clean[2], "program": clean[3]})
    return rows


def names_to_screen(ref):
    """Operator names from the asset register, split on '/' and ',' (joint ventures), plus your watch list."""
    out = []
    for a in ref["assets"]:
        for part in re.split(r"[/,;()]", a.get("operator", "")):
            part = re.sub(r"\b\d+%|\bsince\b.*|\bfrom\b.*", "", part).strip()
            if len(norm(part)) >= 4:
                out.append((part, a["id"]))
    if WATCH_FILE.exists():
        for line in WATCH_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append((line, None))
    return out


def screen(ref, sdn_rows, threshold=0.92):
    index = {}
    for r in sdn_rows:
        n = norm(r["name"])
        if len(n) >= 4:
            index.setdefault(n, r)
    keys = list(index)
    hits = []
    for name, asset_id in names_to_screen(ref):
        n = norm(name)
        if n in index:
            cand, score = [n], 1.0
        else:
            cand = difflib.get_close_matches(n, keys, n=1, cutoff=threshold) if len(n) >= 8 else []
            score = difflib.SequenceMatcher(None, n, cand[0]).ratio() if cand else 0
        if cand:
            r = index[cand[0]]
            hits.append({"list_name": "OFAC SDN", "entry_name": r["name"], "entry_program": r["program"],
                         "matched_name": name, "asset_id": asset_id, "score": round(score, 3),
                         "entry_type": "individual" if r["type"].lower() == "individual" else "entity"})
    return hits


def vessel_names():
    if not VESSEL_FILE.exists():
        return []
    return [l.strip() for l in VESSEL_FILE.read_text().splitlines() if l.strip() and not l.startswith("#")]


def screen_vessels(sdn_rows, names, threshold=0.9):
    """Match vessel names (from collector/watch_vessels.txt) against SDN entries typed 'vessel'."""
    index = {}
    for r in sdn_rows:
        if r["type"].lower() != "vessel":
            continue
        n = norm(r["name"])
        if len(n) >= 3:
            index.setdefault(n, r)
    keys = list(index)
    hits = []
    for name in names:
        n = norm(name)
        if n in index:
            cand, score = [n], 1.0
        else:
            cand = difflib.get_close_matches(n, keys, n=1, cutoff=threshold) if len(n) >= 5 else []
            score = difflib.SequenceMatcher(None, n, cand[0]).ratio() if cand else 0
        if cand:
            r = index[cand[0]]
            hits.append({"list_name": "OFAC SDN", "entry_name": r["name"], "entry_program": r["program"],
                         "matched_name": name, "asset_id": None, "score": round(score, 3), "entry_type": "vessel"})
    return hits


def fetch_sdn(sample=False):
    if sample:
        return (config.ROOT / "collector" / "fixtures" / "sdn_sample.csv").read_text()
    r = requests.get(config.OFAC_SDN_URL, timeout=120, headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    return r.content.decode("latin-1")
