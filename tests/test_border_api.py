"""Border Bridge HTTP API: full flows, input validation, no answer leaks."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.games.border_bridge import logic as L
from app.games.border_bridge.graph import get_graph

API = "/games/border-bridge/api"


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
                      "BORDER_BRIDGE_SEED": 7, "BORDER_BRIDGE_CLOCK": clock})


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def post(client: FlaskClient, path: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    resp = client.post(f"{API}/{path}", json=body)
    assert resp.status_code == status, resp.get_json()
    return resp.get_json()


def server_round(app: Flask, round_id: str) -> L.Round:
    return app.extensions["border_bridge.rounds"].get(round_id)


def correct_now(app: Flask, round_id: str) -> set[str]:
    p = server_round(app, round_id).current
    return L.correct_set(get_graph(), p.revealed, p.a, p.b)


def start(client: FlaskClient, **settings: Any) -> dict[str, Any]:
    return post(client, "start", {"settings": {"difficulty": "easy", "puzzles": 3, **settings}})


def test_start_payload_has_no_answers(client: FlaskClient) -> None:
    r = start(client)
    pz = r["puzzle"]
    assert set(pz) == {"index", "number", "total", "a", "b", "between", "found", "options",
                       "max_points", "status", "trail", "wrong"}
    assert pz["trail"] == {"a_side": [], "b_side": [], "remaining": 1}
    assert pz["between"] == 1 and pz["found"] == [] and len(pz["options"]) == 4
    for o in pz["options"]:
        assert set(o) == {"id", "name"}
    assert set(pz["a"]) == {"id", "shape", "name"}
    assert r["max_score"] == 3 * 30 and r["best"] is None


def test_full_round_flow(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    r = start(client)
    rid = r["round_id"]

    # Puzzle 1: one wrong pick, then solve after the countdown -> 10 - 5 = 5, no bonuses.
    opts = [o["id"] for o in r["puzzle"]["options"]]
    good = correct_now(app, rid)
    wrong = next(o for o in opts if o not in good)
    w = post(client, "pick", {"round_id": rid, "puzzle_index": 0, "option_id": wrong})
    assert w["result"] in ("detour", "far") and not w["solved"]
    assert wrong not in [o["id"] for o in w["options"]] and len(w["options"]) == 3
    assert w["penalty"] == 5 and w["wrong"] == 1 and w["score"] == 0
    if w["result"] == "detour":
        assert "but it's a detour" in w["message"] and w["via"]
    else:
        assert "doesn't border any of the connected countries" in w["message"]
    assert "correct" not in str(w["options"])
    post(client, "pick", {"round_id": rid, "puzzle_index": 0, "option_id": wrong}, status=409)

    clock.t += 40
    c = post(client, "pick", {"round_id": rid, "puzzle_index": 0, "option_id": good.pop()})
    assert c["result"] == "correct" and c["solved"]
    assert len(c["chain"]) == 3 and c["points"] == 5 and c["bonuses"] == {}
    assert c["elapsed_ms"] == 40000 and c["score"] == 5 and c["message"].startswith("Connected!")
    post(client, "pick", {"round_id": rid, "puzzle_index": 0, "option_id": c["options"][0]["id"] if c["options"] else "AAA"},
         status=409)

    # next is idempotent
    n1 = post(client, "next", {"round_id": rid, "from_index": 0})
    n2 = post(client, "next", {"round_id": rid, "from_index": 0})
    assert n1["puzzle"]["number"] == n2["puzzle"]["number"] == 2

    # Puzzle 2: perfect solve 3 s in -> 10 + 5 + 12 seconds left.
    clock.t += 3
    c = post(client, "pick", {"round_id": rid, "puzzle_index": 1, "option_id": correct_now(app, rid).pop()})
    assert c["solved"] and c["points"] == 27 and c["bonuses"] == {"perfect": 5, "time": 12} and c["score"] == 32
    post(client, "next", {"round_id": rid, "from_index": 1})

    # Puzzle 3: give up -> 0 and the chain is revealed.
    post(client, "summary", {"round_id": rid}, status=409)
    post(client, "next", {"round_id": rid, "from_index": 2}, status=409)  # still active
    g = post(client, "giveup", {"round_id": rid, "puzzle_index": 2})
    assert g["points"] == 0 and len(g["chain"]) == 3 and len(g["added"]) == 1 and g["is_last"]
    post(client, "giveup", {"round_id": rid, "puzzle_index": 2}, status=409)
    assert post(client, "next", {"round_id": rid, "from_index": 2})["finished"]

    s = post(client, "summary", {"round_id": rid})
    assert s["score"] == 32 and s["max_score"] == 90 and s["is_new_best"]
    assert [p["points"] for p in s["puzzles"]] == [5, 27, 0]
    assert [p["status"] for p in s["puzzles"]] == ["solved", "solved", "gave_up"]
    assert len(s["puzzles"][0]["wrong"]) == 1 and s["puzzles"][0]["elapsed_ms"] == 40000
    assert post(client, "summary", {"round_id": rid})["best"] == 32  # recorded once

    best = post(client, "best", {"settings": {"difficulty": "easy", "puzzles": 3, "view": "globe"}})
    assert best["best"] == 32  # view mode doesn't split high scores
    best = post(client, "best", {"settings": {"difficulty": "easy", "puzzles": 3, "borders": "off"}})
    assert best["best"] is None


def test_correct_pick_regenerates_options(app: Flask, client: FlaskClient) -> None:
    r = post(client, "start", {"settings": {"difficulty": "hard", "puzzles": 1}})
    rid = r["round_id"]
    p = server_round(app, rid).current
    first = correct_now(app, rid) & {o["id"] for o in r["puzzle"]["options"]}
    c = post(client, "pick", {"round_id": rid, "puzzle_index": 0, "option_id": sorted(first)[0]})
    assert c["result"] == "correct" and not c["solved"]
    assert len(c["options"]) == 4 and c["penalty"] == 0 and c["score"] == 0
    assert [f["id"] for f in c["found"]] == [sorted(first)[0]]
    assert {o["id"] for o in c["options"]} == set(p.options)


@pytest.mark.parametrize("body,code", [
    ({}, "bad_request"),
    ({"round_id": "nope", "puzzle_index": 0, "option_id": "FRA"}, "round_not_found"),
])
def test_pick_bad_round(client: FlaskClient, body: dict, code: str) -> None:
    status = 404 if code == "round_not_found" else 400
    assert post(client, "pick", body, status=status)["error"]["code"] == code


def test_pick_validates_input(client: FlaskClient) -> None:
    rid = start(client)["round_id"]
    for option, status in (("fr", 400), ("FRANCE", 400), ("1; DROP", 400), ("ZZZ", 409)):
        post(client, "pick", {"round_id": rid, "puzzle_index": 0, "option_id": option}, status=status)
    post(client, "pick", {"round_id": rid, "puzzle_index": 5, "option_id": "FRA"}, status=400)
    post(client, "pick", {"round_id": rid, "puzzle_index": 1, "option_id": "FRA"}, status=409)
    post(client, "pick", {"round_id": rid, "puzzle_index": True, "option_id": "FRA"}, status=400)


def test_invalid_settings(client: FlaskClient) -> None:
    e = post(client, "start", {"settings": {"puzzles": 99}}, status=400)["error"]
    assert e["code"] == "invalid_settings" and "puzzles" in e["details"]["fields"]
    e = post(client, "start", {"settings": {"difficulty": "nightmare"}}, status=400)["error"]
    assert "difficulty" in e["details"]["fields"]
    n = len(L.pair_pool(get_graph(), "easy", "americas"))
    e = post(client, "start", {"settings": {"difficulty": "easy", "region": "americas", "puzzles": n + 1}},
             status=400)["error"]
    assert str(n) in e["details"]["fields"]["puzzles"]
    post(client, "start", {"settings": "x"}, status=400)


def test_page_renders(client: FlaskClient) -> None:
    html = client.get("/games/border-bridge/").get_data(as_text=True)
    assert "Border Bridge" in html and "Puzzles per round" in html and "Show country borders" in html
    assert "/static/vendor/d3/d3.min.js" in html
    assert client.get("/games/border-bridge/static/data/countries-50m.json").status_code == 200
    assert client.get("/games/border-bridge/static/data/geo_meta.json").status_code == 200
