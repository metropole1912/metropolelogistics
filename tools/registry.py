"""Shared helpers for the register importers: flexible column matching, country lookup, and pushing rows to the database.

Each importer reads a downloaded file, turns rows into Metropole register rows, writes them to
register/<name>.json for you to check, and (with --push) upserts them into the database.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector import db as dbmod  # noqa: E402

OUT = ROOT / "register"

# Countries the engine covers: ISO3 -> names the datasets use
COUNTRY_NAMES = {
    "COD": ["democratic republic of the congo", "dr congo", "congo, dem. rep.", "congo (kinshasa)", "congo, the democratic republic of the", "drc"],
    "ZMB": ["zambia"], "ZWE": ["zimbabwe"], "ZAF": ["south africa"], "GIN": ["guinea"], "MLI": ["mali"], "GHA": ["ghana"],
    "TZA": ["tanzania", "tanzania, united republic of"], "MOZ": ["mozambique"], "AGO": ["angola"], "NAM": ["namibia"], "GAB": ["gabon"],
    "BFA": ["burkina faso"], "NER": ["niger"], "CIV": ["cote d'ivoire", "côte d'ivoire", "ivory coast"], "BWA": ["botswana"],
    "SLE": ["sierra leone"], "LBR": ["liberia"], "RWA": ["rwanda"], "UGA": ["uganda"], "SEN": ["senegal"], "KEN": ["kenya"],
    "CMR": ["cameroon"], "MDG": ["madagascar"], "SDN": ["sudan"], "ETH": ["ethiopia"], "NGA": ["nigeria"], "LBY": ["libya"],
    "DZA": ["algeria"], "EGY": ["egypt"], "MRT": ["mauritania"], "GNQ": ["equatorial guinea"], "COG": ["congo", "republic of the congo", "congo (brazzaville)", "congo, rep."],
    "TCD": ["chad"], "SSD": ["south sudan"], "MAR": ["morocco"],
    # South America and the Caribbean
    "BRA": ["brazil"], "ARG": ["argentina"], "CHL": ["chile"], "PER": ["peru"], "BOL": ["bolivia", "bolivia (plurinational state of)"],
    "COL": ["colombia"], "VEN": ["venezuela", "venezuela (bolivarian republic of)", "venezuela, rb"], "GUY": ["guyana"], "ECU": ["ecuador"],
    "PAN": ["panama"], "TTO": ["trinidad and tobago", "trinidad & tobago"], "SUR": ["suriname"], "URY": ["uruguay"], "PRY": ["paraguay"],
    # Largest oil consumers (demand tracker)
    "USA": ["united states", "united states of america", "usa", "us"], "CHN": ["china", "people's republic of china"], "IND": ["india"],
    "SAU": ["saudi arabia"], "RUS": ["russia", "russian federation"], "JPN": ["japan"], "KOR": ["south korea", "korea", "korea, republic of", "republic of korea"],
    "CAN": ["canada"], "DEU": ["germany"], "IRN": ["iran", "iran (islamic republic of)"], "MEX": ["mexico"], "IDN": ["indonesia"],
    "SGP": ["singapore"], "FRA": ["france"], "ESP": ["spain"], "GBR": ["united kingdom", "uk", "great britain"], "THA": ["thailand"],
    "ITA": ["italy"], "TUR": ["turkey", "türkiye", "turkiye"], "ARE": ["united arab emirates", "uae"], "AUS": ["australia"], "IRQ": ["iraq"],
    "TWN": ["taiwan", "chinese taipei", "taiwan, province of china"], "MYS": ["malaysia"], "NLD": ["netherlands", "the netherlands"],
    "POL": ["poland"], "VNM": ["vietnam", "viet nam"], "KWT": ["kuwait"], "PAK": ["pakistan"], "BEL": ["belgium"], "QAT": ["qatar"], "OMN": ["oman"],
}
ISO2 = {"COD": "CD", "ZMB": "ZM", "ZWE": "ZW", "ZAF": "ZA", "GIN": "GN", "MLI": "ML", "GHA": "GH", "TZA": "TZ", "MOZ": "MZ", "AGO": "AO",
        "NAM": "NA", "GAB": "GA", "BFA": "BF", "NER": "NE", "CIV": "CI", "BWA": "BW", "SLE": "SL", "LBR": "LR", "RWA": "RW", "UGA": "UG",
        "SEN": "SN", "KEN": "KE", "CMR": "CM", "MDG": "MG", "SDN": "SD", "ETH": "ET", "NGA": "NG", "LBY": "LY", "DZA": "DZ", "EGY": "EG",
        "MRT": "MR", "GNQ": "GQ", "COG": "CG", "TCD": "TD", "SSD": "SS", "MAR": "MA",
        "BRA": "BR", "ARG": "AR", "CHL": "CL", "PER": "PE", "BOL": "BO", "COL": "CO", "VEN": "VE", "GUY": "GY", "ECU": "EC", "PAN": "PA",
        "TTO": "TT", "SUR": "SR", "URY": "UY", "PRY": "PY", "USA": "US", "CHN": "CN", "IND": "IN", "SAU": "SA", "RUS": "RU", "JPN": "JP",
        "KOR": "KR", "CAN": "CA", "DEU": "DE", "IRN": "IR", "MEX": "MX", "IDN": "ID", "SGP": "SG", "FRA": "FR", "ESP": "ES", "GBR": "GB",
        "THA": "TH", "ITA": "IT", "TUR": "TR", "ARE": "AE", "AUS": "AU", "IRQ": "IQ", "TWN": "TW", "MYS": "MY", "NLD": "NL", "POL": "PL",
        "VNM": "VN", "KWT": "KW", "PAK": "PK", "BEL": "BE", "QAT": "QA", "OMN": "OM"}


def iso3(value):
    v = str(value or "").strip()
    if not v:
        return None
    u = v.upper()
    if u in COUNTRY_NAMES:
        return u
    for k, v2 in ISO2.items():
        if u == v2:
            return k
    low = v.lower()
    for k, names in COUNTRY_NAMES.items():
        if low in names:
            return k
    return None


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:80]


def norm(h):
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


def find_col(headers, *candidates):
    """Return the header that best matches any candidate phrase (exact first, then contains)."""
    normed = {h: norm(h) for h in headers}
    for c in candidates:
        for h, n in normed.items():
            if n == c:
                return h
    for c in candidates:
        for h, n in normed.items():
            if c in n:
                return h
    return None


def read_table(path):
    """Reads .csv, .tsv, .xlsx or .xls into (headers, rows of dicts). Picks the first sheet with a Latitude-like column."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        try:
            import openpyxl
        except ImportError:
            sys.exit("Install the spreadsheet reader first:  pip install openpyxl")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        best = None
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            # header row = first row with at least 3 text cells
            for i, r in enumerate(rows[:10]):
                if sum(1 for c in r if isinstance(c, str)) >= 3:
                    headers = [str(c) if c is not None else "" for c in r]
                    body = [dict(zip(headers, rr)) for rr in rows[i + 1:] if any(c not in (None, "") for c in rr)]
                    if find_col(headers, "latitude", "lat") or best is None:
                        best = (headers, body)
                    break
            if best and find_col(best[0], "latitude", "lat"):
                break
        return best or ([], [])
    import csv
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    rows = list(reader)
    return (reader.fieldnames or [], rows)


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def save(name, rows):
    OUT.mkdir(exist_ok=True)
    f = OUT / f"{name}.json"
    f.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"Wrote {len(rows)} rows to {f}")
    return f


def push(table, key, rows):
    db = dbmod.connect()
    if not hasattr(db, "upsert_rows"):
        sys.exit("No database connection: fill SUPABASE_URL and SUPABASE_SERVICE_KEY in .env")
    n = db.upsert_rows(table, rows, key)
    print(f"Pushed {n} rows to {table} ({type(db).__name__})")
