"""The contract every game package fulfils so the registry can wire it up."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import Blueprint

from .settings import SettingsSchema


@dataclass(frozen=True)
class GameSpec:
    id: str                       # URL slug, e.g. "flag-reveal" -> /games/flag-reveal
    title: str
    description: str
    icon: str                     # Lucide icon name (see shared/static/vendor/lucide)
    blueprint: Blueprint | None   # None for "coming soon" placeholders
    settings_schema: SettingsSchema = ()
    tagline: str = ""             # short badge text on the hub card
    coming_soon: bool = False
    # Embedding (feed + full page): a Jinja file defining macros styles(), scripts()
    # and panel(ctx, mode), and a function returning the ctx dict panel() needs.
    embed_template: str = ""
    embed_context: Callable[[], dict[str, Any]] | None = None

    @property
    def embeddable(self) -> bool:
        return self.playable and bool(self.embed_template) and self.embed_context is not None

    @property
    def playable(self) -> bool:
        return self.blueprint is not None and not self.coming_soon
