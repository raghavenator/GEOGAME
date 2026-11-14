"""Capital Quest HTTP API: full flows, the play clock, input validation and no answer leaks."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.games.capital_quest import logic as L
from app.games.capital_quest.capital_data import capitals_by_code

API = "/games/capital-quest/api"


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
                      "CAPITAL_QUEST_SEED": 11, "CAPITAL_QUEST_CLOCK": clock})


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def post(client: FlaskClient, path: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    resp = client.post(f"{API}/{path}", json=body)
    assert resp.status_code == status, resp.get_json()
    return resp.get_json()


def server_q(app: Flask, rid: str) -> L.Question:
    return app.extensions["capital_quest.rounds"].get(rid).current


def answer_of(app: Flask, rid: str) -> int:
    return server_q(app, rid).answer_option


def wrong_of(app: Flask, rid: str, n: int = 0) -> int:
    q = server_q(app, rid)
    return [i for i in q.live_options() if i != q.answer_option][n]


def assert_no_answer(app: Flask, rid: str, payload: Any) -> None:
    """Nothing in ``payload`` identifies the current question's answer."""
    q = server_q(app, rid)
    e = capitals_by_code()[q.code]
    holder = payload.get("question", payload)
    options = holder.get("options", [])
    # The capital is listed among the options (that's the game) but never flagged.
    for o in options:
        assert isinstance(o, int) or set(o) == {"id", "name"}
    rest = {**payload, "question": {k: v for k, v in holder.items() if k != "options"}}
    rest.pop("options", None)
    text = json.dumps(rest, ensure_ascii=False)
    for secret in (e.name, e.capital, f'"{e.code}"', e.cca3, '"answer"', "correct"):
        assert secret not in text, secret


def begin(client: FlaskClient, **settings: Any) -> dict[str, Any]:
    r = post(client, "start", {"settings": {"questions": 3, **settings}})
    b = post(client, "begin", {"round_id": r["round_id"]})
    return {**r, **b}


def test_start_reveals_nothing(app: Flask, client: FlaskClient) -> None:
    r = post(client, "start", {"settings": {}})
    assert set(r) == {"round_id", "settings", "total", "max_score", "best", "scoring"}
    assert r["total"] == 5 and r["max_score"] == 100 and r["best"] is None
    assert r["scoring"] == {"start_points": 20, "wrong_penalty": 3, "min_correct_points": 2, "time_limit_ms": 20000}
    assert r["settings"] == {"questions": 5, "seconds": 20, "options": "8", "view": "flat", "framing": "region",
                             "region": "all"}
    q = server_q(app, r["round_id"])
    assert q.started_at is None  # the clock starts at the tap
    text = json.dumps({k: v for k, v in r.items() if k != "settings"})
    assert '"shape"' not in text and '"options"' not in text and '"question"' not in text
    for e in (capitals_by_code()[q.code],):
        assert e.capital not in text and e.name not in text


def test_actions_before_begin_are_rejected(client: FlaskClient) -> None:
    rid = post(client, "start", {"settings": {}})["round_id"]
    for path, body in (("pick", {"question_index": 0, "option_id": 0}), ("timeout", {"question_index": 0}),
                       ("next", {"from_index": 0})):
        assert post(client, path, {"round_id": rid, **body}, status=409)["error"]["code"] == "not_started"


