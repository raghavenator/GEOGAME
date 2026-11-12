"""HTTP layer for Flag Reveal. All rules live in logic.py; this file only
parses input, guards state with a per-round lock, and shapes JSON."""
from __future__ import annotations

import random
import secrets
import time
from pathlib import Path
from typing import Any

from flask import Response, abort, current_app, jsonify, render_template, url_for
from flask.blueprints import BlueprintSetupState
from flask.typing import ResponseReturnValue

from app.shared import highscores
from app.shared.api import ApiError, json_body, register_error_handlers, require_int, require_str
from app.shared.countries import get_index
from app.shared.round_store import RoundStore
from app.shared.settings import SettingsError, defaults, settings_key, validate_settings

from . import bp
from . import logic
from .logic import CONFIG, SETTINGS_SCHEMA, Question, Round

GAME_ID = "flag-reveal"
FLAG_DIR = Path(__file__).resolve().parents[2] / "shared" / "static" / "flags"

register_error_handlers(bp)


@bp.record_once
def _setup(state: BlueprintSetupState) -> None:
    app = state.app
    app.extensions["flag_reveal.rounds"] = RoundStore[Round]()
    seed = app.config.get("FLAG_REVEAL_SEED")
    app.extensions["flag_reveal.rng"] = random.Random(seed) if seed is not None else random.SystemRandom()
    app.extensions["flag_reveal.clock"] = app.config.get("FLAG_REVEAL_CLOCK", time.monotonic)


def _store() -> RoundStore[Round]:
    return current_app.extensions["flag_reveal.rounds"]


def _rng() -> random.Random:
    return current_app.extensions["flag_reveal.rng"]


def _now() -> float:
    return current_app.extensions["flag_reveal.clock"]()


def _score_key(settings: dict[str, Any]) -> str:
    # "mode=choice" keeps multiple-choice bests apart from the old typed-answer scores.
    return settings_key({**settings, "mode": "choice"})


# --------------------------------------------------------------------------- helpers
def _clean_settings(raw: Any) -> dict[str, Any]:
    try:
        return validate_settings(SETTINGS_SCHEMA, raw)
    except SettingsError as exc:
        raise ApiError(400, "invalid_settings", "Some settings are invalid.", {"fields": exc.errors}) from exc


def _load_round(data: dict[str, Any]) -> Round:
    round_id = require_str(data, "round_id", max_len=64)
    rnd = _store().get(round_id)
    if rnd is None:
        raise ApiError(404, "round_not_found", "That round has expired or doesn't exist. Start a new one.")
    return rnd


def _require_begun(rnd: Round) -> None:
    if not rnd.begun:
        raise ApiError(409, "not_started", "This round hasn't started yet.")
    # A pick or timeout while paused means the player is back: resume the clock first.
    logic.resume(rnd, _now())


def _question_json(rnd: Round) -> dict[str, Any]:
    q = rnd.current
    return {
        "index": rnd.index,
        "number": rnd.index + 1,
        "total": len(rnd.questions),
        "flag_url": url_for("flag_reveal.flag", token=q.token),
        "reveal": q.reveal.to_json(),
        "stage": q.stage,
        "options": [{"id": i, "name": get_index().by_code[code].name}
                    for i, code in enumerate(q.options) if i not in q.eliminated],
    }


def _country_json(code: str) -> dict[str, str]:
    c = get_index().by_code[code]
    return {"name": c.name, "region": c.region}


def _ended_json(rnd: Round) -> dict[str, Any]:
    q = rnd.current
    return {"country": _country_json(q.code), "answer_option": q.options.index(q.code),
            "score": rnd.score, "is_last": rnd.is_last}


# --------------------------------------------------------------------------- pages
def embed_context() -> dict[str, Any]:
    """What the embed macros (templates/flag_reveal/_embed.html) need, for the page and the feed."""
    from app.games.registry import get_game

    return {"game": get_game(GAME_ID), "config": CONFIG, "settings_defaults": defaults(SETTINGS_SCHEMA)}


@bp.get("/")
def play() -> str:
    ctx = embed_context()
    return render_template("flag_reveal/play.html", game=ctx["game"], ctx=ctx)


@bp.get("/flag/<token>")
def flag(token: str) -> Response:
    hit = _store().resolve_alias(token)
    if hit is None:
        abort(404)
    _, rnd = hit
    q: Question = next(q for q in rnd.questions if q.token == token)
    svg = (FLAG_DIR / f"{q.code}.svg").read_bytes()
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "private, max-age=7200"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return resp


