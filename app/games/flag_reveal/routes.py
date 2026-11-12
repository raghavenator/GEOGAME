"""HTTP layer for Flag Reveal. All rules live in logic.py; this file only
parses input, guards state with a per-round lock, and shapes JSON."""
from __future__ import annotations

import random
import secrets
from pathlib import Path
from typing import Any

from flask import Response, abort, current_app, jsonify, render_template, url_for
from flask.blueprints import BlueprintSetupState
from flask.typing import ResponseReturnValue

from app.shared import highscores
from app.shared.api import ApiError, json_body, register_error_handlers, require_int, require_str
from app.shared.countries import get_index
from app.shared.round_store import RoundStore
from app.shared.settings import SettingsError, defaults, schema_to_json, settings_key, validate_settings

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


def _store() -> RoundStore[Round]:
    return current_app.extensions["flag_reveal.rounds"]


def _rng() -> random.Random:
    return current_app.extensions["flag_reveal.rng"]


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


def _question_json(rnd: Round) -> dict[str, Any]:
    q = rnd.current
    return {
        "index": rnd.index,
        "number": rnd.index + 1,
        "total": len(rnd.questions),
        "flag_url": url_for("flag_reveal.flag", token=q.token),
        "reveal": q.reveal.to_json(),
        "stage": q.stage,
    }


def _country_json(code: str) -> dict[str, str]:
    c = get_index().by_code[code]
    return {"name": c.name, "region": c.region}


def _ended_json(rnd: Round) -> dict[str, Any]:
    q = rnd.current
    return {"country": _country_json(q.code), "score": rnd.score, "is_last": rnd.is_last}


# --------------------------------------------------------------------------- pages
@bp.get("/")
def play() -> str:
    from app.games.registry import get_game

    return render_template(
        "flag_reveal/play.html",
        game=get_game(GAME_ID),
        schema_json=schema_to_json(SETTINGS_SCHEMA),
        settings_defaults=defaults(SETTINGS_SCHEMA),
        config=CONFIG,
    )


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
@bp.get("/api/countries")
def countries() -> ResponseReturnValue:
    resp = jsonify(get_index().name_list())
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.post("/api/best")
def best() -> ResponseReturnValue:
    settings = _clean_settings(json_body().get("settings"))
    n = settings["questions"]
    return jsonify({
        "best": highscores.get_best(GAME_ID, settings_key(settings)),
        "max_score": n * max(CONFIG.stage_points),
        "settings": settings,
    })


@bp.post("/api/start")
def start() -> ResponseReturnValue:
    settings = _clean_settings(json_body().get("settings"))
    round_id = secrets.token_urlsafe(12)
    try:
        rnd = logic.new_round(round_id, settings, get_index(), _rng(), lambda: secrets.token_urlsafe(16))
    except ValueError as exc:
        raise ApiError(400, "invalid_settings", str(exc), {"fields": {"questions": str(exc)}}) from exc
    _store().put(round_id, rnd, aliases=[q.token for q in rnd.questions])
    return jsonify({
        "round_id": round_id,
        "settings": settings,
        "stage_seconds": settings["stage_seconds"],
        "stage_points": list(CONFIG.stage_points),
        "feedback_ms": CONFIG.feedback_ms,
        "missed_feedback_ms": CONFIG.missed_feedback_ms,
        "max_score": rnd.max_score,
        "best": highscores.get_best(GAME_ID, settings_key(settings)),
        "question": _question_json(rnd),
    })


@bp.post("/api/guess")
def guess() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    text = require_str(data, "guess", max_len=80)
    stage = require_int(data, "stage", 1, CONFIG.stages)
    with _store().lock(rnd.id):
        matched = get_index().match(text)
        try:
            outcome = logic.apply_guess(rnd.current, matched.code if matched else None, stage)
        except logic.QuestionOver:
            raise ApiError(409, "question_over", "This question has already ended.") from None
        if outcome.correct:
            body = {"correct": True, "points": outcome.points, "stage": outcome.stage, **_ended_json(rnd)}
            if matched and matched.code != rnd.current.code:
                body["lookalike_of"] = matched.name
            return jsonify(body)
        return jsonify({
            "correct": False,
            "stage": outcome.stage,
            "recognised": outcome.recognised,
            "guessed": matched.name if matched else None,
            "message": "Not quite" if outcome.recognised else "Not a country we recognise",
        })


@bp.post("/api/timeout")
def timeout() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    stage = require_int(data, "stage", 1, CONFIG.stages)
    with _store().lock(rnd.id):
        q = rnd.current
        outcome = logic.apply_timeout(q, stage)
        body: dict[str, Any] = {"stage": outcome.stage, "missed": outcome.missed, "stale": outcome.stale,
                                "status": q.status}
        if q.status != "active":
            body.update(_ended_json(rnd))
        return jsonify(body)


@bp.post("/api/next")
def next_question() -> ResponseReturnValue:
    data = json_body()
    rnd = _load_round(data)
    from_index = require_int(data, "from_index", 0, len(rnd.questions) - 1)
    with _store().lock(rnd.id):
        try:
            logic.advance(rnd, from_index)
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
        key = settings_key(rnd.settings)
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
                "label": logic.stage_label(q),
            }
            for i, q in enumerate(rnd.questions)
        ],
    })
