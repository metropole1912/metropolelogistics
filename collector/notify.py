"""Email alerts through Resend (resend.com). Without RESEND_API_KEY, emails are printed and saved to local_db/outbox/."""
import html
import json
from datetime import datetime, timezone

import requests

from . import config

SEV = {1: ("Low", "#3f8f6e"), 2: ("Elevated", "#c9a227"), 3: ("High", "#e07b20"), 4: ("Critical", "#d83a3f")}


def matches(sub, ev):
    if ev["severity"] < sub.get("min_severity", 3):
        return False
    if sub.get("countries") and ev["iso"] not in sub["countries"]:
        return False
    if sub.get("sectors") and ev.get("sector", "metals") != "all" and ev.get("sector", "metals") not in sub["sectors"]:
        return False
    if sub.get("metals") and not set(sub["metals"]) & set(ev.get("metals") or []):
        return False
    if sub.get("corridors") and not set(sub["corridors"]) & set(ev.get("corridors") or []):
        return False
    return True


def event_html(ev):
    name, color = SEV[ev["severity"]]
    src = "".join(f'<a href="{html.escape(u)}" style="color:#2a7fa0">source</a> ' for u in (ev.get("source_urls") or [])[:3])
    return (f'<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb">'
            f'<div style="font:600 11px monospace;letter-spacing:.08em;color:{color}">{name.upper()} · {html.escape(str(ev["date"]))} · {html.escape(ev["iso"])}</div>'
            f'<div style="font:600 16px Arial,sans-serif;margin:4px 0;color:#111">{html.escape(ev["title"])}</div>'
            f'<div style="font:14px/1.5 Arial,sans-serif;color:#333">{html.escape(ev.get("summary", ""))}</div>'
            f'<div style="font:12px Arial,sans-serif;margin-top:4px">{src}</div></td></tr>')


def wrap(heading, rows_html, footer=""):
    link = f'<p style="font:13px Arial,sans-serif"><a href="{html.escape(config.SITE_URL)}#alerts">Open the map</a></p>' if config.SITE_URL else ""
    return (f'<div style="max-width:620px;margin:0 auto;padding:20px">'
            f'<div style="font:700 13px monospace;letter-spacing:.16em;color:#9a7a20">METROPOLE LOGISTICS</div>'
            f'<h2 style="font:600 20px Arial,sans-serif;margin:6px 0 10px">{html.escape(heading)}</h2>'
            f'<table style="width:100%;border-collapse:collapse">{rows_html}</table>{link}'
            f'<p style="font:11px Arial,sans-serif;color:#888">{footer or "Information only; not investment advice. Reply to unsubscribe."}</p></div>')


def send(to, subject, body_html):
    if not config.RESEND_API_KEY:
        out = config.LOCAL_DB_DIR / "outbox"
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        (out / f"{stamp}.html").write_text(f"<!-- to: {to} | subject: {subject} -->\n{body_html}")
        print(f"  email (not sent, no RESEND_API_KEY): to={to} subject={subject!r}")
        return True
    r = requests.post("https://api.resend.com/emails", timeout=30,
                      headers={"Authorization": f"Bearer {config.RESEND_API_KEY}", "Content-Type": "application/json"},
                      data=json.dumps({"from": config.ALERT_FROM, "to": [to], "subject": subject, "html": body_html}))
    if r.status_code >= 300:
        print(f"  email to {to} failed: {r.status_code} {r.text[:200]}")
        return False
    return True


def instant_alerts(db):
    events = db.events_to_notify()
    if not events:
        return 0
    sent = 0
    for sub in db.subscribers():
        if not sub.get("instant", True):
            continue
        mine = [e for e in events if matches(sub, e)]
        if not mine:
            continue
        top = max(mine, key=lambda e: e["severity"])
        subject = f"[{SEV[top['severity']][0]}] {top['title']}" + (f" (+{len(mine) - 1} more)" if len(mine) > 1 else "")
        if send(sub["email"], subject[:180], wrap("New alerts", "".join(event_html(e) for e in sorted(mine, key=lambda e: -e["severity"])))):
            sent += 1
    stamp = datetime.now(timezone.utc).isoformat()
    for e in events:
        db.update_event(e["id"], {"notified_at": stamp})
    return sent


def review_request(db, new_pending):
    if not new_pending or not config.ADMIN_EMAIL:
        return
    send(config.ADMIN_EMAIL, f"{len(new_pending)} event(s) waiting for your review",
         wrap("Waiting for review", "".join(event_html(e) for e in new_pending),
              "Approve or reject in Supabase: Table Editor > events > filter status = pending, then set status to approved or rejected."))


def daily_digest(db):
    events = db.events_since(24)
    n = 0
    for sub in db.subscribers():
        if not sub.get("digest", True):
            continue
        digest_sub = {**sub, "min_severity": 1}
        mine = [e for e in events if matches(digest_sub, e)]
        if mine and send(sub["email"], f"Metropole daily: {len(mine)} event(s)", wrap("Last 24 hours", "".join(event_html(e) for e in mine))):
            n += 1
    return n
