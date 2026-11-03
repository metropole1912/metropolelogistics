"""GDELT DOC 2.0 API — free global news search, updated every 15 minutes.
Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/

GDELT limits how often one computer can query it. If it answers "429 Too Many Requests",
we wait and retry, then skip GDELT for this run (other sources still run). Running the
collector several times in a row from the same computer is the usual cause; wait ~10 minutes.
"""
import json
import time
from datetime import datetime, timezone

import requests

from .. import config
from . import item

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GAP_SECONDS = 12          # pause between queries (GDELT asks for at least 5)
RETRY_WAITS = [20, 45]    # pauses before retrying a rate-limited query

# Each query is: (metal / mining terms) AND (place terms). GDELT does not allow nested OR groups,
# so we run a few short queries (GDELT rejects long ones). Edit these to widen or narrow coverage.
METAL_TERMS = '(cobalt OR copper OR lithium OR bauxite OR manganese OR "iron ore" OR platinum OR uranium OR gold OR mining)'
ENERGY_TERMS = '(crude OR refinery OR LNG OR pipeline OR tanker OR "force majeure" OR "oil field" OR gasoline OR diesel)'
# (terms, places) pairs — each must stay short; GDELT rejects long queries
QUERIES = [
    (METAL_TERMS, '(Congo OR DRC OR Zambia OR Katanga OR Kolwezi OR Copperbelt OR Kasumbalesa OR Lobito)'),
    (METAL_TERMS, '("South Africa" OR Zimbabwe OR Namibia OR Mozambique OR Tanzania OR Angola OR Transnet OR Durban)'),
    (METAL_TERMS, '(Mali OR Guinea OR "Burkina Faso" OR Niger OR Ghana OR Gabon OR Madagascar OR Simandou)'),
    (ENERGY_TERMS, '(Nigeria OR Dangote OR Libya OR Sharara OR Algeria OR Sonatrach OR Egypt OR Zohr)'),
    (ENERGY_TERMS, '(Mozambique OR EACOP OR Uganda OR Angola OR Senegal OR Mauritania OR "South Sudan" OR Chad)'),
    (ENERGY_TERMS, '(Hormuz OR "Red Sea" OR Houthi OR "Suez Canal" OR "Bab el-Mandeb" OR "Gulf of Guinea")'),
    # South America
    (METAL_TERMS, '(Chile OR Peru OR Codelco OR Escondida OR "Las Bambas" OR Antofagasta OR Bolivia OR Uyuni)'),
    (METAL_TERMS, '(Brazil OR Vale OR Carajás OR Argentina OR "Vaca Muerta" OR Panama OR "Cobre Panama" OR Ecuador)'),
    (ENERGY_TERMS, '(Venezuela OR PDVSA OR Guyana OR Stabroek OR Petrobras OR Ecopetrol OR Trinidad OR "Panama Canal")'),
]
PLACE_GROUPS = [q[1] for q in QUERIES]  # kept for compatibility


def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        return None


def parse(payload):
    out = []
    for a in (payload or {}).get("articles", []):
        if not a.get("title") or not a.get("url"):
            continue
        out.append(item("gdelt", a["title"], url=a["url"],
                        body=f'{a.get("domain", "")} · {a.get("sourcecountry", "")}',
                        published_at=_parse_date(a.get("seendate", ""))))
    return out


def _query(params):
    """Returns (items, rate_limited)."""
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            r = requests.get(URL, params=params, timeout=45, headers={"User-Agent": config.USER_AGENT})
        except Exception as e:
            print(f"  gdelt: network error: {e}")
            return [], False
        if r.status_code == 429:
            if attempt < len(RETRY_WAITS):
                wait = RETRY_WAITS[attempt]
                print(f"  gdelt: rate limited, waiting {wait}s and retrying...")
                time.sleep(wait)
                continue
            return [], True
        if r.status_code >= 400:
            print(f"  gdelt: error {r.status_code}")
            return [], False
        text = r.text.strip()
        if not text.startswith("{"):
            # GDELT replies in plain text when the query is malformed or too long
            print(f"  gdelt: unexpected reply: {text[:120]}")
            return [], False
        return parse(json.loads(text)), False
    return [], True


def fetch(ref, sample=False):
    if sample:
        return parse(json.loads((config.ROOT / "collector" / "fixtures" / "gdelt.json").read_text()))
    results = []
    hours = max(1, config.LOOKBACK_HOURS)
    for i, (terms, places) in enumerate(QUERIES):
        if i:
            time.sleep(GAP_SECONDS)
        params = {"query": f"{terms} {places} sourcelang:english", "mode": "artlist", "format": "json",
                  "maxrecords": 100, "timespan": f"{hours}h", "sort": "datedesc"}
        got, limited = _query(params)
        if limited:
            print("  gdelt: still rate limited; skipping GDELT this run. Wait about 10 minutes before running again.")
            break
        results += got
    return results
