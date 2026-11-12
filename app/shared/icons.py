"""Inline Lucide SVG icons in templates: {{ icon('flag', 'icon--lg') }}."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from markupsafe import Markup, escape

ICON_DIR = Path(__file__).parent / "static" / "vendor" / "lucide"


@lru_cache(maxsize=None)
def _svg_body(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise ValueError(f"bad icon name {name!r}")
    svg = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    return re.sub(r"\s+", " ", svg).strip()


def icon(name: str, extra_class: str = "", label: str | None = None) -> Markup:
    svg = _svg_body(name)
    cls = f"icon {escape(extra_class)}".strip()
    a11y = f'role="img" aria-label="{escape(label)}"' if label else 'aria-hidden="true" focusable="false"'
    svg = re.sub(r'class="[^"]*"', f'class="{cls}" {a11y}', svg, count=1)
    return Markup(svg)
