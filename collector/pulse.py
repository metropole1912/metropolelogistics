"""Corridor Pulse: turns daily reports from clearing agents into delay metrics.

  python -m collector pulse                 read new form answers, recompute the index, send alerts
  python -m collector pulse --sample        same, using collector/fixtures/pulse_sample.csv (synthetic) and the local test database
  python -m collector pulse-brief           draft this week's Copperbelt Corridor Weekly and email it to ADMIN_EMAIL

How it works
  1. Reporters answer a Google Form each morning about YESTERDAY. The form's answer sheet is published as CSV
     (File > Share > Publish to web > CSV) and its link goes in .env as PULSE_FORM_CSV_URL.
  2. Each answer becomes a row in border_reports (kept private: only the service key can read it).
  3. Reports are grouped into UNITS: crossing x direction x cargo x side. They are only ever combined with
     reports for the same unit, never across units.
  4. For each unit and day: median transit hours, median customs hours, causes ticked, number of reports,
     the unit's NORMAL (median of the previous 30 days once 7+ days exist, else the crossing's starting
     assumption), change vs normal, a status, and a confidence:
        high    2+ reports that agree (spread within 6 h or 35% of the median)
        medium  1 report from someone who saw it themselves
        low     2+ reports that disagree (flagged for you to check), or 1 second-hand report
  5. Routes are chains of crossings (route_legs). A route's delay is the sum of each leg's extra hours over
     normal for outbound mineral cargo, side by side. Missing sides count as 0 and lower the coverage.
  6. Alerts go out when a unit is High or Severe (75%+ over normal and 6+ extra hours) with high confidence,
     or medium confidence two days running. Each unit alerts at most once a day.
"""
import csv
import io
import json
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from html import escape

import requests

from . import config, db as dbmod, notify

CAT = timezone(timedelta(hours=2))          # Central Africa Time, where the reporters are
CAUSES = {
    "system_down": "Customs system down", "new_rule": "New rule, fee or document", "strike_blockade": "Strike, protest or blockade",
    "accident": "Accident or breakdown on the road", "security": "Theft or security incident", "power_cut": "Power cut at the border",
    "fuel": "Fuel shortage", "hours_changed": "Border hours changed or closed", "other": "Other", "none": "Nothing unusual",
}
SIDE_NAMES = {"cod": "DRC side", "zmb": "Zambian side", "zwe": "Zimbabwean side", "zaf": "South African side", "tza": "Tanzanian side",
              "bwa": "Botswana side", "nam": "Namibian side", "ago": "Angolan side", "port": "Port"}


# ---------- reading the form ----------
def _col(headers, *words):
    low = [(h, h.lower()) for h in headers]
    for w in words:
        for h, l in low:
            if w in l:
                return h
    return None


def _parse_ts(s):
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=CAT)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(CAT)
    except ValueError:
        return None


def _num(v):
    m = re.search(r"-?\d+(\.\d+)?", str(v or "").replace(",", "."))
    return float(m.group()) if m else None


def _crossing_id(text, crossings):
    t = (text or "").lower()
    for c in crossings:
        for w in [c["name"].lower()] + [a.lower() for a in c.get("aliases") or []]:
            if w and w in t:
                return c["id"]
    return None


def _side(text):
    t = (text or "").lower()
    for code, words in [("cod", ["drc", "congo"]), ("zmb", ["zambia"]), ("zwe", ["zimbabwe"]), ("zaf", ["south africa"]),
                        ("tza", ["tanzania"]), ("bwa", ["botswana"]), ("nam", ["namibia"]), ("ago", ["angola"]), ("port", ["port", "single"])]:
        if any(w in t for w in words):
            return code
    return None


def _direction(text):
    t = (text or "").lower()
    if any(w in t for w in ("outbound", "toward the coast", "towards the coast", "export", "leaving")):
        return "out"
    if any(w in t for w in ("inbound", "from the coast", "import", "entering")):
        return "in"
    return None


def _cargo(text):
    t = (text or "").lower()
    if any(w in t for w in ("copper", "cobalt", "mineral", "concentrate", "cathode")):
        return "minerals"
    if "empty" in t:
        return "empty"
    return "other" if t else None


def _causes(text):
    t = (text or "").lower()
    out = []
    for code, words in [("system_down", ["system"]), ("new_rule", ["rule", "fee", "document"]), ("strike_blockade", ["strike", "protest", "blockade"]),
                        ("accident", ["accident", "breakdown"]), ("security", ["theft", "hijack", "security"]), ("power_cut", ["power"]),
                        ("fuel", ["fuel"]), ("hours_changed", ["hours changed", "closed"]), ("none", ["nothing unusual"])]:
        if any(w in t for w in words):
            out.append(code)
    if not out and t.strip():
        out.append("other")
    return out


