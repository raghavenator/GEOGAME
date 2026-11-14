"""Geo Arcade application factory."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Flask, abort, redirect, render_template, url_for
from werkzeug.wrappers import Response

from app.shared import highscores
from app.shared.icons import icon
from app.shared.settings import schema_to_json


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(
        __name__,
        static_folder="shared/static",
        template_folder="shared/templates",
        instance_relative_config=True,
    )
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("GEO_ARCADE_SECRET", "dev"),
        DATABASE=str(Path(app.instance_path) / "geo_arcade.sqlite3"),
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    highscores.init_app(app)
    app.jinja_env.globals["icon"] = icon
    app.jinja_env.filters["schema_json"] = schema_to_json

    # Import lazily so test config (e.g. RNG seed) is set before blueprints initialise.
    from app.feed import FEED, resolve_feed
    from app.games.registry import GAMES, get_game

    feed_cards = resolve_feed(FEED, {g.id: g for g in GAMES})  # fails fast on a bad feed config

    for game in GAMES:
        if game.playable and game.blueprint is not None:
            app.register_blueprint(game.blueprint, url_prefix=f"/games/{game.id}")

    @app.context_processor
    def _inject() -> dict[str, Any]:
        return {"all_games": GAMES}

    feed_items = [{"id": c.item.id, "kind": c.item.kind, "gameId": c.item.game_id, "title": c.title,
                   "tint": c.item.tint, "mapZone": bool(c.game and c.game.map_zone)} for c in feed_cards]

    @app.get("/")
    def feed() -> str:
        return render_template("feed.html", cards=feed_cards, feed_items=feed_items)

    @app.get("/games")
    def hub() -> str:
        return render_template("hub.html", games=GAMES)

    @app.get("/games/<game_id>")
    def game_redirect(game_id: str) -> Response:
        # /games/<id> (no trailing slash) -> the game's blueprint index.
        game = get_game(game_id)
        if game is None or not game.playable or game.blueprint is None:
            abort(404)
        return redirect(url_for(f"{game.blueprint.name}.play"))

    @app.get("/styleguide")
    def styleguide() -> str:
        return render_template("styleguide.html")

    @app.errorhandler(404)
    def not_found(_e: Exception) -> tuple[str, int]:
        return render_template("404.html"), 404

    return app
