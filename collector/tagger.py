"""Turns a raw news item into a structured event.

1. prefilter()   — cheap keyword check so we only pay the AI for items that might matter.
2. ClaudeTagger  — asks Claude to tag country, assets, corridors, metals, type, severity (needs ANTHROPIC_API_KEY).
3. KeywordTagger — free fallback when no API key is set. Less accurate; everything it tags goes to review.
"""
import json
import re

from . import config

TYPES = ["export-control", "regulatory", "nationalisation", "sanctions", "conflict", "unrest", "political",
         "infrastructure", "trade", "counterparty", "milestone", "exogenous"]

METAL_WORDS = {
    "Cu": ["copper", "cathode"], "Co": ["cobalt", "hydroxide"], "Au": ["gold", "bullion"], "Li": ["lithium", "spodumene"],
    "PGM": ["platinum", "palladium", "pgm", "rhodium"], "Fe": ["iron ore", "iron-ore"], "Bx": ["bauxite", "alumina"],
    "Mn": ["manganese"], "Ni": ["nickel"], "U": ["uranium", "yellowcake"], "Zn": ["zinc"],
    "3T": ["coltan", "tantalum", "tin ore", "cassiterite", "tungsten", "wolframite"], "Gr": ["graphite"], "Ag": ["silver"],
    # energy
    "Crude": ["crude", "crude oil", "oil field", "oilfield", "oil output", "oil production", "barrels", "bpd", "b/d", "fpso", "oil terminal", "oil pipeline"],
    "Prod": ["refinery", "refined products", "gasoline", "petrol", "diesel", "gasoil", "jet fuel", "kerosene", "naphtha", "fuel oil", "fuel imports", "fuel shortage"],
    "LNG": ["lng", "liquefied natural gas", "lng terminal", "lng plant", "flng"],
    "Gas": ["natural gas", "gas field", "gas pipeline", "gas exports", "gas supply"],
    "LPG": ["lpg", "propane", "butane", "cooking gas"],
    "Bunk": ["bunker", "bunkering", "marine fuel", "vlsfo", "hsfo"],
}
ENERGY_CODES = {"Crude", "Prod", "LNG", "Gas", "LPG", "Bunk"}
TOPIC_WORDS = sorted({w for ws in METAL_WORDS.values() for w in ws} | {
    "mine", "mining", "miner", "minerals", "smelter", "refinery", "export", "concentrate", "royalty", "licence",
    "license", "permit", "railway", "corridor", "border", "port", "truck", "convoy", "quota", "ban", "sanction",
    "tanker", "vessel", "shipping", "strait", "canal", "force majeure", "opec", "noc", "sonatrach", "nnpc", "cargo", "loading", "loadings"})

