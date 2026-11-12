"""Copy flag SVGs from an extracted `flag-icons` package into app/shared/static/flags.

Usage:
    python scripts/vendor_flags.py <path-to-flag-icons>/flags/4x3

Only flags for countries listed in countries.json are copied. Each SVG is
sanitised so it cannot reveal its country: the root ``id="flag-icons-xx"``
is removed and internal ids prefixed with the country code (``us-a``) are
renamed to a neutral prefix (``f-a``).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COUNTRIES = ROOT / "app" / "shared" / "data" / "countries.json"
DEST = ROOT / "app" / "shared" / "static" / "flags"


def sanitise_svg(svg: str, code: str) -> str:
    svg = re.sub(r'\s+id="flag-icons-[a-z-]+"', "", svg)
    # ids, url(#..), href="#..", and space-separated class lists
    svg = re.sub(rf'(["#( ]){re.escape(code)}-', r"\1f-", svg)
    return svg


def main(src_dir: str) -> None:
    src = Path(src_dir)
    codes = [c["code"] for c in json.loads(COUNTRIES.read_text(encoding="utf-8"))]
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    for code in codes:
        svg = (src / f"{code}.svg").read_text(encoding="utf-8")
        clean = sanitise_svg(svg, code)
        if re.search(rf"\b{re.escape(code)}-|flag-icons", clean):
            raise SystemExit(f"{code}.svg still contains identifying ids")
        (DEST / f"{code}.svg").write_text(clean, encoding="utf-8")
    licence = src.parent.parent / "LICENSE"
    if licence.exists():
        shutil.copy(licence, DEST / "LICENSE-flag-icons.txt")
    print(f"Copied {len(codes)} flags to {DEST}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