def test_begin_payload_has_no_answer(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    r = post(client, "start", {"settings": {"options": "6"}})
    clock.t += 900                                   # sat preloaded in the feed for 15 minutes
    b = post(client, "begin", {"round_id": r["round_id"]})
    assert b["started"] and not b["finished"]
    q = b["question"]
    assert set(q) == {"index", "number", "total", "shape", "options", "wrong", "time_limit_ms", "elapsed_ms",
                      "potential", "status"}
    assert q["elapsed_ms"] == 0 and q["potential"] == 20 and len(q["options"]) == 6
    assert [o["id"] for o in q["options"]] == list(range(6))
    assert_no_answer(app, r["round_id"], b)
    assert post(client, "begin", {"round_id": r["round_id"]})["started"] is False   # idempotent


def test_full_round(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    r = begin(client)
    rid = r["round_id"]

    # Q1: a wrong pick at 2 s, then correct at 5 s -> 15 - 3 = 12.
    clock.t += 2
    w = wrong_of(app, rid)
    out = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": w})
    assert out["result"] == "wrong" and out["penalty"] == 3 and out["wrong"] == 1
    assert out["potential"] == 15 and out["elapsed_ms"] == 2000 and out["score"] == 0
    assert w not in out["options"] and len(out["options"]) == 7
    assert_no_answer(app, rid, out)
    again = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": w}, status=409)
    assert again["error"]["code"] == "option_unavailable"
    clock.t += 3
    c = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": answer_of(app, rid)})
    assert c["result"] == "correct" and c["points"] == 12 and c["score"] == 12 and c["elapsed_ms"] == 5000
    entry = capitals_by_code()[c["answer"]["code"]]
    assert c["answer"]["capital"] == entry.capital and c["answer"]["country"] == entry.name
    assert c["answer"]["flag_url"] == f"/static/flags/{entry.code}.svg"
    assert client.get(c["answer"]["flag_url"]).status_code == 200
    # A pick on a finished question is rejected.
    assert post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": 0},
                status=409)["error"]["code"] == "question_over"

    n1 = post(client, "next", {"round_id": rid, "from_index": 0})
    n2 = post(client, "next", {"round_id": rid, "from_index": 0})   # idempotent
    assert n1["question"]["number"] == n2["question"]["number"] == 2
    assert_no_answer(app, rid, n1)

    # Q2: timeout -> 0, answer revealed. Too-early timeouts are refused.
    clock.t += 10
    e = post(client, "timeout", {"round_id": rid, "question_index": 1}, status=409)["error"]
    assert e["code"] == "too_early" and e["details"]["remaining_ms"] == 10000
    clock.t += 10
    t = post(client, "timeout", {"round_id": rid, "question_index": 1})
    assert t["result"] == "timeout" and t["points"] == 0 and not t["stale"] and t["answer"]["capital"]
    assert post(client, "timeout", {"round_id": rid, "question_index": 1})["stale"]
    post(client, "pick", {"round_id": rid, "question_index": 1, "option_id": 0}, status=409)
    post(client, "next", {"round_id": rid, "from_index": 1})

    # Q3: correct instantly with three wrong picks first -> 20 - 9 = 11.
    post(client, "summary", {"round_id": rid}, status=409)
    post(client, "next", {"round_id": rid, "from_index": 2}, status=409)   # still active
    for _ in range(3):
        post(client, "pick", {"round_id": rid, "question_index": 2, "option_id": wrong_of(app, rid)})
    c = post(client, "pick", {"round_id": rid, "question_index": 2, "option_id": answer_of(app, rid)})
    assert c["points"] == 11 and c["is_last"]
    assert post(client, "next", {"round_id": rid, "from_index": 2})["finished"]

    s = post(client, "summary", {"round_id": rid})
    assert s["score"] == 23 and s["max_score"] == 60 and s["is_new_best"]
    assert [q["points"] for q in s["questions"]] == [12, 0, 11]
    assert [q["status"] for q in s["questions"]] == ["correct", "timeout", "correct"]
    assert [q["wrong"] for q in s["questions"]] == [1, 0, 3]
    assert [q["elapsed_ms"] for q in s["questions"]] == [5000, 20000, 0]
    assert len(s["questions"][2]["wrong_picks"]) == 3
    assert all(q["country"] and q["capital"] and q["flag_url"] for q in s["questions"])
    assert post(client, "summary", {"round_id": rid})["best"] == 23     # recorded once

    best = post(client, "best", {"settings": {"questions": 3, "view": "globe"}})
    assert best["best"] == 23 and best["max_score"] == 60             # view doesn't split high scores
    assert post(client, "best", {"settings": {"questions": 3, "framing": "world"}})["best"] is None


def test_paused_time_is_excluded(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = begin(client)["round_id"]
    clock.t += 4
    assert post(client, "pause", {"round_id": rid})["paused"]
    clock.t += 300                                   # scrolled away for five minutes
    assert post(client, "resume", {"round_id": rid})["elapsed_ms"] == 4000
    clock.t += 6
    c = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": answer_of(app, rid)})
    assert c["points"] == 10 and c["elapsed_ms"] == 10000


def test_pick_while_paused_resumes_first(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = begin(client)["round_id"]
    clock.t += 5
    post(client, "pause", {"round_id": rid})
    clock.t += 100
    c = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": answer_of(app, rid)})
    assert c["points"] == 15


def test_late_pick_becomes_a_timeout(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = begin(client)["round_id"]
    clock.t += 25
    c = post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": answer_of(app, rid)})
    assert c["result"] == "timeout" and c["points"] == 0 and c["elapsed_ms"] == 20000


def test_expired_round(client: FlaskClient) -> None:
    e = post(client, "begin", {"round_id": "gone"}, status=404)["error"]
    assert e["code"] == "round_not_found"


@pytest.mark.parametrize("body,status,code", [
    ({}, 400, "bad_request"),
    ({"round_id": ""}, 400, "bad_request"),
    ({"round_id": "x" * 65}, 400, "bad_request"),
    ({"round_id": "nope", "question_index": 0, "option_id": 0}, 404, "round_not_found"),
])
def test_pick_bad_round(client: FlaskClient, body: dict, status: int, code: str) -> None:
    assert post(client, "pick", body, status=status)["error"]["code"] == code


def test_pick_validates_input(client: FlaskClient) -> None:
    r = begin(client, options="4")
    rid = r["round_id"]
    for option, status in (("1", 400), (-1, 400), (8, 400), (True, 400), (1.5, 400), (None, 400), (5, 409)):
        post(client, "pick", {"round_id": rid, "question_index": 0, "option_id": option}, status=status)
    post(client, "pick", {"round_id": rid, "question_index": 3, "option_id": 0}, status=400)   # out of range
    post(client, "pick", {"round_id": rid, "question_index": 1, "option_id": 0}, status=409)   # not current
    post(client, "pick", {"round_id": rid, "question_index": "0", "option_id": 0}, status=400)
    assert client.post(f"{API}/pick", data="not json", content_type="application/json").status_code == 400


def test_invalid_settings(client: FlaskClient) -> None:
    for bad, field in (({"questions": 31}, "questions"), ({"seconds": 5}, "seconds"), ({"options": "7"}, "options"),
                       ({"view": "3d"}, "view"), ({"framing": "zoom"}, "framing"), ({"region": "mars"}, "region"),
                       ({"lives": 3}, "lives")):
        e = post(client, "start", {"settings": bad}, status=400)["error"]
        assert e["code"] == "invalid_settings" and field in e["details"]["fields"]
    e = post(client, "start", {"settings": {"region": "oceania", "questions": 20}}, status=400)["error"]
    assert "Only 13" in e["details"]["fields"]["questions"]
    post(client, "start", {"settings": "x"}, status=400)


def test_page_renders(client: FlaskClient) -> None:
    html = client.get("/games/capital-quest/").get_data(as_text=True)
    assert "Capital Quest" in html and 'data-mode="page"' in html
    for label in ("Questions per round", "Time per question", "Number of options", "Map view", "Map framing"):
        assert label in html
    assert "/static/js/map_view.js" in html and "/static/vendor/d3/d3.min.js" in html
    assert "/games/border-bridge/static/data/countries-50m.json" in html
