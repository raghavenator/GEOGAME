"""The contract every game package fulfils so the registry can wire it up."""
from __future__ import annotations

from dataclasses import dataclass

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

    @property
    def playable(self) -> bool:
        return self.blueprint is not None and not self.coming_soon