# --------------------------------------------------------------------------- API
@bp.post("/api/best")
def best() -> ResponseReturnValue:
    settings = _clean_settings(json_body().get("settings"))
    return jsonify({
        "best": highscores.get_best(GAME_ID, _score_key(settings)),
        "max_score": logic.max_score_for(settings["questions"], settings["stage_seconds"]),
        "settings": settings,
    })


@bp.post("/api/start")
def start() -> ResponseReturnValue:
    settings = _clean_settings(json_body().get("settings"))
    round_id = secrets.token_urlsafe(12)
    try:
        rnd = logic.new_round(round_id, settings, get_index(), _rng(), lambda: secrets.token_urlsafe(16), _now())
    except ValueError as exc:
        raise ApiError(400, "invalid_settings", str(exc), {"fields": {"questions": str(exc)}}) from exc
    _store().put(round_id, rnd, aliases=[q.token for q in rnd.questions])
    return jsonify({
        "round_id": round_id,
        "settings": settings,
        "stage_seconds": settings["stage_seconds"],
        "stage_points": list(CONFIG.stage_points),
        "wrong_penalty": CONFIG.wrong_penalty,
        "feedback_ms": CONFIG.feedback_ms,
        "missed_feedback_ms": CONFIG.missed_feedback_ms,
        "max_score": rnd.max_score,
        "best": highscores.get_best(GAME_ID, _score_key(settings)),
        "question": _question_json(rnd),
    })


@bp.post("/api/pick")
def pick() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    stage = require_int(data, "stage", 1, CONFIG.stages)
    option_id = require_int(data, "option_id", 0, CONFIG.options_shown - 1)
    stage_seconds = rnd.settings["stage_seconds"]
    remaining_ms = require_int(data, "remaining_ms", 0, stage_seconds * 1000)
    with _store().lock(rnd.id):
        _require_begun(rnd)
        q = rnd.current
        try:
            out = logic.apply_pick(q, option_id, stage, remaining_ms, stage_seconds, _now())
        except logic.QuestionOver:
            raise ApiError(409, "question_over", "This question has already ended.") from None
        except logic.OptionUnavailable:
            raise ApiError(409, "option_unavailable", "That option isn't available any more.") from None
        if out.correct:
            return jsonify({"correct": True, "option_id": option_id, "points": out.points,
                            "stage_points": out.stage_points, "speed_bonus": out.speed_bonus,
                            "stage": out.stage, "wrong": q.wrong_guesses, **_ended_json(rnd)})
        return jsonify({
            "correct": False,
            "option_id": option_id,
            "stage": out.stage,
            "guessed": get_index().by_code[out.code].name,
            "penalty": CONFIG.wrong_penalty,
            "wrong": q.wrong_guesses,
        })


@bp.post("/api/timeout")
def timeout() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    stage = require_int(data, "stage", 1, CONFIG.stages)
    with _store().lock(rnd.id):
        _require_begun(rnd)
        q = rnd.current
        outcome = logic.apply_timeout(q, stage)
        body: dict[str, Any] = {"stage": outcome.stage, "missed": outcome.missed, "stale": outcome.stale,
                                "status": q.status}
        if q.status != "active":
            body.update(_ended_json(rnd))
        return jsonify(body)


@bp.post("/api/begin")
def begin() -> ResponseReturnValue:
    """The player tapped play: start the scoring clock for the prepared round."""
    rnd = _load_round(json_body())
    with _store().lock(rnd.id):
        started = logic.begin(rnd, _now())
        return jsonify({"started": started, "question": _question_json(rnd), "score": rnd.score})


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
        return jsonify({"paused": False})


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
        key = _score_key(rnd.settings)
        if not rnd.recorded:
            result = highscores.record_score(GAME_ID, key, rnd.score, rnd.max_score)
            rnd.recorded = True
            rnd.extra["record"] = {"best": result.best, "is_new_best": result.is_new_best,
                                   "previous_best": result.previous_best}
        record = rnd.extra["record"]
    return jsonify({
        "score": rnd.score,
        "max_score": rnd.max_score,
        "settings": rnd.settings,
        **record,
        "questions": [
            {
                "number": i + 1,
                "flag_url": url_for("flag_reveal.flag", token=q.token),
                "country": _country_json(q.code),
                "points": q.points,
                "status": q.status,
                "solved_stage": q.solved_stage,
                "wrong": q.wrong_guesses,
                "speed_bonus": q.speed_bonus,
                "label": logic.stage_label(q),
            }
            for i, q in enumerate(rnd.questions)
        ],
    })
