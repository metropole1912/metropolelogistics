"""Metropole Logistics collector.

  python -m collector run              fetch, tag, store, send alerts (normal mode)
  python -m collector run --sample     same, but with built-in sample data (no internet, good for testing)
  python -m collector digest           send the daily digest email
  python -m collector sanctions        screen operators and watch-listed vessels against the OFAC sanctions list
  python -m collector prices           pull daily benchmark prices from the US EIA (needs EIA_API_KEY)
  python -m collector status           show what is in the database
"""
import argparse
import sys
import time

from . import config, db as dbmod, notify, process
from .sources import eia, gdelt, reliefweb, rss, sanctions
from .tagger import make_tagger, prefilter

SOURCES = [("gdelt", gdelt), ("reliefweb", reliefweb), ("rss", rss)]


def run(sample=False, send_email=True):
    db = dbmod.connect()
    print(f"Database: {type(db).__name__}")
    ref = db.reference()

    # 1. collect
    items = []
    for name, mod in SOURCES:
        got = mod.fetch(ref, sample=sample)
        print(f"  {name}: {len(got)} items")
        items += got
    new = db.insert_raw(items)
    print(f"New (not seen before): {len(new)}")

    # 2. filter + tag
    tagger = make_tagger(ref)
    print(f"Tagger: {tagger.name}")
    todo = db.unprocessed(config.MAX_ITEMS_PER_RUN)
    recent = db.recent_events(7)
    created, merged, skipped, new_pending = 0, 0, 0, []
    for raw in todo:
        if not prefilter(raw, ref):
            db.mark_raw(raw["id"], False)
            skipped += 1
            continue
        try:
            tags = tagger.tag(raw)
        except Exception as e:
            print(f"  tagging failed for {raw['title'][:60]!r}: {e}")
            continue  # left unprocessed, retried next run
        t = process.clean(tags, ref) if tags.get("relevant") else None
        if not t:
            db.mark_raw(raw["id"], False)
            skipped += 1
            continue
        precise = bool(t["asset_ids"] or t.get("chokepoint_id") or t.get("port_id"))
        dup = process.find_duplicate(t, recent, process.locate(t, ref) if precise else None)
        if dup:
            urls = list(dict.fromkeys((dup.get("source_urls") or []) + ([raw["url"]] if raw.get("url") else [])))
            patch = {"source_urls": urls}
            if t["severity"] > dup["severity"]:
                patch["severity"] = t["severity"]
            db.update_event(dup["id"], patch)
            dup.update(patch)
            db.mark_raw(raw["id"], True, dup["id"])
            merged += 1
            continue
        ev = db.insert_event(process.to_event(t, raw, ref))
        recent.append(ev)
        db.mark_raw(raw["id"], True, ev["id"])
        created += 1
        if ev["status"] == "pending":
            new_pending.append(ev)
        if tagger.name == "claude":
            time.sleep(0.3)
    print(f"Events: {created} new ({len(new_pending)} need review), {merged} merged into existing, {skipped} not relevant")

    # 3. alerts
    if send_email:
        notify.review_request(db, new_pending)
        n = notify.instant_alerts(db)
        print(f"Alert emails sent: {n}")


def screen(sample=False):
    db = dbmod.connect()
    ref = db.reference()
    rows = sanctions.load_sdn(sanctions.fetch_sdn(sample))
    hits = sanctions.screen(ref, rows) + sanctions.screen_vessels(rows, sanctions.vessel_names())
    new = db.upsert_sanction_hits(hits)
    print(f"Screened {len(sanctions.names_to_screen(ref))} names and {len(sanctions.vessel_names())} vessels against {len(rows)} OFAC entries: {len(hits)} possible matches, {len(new)} new")
    for h in new:
        print(f"  CHECK: {h['matched_name']!r} ~ {h['entry_name']!r} [{h['entry_program']}] score {h['score']}")
    if new and config.ADMIN_EMAIL:
        notify.send(config.ADMIN_EMAIL, f"{len(new)} possible sanctions match(es) to check",
                    notify.wrap("Possible sanctions matches", "".join(
                        f"<tr><td style='padding:8px 0;font:14px Arial'>{h['matched_name']} ~ <b>{h['entry_name']}</b> ({h['entry_program']}), score {h['score']}</td></tr>" for h in new)))


def prices(sample=False):
    db = dbmod.connect()
    rows = eia.fetch(sample=sample)
    n = db.upsert_prices(rows)
    latest = {}
    for r in rows:
        if r["series"] not in latest or r["date"] > latest[r["series"]]["date"]:
            latest[r["series"]] = r
    print(f"Prices stored: {n}" + "".join(f"\n  {k}: {v['value']} {v['unit']} ({v['date']})" for k, v in latest.items()))


def status():
    db = dbmod.connect()
    ref = db.reference()
    print(f"Database: {type(db).__name__}")
    for k, v in ref.items():
        print(f"  {k}: {len(v)}")
    print(f"  events awaiting review: {len(db.pending_events())}")
    print(f"  events in last 7 days: {len(db.recent_events(7))}")
    print(f"  subscribers: {len(db.subscribers())}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="collector", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--sample", action="store_true", help="use built-in sample data instead of the internet")
    r.add_argument("--no-email", action="store_true")
    sub.add_parser("digest")
    s = sub.add_parser("sanctions")
    s.add_argument("--sample", action="store_true")
    pr = sub.add_parser("prices")
    pr.add_argument("--sample", action="store_true")
    sub.add_parser("status")
    a = p.parse_args(argv)
    if a.cmd == "run":
        run(sample=a.sample, send_email=not a.no_email)
    elif a.cmd == "digest":
        print(f"Digest emails sent: {notify.daily_digest(dbmod.connect())}")
    elif a.cmd == "sanctions":
        screen(sample=a.sample)
    elif a.cmd == "prices":
        prices(sample=a.sample)
    elif a.cmd == "status":
        status()


if __name__ == "__main__":
    sys.exit(main())
