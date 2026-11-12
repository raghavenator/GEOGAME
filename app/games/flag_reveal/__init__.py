"""Flag Reveal: name the country as its flag is revealed one third at a time."""
from __future__ import annotations

from flask import Blueprint

from app.shared.game import GameSpec

from .logic import SETTINGS_SCHEMA

bp = Blueprint(
    "flag_reveal",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)

from . import routes  # noqa: E402,F401  (registers views on bp)

GAME = GameSpec(
    id="flag-reveal",
    title="Flag Reveal",
    description="A flag appears a third at a time. Name the country early for more points.",
    icon="flag",
    tagline="15 · 10 · 5 pts",
    blueprint=bp,
    settings_schema=SETTINGS_SCHEMA,
)