COUNTRY_ALIASES = {
    "COD": ["congo", "drc", "congolese", "kinshasa", "katanga", "lualaba", "kolwezi", "lubumbashi", "goma", "bukavu", "kivu", "m23"],
    "ZMB": ["zambia", "zambian", "copperbelt", "lusaka", "ndola", "kitwe"], "ZWE": ["zimbabwe", "zimbabwean", "harare"],
    "ZAF": ["south africa", "south african", "transnet", "durban", "richards bay", "rustenburg", "johannesburg"],
    "GIN": ["guinea", "guinean", "conakry", "simandou", "boké", "boke"], "MLI": ["mali", "malian", "bamako"],
    "GHA": ["ghana", "ghanaian", "accra"], "TZA": ["tanzania", "tanzanian", "dar es salaam"],
    "MOZ": ["mozambique", "mozambican", "maputo", "beira", "cabo delgado"], "AGO": ["angola", "angolan", "luanda", "lobito"],
    "NAM": ["namibia", "namibian", "walvis bay", "windhoek"], "GAB": ["gabon", "gabonese", "libreville", "moanda"],
    "BFA": ["burkina faso", "burkinabe", "ouagadougou"], "NER": ["niger", "nigerien", "niamey", "arlit"],
    "CIV": ["ivory coast", "côte d'ivoire", "cote d'ivoire", "ivorian", "abidjan"], "BWA": ["botswana", "gaborone"],
    "SLE": ["sierra leone", "freetown"], "LBR": ["liberia", "liberian", "monrovia"], "RWA": ["rwanda", "rwandan", "kigali"],
    "UGA": ["uganda", "ugandan", "kampala"], "SEN": ["senegal", "senegalese", "dakar"], "KEN": ["kenya", "kenyan", "mombasa", "nairobi"],
    "CMR": ["cameroon", "cameroonian", "yaoundé", "yaounde"], "MDG": ["madagascar", "malagasy", "antananarivo"],
    "SDN": ["sudan", "sudanese", "khartoum", "port sudan"], "ETH": ["ethiopia", "ethiopian", "addis ababa"],
    "NGA": ["nigeria", "nigerian", "nnpc", "dangote", "niger delta", "bonny", "forcados", "escravos", "lagos", "rivers state"],
    "LBY": ["libya", "libyan", "tripoli", "benghazi", "sharara", "zawiya", "es sider", "ras lanuf", "national oil corporation", "mellitah"],
    "DZA": ["algeria", "algerian", "sonatrach", "hassi messaoud", "hassi r'mel", "arzew", "skikda"],
    "EGY": ["egypt", "egyptian", "suez", "zohr", "damietta", "idku", "sumed", "ain sukhna"],
    "MRT": ["mauritania", "mauritanian", "nouakchott", "nouadhibou", "tortue", "gta lng"],
    "GNQ": ["equatorial guinea", "malabo", "punta europa"], "COG": ["congo-brazzaville", "brazzaville", "pointe-noire", "marine xii"],
    "TCD": ["chad", "chadian", "n'djamena", "doba"], "SSD": ["south sudan", "juba", "palouge", "unity state"],
    "MAR": ["morocco", "moroccan", "tangier", "tanger med"],
    # South America
    "BRA": ["brazil", "brazilian", "petrobras", "vale", "carajás", "carajas", "santos", "ponta da madeira", "pre-salt", "búzios", "buzios", "ibama", "equatorial margin"],
    "ARG": ["argentina", "argentine", "vaca muerta", "ypf", "neuquén", "neuquen", "jujuy", "catamarca", "salta", "rigi", "bahía blanca", "bahia blanca", "milei"],
    "CHL": ["chile", "chilean", "codelco", "escondida", "collahuasi", "antofagasta", "atacama", "sqm", "el teniente", "chuquicamata", "sernageomin"],
    "PER": ["peru", "peruvian", "las bambas", "cerro verde", "antamina", "quellaveco", "matarani", "callao", "chancay", "apurímac", "apurimac", "petroperú", "petroperu", "reinfo", "lima"],
    "BOL": ["bolivia", "bolivian", "uyuni", "ylb", "la paz", "santa cruz de la sierra", "potosí", "potosi"],
    "COL": ["colombia", "colombian", "ecopetrol", "caño limón", "cano limon", "coveñas", "covenas", "catatumbo", "arauca", "eln", "cartagena", "bogotá", "bogota", "cerro matoso", "buriticá", "buritica"],
    "VEN": ["venezuela", "venezuelan", "pdvsa", "maduro", "caracas", "orinoco", "jose terminal", "paraguaná", "paraguana", "amuay", "delcy rodríguez", "delcy rodriguez"],
    "GUY": ["guyana", "guyanese", "stabroek", "essequibo", "georgetown", "yellowtail", "uaru", "whiptail"],
    "ECU": ["ecuador", "ecuadorian", "petroecuador", "esmeraldas", "sote", "ocp pipeline", "guayaquil", "quito", "fruta del norte", "mirador"],
    "PAN": ["panama", "panamanian", "panama canal", "cobre panamá", "cobre panama", "first quantum", "gatún", "gatun", "colón", "colon"],
    "TTO": ["trinidad", "tobago", "atlantic lng", "point lisas", "dragon gas", "port of spain"],
    "SUR": ["suriname", "surinamese", "staatsolie", "granmorgu", "block 58", "paramaribo"],
    "XX": ["strait of hormuz", "hormuz", "bab el-mandeb", "bab al-mandab", "red sea", "houthi", "gulf of aden", "suez canal", "strait of magellan", "windward passage", "cape of good hope"],
}


