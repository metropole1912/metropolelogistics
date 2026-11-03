"""All settings come from environment variables (or a .env file in the project folder).

Nothing here is secret: the secrets live in .env on your computer and in GitHub Secrets.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def env(name, default=""):
    return os.environ.get(name, default).strip()


# Database (Supabase). Leave empty to use the local test database in ./local_db
SUPABASE_URL = env("SUPABASE_URL").rstrip("/")
SUPABASE_SERVICE_KEY = env("SUPABASE_SERVICE_KEY")

# AI tagging. Leave empty to use the simple keyword tagger (free, less accurate).
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-haiku-5-5")

# Email alerts via Resend (resend.com). Leave empty to print emails instead of sending.
RESEND_API_KEY = env("RESEND_API_KEY")
ALERT_FROM = env("ALERT_FROM", "Metropole Alerts <alerts@example.com>")
ADMIN_EMAIL = env("ADMIN_EMAIL")          # you: gets "events need review" emails
SITE_URL = env("SITE_URL", "")            # your website, used for links in emails

# Data sources
RELIEFWEB_APPNAME = env("RELIEFWEB_APPNAME")   # request one at apidoc.reliefweb.int; source is skipped until set
LOOKBACK_HOURS = int(env("LOOKBACK_HOURS", "6"))
OFAC_SDN_URL = env("OFAC_SDN_URL", "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV")
RSS_FEEDS_FILE = ROOT / "collector" / "feeds.txt"

# Rules
# Events at or above this severity wait for your approval before they show on the site or go out by email.
REVIEW_FROM_SEVERITY = int(env("REVIEW_FROM_SEVERITY", "3"))
# Below this AI confidence, events also wait for review.
REVIEW_BELOW_CONFIDENCE = float(env("REVIEW_BELOW_CONFIDENCE", "0.6"))
MAX_ITEMS_PER_RUN = int(env("MAX_ITEMS_PER_RUN", "60"))   # caps AI cost per run

LOCAL_DB_DIR = ROOT / "local_db"
USER_AGENT = "MetropoleLogistics-Collector/0.1"
