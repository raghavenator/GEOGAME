"""Capital Quest: a country lights up on the map; pick its capital before the points drain away."""
from flask import Blueprint

from app.shared.game import GameSpec

from .logic import SETTINGS_SCHEMA

bp = Blueprint("capital_quest", __name__, template_folder="templates", static_folder="static",
               static_url_path="/static")

from . import routes  # noqa: E402,F401  (registers views on bp)

GAME = GameSpec(
    id="capital-quest",
    title="Capital Quest",
    description="A country lights up on the map. Pick its capital before the points drain away.",
    icon="landmark",
    blueprint=bp,
    settings_schema=SETTINGS_SCHEMA,
    tagline="Beat the clock",
    embed_template="capital_quest/_embed.html",
    embed_context=routes.embed_context,
    map_zone=True,
)
