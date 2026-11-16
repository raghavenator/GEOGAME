"""Currency Quest: a country is shown; pick the currency it uses before the points drain away."""
from flask import Blueprint

from app.shared.game import GameSpec

from .logic import SETTINGS_SCHEMA

bp = Blueprint("currency_quest", __name__, template_folder="templates", static_folder="static",
               static_url_path="/static")

from . import routes  # noqa: E402,F401  (registers views on bp)

GAME = GameSpec(
    id="currency-quest",
    title="Currency Quest",
    description="A country is named. Pick the currency it uses before the points drain away.",
    icon="banknote",
    blueprint=bp,
    settings_schema=SETTINGS_SCHEMA,
    tagline="Money talks",
    embed_template="currency_quest/_embed.html",
    embed_context=routes.embed_context,
)
