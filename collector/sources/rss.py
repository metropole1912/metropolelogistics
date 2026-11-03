"""Any RSS or Atom feed: company announcements, exchange news, mining press, ministry notices.
List feed URLs in collector/feeds.txt, one per line.
"""
import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

from .. import config
from . import item

ATOM = "{http://www.w3.org/2005/Atom}"


def _date(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).isoformat()
    except Exception:
        return s  # Atom dates are already ISO


def clean(text):
    """Feed descriptions often contain HTML; keep only the readable text."""
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def tidy(title, link, body, source_name=""):
    title, body = clean(title), clean(body)
    # Google News titles end with " - Publisher"; descriptions just repeat the headline.
    if "news.google.com" in (link or ""):
        m = re.match(r"^(.*\S)\s+-\s+([^-]{2,80})$", title)
        if m:
            title, source_name = m.group(1), m.group(2).strip()
        body = ""
    if body and (body.lower().startswith(title.lower()[:60]) or len(body) < 20):
        body = ""
    if not body and source_name:
        body = f"Reported by {source_name}."
    return title, body


def parse(xml_text, feed_url=""):
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):  # RSS 2.0
        link = it.findtext("link", "")
        title, body = tidy(it.findtext("title", ""), link, it.findtext("description", ""), it.findtext("source", ""))
        out.append(item("rss", title, url=link, body=body, published_at=_date(it.findtext("pubDate"))))
    for e in root.iter(ATOM + "entry"):  # Atom
        link = e.find(ATOM + "link")
        href = link.get("href", "") if link is not None else ""
        title, body = tidy(e.findtext(ATOM + "title", ""), href, e.findtext(ATOM + "summary", "") or e.findtext(ATOM + "content", ""))
        out.append(item("rss", title, url=href, body=body,
                        published_at=e.findtext(ATOM + "updated") or e.findtext(ATOM + "published")))
    return [i for i in out if i["title"]]


def feeds():
    f = config.RSS_FEEDS_FILE
    if not f.exists():
        return []
    return [l.strip() for l in f.read_text().splitlines() if l.strip() and not l.strip().startswith("#")]


def fetch(ref, sample=False):
    if sample:
        return parse((config.ROOT / "collector" / "fixtures" / "feed.xml").read_text())
    out = []
    for url in feeds():
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": config.USER_AGENT})
            r.raise_for_status()
            out += parse(r.content, url)
        except Exception as e:
            print(f"  rss: {url} failed: {e}")
    return out
