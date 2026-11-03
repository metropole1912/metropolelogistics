"""ReliefWeb API v2 — UN/NGO situation reports. Free, but needs an approved "appname".
Request one at https://apidoc.reliefweb.int/ and put it in RELIEFWEB_APPNAME. Skipped until set.
"""
import json
from datetime import datetime, timedelta, timezone

import requests

from .. import config
from . import item

URL = "https://api.reliefweb.int/v2/reports"


def parse(payload):
    out = []
    for d in (payload or {}).get("data", []):
        f = d.get("fields", {})
        title = f.get("title")
        if not title:
            continue
        url = f.get("url_alias") or f.get("url") or d.get("href", "")
        countries = ", ".join(c.get("iso3", "").upper() for c in f.get("country", []) if isinstance(c, dict))
        out.append(item("reliefweb", title, url=url, body=f"[{countries}] " + (f.get("body") or "")[:1500],
                        published_at=(f.get("date") or {}).get("created"), external_id=str(d.get("id", ""))))
    return out


def fetch(ref, sample=False):
    if sample:
        return parse(json.loads((config.ROOT / "collector" / "fixtures" / "reliefweb.json").read_text()))
    if not config.RELIEFWEB_APPNAME:
        print("  reliefweb: skipped (set RELIEFWEB_APPNAME once ReliefWeb approves your app name)")
        return []
    since = (datetime.now(timezone.utc) - timedelta(hours=max(config.LOOKBACK_HOURS, 24))).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    body = {
        "limit": 60,
        "sort": ["date.created:desc"],
        "query": {"value": "mining OR mine OR minerals OR cobalt OR copper OR gold OR lithium OR corridor OR border OR fuel", "operator": "OR"},
        "fields": {"include": ["title", "url", "url_alias", "date.created", "country.iso3", "body"]},
        "filter": {"operator": "AND", "conditions": [
            {"field": "country.iso3", "value": [c["iso"].lower() for c in ref["countries"]], "operator": "OR"},
            {"field": "date.created", "value": {"from": since}},
        ]},
    }
    try:
        r = requests.post(URL, params={"appname": config.RELIEFWEB_APPNAME}, json=body, timeout=40,
                          headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        return parse(r.json())
    except Exception as e:
        print(f"  reliefweb: failed: {e}")
        return []
