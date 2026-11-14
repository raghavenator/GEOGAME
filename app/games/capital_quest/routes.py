"""HTTP layer for Capital Quest. Rules live in logic.py; this file parses input,
guards each round with its lock, and shapes JSON.

Nothing about a question is sent before the player taps play: ``start`` returns only
the round id and settings, ``begin`` returns the first question. Options are opaque
indices; which one is correct is only sent once the question ends."""
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
from app.shared.geo import world_data_urls
from app.shared.round_store import RoundStore
from app.shared.settings import SettingsError, defaults, settings_key, validate_settings

from . import bp
from . import logic
from .capital_data import CapitalEntry, capitals_by_code, load_capitals
from .logic import CONFIG, SETTINGS_SCHEMA, Question, Round

GAME_ID = "capital-quest"

register_error_handlers(bp)


@bp.record_once
def _setup(state: BlueprintSetupState) -> None:
    app = state.app
    app.extensions["capital_quest.rounds"] = RoundStore[Round]()
    seed = app.config.get("CAPITAL_QUEST_SEED")
    app.extensions["capital_quest.rng"] = random.Random(seed) if seed is not None else random.SystemRandom()
    app.extensions["capital_quest.clock"] = app.config.get("CAPITAL_QUEST_CLOCK", time.monotonic)
    load_capitals()  # fail at startup, not on the first request, if the data is missing


def _store() -> RoundStore[Round]:
    return current_app.extensions["capital_quest.rounds"]


def _rng() -> random.Random:
    return current_app.extensions["capital_quest.rng"]


def _now() -> float:
    return current_app.extensions["capital_quest.clock"]()


def _entry(code: str) -> CapitalEntry:
    return capitals_by_code()[code]


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


def _current_question(rnd: Round, data: dict[str, Any]) -> Question:
    _require_begun(rnd)
    index = require_int(data, "question_index", 0, len(rnd.questions) - 1)
    if rnd.finished or index != rnd.index or rnd.current.status != "active":
        raise ApiError(409, "question_over", "That question has already ended.")
    return rnd.current


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def _question_json(rnd: Round) -> dict[str, Any]:
    """The active question as the player may see it: the shape to highlight and the
    remaining options' names. No country name, no answer."""
    q = rnd.current
    now = _now()
    return {
        "index": rnd.index,
        "number": rnd.index + 1,
        "total": len(rnd.questions),
        "shape": _entry(q.code).shape,
        "options": [{"id": i, "name": _entry(q.options[i]).capital} for i in q.live_options()],
        "wrong": q.wrong,
        "time_limit_ms": rnd.limit * 1000,
        "elapsed_ms": _ms(q.elapsed(now)),
        "potential": logic.potential_points(q.elapsed(now), q.wrong, rnd.limit),
        "status": q.status,
    }


def _answer_json(q: Question) -> dict[str, Any]:
    e = _entry(q.code)
    return {"option_id": q.answer_option, "capital": e.capital, "country": e.name, "code": e.code,
            "shape": e.shape, "flag_url": url_for("static", filename=f"flags/{e.code}.svg")}


def _ended_json(rnd: Round, q: Question) -> dict[str, Any]:
    return {
        "status": q.status,
        "answer": _answer_json(q),
        "points": q.points,
        "elapsed_ms": _ms(q.elapsed(_now())),
        "wrong": q.wrong,
        "score": rnd.score,
        "is_last": rnd.is_last,
    }


# --------------------------------------------------------------------------- pages
def embed_context() -> dict[str, Any]:
    """What the embed macros (templates/capital_quest/_embed.html) need, for the page and the feed."""
    from app.games.registry import get_game

    return {"game": get_game(GAME_ID), "config": CONFIG, "settings_defaults": defaults(SETTINGS_SCHEMA),
            "world": world_data_urls()}


