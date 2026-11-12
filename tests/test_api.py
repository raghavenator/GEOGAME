"""End-to-end API flow through the Flask test client."""
from __future__ import annotations

from typing import Any

from flask import Flask
from flask.testing import FlaskClient

from app.shared.countries import get_index

API = "/games/flag-reveal/api"


def post(client: FlaskClient, path: str, body: Any) -> tuple[int, dict[str, Any]]:
    resp = client.post(f"{API}/{path}", json=body)
    return resp.status_code, resp.get_json()


def answer_for(app: Flask, round_id: str) -> str:
    rnd = app.extensions["flag_reveal.rounds"].get(round_id)
    return get_index().by_code[rnd.current.code].name


def start(client: FlaskClient, **settings: Any) -> dict[str, Any]:
    status, body = post(client, "start", {"settings": settings})
    assert status == 200, body
    return body


def test_start_does_not_leak_answer(app: Flask, client: FlaskClient) -> None:
    body = start(client, questions=3)
    q = body["question"]
    answer = app.extensions["flag_reveal.rounds"].get(body["round_id"]).current.code
    assert set(q) == {"index", "number", "total", "flag_url", "reveal", "stage"}
    assert ".svg" not in q["flag_url"] and not q["flag_url"].endswith(f"/{answer}")
    assert answer_for(app, body["round_id"]) not in str(body)
    assert body["max_score"] == 45 and body["best"] is None


def test_flag_served_by_token(client: FlaskClient) -> None:
    body = start(client)
    resp = client.get(body["question"]["flag_url"])
    assert resp.status_code == 200
    assert resp.mimetype == "image/svg+xml"
    assert b"flag-icons" not in resp.data
    assert client.get("/games/flag-reveal/flag/not-a-token").status_code == 404


def test_full_round_flow_with_every_outcome(app: Flask, client: FlaskClient) -> None:
    body = start(client, questions=4, stage_seconds=3)
    rid = body["round_id"]

    # Q1: wrong guess, then correct at stage 1 -> 15
    status, r = post(client, "guess", {"round_id": rid, "guess": "Narnia", "stage": 1})
    assert status == 200 and r["correct"] is False and r["recognised"] is False
    wrong = "Germany" if answer_for(app, rid) != "Germany" else "France"
    _, r = post(client, "guess", {"round_id": rid, "guess": wrong, "stage": 1})
    assert r == {"correct": False, "stage": 1, "recognised": True, "guessed": wrong, "message": "Not quite"}
    name = answer_for(app, rid)
    _, r = post(client, "guess", {"round_id": rid, "guess": name.upper(), "stage": 1})
    assert r["correct"] and r["points"] == 15 and r["country"]["name"] == name and r["score"] == 15
    status, r = post(client, "guess", {"round_id": rid, "guess": name, "stage": 1})
    assert status == 409 and r["error"]["code"] == "question_over"

    # Q2: correct at stage 2 -> 10
    _, r = post(client, "next", {"round_id": rid, "from_index": 0})
    assert r["question"]["number"] == 2
    _, r = post(client, "next", {"round_id": rid, "from_index": 0})   # double-click: no skip
    assert r["question"]["number"] == 2
    _, r = post(client, "timeout", {"round_id": rid, "stage": 1})
    assert r["stage"] == 2 and not r["missed"] and "country" not in r
    _, r = post(client, "timeout", {"round_id": rid, "stage": 1})      # duplicate
    assert r["stale"] and r["stage"] == 2
    _, r = post(client, "guess", {"round_id": rid, "guess": answer_for(app, rid), "stage": 1})
    assert r["points"] == 10, "server ignores a stage earlier than it has seen"

    # Q3: correct at stage 3 -> 5
    post(client, "next", {"round_id": rid, "from_index": 1})
    post(client, "timeout", {"round_id": rid, "stage": 1})
    post(client, "timeout", {"round_id": rid, "stage": 2})
    _, r = post(client, "guess", {"round_id": rid, "guess": answer_for(app, rid), "stage": 3})
    assert r["points"] == 5

    # summary before finishing is refused
    status, r = post(client, "summary", {"round_id": rid})
    assert status == 409

    # Q4: time out to 0
    post(client, "next", {"round_id": rid, "from_index": 2})
    name = answer_for(app, rid)
    for s in (1, 2):
        post(client, "timeout", {"round_id": rid, "stage": s})
    _, r = post(client, "timeout", {"round_id": rid, "stage": 3})
    assert r["missed"] and r["country"]["name"] == name and r["is_last"]

    _, r = post(client, "next", {"round_id": rid, "from_index": 3})
    assert r == {"finished": True, "score": 30}

    _, s = post(client, "summary", {"round_id": rid})
    assert s["score"] == 30 and s["max_score"] == 60 and s["is_new_best"] and s["best"] == 30
    assert [q["points"] for q in s["questions"]] == [15, 10, 5, 0]
    assert [q["label"] for q in s["questions"]] == ["Stage 1", "Stage 2", "Stage 3", "Missed"]

    # summary is idempotent (score recorded once)
    _, s2 = post(client, "summary", {"round_id": rid})
    assert s2["is_new_best"] and s2["best"] == 30

    # best score shows for the same settings, not for others
    _, b = post(client, "best", {"settings": {"questions": 4, "stage_seconds": 3}})
    assert b["best"] == 30 and b["max_score"] == 60
    _, b = post(client, "best", {"settings": {"questions": 4, "stage_seconds": 4}})
    assert b["best"] is None


def test_lower_score_does_not_replace_best(app: Flask, client: FlaskClient) -> None:
    for expected_best, solve in ((15, True), (15, False)):
        rid = start(client, questions=1)["round_id"]
        if solve:
            post(client, "guess", {"round_id": rid, "guess": answer_for(app, rid), "stage": 1})
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
    status, r = post(client, "start", {"settings": {"questions": 15, "region": "oceania"}})
    assert status == 400 and "14" in r["error"]["message"]


def test_validation_errors_are_clear_json(client: FlaskClient) -> None:
    status, r = post(client, "start", {"settings": {"questions": 99, "stage_seconds": 1}})
    assert status == 400 and r["error"]["code"] == "invalid_settings"
    assert set(r["error"]["details"]["fields"]) == {"questions", "stage_seconds"}

    resp = client.post(f"{API}/start", data="nope", content_type="text/plain")
    assert resp.status_code == 400 and resp.get_json()["error"]["code"] == "bad_request"

    status, r = post(client, "guess", {"round_id": "missing", "guess": "France", "stage": 1})
    assert status == 404 and r["error"]["code"] == "round_not_found"

    rid = start(client)["round_id"]
    for bad in ({"stage": 0}, {"stage": 4}, {"stage": "1"}, {"stage": None}):
        status, r = post(client, "guess", {"round_id": rid, "guess": "France", **bad})
        assert status == 400, bad
    status, _ = post(client, "guess", {"round_id": rid, "guess": "", "stage": 1})
    assert status == 400
    status, _ = post(client, "guess", {"round_id": rid, "guess": "x" * 81, "stage": 1})
    assert status == 400
    status, r = post(client, "next", {"round_id": rid, "from_index": 0})
    assert status == 409 and r["error"]["code"] == "question_active"
    status, _ = post(client, "next", {"round_id": rid, "from_index": 9})
    assert status == 400


def test_countries_endpoint(client: FlaskClient) -> None:
    data = client.get(f"{API}/countries").get_json()
    assert len(data) == 195 and {"name": "France", "aliases": []} in data
