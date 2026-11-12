"""HTTP layer for Border Bridge. Rules live in logic.py and graph.py; this file
parses input, guards each round with its lock, and shapes JSON. The client never
learns which options are correct before it picks one."""
from __future__ import annotations

import random
import re
import secrets
import time
from typing import Any

from flask import current_app, jsonify, render_template
from flask.blueprints import BlueprintSetupState
from flask.typing import ResponseReturnValue

from app.shared import highscores
from app.shared.api import ApiError, json_body, register_error_handlers, require_int, require_str
from app.shared.round_store import RoundStore
from app.shared.settings import SettingsError, defaults, schema_to_json, settings_key, validate_settings

from . import bp
from . import logic
from .graph import BorderGraph, get_graph
from .logic import CONFIG, SETTINGS_SCHEMA, Puzzle, Round

GAME_ID = "border-bridge"
OPTION_ID = re.compile(r"[A-Z]{3}")

register_error_handlers(bp)


@bp.record_once
def _setup(state: BlueprintSetupState) -> None:
    app = state.app
    app.extensions["border_bridge.rounds"] = RoundStore[Round]()
    seed = app.config.get("BORDER_BRIDGE_SEED")
    app.extensions["border_bridge.rng"] = random.Random(seed) if seed is not None else random.SystemRandom()
    app.extensions["border_bridge.clock"] = app.config.get("BORDER_BRIDGE_CLOCK", time.monotonic)
    logic.warm_pair_pools(get_graph())  # pre-compute every pair pool once per process


def _store() -> RoundStore[Round]:
    return current_app.extensions["border_bridge.rounds"]


def _rng() -> random.Random:
    return current_app.extensions["border_bridge.rng"]


def _now() -> float:
    return current_app.extensions["border_bridge.clock"]()


# --------------------------------------------------------------------------- helpers
def _clean_settings(raw: Any) -> dict[str, Any]:
    try:
        return validate_settings(SETTINGS_SCHEMA, raw)
    except SettingsError as exc:
        raise ApiError(400, "invalid_settings", "Some settings are invalid.", {"fields": exc.errors}) from exc


def _score_key(settings: dict[str, Any]) -> str:
    return settings_key(logic.scored_settings(settings))


def _load_round(data: dict[str, Any]) -> Round:
    rnd = _store().get(require_str(data, "round_id", max_len=64))
    if rnd is None:
        raise ApiError(404, "round_not_found", "That round has expired or doesn't exist. Start a new one.")
    return rnd


def _current_puzzle(rnd: Round, data: dict[str, Any]) -> Puzzle:
    index = require_int(data, "puzzle_index", 0, len(rnd.puzzles) - 1)
    if rnd.finished or index != rnd.index:
        raise ApiError(409, "puzzle_over", "That puzzle has already finished.")
    return rnd.current


def _country(g: BorderGraph, cca3: str | None) -> dict[str, str] | None:
    if cca3 is None:
        return None
    n = g.nodes[cca3]
    return {"id": n.cca3, "shape": n.ccn3, "name": n.name}


def _running_score(rnd: Round) -> int:
    p = rnd.current
    return rnd.score


def _trail_json(g: BorderGraph, p: Puzzle) -> dict[str, Any]:
    a_side, b_side = logic.trail(g, p)
    return {"a_side": [_country(g, c) for c in a_side], "b_side": [_country(g, c) for c in b_side],
            "remaining": max(0, p.between - p.correct_picks)}


def _puzzle_json(g: BorderGraph, rnd: Round) -> dict[str, Any]:
    p = rnd.current
    return {
        "trail": _trail_json(g, p),
        "index": rnd.index,
        "number": rnd.index + 1,
        "total": len(rnd.puzzles),
        "a": _country(g, p.a),
        "b": _country(g, p.b),
        "between": p.between,
        "found": [_country(g, c) for c in p.revealed[2:]],
        "options": [{"id": c, "name": g.nodes[c].name} for c in p.options],
        "wrong": p.wrong_picks,
        "max_points": logic.max_points(p.between),
        "status": p.status,
    }


def _ended_json(g: BorderGraph, rnd: Round, p: Puzzle, added: list[str] | None = None) -> dict[str, Any]:
    return {
        "chain": [_country(g, c) for c in p.chain],
        "added": [_country(g, c) for c in added or []],
        "points": p.points,
        "bonuses": p.bonuses,
        "elapsed_ms": round(p.elapsed(_now()) * 1000),
        "score": rnd.score,
        "is_last": rnd.is_last,
    }


# --------------------------------------------------------------------------- pages
@bp.get("/")
def play() -> str:
    from app.games.registry import get_game

    return render_template(
        "border_bridge/play.html",
        game=get_game(GAME_ID),
        schema_json=schema_to_json(SETTINGS_SCHEMA),
        settings_defaults=defaults(SETTINGS_SCHEMA),
        config=CONFIG,
    )


