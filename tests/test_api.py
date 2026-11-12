"""End-to-end Flag Reveal API flow through the Flask test client."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.shared.countries import get_index

API = "/games/flag-reveal/api"


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def app(tmp_path: Path, clock: FakeClock) -> Iterator[Flask]:
    yield create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.sqlite3"), "FLAG_REVEAL_SEED": 1234,
                      "FLAG_REVEAL_CLOCK": clock})


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def post(client: FlaskClient, path: str, body: Any) -> tuple[int, dict[str, Any]]:
    resp = client.post(f"{API}/{path}", json=body)
    return resp.status_code, resp.get_json()


def current(app: Flask, round_id: str):
    return app.extensions["flag_reveal.rounds"].get(round_id).current


def answer_option(app: Flask, round_id: str) -> int:
    q = current(app, round_id)
    return q.options.index(q.code)


def wrong_option(app: Flask, round_id: str) -> int:
    q = current(app, round_id)
    return next(i for i, c in enumerate(q.options) if c != q.code and i not in q.eliminated)


def pick(client: FlaskClient, rid: str, option: int, stage: int = 1, remaining_ms: int = 0) -> tuple[int, dict]:
    return post(client, "pick", {"round_id": rid, "option_id": option, "stage": stage, "remaining_ms": remaining_ms})


def start(client: FlaskClient, **settings: Any) -> dict[str, Any]:
    status, body = post(client, "start", {"settings": settings})
    assert status == 200, body
    return body


def test_start_offers_options_without_leaking_answer(app: Flask, client: FlaskClient) -> None:
    body = start(client, questions=3)
    q = body["question"]
    rnd_q = current(app, body["round_id"])
    assert set(q) == {"index", "number", "total", "flag_url", "reveal", "stage", "options"}
    assert ".svg" not in q["flag_url"] and not q["flag_url"].endswith(f"/{rnd_q.code}")
    assert [o["id"] for o in q["options"]] == list(range(8))
    assert set(q["options"][0]) == {"id", "name"}
    assert get_index().by_code[rnd_q.code].name in [o["name"] for o in q["options"]]
    assert "correct" not in str(q) and "code" not in str(q)
    assert body["max_score"] == 3 * (15 + 5) and body["best"] is None and body["wrong_penalty"] == 5


def test_flag_served_by_token(client: FlaskClient) -> None:
    body = start(client)
    resp = client.get(body["question"]["flag_url"])
    assert resp.status_code == 200
    assert resp.mimetype == "image/svg+xml"
    assert b"flag-icons" not in resp.data
    assert client.get("/games/flag-reveal/flag/not-a-token").status_code == 404


def test_full_round_flow_with_every_outcome(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    body = start(client, questions=4, stage_seconds=3)
    rid = body["round_id"]

    # Q1: wrong pick (-5, option gone), then correct at stage 1 with 2.2 s left -> 10 + 3
    bad = wrong_option(app, rid)
    status, r = pick(client, rid, bad)
    assert status == 200 and r["correct"] is False and r["penalty"] == 5 and r["wrong"] == 1
    assert r["guessed"] == get_index().by_code[current(app, rid).options[bad]].name and "country" not in r
    status, r = pick(client, rid, bad)
    assert status == 409 and r["error"]["code"] == "option_unavailable"
    clock.t += 0.8
    _, r = pick(client, rid, answer_option(app, rid), remaining_ms=2200)
    assert r["correct"] and r["stage_points"] == 10 and r["speed_bonus"] == 3 and r["points"] == 13
    assert r["score"] == 13 and r["answer_option"] == answer_option(app, rid)
    status, r = pick(client, rid, answer_option(app, rid))
    assert status == 409 and r["error"]["code"] == "question_over"

    # Q2: correct at stage 2. The client claims a full stage left, but the server
    # clock says 6 - 4.5 + 0.75 = 2.25 s -> speed bonus capped at 3 (rounded up).
    _, r = post(client, "next", {"round_id": rid, "from_index": 0})
    assert r["question"]["number"] == 2 and len(r["question"]["options"]) == 8
    _, r = post(client, "next", {"round_id": rid, "from_index": 0})   # double-click: no skip
    assert r["question"]["number"] == 2
    _, r = post(client, "timeout", {"round_id": rid, "stage": 1})
    assert r["stage"] == 2 and not r["missed"] and "country" not in r
    _, r = post(client, "timeout", {"round_id": rid, "stage": 1})      # duplicate
    assert r["stale"] and r["stage"] == 2
    clock.t += 4.5
    _, r = pick(client, rid, answer_option(app, rid), stage=1, remaining_ms=3000)
    assert r["stage"] == 2 and r["stage_points"] == 10, "server ignores a stage earlier than it has seen"
    assert r["speed_bonus"] == 3 and r["points"] == 13

    # Q3: correct at stage 3, out of time on the server -> 5 + 0
    post(client, "next", {"round_id": rid, "from_index": 1})
    post(client, "timeout", {"round_id": rid, "stage": 1})
    post(client, "timeout", {"round_id": rid, "stage": 2})
    clock.t += 30
    _, r = pick(client, rid, answer_option(app, rid), stage=3, remaining_ms=3000)
    assert r["points"] == 5 and r["speed_bonus"] == 0

    status, r = post(client, "summary", {"round_id": rid})
    assert status == 409

    # Q4: a wrong pick, then time out to 0; the answer's option is revealed
    post(client, "next", {"round_id": rid, "from_index": 2})
    name = get_index().by_code[current(app, rid).code].name
    pick(client, rid, wrong_option(app, rid))
    for s in (1, 2):
        post(client, "timeout", {"round_id": rid, "stage": s})
    _, r = post(client, "timeout", {"round_id": rid, "stage": 3})
    assert r["missed"] and r["country"]["name"] == name and r["is_last"]
    assert r["answer_option"] == answer_option(app, rid)

    _, r = post(client, "next", {"round_id": rid, "from_index": 3})
    assert r == {"finished": True, "score": 31}

    _, s = post(client, "summary", {"round_id": rid})
    assert s["score"] == 31 and s["max_score"] == 4 * (15 + 3) and s["is_new_best"] and s["best"] == 31
    assert [q["points"] for q in s["questions"]] == [13, 13, 5, 0]
    assert [q["wrong"] for q in s["questions"]] == [1, 0, 0, 1]
    assert [q["label"] for q in s["questions"]] == [
        "Stage 1 · 1 wrong · +3 speed", "Stage 2 · +3 speed", "Stage 3", "Missed · 1 wrong"]

    _, s2 = post(client, "summary", {"round_id": rid})
    assert s2["is_new_best"] and s2["best"] == 31

    _, b = post(client, "best", {"settings": {"questions": 4, "stage_seconds": 3}})
    assert b["best"] == 31 and b["max_score"] == 72
    _, b = post(client, "best", {"settings": {"questions": 4, "stage_seconds": 4}})
    assert b["best"] is None


def test_lower_score_does_not_replace_best(app: Flask, client: FlaskClient) -> None:
    for expected_best, solve in ((15, True), (15, False)):
        rid = start(client, questions=1)["round_id"]
        if solve:
            pick(client, rid, answer_option(app, rid))
        else:
            for s in (1, 2, 3):
                post(client, "timeout", {"round_id": rid, "stage": s})
        _, s = post(client, "summary", {"round_id": rid})
        assert s["best"] == expected_best
        assert s["is_new_best"] is solve


def test_region_restriction(app: Flask, client: FlaskClient) -> None:
    rid = start(client, questions=14, region="oceania")["round_id"]
    rnd = app.extensions["flag_reveal.rounds"].get(rid)
    assert {get_index().by_code[q.code].region for q in rnd.questions} == {"Oceania"}
    assert {get_index().by_code[c].region for q in rnd.questions for c in q.options} == {"Oceania"}
    status, r = post(client, "start", {"settings": {"questions": 15, "region": "oceania"}})
    assert status == 400 and "14" in r["error"]["message"]


def test_validation_errors_are_clear_json(client: FlaskClient) -> None:
    status, r = post(client, "start", {"settings": {"questions": 99, "stage_seconds": 1}})
    assert status == 400 and r["error"]["code"] == "invalid_settings"
    assert set(r["error"]["details"]["fields"]) == {"questions", "stage_seconds"}

    resp = client.post(f"{API}/start", data="nope", content_type="text/plain")
    assert resp.status_code == 400 and resp.get_json()["error"]["code"] == "bad_request"

    status, r = pick(client, "missing", 0)
    assert status == 404 and r["error"]["code"] == "round_not_found"

    rid = start(client)["round_id"]
    base = {"round_id": rid, "option_id": 0, "stage": 1, "remaining_ms": 0}
    for bad in ({"stage": 0}, {"stage": 4}, {"stage": "1"}, {"stage": None}, {"option_id": 8},
                {"option_id": -1}, {"option_id": "0"}, {"remaining_ms": -1}, {"remaining_ms": 5001},
                {"remaining_ms": None}):
        status, r = post(client, "pick", {**base, **bad})
        assert status == 400, bad
    status, r = post(client, "next", {"round_id": rid, "from_index": 0})
    assert status == 409 and r["error"]["code"] == "question_active"
    status, _ = post(client, "next", {"round_id": rid, "from_index": 9})
    assert status == 400


def test_typing_endpoints_removed(client: FlaskClient) -> None:
    assert client.post(f"{API}/guess", json={}).status_code in (404, 405)
    assert client.get(f"{API}/countries").status_code in (404, 405)
