"""Push a register JSON file (written by one of the importers, or edited by hand) into the database.

  python tools/push_register.py assets register/gem_assets.json
  python tools/push_register.py ports register/wpi_ports.json
  python tools/push_register.py corridors register/osm_eacop.json

Rows are upserted on their id, so re-pushing updates in place.
"""
import json
import sys

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from registry import push

KEYS = {"assets": "id", "ports": "id", "corridors": "id", "chokepoints": "id", "countries": "iso", "zones": "id", "forecasts": "id", "briefings": "id"}

if len(sys.argv) != 3 or sys.argv[1] not in KEYS:
    sys.exit(__doc__)
table, path = sys.argv[1], sys.argv[2]
rows = json.load(open(path))
push(table, KEYS[table], rows)