@bp.get("/")
def play() -> str:
    ctx = embed_context()
    return render_template("capital_quest/play.html", game=ctx["game"], ctx=ctx)


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
        rnd = logic.new_round(round_id, settings, load_capitals(), _rng())
    except ValueError as exc:
        raise ApiError(400, "invalid_settings", str(exc), {"fields": {"questions": str(exc)}}) from exc
    _store().put(round_id, rnd)
    return jsonify({
        "round_id": round_id,
        "settings": settings,
        "total": len(rnd.questions),
        "max_score": rnd.max_score,
        "best": highscores.get_best(GAME_ID, _score_key(settings)),
        "scoring": {"start_points": CONFIG.start_points, "wrong_penalty": CONFIG.wrong_penalty,
                    "min_correct_points": CONFIG.min_correct_points, "time_limit_ms": rnd.limit * 1000},
    })


@bp.post("/api/begin")
def begin() -> ResponseReturnValue:
    """The player tapped play: start the clock and reveal the first question."""
    rnd = _load_round(json_body())
    with _store().lock(rnd.id):
        started = logic.begin(rnd, _now())
        if rnd.finished:
            return jsonify({"started": started, "finished": True, "score": rnd.score})
        logic.expire_if_overdue(rnd.current, _now(), rnd.limit)
        body: dict[str, Any] = {"started": started, "finished": False, "question": _question_json(rnd),
                                "score": rnd.score}
        if rnd.current.status != "active":
            body["ended"] = _ended_json(rnd, rnd.current)
        return jsonify(body)


@bp.post("/api/pick")
def pick() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    option_id = require_int(data, "option_id", 0, max(CONFIG.option_counts) - 1)
    with _store().lock(rnd.id):
        q = _current_question(rnd, data)
        try:
            out = logic.apply_pick(q, option_id, _now(), rnd.limit)
        except logic.QuestionOver:
            raise ApiError(409, "question_over", "That question has already ended.") from None
        except logic.OptionUnavailable:
            raise ApiError(409, "option_unavailable", "That option isn't available.") from None
        if out.kind == "wrong":
            return jsonify({
                "result": "wrong",
                "option_id": option_id,
                "penalty": CONFIG.wrong_penalty,
                "wrong": q.wrong,
                "potential": out.potential,
                "elapsed_ms": _ms(q.elapsed(_now())),
                "options": q.live_options(),
                "score": rnd.score,
            })
        return jsonify({"result": out.kind, "option_id": option_id, **_ended_json(rnd, q)})


@bp.post("/api/timeout")
def timeout() -> ResponseReturnValue:
    """The client's countdown ran out. Idempotent; rejected if the server's clock has time left."""
    data = json_body()
    rnd = _load_round(data)
    index = require_int(data, "question_index", 0, len(rnd.questions) - 1)
    with _store().lock(rnd.id):
        _require_begun(rnd)
        if index != rnd.index:
            raise ApiError(409, "question_over", "That question has already ended.")
        q = rnd.current
        try:
            stale = not logic.apply_timeout(q, _now(), rnd.limit)
        except logic.TimeoutTooEarly as exc:
            raise ApiError(409, "too_early", "There's still time left on this question.",
                           {"remaining_ms": _ms(exc.remaining_s)}) from None
        return jsonify({"result": q.status, "stale": stale, **_ended_json(rnd, q)})


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
        q = rnd.current
        return jsonify({"paused": False, "elapsed_ms": _ms(q.elapsed(_now())) if rnd.begun else 0})


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
            raise ApiError(409, "question_active", "Answer or run out the clock before moving on.") from None
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
        "questions": [
            {
                "number": i + 1,
                **_answer_json(q),
                "status": q.status,
                "elapsed_ms": _ms(q.elapsed(now)),
                "wrong": q.wrong,
                "wrong_picks": [_entry(q.options[w]).capital for w in q.wrong_picks],
                "points": q.points,
                "max_points": CONFIG.start_points,
            }
            for i, q in enumerate(rnd.questions)
        ],
    })
