"""HTTP layer for Pinpoint. Rules live in logic.py; this file parses input, guards each
round with its lock, and shapes JSON.

Nothing about a question is sent before the player taps play: ``start`` returns only the
round id and settings; ``begin``/``next`` return the country's display name and nothing
else (no coordinates, id or code). The target is only sent with the result of ``submit``."""
from __future__ import annotations

import random
import secrets
import time
from typing import Any

from flask import current_app, jsonify, render_template, url_for
from flask.blueprints import BlueprintSetupState
from flask.typing import ResponseReturnValue

from app.shared import highscores
from app.shared.api import ApiError, json_body, register_error_handlers, require_int, require_str
from app.shared.geo import load_country_polygons, world_data_urls
from app.shared.round_store import RoundStore
from app.shared.settings import SettingsError, defaults, settings_key, validate_settings

from . import bp
from . import logic
from .location_data import Location, load_locations, locations_by_code
from .logic import CONFIG, SETTINGS_SCHEMA, Question, Round

GAME_ID = "pinpoint"

register_error_handlers(bp)


@bp.record_once
def _setup(state: BlueprintSetupState) -> None:
    app = state.app
    app.extensions["pinpoint.rounds"] = RoundStore[Round]()
    seed = app.config.get("PINPOINT_SEED")
    app.extensions["pinpoint.rng"] = random.Random(seed) if seed is not None else random.SystemRandom()
    app.extensions["pinpoint.clock"] = app.config.get("PINPOINT_CLOCK", time.monotonic)
    load_locations()  # fail at startup, not on the first request, if the data is missing
    load_country_polygons()  # decode the borders once (~0.5 s) so the first submit isn't slow


def _store() -> RoundStore[Round]:
    return current_app.extensions["pinpoint.rounds"]


def _rng() -> random.Random:
    return current_app.extensions["pinpoint.rng"]


def _now() -> float:
    return current_app.extensions["pinpoint.clock"]()


def _entry(code: str) -> Location:
    return locations_by_code()[code]


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


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


def _require_begun(rnd: Round) -> None:
    if not rnd.begun:
        raise ApiError(409, "not_started", "This round hasn't started yet.")
    logic.resume(rnd, _now())  # acting while paused means the player is back


def _question_json(rnd: Round) -> dict[str, Any]:
    """The active question as the player may see it: the country's name, nothing else."""
    q = rnd.current
    return {
        "index": rnd.index,
        "number": rnd.index + 1,
        "total": len(rnd.questions),
        "name": _entry(q.code).name,
        "time_limit_ms": rnd.limit * 1000,
        "elapsed_ms": _ms(q.elapsed(_now())),
        "status": q.status,
    }


def _target_json(e: Location) -> dict[str, Any]:
    return {"lat": e.lat, "lng": e.lng, "name": e.name, "code": e.code, "shape": e.shape,
            "flag_url": url_for("static", filename=f"flags/{e.code}.svg")}


def _result_json(rnd: Round, q: Question, now: float) -> dict[str, Any]:
    s = q.score
    assert s is not None and q.guess is not None
    return {
        "guess": {"lat": q.guess[0], "lng": q.guess[1]},
        "target": _target_json(_entry(q.code)),
        "distance_km": round(s.distance_km, 1),
        "distance_mi": round(logic.km_to_miles(s.distance_km), 1),
        "accuracy": s.accuracy,
        "speed_factor": round(s.speed, 3),
        "points": s.points,
        "inside": s.inside,
        "elapsed_ms": _ms(q.elapsed(now)),
        "timed_out": q.timed_out,
    }


# --------------------------------------------------------------------------- pages
def embed_context() -> dict[str, Any]:
    """What the embed macros (templates/pinpoint/_embed.html) need, for the page and the feed."""
    from app.games.registry import get_game

    return {"game": get_game(GAME_ID), "config": CONFIG, "settings_defaults": defaults(SETTINGS_SCHEMA),
            "world": world_data_urls()}


@bp.get("/")
def play() -> str:
    ctx = embed_context()
    return render_template("pinpoint/play.html", game=ctx["game"], ctx=ctx)


# --------------------------------------------------------------------------- API
@bp.post("/api/best")
def best() -> ResponseReturnValue:
    settings = _clean_settings(json_body().get("settings"))
    return jsonify({"best": highscores.get_best(GAME_ID, _score_key(settings)),
                    "max_score": logic.max_score_for(settings["questions"]), "settings": settings})