def _text(raw):
    return f"{raw['title']} {raw.get('body', '')}".lower()


def _has(text, word):
    return re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", text) is not None


def place_terms(ref):
    terms = {}
    for iso, words in COUNTRY_ALIASES.items():
        for w in words:
            terms[w] = iso
    for c in ref["countries"]:
        terms.setdefault(c["name"].lower(), c["iso"])
    for a in ref["assets"]:
        for n in [a["name"]] + list(a.get("aliases") or []):
            base = re.sub(r"\s*\(.*\)", "", n).lower()
            if len(base) >= 5:
                terms.setdefault(base, a["iso"])
    for k in ref["chokepoints"] + ref["ports"]:
        terms.setdefault(k["name"].lower().split(" / ")[0], k.get("iso") or "")
    return terms


def prefilter(raw, ref, _cache={}):
    key = id(ref)
    if key not in _cache:
        _cache[key] = place_terms(ref)
    text = _text(raw)
    return any(_has(text, w) for w in _cache[key]) and any(_has(text, w) for w in TOPIC_WORDS)


# ------------------------------------------------------------------ Claude

SYSTEM = """You are the tagging analyst for Metropole Logistics, a geopolitical risk service for traders of African metals.
For each news item, decide whether it describes a real-world development that could affect the production, export,
transport, financing or legal status of metals OR energy (crude, refined products, LNG, pipeline gas, LPG, bunker fuel)
from the African and Mediterranean countries we cover, or the sea lanes that serve them (Hormuz, Red Sea, Suez, Cape, Gulf of Guinea). Then tag it precisely.

Rules:
- relevant=false for opinion pieces, price commentary without a physical or policy cause, company PR with no risk angle,
  or anything outside our countries and commodities (unless it is a global shock to African trade logistics: type "exogenous").
- country_iso3 "XX" is for sea-lane and global events (Hormuz, Red Sea, Suez attacks); still tag the corridor ids.
- Use ONLY ids that appear in the register below. Never invent an asset, corridor or chokepoint id. Leave lists empty if unsure.
- severity: 1 low (milestone, minor), 2 elevated (watch: protest, minor delay, proposed rule), 3 high (operations or exports
  disrupted, rule enacted, licence revoked, sanctions designation), 4 critical (mine seized, export ban in force on a major
  metal, armed attack on a mine or corridor, coup, border closed).
- title: neutral, factual, under 110 characters, in the style "DRC suspends cobalt exports for four months".
- summary: two sentences. First: what happened. Second: why it matters for metal flows or counterparties.
- event_date: the date the development happened (YYYY-MM-DD), best estimate from the item.
- confidence: 0 to 1, how sure you are that the item is accurate, relevant and correctly tagged.

REGISTER
"""


def register_text(ref):
    lines = ["Countries (iso3: name): " + "; ".join(f"{c['iso']}: {c['name']}" for c in ref["countries"])]
    lines.append("Assets (id | name | iso | kind | commodities | operator):")
    lines += [f"{a['id']} | {a['name']} | {a['iso']} | {a.get('kind', 'mine')} | {','.join(a['metals'])} | {a.get('operator', '')}" for a in ref["assets"]]
    lines.append("Corridors (id | name | metals):")
    lines += [f"{c['id']} | {c['name']} | {','.join(c.get('metals', []))}" for c in ref["corridors"]]
    lines.append("Chokepoints (id | name | between):")
    lines += [f"{c['id']} | {c['name']} | {c.get('between_text', '')}" for c in ref["chokepoints"]]
    lines.append("Ports (id | name | iso): " + "; ".join(f"{p['id']}|{p['name']}|{p['iso']}" for p in ref["ports"]))
    lines.append("Commodity codes: " + ", ".join(METAL_WORDS) + " (Crude, Prod, LNG, Gas, LPG, Bunk are energy; the rest are metals)")
    return "\n".join(lines)