def _certainty(text):
    t = (text or "").lower()
    if "myself" in t or "saw" in t:
        return "saw"
    if "driver" in t:
        return "drivers"
    return "heard" if t else "heard"


def parse_form_csv(text, crossings):
    """Google Form answer sheet (CSV) -> border_reports rows. Column names are matched by keywords, so small wording changes are fine."""
    rd = csv.DictReader(io.StringIO(text))
    h = rd.fieldnames or []
    c = {"ts": _col(h, "timestamp"), "who": _col(h, "reporter"), "crossing": _col(h, "which crossing", "crossing", "border or port"),
         "side": _col(h, "which side", "side"), "dir": _col(h, "direction"), "cargo": _col(h, "cargo"), "queue": _col(h, "queue"),
         "transit": _col(h, "joining the queue", "transit"), "customs": _col(h, "customs documents", "customs"),
         "causes": _col(h, "slowed", "cause"), "cert": _col(h, "how sure", "sure", "certain"), "day": _col(h, "which day", "date of"),
         "notes": _col(h, "anything else", "notes")}
    rows, skipped = [], 0
    for r in rd:
        ts = _parse_ts(r.get(c["ts"]))
        crossing = _crossing_id(r.get(c["crossing"]), crossings)
        transit = _num(r.get(c["transit"]))
        if not ts or not crossing or transit is None or transit < 0 or transit > 24 * 30:
            skipped += 1
            continue
        day = None
        if c["day"] and r.get(c["day"]):
            d = _parse_ts(r[c["day"]] + " 00:00:00")
            day = d.date() if d else None
        day = day or (ts.date() - timedelta(days=1))      # answers describe yesterday
        who = (r.get(c["who"]) or "anon").strip()[:40]
        side = _side(r.get(c["side"])) or ("port" if crossing.endswith("_port") else None)
        rows.append({
            "id": f"{ts.isoformat()}|{who}",
            "submitted_at": ts.astimezone(timezone.utc).isoformat(), "report_date": day.isoformat(), "reporter": who,
            "crossing_id": crossing, "side": side or "unknown", "direction": _direction(r.get(c["dir"])) or "out",
            "cargo": _cargo(r.get(c["cargo"])) or "minerals", "queue": (r.get(c["queue"]) or "").strip()[:40],
            "transit_hours": transit, "customs_hours": _num(r.get(c["customs"])), "causes": _causes(r.get(c["causes"])),
            "certainty": _certainty(r.get(c["cert"])), "notes": (r.get(c["notes"]) or "").strip()[:500] if c["notes"] else "",
        })
    return rows, skipped


# ---------- the index ----------
def unit_key(r):
    return (r["crossing_id"], r["direction"], r["cargo"], r["side"])


