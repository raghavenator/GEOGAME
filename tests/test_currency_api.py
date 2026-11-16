"""Currency Quest HTTP API: full flows, the play clock, input validation and no answer leaks."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.games.currency_quest import logic as L
from app.games.currency_quest.currency_data import load_currency_data

API = "/games/currency-quest/api"
DATA = load_currency_data()


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def app(tmp_path: Path, clock: FakeClock) -> Iterator[Flask]:
    yield create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.sqlite3"),
                      "CURRENCY_QUEST_SEED": 7, "CURRENCY_QUEST_CLOCK": clock})


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def post(client: FlaskClient, path: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    resp = client.post(f"{API}/{path}", json=body)
    assert resp.status_code == status, resp.get_json()
    return resp.get_json()


def server_q(app: Flask, rid: str) -> L.Question:
    return app.extensions["currency_quest.rounds"].get(rid).current


def answer_of(app: Flask, rid: str) -> int:
    return server_q(app, rid).answer_option


def wrong_of(app: Flask, rid: str, n: int = 0) -> int:
    q = server_q(app, rid)
    return [i for i in q.live_options() if i != q.answer_option][n]


def start(client: FlaskClient, **settings: Any) -> dict[str, Any]:
    return post(client, "start", {"settings": settings})


def keys_of(body: Any) -> set[str]:
    if isinstance(body, dict):
        return set(body) | set().union(*(keys_of(v) for v in body.values()))
    if isinstance(body, list):
        return set().union(*(keys_of(v) for v in body)) if body else set()
    return set()


def assert_no_answer(body: Any) -> None:
    """Nothing in a pre-answer response says which option is right."""
    assert not keys_of(body) & {"answer", "answer_option", "correct", "is_correct", "also_used_in", "primary",
                                "currencies"}, keys_of(body)


# --------------------------------------------------------------------------- start / begin
def test_start_says_nothing_about_the_questions(client: FlaskClient) -> None:
    r = start(client)
    assert set(r) == {"round_id", "settings", "total", "max_score", "best", "scoring"}
    assert r["total"] == 5 and r["max_score"] == 100 and r["best"] is None
    text = json.dumps(r)
    for c in DATA.countries:
        assert f'"{c.name}"' not in text


def test_begin_starts_the_clock_and_shows_the_question(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    r = start(client, options="6")
    clock.t += 3600                        # a prepared round can sit idle: the clock starts at begin
    b = post(client, "begin", {"round_id": r["round_id"]})
    q = b["question"]
    assert b["started"] and q["number"] == 1 and q["elapsed_ms"] == 0 and q["potential"] == 20
    assert set(q["country"]) == {"name", "flag_url", "shape"} and q["country"]["flag_url"].endswith(".svg")
    assert len(q["options"]) == 6
    for o in q["options"]:
        assert set(o) == {"id", "symbol", "code"} and o["code"]      # no names: they would give the answer away
    assert len({o["code"] for o in q["options"]}) == 6
    text = json.dumps(b, ensure_ascii=False)
    for cur in DATA.currencies.values():
        assert cur.name not in text, cur.name
    assert_no_answer(b)
    again = post(client, "begin", {"round_id": r["round_id"]})
    assert not again["started"]            # idempotent


def test_names_are_revealed_only_when_safe(app: Flask, client: FlaskClient) -> None:
    rid = start(client, questions=1)["round_id"]
    post(client, "begin", {"round_id": rid})
    q = server_q(app, rid)
    w = wrong_of(app, rid)
    out = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": w})
    assert out["name"] == DATA.currencies[q.options[w]].name           # a wrong option's name is safe to show
    assert "option_names" not in out
    out = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": answer_of(app, rid)})
    assert out["option_names"] == {str(i): DATA.currencies[c].name for i, c in enumerate(q.options)}


def test_actions_before_begin_are_refused(client: FlaskClient) -> None:
    rid = start(client)["round_id"]
    post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": 0}, 409)
    post(client, "timeout", {"round_id": rid, "question_index": 0}, 409)
    post(client, "next", {"round_id": rid, "from_index": 0}, 409)


# --------------------------------------------------------------------------- a full round
def test_full_round_with_wrong_correct_and_timeout(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    r = start(client, questions=3)
    rid = r["round_id"]
    post(client, "begin", {"round_id": rid})

    # Q1: a wrong pick, then the right one 5 s in.
    clock.t += 1
    w = wrong_of(app, rid)
    out = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": w})
    assert out["result"] == "wrong" and out["penalty"] == 3 and out["potential"] == 16
    assert w not in out["options"] and len(out["options"]) == 7
    assert_no_answer(out)
    post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": w}, 409)   # gone for good
    clock.t += 4
    ans = answer_of(app, rid)
    out = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": ans})
    assert out["result"] == "correct" and out["points"] == 12 and out["answer"]["option_id"] == ans
    cur = out["answer"]["currency"]
    assert set(cur) == {"code", "name", "symbol"}
    assert isinstance(out["answer"]["also_used_in"], list) and isinstance(out["answer"]["also_more"], int)
    post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": ans}, 409)   # no double answers
    post(client, "summary", {"round_id": rid}, 409)

    # Q2: time runs out.
    n = post(client, "next", {"round_id": rid, "from_index": 0})
    assert n["question"]["number"] == 2
    assert_no_answer(n)
    post(client, "next", {"round_id": rid, "from_index": 1}, 409)          # still active
    clock.t += 10
    early = post(client, "timeout", {"round_id": rid, "question_index": 1}, 409)
    assert early["error"]["code"] == "too_early"
    clock.t += 10
    t = post(client, "timeout", {"round_id": rid, "question_index": 1})
    assert t["status"] == "timeout" and t["points"] == 0 and "currency" in t["answer"]
    assert post(client, "timeout", {"round_id": rid, "question_index": 1})["stale"]

    # Q3: an instant right answer.
    post(client, "next", {"round_id": rid, "from_index": 1})
    out = post(client, "pick", {"round_id": rid, "question_index": 2, "option_id": answer_of(app, rid)})
    assert out["points"] == 20 and out["is_last"]
    assert post(client, "next", {"round_id": rid, "from_index": 2})["finished"]

    s = post(client, "summary", {"round_id": rid})
    assert s["score"] == 32 and s["max_score"] == 60 and s["is_new_best"]
    rows = s["questions"]
    assert [q["points"] for q in rows] == [12, 0, 20]
    assert rows[0]["wrong"] == 1 and len(rows[0]["wrong_picks"]) == 1 and rows[1]["status"] == "timeout"
    for row in rows:
        assert {"country", "currency", "elapsed_ms", "wrong", "points", "max_points", "flag_url"} <= set(row)
    assert post(client, "summary", {"round_id": rid})["best"] == 32       # recorded once
    assert post(client, "best", {"settings": {"questions": 3}})["best"] == 32


def test_paused_time_is_excluded(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = start(client, questions=1)["round_id"]
    post(client, "begin", {"round_id": rid})
    clock.t += 2
    post(client, "pause", {"round_id": rid})
    clock.t += 120
    assert post(client, "resume", {"round_id": rid})["elapsed_ms"] == 2000
    clock.t += 3
    out = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": answer_of(app, rid)})
    assert out["points"] == 15 and out["elapsed_ms"] == 5000


def test_picking_while_paused_resumes(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = start(client, questions=1)["round_id"]
    post(client, "begin", {"round_id": rid})
    post(client, "pause", {"round_id": rid})
    clock.t += 50
    out = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": answer_of(app, rid)})
    assert out["points"] == 20


def test_expired_round(client: FlaskClient) -> None:
    out = post(client, "begin", {"round_id": "nope"}, 404)
    assert out["error"]["code"] == "round_not_found"


def test_euro_question_shows_the_euro_once(app: Flask, client: FlaskClient) -> None:
    # Find a round whose first question is a euro country and check what the client sees.
    for _ in range(400):
        rid = start(client, questions=1, difficulty="hard")["round_id"]
        if server_q(app, rid).code == "EUR":
            break
    else:
        pytest.fail("no euro question drawn")
    q = post(client, "begin", {"round_id": rid})["question"]
    codes = [o["code"] for o in q["options"]]
    assert codes.count("EUR") == 1
    country = next(c for c in DATA.countries if c.name == q["country"]["name"])
    assert not {o["code"] for o in q["options"]} - {"EUR"} & set(country.currencies)
    out = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": answer_of(app, rid)})
    assert out["answer"]["currency"]["name"] == "Euro"
    assert len(out["answer"]["also_used_in"]) == 6 and out["answer"]["also_more"] > 10
    assert country.name not in out["answer"]["also_used_in"]


# --------------------------------------------------------------------------- validation
@pytest.mark.parametrize("settings", [{"questions": 0}, {"questions": 31}, {"seconds": 5}, {"options": "7"},
                                      {"difficulty": "x"}, {"codes": "on"}, {"locator": "maybe"}, {"bogus": 1}])
def test_bad_settings(client: FlaskClient, settings: dict[str, Any]) -> None:
    out = post(client, "start", {"settings": settings}, 400)
    assert out["error"]["code"] == "invalid_settings"


def test_too_many_questions_for_region(client: FlaskClient) -> None:
    out = post(client, "start", {"settings": {"region": "oceania", "questions": 30}}, 400)
    assert "different currencies" in out["error"]["message"]


@pytest.mark.parametrize("body", [{}, {"round_id": 5}, {"round_id": ""}, {"round_id": "x" * 65}])
def test_bad_round_ids(client: FlaskClient, body: dict[str, Any]) -> None:
    post(client, "begin", body, 400)


def test_bad_option_and_index(client: FlaskClient) -> None:
    rid = start(client, options="4")["round_id"]
    post(client, "begin", {"round_id": rid})
    for oid in (-1, 8, "1", True, None):
        post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": oid}, 400)
    post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": 6}, 409)   # not in a 4-option question
    post(client, "pick", {"round_id": rid, "question_index": 3, "option_id": 0}, 409)   # not the current question
    post(client, "pick", {"round_id": rid, "question_index": 9, "option_id": 0}, 400)
    assert client.post(f"{API}/start", data="nope", content_type="application/json").status_code == 400


# --------------------------------------------------------------------------- pages
def test_pages(client: FlaskClient) -> None:
    html = client.get("/games/currency-quest/").get_data(as_text=True)
    assert 'data-game-root="currency-quest"' in html and 'data-mode="page"' in html
    assert "currency_quest.js" in html and "Currency Quest" in html
    assert client.get("/games/currency-quest").status_code in (301, 302, 308)
    hub = client.get("/games").get_data(as_text=True)
    assert 'href="/games/currency-quest/"' in hub