TOOL = {
    "name": "record_event",
    "description": "Record the tagged event for this news item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant": {"type": "boolean"},
            "country_iso3": {"type": "string", "description": "ISO3 code from the register, or XX for global"},
            "asset_ids": {"type": "array", "items": {"type": "string"}},
            "corridor_ids": {"type": "array", "items": {"type": "string"}},
            "chokepoint_id": {"type": "string"},
            "port_id": {"type": "string"},
            "metals": {"type": "array", "items": {"type": "string", "enum": list(METAL_WORDS)}},
            "type": {"type": "string", "enum": TYPES},
            "severity": {"type": "integer", "minimum": 1, "maximum": 4},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "event_date": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["relevant", "country_iso3", "metals", "type", "severity", "title", "summary", "event_date", "confidence"],
    },
}


class ClaudeTagger:
    name = "claude"

    def __init__(self, ref):
        import anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.system = [{"type": "text", "text": SYSTEM + register_text(ref), "cache_control": {"type": "ephemeral"}}]

    def tag(self, raw):
        msg = (f"Source: {raw['source']}\nPublished: {raw.get('published_at') or 'unknown'}\nURL: {raw.get('url', '')}\n"
               f"Title: {raw['title']}\nText: {raw.get('body', '')[:2500]}")
        resp = self.client.messages.create(
            model=config.ANTHROPIC_MODEL, max_tokens=700, system=self.system,
            tools=[TOOL], tool_choice={"type": "tool", "name": "record_event"},
            messages=[{"role": "user", "content": msg}],
        )
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                return dict(block.input)
        return {"relevant": False}


# ------------------------------------------------------------------ fallback

TYPE_RULES = [
    ("export-control", ["export ban", "ban on export", "quota", "suspend export", "suspends export", "export suspension"]),
    ("nationalisation", ["nationalis", "nationaliz", "seize", "seized", "expropriat", "takes control"]),
    ("sanctions", ["sanction", "ofac", "designat"]),
    ("conflict", ["attack", "rebel", "militia", "jihad", "insurgent", "armed", "m23", "jnim", "killed"]),
    ("unrest", ["protest", "strike", "riot", "blockade", "demonstrat"]),
    ("political", ["election", "coup", "junta", "president", "parliament"]),
    ("regulatory", ["licence", "license", "permit", "mining code", "royalty", "tax", "revok"]),
    ("infrastructure", ["derail", "congestion", "queue", "stuck", "delays", "port", "railway", "rail ", "locomotive", "power cut", "outage", "flood", "force majeure"]),
    ("milestone", ["first shipment", "commission", "start production", "first ore", "ramp-up", "ramp up"]),
]


class KeywordTagger:
    name = "keywords"

    def __init__(self, ref):
        self.ref = ref
        self.terms = place_terms(ref)

    def tag(self, raw):
        text = _text(raw)
        isos = [iso for w, iso in self.terms.items() if iso and _has(text, w)]
        if not isos:
            return {"relevant": False}
        iso = max(set(isos), key=isos.count)
        assets = [a["id"] for a in self.ref["assets"] if _has(text, re.sub(r"\s*\(.*\)", "", a["name"]).lower())]
        metals = [m for m, ws in METAL_WORDS.items() if any(_has(text, w) for w in ws)]
        etype = next((t for t, ws in TYPE_RULES if any(w in text for w in ws)), "political")
        sev = 3 if etype in ("export-control", "nationalisation", "conflict", "sanctions") else 2
        if any(w in text for w in ["coup", "seized", "border closed", "killed"]):
            sev = 4
        choke = next((c for c in self.ref["chokepoints"] if _has(text, c["name"].lower().split(" / ")[0].split(" (")[0])), None)
        corridors = [c["id"] for c in self.ref["corridors"] if any(s.lower() in text for s in c.get("serves", [])) or c["name"].lower() in text]
        return {"relevant": True, "country_iso3": iso, "asset_ids": assets, "corridor_ids": corridors, "metals": metals,
                "chokepoint_id": choke["id"] if choke else None, "type": etype, "severity": sev, "title": raw["title"][:110],
                "summary": (raw.get("body") or "")[:300], "event_date": (raw.get("published_at") or "")[:10],
                "confidence": 0.4}


def make_tagger(ref):
    if config.ANTHROPIC_API_KEY:
        try:
            return ClaudeTagger(ref)
        except ImportError:
            print("  tagger: 'anthropic' package missing; run: pip install -r requirements.txt")
    return KeywordTagger(ref)