# --------------------------------------------------------------------------- API
@bp.post("/api/best")
def best() -> ResponseReturnValue:
    settings = _clean_settings(json_body().get("settings"))
    return jsonify({"best": highscores.get_best(GAME_ID, _score_key(settings)), "settings": settings})


@bp.post("/api/start")
def start() -> ResponseReturnValue:
    settings = _clean_settings(json_body().get("settings"))
    round_id = secrets.token_urlsafe(12)
    g = get_graph()
    try:
        rnd = logic.new_round(round_id, settings, g, _rng(), _now)
    except ValueError as exc:
        raise ApiError(400, "invalid_settings", str(exc), {"fields": {"puzzles": str(exc)}}) from exc
    _store().put(round_id, rnd)
    return jsonify({
        "round_id": round_id,
        "settings": settings,
        "max_score": rnd.max_score,
        "best": highscores.get_best(GAME_ID, _score_key(settings)),
        "puzzle": _puzzle_json(g, rnd),
    })


@bp.post("/api/pick")
def pick() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    option = require_str(data, "option_id", max_len=3)
    if not OPTION_ID.fullmatch(option):
        raise ApiError(400, "bad_request", "'option_id' must be a three-letter country id.")
    g = get_graph()
    with _store().lock(rnd.id):
        p = _current_puzzle(rnd, data)
        try:
            out = logic.apply_pick(g, p, option, _rng(), _now())
        except logic.PuzzleOver:
            raise ApiError(409, "puzzle_over", "That puzzle has already finished.") from None
        except logic.OptionUnavailable:
            raise ApiError(409, "option_unavailable", "That option isn't available any more.") from None

        name = g.nodes[option].name
        touching = [r for r in reversed(p.revealed) if r != option and r in g.neighbours(option)]
        if out.kind == "correct":
            message = f"{name} borders {g.nodes[touching[0]].name}."
        elif out.kind == "detour":
            message = f"{name} borders {g.nodes[out.via].name}, but it's a detour."  # type: ignore[index]
        else:
            message = f"{name} doesn't border any of the connected countries."
        body: dict[str, Any] = {
            "result": out.kind,
            "country": _country(g, option),
            "via": _country(g, touching[0] if touching else None),
            "message": message,
            "penalty": CONFIG.wrong_penalty if out.kind != "correct" else 0,
            "wrong": p.wrong_picks,
            "solved": out.solved,
            "found": [_country(g, c) for c in p.revealed[2:]],
            "trail": _trail_json(g, p),
            "options": [{"id": c, "name": g.nodes[c].name} for c in p.options],
            "score": _running_score(rnd),
        }
        if out.solved:
            body.update(_ended_json(g, rnd, p))
            body["message"] = f"Connected! {message}"
        return jsonify(body)


@bp.post("/api/giveup")
def giveup() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    g = get_graph()
    with _store().lock(rnd.id):
        p = _current_puzzle(rnd, data)
        try:
            added = logic.give_up(g, p, _now())
        except logic.PuzzleOver:
            raise ApiError(409, "puzzle_over", "That puzzle has already finished.") from None
        return jsonify({"result": "gave_up", **_ended_json(g, rnd, p, added)})


@bp.post("/api/next")
def next_puzzle() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    from_index = require_int(data, "from_index", 0, len(rnd.puzzles) - 1)
    g = get_graph()
    with _store().lock(rnd.id):
        try:
            logic.advance(g, rnd, from_index, _rng(), _now())
        except logic.PuzzleActive:
            raise ApiError(409, "puzzle_active", "Finish or give up on this puzzle first.") from None
        if rnd.finished:
            return jsonify({"finished": True, "score": rnd.score})
        return jsonify({"finished": False, "puzzle": _puzzle_json(g, rnd), "score": _running_score(rnd)})


@bp.post("/api/summary")
def summary() -> ResponseReturnValue:
    rnd = _load_round(json_body())
    g = get_graph()
    with _store().lock(rnd.id):
        if not rnd.finished:
            raise ApiError(409, "round_in_progress", "The round isn't finished yet.")
        if not rnd.recorded:
            result = highscores.record_score(GAME_ID, _score_key(rnd.settings), rnd.score, rnd.max_score)
            rnd.recorded = True
            rnd.extra["record"] = {"best": result.best, "is_new_best": result.is_new_best,
                                   "previous_best": result.previous_best}
        record = rnd.extra["record"]
    return jsonify({
        "score": rnd.score,
        "max_score": rnd.max_score,
        "settings": rnd.settings,
        **record,
        "puzzles": [
            {
                "number": i + 1,
                "a": _country(g, p.a),
                "b": _country(g, p.b),
                "chain": [_country(g, c) for c in p.chain],
                "status": p.status,
                "wrong": [_country(g, k.cca3) for k in p.picks if k.kind != "correct"],
                "elapsed_ms": round(p.elapsed(_now()) * 1000),
                "points": p.points,
                "max_points": logic.max_points(p.between),
                "bonuses": p.bonuses,
            }
            for i, p in enumerate(rnd.puzzles)
        ],
    })
