"""Each source module exposes fetch(ref, sample=False) -> list of raw items:
{source, external_id, url, title, body, published_at, fingerprint}
"""
import hashlib
import re


def fingerprint(source, url, title):
    base = (url or "").split("?")[0].rstrip("/").lower() or re.sub(r"\W+", " ", title.lower()).strip()
    return hashlib.sha1(f"{source}|{base}".encode()).hexdigest()


def item(source, title, url="", body="", published_at=None, external_id=None):
    title = re.sub(r"\s+", " ", title or "").strip()
    return {
        "source": source,
        "external_id": external_id,
        "url": url,
        "title": title[:500],
        "body": re.sub(r"\s+", " ", body or "").strip()[:4000],
        "published_at": published_at,
        "fingerprint": fingerprint(source, url, title),
    }
