"""Database access.

Two interchangeable backends with the same methods:
- SupabaseDB talks to your Supabase project over its REST API (used when SUPABASE_URL is set).
- LocalDB keeps everything in JSON files under ./local_db (for testing on your own computer).
"""
import json
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config

REFERENCE_TABLES = ["countries", "assets", "ports", "corridors", "chokepoints"]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class SupabaseDB:
    def __init__(self, url, key):
        self.base = url + "/rest/v1/"
        # Works with both key styles: legacy JWT keys (eyJ...) and the newer sb_secret_... keys,
        # which must be sent only in the apikey header.
        self.h = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):
            self.h["Authorization"] = f"Bearer {key}"

    def _req(self, method, path, **kw):
        for attempt in range(3):
            r = requests.request(method, self.base + path, headers={**self.h, **kw.pop("headers", {})}, timeout=30, **kw)
            if r.status_code < 500:
                break
            time.sleep(2 * (attempt + 1))
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase {method} {path} failed: {r.status_code} {r.text[:300]}")
        return r.json() if r.text else None

    def select(self, table, query="select=*"):
        return self._req("GET", f"{table}?{query}")

    def reference(self):
        return {t: self.select(t) for t in REFERENCE_TABLES}

    def insert_raw(self, items):
        """Insert new raw items; silently skips ones already seen. Returns the ones that were new."""
        if not items:
            return []
        return self._req("POST", "raw_items?on_conflict=fingerprint", json=items,
                         headers={"Prefer": "resolution=ignore-duplicates,return=representation"}) or []

    def unprocessed(self, limit):
        return self.select("raw_items", f"select=*&processed=eq.false&order=fetched_at.asc&limit={limit}")

    def mark_raw(self, raw_id, relevant, event_id=None):
        self._req("PATCH", f"raw_items?id=eq.{raw_id}", json={"processed": True, "relevant": relevant, "event_id": event_id})

    def recent_events(self, days):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        return self.select("events", f"select=*&date=gte.{since}&status=neq.rejected")

    def insert_event(self, ev):
        return self._req("POST", "events", json=ev, headers={"Prefer": "return=representation"})[0]

    def update_event(self, ev_id, patch):
        self._req("PATCH", f"events?id=eq.{ev_id}", json=patch)

    def events_to_notify(self):
        return self.select("events", "select=*&notified_at=is.null&status=in.(auto,approved)&order=date.asc")

    def pending_events(self):
        return self.select("events", "select=*&status=eq.pending&order=created_at.desc")

    def events_since(self, hours):
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self.select("events", f"select=*&created_at=gte.{since}&status=in.(auto,approved)&order=severity.desc")

    def subscribers(self):
        return self.select("subscribers", "select=*&active=eq.true")

    def upsert_sanction_hits(self, hits):
        if not hits:
            return []
        return self._req("POST", "sanctions_hits?on_conflict=list_name,entry_name,matched_name", json=hits,
                         headers={"Prefer": "resolution=ignore-duplicates,return=representation"}) or []

    def upsert_prices(self, rows):
        if not rows:
            return 0
        self._req("POST", "prices?on_conflict=series,date", json=rows, headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return len(rows)

    def rpc(self, fn, args=None):
        """Call a Postgres function exposed through PostgREST (e.g. prune_vessels)."""
        return self._req("POST", f"rpc/{fn}", json=args or {})

    def upsert_rows(self, table, rows, key):
        """Generic upsert used by the register importers."""
        if not rows:
            return 0
        self._req("POST", f"{table}?on_conflict={key}", json=rows, headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return len(rows)


class LocalDB:
    """Same interface, stored as JSON files. Starts from supabase/seed_data.json."""

    def __init__(self, folder):
        self.dir = folder
        self.dir.mkdir(exist_ok=True)
        self.f = self.dir / "db.json"
        if self.f.exists():
            self.d = json.loads(self.f.read_text())
        else:
            self.d = self._seed()
            self._save()

    def _seed(self):
        import re
        seed = json.loads((config.ROOT / "supabase" / "seed_data.json").read_text())
        slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
        return {
            "countries": [{"iso": c["iso"], "name": c["name"], "lon": c["c"][0], "lat": c["c"][1], "metals": c["metals"], "sectors": c.get("sectors", ["metals"]), "region": c.get("region", "africa"), "tier": c.get("tier", "desk")} for c in seed["COUNTRIES"]],
            "assets": [{"id": slug(m[0]), "name": m[0], "iso": m[1], "metals": m[2], "operator": m[3], "lon": m[4], "lat": m[5], "status": m[6],
                        "kind": (m[8] if len(m) > 8 and m[8] else ("plant" if m[6] == "plant" else "mine")), "aliases": []} for m in seed["MINES"]],
            "ports": [{"id": slug(p[0]), "name": p[0], "iso": p[1], "lon": p[2], "lat": p[3]} for p in seed["PORTS"]],
            "corridors": [{"id": c["id"], "name": c["name"], "metals": c["metals"], "serves": c["serves"]} for c in seed["CORRIDORS"]],
            "chokepoints": [{"id": slug(c[0]), "name": c[0], "between_text": c[1], "lon": c[2], "lat": c[3]} for c in seed["CHOKES"]],
            "raw_items": [], "events": [], "subscribers": [], "sanctions_hits": [], "prices": [], "_seq": 1,
        }

    def _save(self):
        self.f.write_text(json.dumps(self.d, indent=1))

    def _id(self):
        self.d["_seq"] += 1
        return self.d["_seq"]

    def reference(self):
        return {t: self.d[t] for t in REFERENCE_TABLES}

    def insert_raw(self, items):
        seen = {r["fingerprint"] for r in self.d["raw_items"]}
        new = []
        for it in items:
            if it["fingerprint"] in seen:
                continue
            seen.add(it["fingerprint"])
            row = {**it, "id": self._id(), "processed": False, "relevant": None, "event_id": None, "fetched_at": now_iso()}
            self.d["raw_items"].append(row)
            new.append(row)
        self._save()
        return new

    def unprocessed(self, limit):
        return [r for r in self.d["raw_items"] if not r["processed"]][:limit]

    def mark_raw(self, raw_id, relevant, event_id=None):
        for r in self.d["raw_items"]:
            if r["id"] == raw_id:
                r.update(processed=True, relevant=relevant, event_id=event_id)
        self._save()

    def recent_events(self, days):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        return [e for e in self.d["events"] if e["date"] >= since and e["status"] != "rejected"]

    def insert_event(self, ev):
        row = {**ev, "id": self._id(), "created_at": now_iso(), "notified_at": None}
        self.d["events"].append(row)
        self._save()
        return row

    def update_event(self, ev_id, patch):
        for e in self.d["events"]:
            if e["id"] == ev_id:
                e.update(patch)
        self._save()

    def events_to_notify(self):
        return [e for e in self.d["events"] if not e.get("notified_at") and e["status"] in ("auto", "approved")]

    def pending_events(self):
        return [e for e in self.d["events"] if e["status"] == "pending"]

    def events_since(self, hours):
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        return [e for e in self.d["events"] if e["created_at"] >= since and e["status"] in ("auto", "approved")]

    def subscribers(self):
        return [s for s in self.d["subscribers"] if s.get("active", True)]

    def upsert_prices(self, rows):
        have = {(r["series"], r["date"]): r for r in self.d.setdefault("prices", [])}
        for r in rows:
            have[(r["series"], r["date"])] = r
        self.d["prices"] = list(have.values())
        self._save()
        return len(rows)

    def upsert_rows(self, table, rows, key):
        have = {r[key]: r for r in self.d.setdefault(table, [])}
        for r in rows:
            have[r[key]] = {**have.get(r[key], {}), **r}
        self.d[table] = list(have.values())
        self._save()
        return len(rows)

    def upsert_sanction_hits(self, hits):
        keys = {(h["list_name"], h["entry_name"], h["matched_name"]) for h in self.d["sanctions_hits"]}
        new = [h for h in hits if (h["list_name"], h["entry_name"], h["matched_name"]) not in keys]
        self.d["sanctions_hits"].extend(new)
        self._save()
        return new


def connect():
    url, key = config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY
    placeholder = "YOUR-PROJECT" in url or key.startswith("paste-")
    if url and key and not placeholder:
        return SupabaseDB(url, key)
    if url or key:
        missing = [n for n, v in [("SUPABASE_URL", url), ("SUPABASE_SERVICE_KEY", key)] if not v or "YOUR-PROJECT" in v or v.startswith("paste-")]
        print(f"  Note: {', '.join(missing)} in .env is empty or still the example text, so the local test database is used.")
    return LocalDB(config.LOCAL_DB_DIR)
