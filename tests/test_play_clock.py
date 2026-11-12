"""begin / pause / resume for both games: the scoring clock starts at the player's
tap (not when the feed preloads the round) and excludes time spent paused."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.games.border_bridge import logic as BL
from app.games.border_bridge.graph import get_graph
from app.games.flag_reveal import logic as FL

FR = "/games/flag-reveal/api"
BB = "/games/border-bridge/api"


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
    yield create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.sqlite3"), "FLAG_REVEAL_SEED": 3,
                      "BORDER_BRIDGE_SEED": 3, "FLAG_REVEAL_CLOCK": clock, "BORDER_BRIDGE_CLOCK": clock})


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def post(client: FlaskClient, url: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    resp = client.post(url, json=body)
    assert resp.status_code == status, resp.get_json()
    return resp.get_json()


# --------------------------------------------------------------------------- Flag Reveal
def fr_round(app: Flask, rid: str) -> FL.Round:
    return app.extensions["flag_reveal.rounds"].get(rid)


def fr_pick_answer(client: FlaskClient, app: Flask, rid: str, remaining_ms: int, stage: int = 1) -> dict[str, Any]:
    q = fr_round(app, rid).current
    return post(client, f"{FR}/pick", {"round_id": rid, "option_id": q.options.index(q.code), "stage": stage,
                                       "remaining_ms": remaining_ms})


def test_flag_reveal_requires_begin(app: Flask, client: FlaskClient) -> None:
    rid = post(client, f"{FR}/start", {"settings": {"questions": 2}})["round_id"]
    q = fr_round(app, rid).current
    assert q.served_at is None, "preparing a round must not start its clock"
    for path, body in (("pick", {"option_id": 0, "stage": 1, "remaining_ms": 0}), ("timeout", {"stage": 1}),
                       ("next", {"from_index": 0})):
        err = post(client, f"{FR}/{path}", {"round_id": rid, **body}, status=409)["error"]
        assert err["code"] == "not_started"
    first = post(client, f"{FR}/begin", {"round_id": rid})
    assert first["started"] and first["question"]["number"] == 1 and len(first["question"]["options"]) == 8
    assert post(client, f"{FR}/begin", {"round_id": rid})["started"] is False      # idempotent


def test_flag_reveal_idle_preload_time_does_not_count(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = post(client, f"{FR}/start", {"settings": {"questions": 1}})["round_id"]
    clock.t += 600                     # the card sat prepared in the feed for ten minutes
    post(client, f"{FR}/begin", {"round_id": rid})
    clock.t += 1
    r = fr_pick_answer(client, app, rid, remaining_ms=4000)
    assert r["speed_bonus"] == 4 and r["points"] == 19


def test_flag_reveal_paused_time_is_excluded(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = post(client, f"{FR}/start", {"settings": {"questions": 1}})["round_id"]
    post(client, f"{FR}/begin", {"round_id": rid})
    clock.t += 1
    assert post(client, f"{FR}/pause", {"round_id": rid})["paused"]
    assert post(client, f"{FR}/pause", {"round_id": rid})["paused"]   # repeat is harmless
    clock.t += 300                     # scrolled away for five minutes
    post(client, f"{FR}/resume", {"round_id": rid})
    post(client, f"{FR}/resume", {"round_id": rid})                    # repeat is a no-op
    clock.t += 1
    # 2 s of play on the server clock: the client's 3 s left is believed.
    r = fr_pick_answer(client, app, rid, remaining_ms=3000)
    assert r["speed_bonus"] == 3


def test_flag_reveal_pick_while_paused_resumes_first(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = post(client, f"{FR}/start", {"settings": {"questions": 1}})["round_id"]
    post(client, f"{FR}/begin", {"round_id": rid})
    post(client, f"{FR}/pause", {"round_id": rid})
    clock.t += 100
    r = fr_pick_answer(client, app, rid, remaining_ms=5000)
    assert r["speed_bonus"] == 5 and fr_round(app, rid).paused_at is None


def test_pause_before_begin_is_ignored(app: Flask, client: FlaskClient) -> None:
    rid = post(client, f"{FR}/start", {"settings": {}})["round_id"]
    assert post(client, f"{FR}/pause", {"round_id": rid})["paused"] is False
    assert fr_round(app, rid).paused_at is None


def test_flag_reveal_logic_clock_helpers() -> None:
    from app.shared.countries import get_index
    import random

    rnd = FL.new_round("r", {"questions": 2, "stage_seconds": 5, "region": "all"}, get_index(), random.Random(1),
                       lambda: "t")
    assert not FL.pause(rnd, 5.0)                 # nothing to pause before begin
    assert FL.begin(rnd, 10.0) and not FL.begin(rnd, 11.0)
    assert FL.pause(rnd, 12.0) and not FL.pause(rnd, 13.0)
    assert FL.resume(rnd, 20.0) and not FL.resume(rnd, 21.0)
    assert rnd.current.served_at == 18.0          # 8 paused seconds shifted out


# --------------------------------------------------------------------------- Border Bridge
def bb_round(app: Flask, rid: str) -> BL.Round:
    return app.extensions["border_bridge.rounds"].get(rid)


def test_border_bridge_requires_begin(app: Flask, client: FlaskClient) -> None:
    body = post(client, f"{BB}/start", {"settings": {"difficulty": "easy", "puzzles": 2}})
    rid = body["round_id"]
    assert bb_round(app, rid).current.started_at is None
    opt = body["puzzle"]["options"][0]["id"]
    for path, extra in (("pick", {"puzzle_index": 0, "option_id": opt}), ("giveup", {"puzzle_index": 0}),
                        ("next", {"from_index": 0})):
        assert post(client, f"{BB}/{path}", {"round_id": rid, **extra}, status=409)["error"]["code"] == "not_started"
    begun = post(client, f"{BB}/begin", {"round_id": rid})
    assert begun["started"] and begun["puzzle"]["a"]["name"] and len(begun["puzzle"]["options"]) == 4
    assert post(client, f"{BB}/begin", {"round_id": rid})["started"] is False


def test_border_bridge_time_bonus_counts_from_begin_minus_pauses(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = post(client, f"{BB}/start", {"settings": {"difficulty": "easy", "puzzles": 1}})["round_id"]
    clock.t += 900                     # prepared long before the tap
    post(client, f"{BB}/begin", {"round_id": rid})
    clock.t += 2
    post(client, f"{BB}/pause", {"round_id": rid})
    clock.t += 120
    post(client, f"{BB}/resume", {"round_id": rid})
    clock.t += 1
    p = bb_round(app, rid).current
    correct = BL.correct_set(get_graph(), p.revealed, p.a, p.b) & set(p.options)
    r = post(client, f"{BB}/pick", {"round_id": rid, "puzzle_index": 0, "option_id": sorted(correct)[0]})
    assert r["solved"] and r["elapsed_ms"] == 3000 and r["bonuses"] == {"perfect": 5, "time": 12}


# --------------------------------------------------------------------------- expiry
@pytest.mark.parametrize("api,store,settings", [
    (FR, "flag_reveal.rounds", {"questions": 1}),
    (BB, "border_bridge.rounds", {"difficulty": "easy", "puzzles": 1}),
])
def test_expired_preloaded_round_reports_round_not_found(app: Flask, client: FlaskClient, api: str, store: str,
                                                         settings: dict[str, Any]) -> None:
    """A round left idle past the store's TTL is gone; begin says so, and the client
    (embed lifecycle) silently prepares a fresh round and begins that instead."""
    rid = post(client, f"{api}/start", {"settings": settings})["round_id"]
    rounds = app.extensions[store]
    real_clock = rounds._clock
    rounds._clock = lambda: real_clock() + 3 * 60 * 60
    try:
        err = post(client, f"{api}/begin", {"round_id": rid}, status=404)["error"]
        assert err["code"] == "round_not_found"
        fresh = post(client, f"{api}/start", {"settings": settings})["round_id"]
        assert post(client, f"{api}/begin", {"round_id": fresh})["started"]
    finally:
        rounds._clock = real_clock
