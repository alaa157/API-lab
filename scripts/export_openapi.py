"""Regenerate the committed openapi.json (make docs)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import create_app

spec = create_app().openapi()
Path("openapi.json").write_text(json.dumps(spec, indent=2) + "\n")
print("wrote openapi.json")
