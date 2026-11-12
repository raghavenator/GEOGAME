"""Border Bridge: connect two countries through a chain of land borders."""
from flask import Blueprint

from app.shared.game import GameSpec

from .logic import SETTINGS_SCHEMA

bp = Blueprint("border_bridge", __name__, template_folder="templates", static_folder="static",
               static_url_path="/static")

from . import routes  # noqa: E402,F401  (registers views on bp)

GAME = GameSpec(
    id="border-bridge",
    title="Border Bridge",
    description="Link two countries through a chain of neighbours. Pick the countries in between, border by border.",
    icon="route",
    blueprint=bp,
    settings_schema=SETTINGS_SCHEMA,
    tagline="Globe + map",
    embed_template="border_bridge/_embed.html",
    embed_context=routes.embed_context,
)
