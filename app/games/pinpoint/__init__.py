"""Pinpoint: a country is named; move the map until the crosshair is on it."""
from flask import Blueprint

from app.shared.game import GameSpec

from .logic import SETTINGS_SCHEMA

bp = Blueprint("pinpoint", __name__, template_folder="templates", static_folder="static",
               static_url_path="/static")

from . import routes  # noqa: E402,F401  (registers views on bp)

GAME = GameSpec(
    id="pinpoint",
    title="Pinpoint",
    description="A country is named. Move the map until the crosshair is on it, then lock in your guess.",
    icon="target",
    blueprint=bp,
    settings_schema=SETTINGS_SCHEMA,
    tagline="Find it fast",
    embed_template="pinpoint/_embed.html",
    embed_context=routes.embed_context,
    map_zone=True,
)