def band(h):
    if h is None:
        return ""
    lo = int(h // 6 * 6)
    return f"{lo}–{lo + 6} h"


def status_of(pct, extra):
    if pct is None:
        return "unknown"
    if pct >= 150 and extra >= 6:
        return "severe"
    if pct >= 75 and extra >= 6:
        return "high"
    if pct >= 25:
        return "elevated"
    return "normal"


def compute(reports, crossings, routes, days=45, today=None):
    """Returns (index_rows, route_rows). Pure function, easy to test."""
    by_unit_day = {}
    for r in reports:
        by_unit_day.setdefault(unit_key(r), {}).setdefault(r["report_date"], []).append(r)
    cmap = {c["id"]: c for c in crossings}
    all_days = sorted({r["report_date"] for r in reports})
    if not all_days:
        return [], []
    end = date.fromisoformat(today) if today else date.fromisoformat(all_days[-1])
    start = end - timedelta(days=days - 1)
    index = []
    for key, per_day in by_unit_day.items():
        crossing, direction, cargo, side = key
        default_normal = (cmap.get(crossing, {}).get("normal_h") or {}).get(side)
        history = []                                    # (day, median) for normals
        prev_status_conf = None
        for d in sorted(per_day):
            rs = per_day[d]
            tr = [x["transit_hours"] for x in rs]
            med = statistics.median(tr)
            cust = [x["customs_hours"] for x in rs if x.get("customs_hours") is not None]
            past = [m for (dd, m) in history if date.fromisoformat(d) - timedelta(days=30) <= date.fromisoformat(dd) < date.fromisoformat(d)]
            normal = statistics.median(past) if len(past) >= 7 else default_normal
            normal_source = "data" if len(past) >= 7 else "assumption"
            extra = med - normal if normal is not None else None
            pct = (extra / normal * 100) if normal else None
            spread = max(tr) - min(tr)
            flag = ""
            if len(rs) >= 2:
                agree = spread <= max(6, 0.35 * med)
                conf = "high" if agree else "low"
                flag = "" if agree else "reporters disagree"
            else:
                conf = "medium" if rs[0]["certainty"] == "saw" else "low"
            causes = {}
            for x in rs:
                for cc in x.get("causes") or []:
                    if cc != "none":
                        causes[cc] = causes.get(cc, 0) + 1
            st = status_of(pct, extra or 0)
            history.append((d, med))
            if date.fromisoformat(d) < start:
                prev_status_conf = (st, conf)
                continue
            alert = st in ("high", "severe") and (conf == "high" or (conf == "medium" and prev_status_conf and prev_status_conf[0] in ("high", "severe")))
            prev_status_conf = (st, conf)
            index.append({
                "id": f"{d}|{crossing}|{direction}|{cargo}|{side}", "date": d, "crossing_id": crossing, "direction": direction,
                "cargo": cargo, "side": side, "n_reports": len(rs), "transit_h": round(med, 1),
                "customs_h": round(statistics.median(cust), 1) if cust else None, "normal_h": round(normal, 1) if normal is not None else None,
                "normal_source": normal_source, "extra_h": round(extra, 1) if extra is not None else None,
                "pct_vs_normal": round(pct) if pct is not None else None, "status": st, "band": band(med),
                "causes": sorted(causes, key=lambda k: -causes[k]), "confidence": conf, "flag": flag, "alert": bool(alert),
            })
    # routes: outbound mineral cargo, each leg's sides summed
    by_id = {r["id"]: r for r in index}
    route_rows = []
    for rt in routes:
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            legs, total, confs, have, need = [], 0.0, [], 0, 0
            for cid in rt["legs"]:
                leg_extra, leg_sides = 0.0, []
                for side in cmap.get(cid, {}).get("sides", []):
                    need += 1
                    row = None
                    for back in range(3):                 # a report up to 2 days old still counts (marked stale)
                        dd = (date.fromisoformat(d) - timedelta(days=back)).isoformat()
                        row = by_id.get(f"{dd}|{cid}|out|minerals|{side}")
                        if row:
                            break
                    if row and row["extra_h"] is not None:
                        have += 1
                        leg_extra += max(row["extra_h"], 0)
                        confs.append(row["confidence"] if back == 0 else "low")
                        leg_sides.append({"side": side, "extra_h": row["extra_h"], "stale": back > 0})
                total += leg_extra
                legs.append({"crossing_id": cid, "extra_h": round(leg_extra, 1), "sides": leg_sides})
            if not have:
                continue
            order = {"high": 2, "medium": 1, "low": 0}
            conf = min(confs, key=lambda c: order[c]) if confs else "low"
            route_rows.append({"id": f"{d}|{rt['id']}", "date": d, "route_id": rt["id"], "extra_h": round(total, 1),
                               "legs": legs, "confidence": conf, "coverage": round(have / need, 2) if need else 0})
    return index, route_rows


# ---------- reference data ----------
def reference(database):
    """Crossings and route legs from the database, or from the seed for the local test database."""
    try:
        if isinstance(database, dbmod.SupabaseDB):
            crossings = database.select("crossings")
            legs = database.select("route_legs", "select=*&order=route_id,seq")
        else:
            raise LookupError
    except Exception:
        seed = json.loads((config.ROOT / "supabase" / "seed_data.json").read_text())
        p = seed.get("PULSE_REF") or {}
        crossings = p.get("crossings", [])
        legs = [{"route_id": r["id"], "seq": i, "crossing_id": c} for r in p.get("routes", []) for i, c in enumerate(r["legs"])]
    routes = {}
    for l in sorted(legs, key=lambda x: (x["route_id"], x["seq"])):
        routes.setdefault(l["route_id"], []).append(l["crossing_id"])
    return crossings, [{"id": k, "legs": v} for k, v in routes.items()]


def load_reports(database, since):
    if isinstance(database, dbmod.SupabaseDB):
        return database.select("border_reports", f"select=*&report_date=gte.{since}&excluded=eq.false&order=report_date.asc&limit=20000")
    return [r for r in database.d.get("border_reports", []) if r["report_date"] >= since and not r.get("excluded")]


# ---------- alerts ----------
def send_alerts(database, index, crossings, routes):
    cmap = {c["id"]: c for c in crossings}
    todays = [r for r in index if r["alert"]]
    if not todays:
        return 0
    if isinstance(database, dbmod.SupabaseDB):
        done = {r["id"] for r in database.select("pulse_alerts", "select=id&order=date.desc&limit=500")}
    else:
        done = {r["id"] for r in database.d.get("pulse_alerts", [])}
    new = [r for r in todays if r["id"] not in done]
    if not new:
        return 0
    rows = ""
    for r in new:
        c = cmap.get(r["crossing_id"], {})
        on = [rt["id"] for rt in routes if r["crossing_id"] in rt["legs"]]
        cause = ", ".join(CAUSES.get(x, x) for x in r["causes"][:3]) or "no cause reported"
        rows += (f'<tr><td style="padding:10px 0;border-bottom:1px solid #e5e7eb;font:14px Arial,sans-serif">'
                 f'<b>{escape(c.get("name", r["crossing_id"]))} · {escape(SIDE_NAMES.get(r["side"], r["side"]))}</b> · {escape(r["date"])}<br>'
                 f'{r["transit_h"]:.0f} h transit vs normal {r["normal_h"]:.0f} h (<b>+{r["pct_vs_normal"]}%</b>, {escape(r["status"])}) · '
                 f'{escape(cause)} · confidence {escape(r["confidence"])} ({r["n_reports"]} report{"s" if r["n_reports"] > 1 else ""})<br>'
                 f'<span style="color:#666">Routes affected: {escape(", ".join(on) or "none mapped")}</span></td></tr>')
    html_body = notify.wrap(f"Corridor Pulse: {len(new)} crossing{'s' if len(new) > 1 else ''} running well above normal", rows,
                            "Delay figures come from clearing agents at each crossing; treat them as a signal, not an exact measurement.")
    sent = 0
    recips = [config.ADMIN_EMAIL] if config.ADMIN_EMAIL else []
    for s in database.subscribers() if hasattr(database, "subscribers") else []:
        if s.get("email") and (not s.get("corridors") or any(r["crossing_id"] in rt["legs"] for rt in routes if rt["id"] in s["corridors"] for r in new)):
            recips.append(s["email"])
    if not recips:
        print(f"  {len(new)} crossing(s) crossed the alert line, but no ADMIN_EMAIL or subscribers are set, so no email was sent")
    for to in dict.fromkeys(recips):
        sent += bool(notify.send(to, "Corridor Pulse alert: border delays above normal", html_body))
    database.upsert_rows("pulse_alerts", [{"id": r["id"], "date": r["date"], "crossing_id": r["crossing_id"], "status": r["status"],
                                           "pct_vs_normal": r["pct_vs_normal"]} for r in new], "id")
    return sent


# ---------- commands ----------
def run(sample=False, today=None):
    database = dbmod.LocalDB(config.LOCAL_DB_DIR) if sample else dbmod.connect()
    print(f"Database: {type(database).__name__}")
    crossings, routes = reference(database)
    if not crossings:
        print("No crossings in the database yet: run supabase/migrate_v5.sql and seed.sql first.")
        return
    if sample:
        text = (config.ROOT / "collector" / "fixtures" / "pulse_sample.csv").read_text()
    else:
        url = config.env("PULSE_FORM_CSV_URL")
        if not url:
            print("PULSE_FORM_CSV_URL is not set: publish the form's answer sheet as CSV and put the link in .env (see README).")
            return
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
    rows, skipped = parse_form_csv(text, crossings)
    print(f"Form answers read: {len(rows)} usable, {skipped} skipped (missing crossing, time or hours)")
    if rows:
        database.upsert_rows("border_reports", rows, "id")
    since = ((date.fromisoformat(today) if today else date.today()) - timedelta(days=90)).isoformat()
    reports = load_reports(database, since) if not sample else rows
    index, route_rows = compute(reports, crossings, routes, today=today)
    if index:
        database.upsert_rows("corridor_index", index, "id")
    if route_rows:
        database.upsert_rows("route_index", route_rows, "id")
    latest = max((r["date"] for r in index), default=None)
    print(f"Index rows: {len(index)} · route rows: {len(route_rows)} · latest day: {latest}")
    cmap = {c["id"]: c["name"] for c in crossings}
    for r in sorted((x for x in index if x["date"] == latest), key=lambda x: -(x["pct_vs_normal"] or 0)):
        print(f"  {cmap.get(r['crossing_id'], r['crossing_id']):<28} {r['direction']:>3} {r['cargo']:<8} {SIDE_NAMES.get(r['side'], r['side']):<20} "
              f"{r['transit_h']:>6.1f} h  normal {r['normal_h'] or 0:>5.1f} ({r['normal_source'][:4]})  {r['pct_vs_normal'] or 0:>+5}%  "
              f"{r['status']:<9} {r['confidence']:<6} {r['flag']}")
    for rr in (x for x in route_rows if x["date"] == latest):
        print(f"  route {rr['route_id']:<8} +{rr['extra_h']:>5.1f} h  confidence {rr['confidence']}  coverage {int(rr['coverage'] * 100)}%")
    n = send_alerts(database, [r for r in index if r["date"] == latest], crossings, routes)
    print(f"Alert emails: {n}")
    return index, route_rows


def weekly_brief(sample=False):
    database = dbmod.LocalDB(config.LOCAL_DB_DIR) if sample else dbmod.connect()
    crossings, routes = reference(database)
    cmap = {c["id"]: c["name"] for c in crossings}
    if isinstance(database, dbmod.SupabaseDB):
        idx = database.select("corridor_index", "select=*&order=date.desc&limit=2000")
        rix = database.select("route_index", "select=*&order=date.desc&limit=500")
    else:
        idx, rix = database.d.get("corridor_index", []), database.d.get("route_index", [])
    if not idx:
        print("No index yet: run `python -m collector pulse` first.")
        return
    end = max(r["date"] for r in idx)
    wk = (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    prev = (date.fromisoformat(end) - timedelta(days=13)).isoformat()
    lines = []
    for rt in routes:
        this = [r["extra_h"] for r in rix if r["route_id"] == rt["id"] and wk <= r["date"] <= end]
        last = [r["extra_h"] for r in rix if r["route_id"] == rt["id"] and prev <= r["date"] < wk]
        if this:
            a, b = statistics.mean(this), (statistics.mean(last) if last else None)
            lines.append(f"- **{rt['id']}**: average +{a:.0f} h over normal this week" + (f" (last week +{b:.0f} h)" if b is not None else ""))
    units = {}
    for r in idx:
        if wk <= r["date"] <= end and r["direction"] == "out" and r["cargo"] == "minerals":
            units.setdefault((r["crossing_id"], r["side"]), []).append(r)
    xl = []
    for (cid, side), rs in sorted(units.items(), key=lambda kv: -statistics.mean([x["pct_vs_normal"] or 0 for x in kv[1]])):
        causes = {}
        for x in rs:
            for c in x["causes"]:
                causes[c] = causes.get(c, 0) + 1
        top = ", ".join(CAUSES.get(c, c).lower() for c in sorted(causes, key=lambda k: -causes[k])[:2]) or "no unusual causes"
        xl.append(f"- {cmap.get(cid, cid)} ({SIDE_NAMES.get(side, side)}): median {statistics.median([x['transit_h'] for x in rs]):.0f} h, "
                  f"{statistics.mean([x['pct_vs_normal'] or 0 for x in rs]):+.0f}% vs normal; {top}")
    facts = f"Week ending {end}\n\nRoutes (outbound copper and cobalt):\n" + "\n".join(lines) + "\n\nCrossings:\n" + "\n".join(xl)
    draft = None
    if config.ANTHROPIC_API_KEY:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            msg = client.messages.create(model=config.ANTHROPIC_MODEL, max_tokens=900, messages=[{"role": "user", "content":
                "Draft the 'Copperbelt Corridor Weekly' (under 300 words) from these facts only. Sections: The number (one sentence), "
                "Five crossings (one line each), What to watch (two lines). Keep every number exactly as given; do not add facts.\n\n" + facts}])
            draft = msg.content[0].text
        except Exception as e:
            print(f"  AI draft skipped ({e}); using the template")
    draft = draft or ("# Copperbelt Corridor Weekly\n\n" + facts + "\n\n**Three things that moved:** [from the Alerts tab]\n\n"
                      "**One call:** [a forecast from the Forecasts tab]\n\nWant this daily, applied to your own shipments? Reply to this email.")
    out = config.LOCAL_DB_DIR / "briefs"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"weekly-{end}.md"
    f.write_text(draft)
    print(f"Draft written to {f}")
    if config.ADMIN_EMAIL:
        notify.send(config.ADMIN_EMAIL, f"Draft: Copperbelt Corridor Weekly, week ending {end}",
                    notify.wrap("Weekly brief draft", f'<tr><td><pre style="white-space:pre-wrap;font:14px/1.5 Arial,sans-serif">{escape(draft)}</pre></td></tr>'))
    print(draft)
