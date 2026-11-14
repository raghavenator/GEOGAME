"""The pinned mledoze/countries source file, shared by the data scripts.

The npm release world-countries 5.1.0 (ODbL) is downloaded once to
app/games/border_bridge/data/raw/world-countries.json (git-ignored), the same file
scripts/fetch_border_data.py uses. Capital Quest and Pinpoint build their data from it.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RAW_FILE = ROOT / "app" / "games" / "border_bridge" / "data" / "raw" / "world-countries.json"
RAW_URL = "https://cdn.jsdelivr.net/npm/world-countries@5.1.0/countries.json"
COUNTRIES_JSON = ROOT / "app" / "shared" / "data" / "countries.json"


def fetch(refresh: bool = False) -> None:
    """Download the source file unless it's already there (or ``refresh``)."""
    if RAW_FILE.exists() and not refresh:
        return
    RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"fetching {RAW_URL}")
    try:
        with urllib.request.urlopen(RAW_URL, timeout=60) as resp:
            RAW_FILE.write_bytes(resp.read())
    except OSError as exc:
        sys.exit(f"Download failed ({exc}). Fetch it by hand and save it as {RAW_FILE.relative_to(ROOT)}.")


def load_raw() -> list[dict[str, Any]]:
    return json.loads(RAW_FILE.read_text(encoding="utf-8"))


def load_countries() -> list[dict[str, Any]]:
    """The shared countries.json rows (code, name, region, cca3, ccn3)."""
    return json.loads(COUNTRIES_JSON.read_text(encoding="utf-8"))