@bp.post("/api/start")
def start() -> ResponseReturnValue:
    """Prepare a round. Deliberately says nothing about its questions."""
    settings = _clean_settings(json_body().get("settings"))
    round_id = secrets.token_urlsafe(12)
    try:
        rnd = logic.new_round(round_id, settings, load_locations(), _rng())
    except ValueError as exc:
        raise ApiError(400, "invalid_settings", str(exc), {"fields": {"questions": str(exc)}}) from exc
    _store().put(round_id, rnd)
    return jsonify({
        "round_id": round_id,
        "settings": settings,
        "total": len(rnd.questions),
        "max_score": rnd.max_score,
        "best": highscores.get_best(GAME_ID, _score_key(settings)),
        "scoring": {"time_limit_ms": rnd.limit * 1000, "full_speed_ms": _ms(CONFIG.full_speed_seconds),
                    "min_speed_factor": CONFIG.min_speed_factor, "max_points": CONFIG.max_points},
    })


@bp.post("/api/begin")
def begin() -> ResponseReturnValue:
    """The player tapped play: start the clock and reveal the first country name."""
    rnd = _load_round(json_body())
    with _store().lock(rnd.id):
        started = logic.begin(rnd, _now())
        if rnd.finished:
            return jsonify({"started": started, "finished": True, "score": rnd.score})
        body: dict[str, Any] = {"started": started, "finished": False, "question": _question_json(rnd),
                                "score": rnd.score}
        if rnd.current.status != "active":
            body["result"] = _result_json(rnd, rnd.current, _now())
        return jsonify(body)


@bp.post("/api/submit")
def submit() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    index = require_int(data, "question_index", 0, len(rnd.questions) - 1)
    try:
        lat, lng = logic.validate_coords(data.get("lat"), data.get("lng"))
    except ValueError as exc:
        raise ApiError(400, "bad_request", str(exc)) from None
    timeout = data.get("timeout", False)
    if not isinstance(timeout, bool):
        raise ApiError(400, "bad_request", "'timeout' must be true or false.")
    with _store().lock(rnd.id):
        _require_begun(rnd)
        if rnd.finished or index != rnd.index:
            raise ApiError(409, "question_over", "That question has already ended.")
        q = rnd.current
        now = _now()
        try:
            e = _entry(q.code)
            logic.apply_submit(q, lat, lng, e, now, rnd.limit, CONFIG.difficulty(rnd.settings["difficulty"]),
                               timeout=timeout, polygons=load_country_polygons().get(e.shape, ()))
        except logic.QuestionOver:
            raise ApiError(409, "question_over", "You've already submitted this question.") from None
        except logic.TimeoutTooEarly as exc:
            raise ApiError(409, "too_early", "There's still time left on this question.",
                           {"remaining_ms": _ms(exc.remaining_s)}) from None
        return jsonify({**_result_json(rnd, q, now), "score": rnd.score, "is_last": rnd.is_last})


@bp.post("/api/pause")
def pause() -> ResponseReturnValue:
    rnd = _load_round(json_body())
    with _store().lock(rnd.id):
        return jsonify({"paused": logic.pause(rnd, _now()) or rnd.paused_at is not None})


@bp.post("/api/resume")
def resume() -> ResponseReturnValue:
    rnd = _load_round(json_body())
    with _store().lock(rnd.id):
        logic.resume(rnd, _now())
        return jsonify({"paused": False, "elapsed_ms": _ms(rnd.current.elapsed(_now())) if rnd.begun else 0})


@bp.post("/api/next")
def next_question() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    from_index = require_int(data, "from_index", 0, len(rnd.questions) - 1)
    with _store().lock(rnd.id):
        _require_begun(rnd)
        try:
            logic.advance(rnd, from_index, _now())
        except logic.QuestionActive:
            raise ApiError(409, "question_active", "Submit a guess before moving on.") from None
        if rnd.finished:
            return jsonify({"finished": True, "score": rnd.score})
        return jsonify({"finished": False, "question": _question_json(rnd), "score": rnd.score})


@bp.post("/api/summary")
def summary() -> ResponseReturnValue:
    rnd = _load_round(json_body())
    with _store().lock(rnd.id):
        if not rnd.finished:
            raise ApiError(409, "round_in_progress", "The round isn't finished yet.")
        if not rnd.recorded:
            result = highscores.record_score(GAME_ID, _score_key(rnd.settings), rnd.score, rnd.max_score)
            rnd.recorded = True
            rnd.extra["record"] = {"best": result.best, "is_new_best": result.is_new_best,
                                   "previous_best": result.previous_best}
        record = rnd.extra["record"]
        now = _now()
    return jsonify({
        "score": rnd.score,
        "max_score": rnd.max_score,
        "settings": rnd.settings,
        **record,
        "questions": [{"number": i + 1, **_result_json(rnd, q, now), "max_points": CONFIG.max_points}
                      for i, q in enumerate(rnd.questions)],
    })
