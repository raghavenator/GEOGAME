"""Pinpoint HTTP API: full flows, the play clock, input validation and no answer leaks."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.games.pinpoint import logic as L
from app.games.pinpoint.location_data import Location, locations_by_code

API = "/games/pinpoint/api"


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
                      "PINPOINT_SEED": 5, "PINPOINT_CLOCK": clock})


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def post(client: FlaskClient, path: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    resp = client.post(f"{API}/{path}", json=body)
    assert resp.status_code == status, resp.get_json()
    return resp.get_json()


def target(app: Flask, rid: str) -> Location:
    return locations_by_code()[app.extensions["pinpoint.rounds"].get(rid).current.code]


def assert_no_target(app: Flask, rid: str, payload: dict[str, Any]) -> None:
    """Nothing but the name identifies the current country: no coordinates, id, code or shape."""
    e = target(app, rid)
    q = payload.get("question", {})
    assert set(q) <= {"index", "number", "total", "name", "time_limit_ms", "elapsed_ms", "status"}
    text = json.dumps({k: v for k, v in payload.items() if k != "settings"}, ensure_ascii=False)
    for secret in (f'"{e.code}"', e.cca3, f'"{e.shape}"', str(e.lat), str(e.lng), '"lat"', '"lng"', '"target"'):
        assert secret not in text, secret


def begin(client: FlaskClient, **settings: Any) -> dict[str, Any]:
    r = post(client, "start", {"settings": {"questions": 3, **settings}})
    return {**r, **post(client, "begin", {"round_id": r["round_id"]})}


def test_start_reveals_nothing(app: Flask, client: FlaskClient) -> None:
    r = post(client, "start", {"settings": {}})
    assert set(r) == {"round_id", "settings", "total", "max_score", "best", "scoring"}
    assert r["total"] == 5 and r["max_score"] == 500 and r["best"] is None
    assert r["scoring"] == {"time_limit_ms": 30000, "full_speed_ms": 5000, "min_speed_factor": 0.5, "max_points": 100}
    assert app.extensions["pinpoint.rounds"].get(r["round_id"]).current.started_at is None
    e = target(app, r["round_id"])
    assert e.name not in json.dumps(r, ensure_ascii=False)
    assert_no_target(app, r["round_id"], r)


def test_actions_before_begin_are_rejected(client: FlaskClient) -> None:
    rid = post(client, "start", {"settings": {}})["round_id"]
    for path, body in (("submit", {"question_index": 0, "lat": 0, "lng": 0}), ("next", {"from_index": 0})):
        assert post(client, path, {"round_id": rid, **body}, status=409)["error"]["code"] == "not_started"


def test_begin_sends_only_the_name(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    r = post(client, "start", {"settings": {}})
    clock.t += 600                                    # preloaded in the feed for ten minutes
    b = post(client, "begin", {"round_id": r["round_id"]})
    assert b["started"] and b["question"]["elapsed_ms"] == 0
    assert b["question"]["name"] == target(app, r["round_id"]).name
    assert_no_target(app, r["round_id"], b)
    assert post(client, "begin", {"round_id": r["round_id"]})["started"] is False


def test_full_round(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    r = begin(client, units="mi")
    rid = r["round_id"]

    # Q1: a perfect guess at 3 s -> 100.
    e = target(app, rid)
    clock.t += 3
    s = post(client, "submit", {"round_id": rid, "question_index": 0, "lat": e.lat, "lng": e.lng})
    assert s["points"] == 100 and s["accuracy"] == 100 and s["speed_factor"] == 1 and s["distance_km"] == 0
    assert s["target"] == {"lat": e.lat, "lng": e.lng, "name": e.name, "code": e.code, "shape": e.shape,
                           "flag_url": f"/static/flags/{e.code}.svg"}
    assert s["guess"] == {"lat": e.lat, "lng": e.lng} and s["elapsed_ms"] == 3000 and not s["timed_out"]
    assert s["score"] == 100 and not s["is_last"]
    again = post(client, "submit", {"round_id": rid, "question_index": 0, "lat": 0, "lng": 0}, status=409)
    assert again["error"]["code"] == "question_over"                   # no double submits

    n = post(client, "next", {"round_id": rid, "from_index": 0})
    assert n["question"]["number"] == 2 and post(client, "next", {"round_id": rid, "from_index": 0})["question"]
    assert_no_target(app, rid, n)

    # Q2: a bad guess (the antipode) at 17.5 s -> 0 whatever the speed.
    e = target(app, rid)
    clock.t += 17.5
    s = post(client, "submit", {"round_id": rid, "question_index": 1, "lat": -e.lat,
                                "lng": L.normalize_lng(e.lng + 180)})
    assert s["points"] == 0 and s["distance_km"] == pytest.approx(20015, abs=2)
    assert s["inside"] is False
    assert s["distance_mi"] == pytest.approx(20015 / 1.609344, abs=2) and s["speed_factor"] == 0.75
    post(client, "next", {"round_id": rid, "from_index": 1})

    # Q3: the countdown ends; the client auto-submits its crosshair. Too early is refused.
    e = target(app, rid)
    clock.t += 20
    err = post(client, "submit", {"round_id": rid, "question_index": 2, "lat": e.lat, "lng": e.lng, "timeout": True},
               status=409)["error"]
    assert err["code"] == "too_early" and err["details"]["remaining_ms"] == 10000
    clock.t += 10
    s = post(client, "submit", {"round_id": rid, "question_index": 2, "lat": e.lat, "lng": e.lng, "timeout": True})
    assert s["timed_out"] and s["speed_factor"] == 0.5 and s["points"] == 50 and s["is_last"]
    post(client, "summary", {"round_id": rid}, status=409)
    assert post(client, "next", {"round_id": rid, "from_index": 2})["finished"]

    sm = post(client, "summary", {"round_id": rid})
    assert sm["score"] == 150 and sm["max_score"] == 300 and sm["is_new_best"]
    assert [q["points"] for q in sm["questions"]] == [100, 0, 50]
    assert [q["elapsed_ms"] for q in sm["questions"]] == [3000, 17500, 30000]
    assert all(q["target"]["name"] and "guess" in q and q["max_points"] == 100 for q in sm["questions"])
    assert post(client, "summary", {"round_id": rid})["best"] == 150     # recorded once
    assert post(client, "best", {"settings": {"questions": 3, "view": "globe", "units": "km"}})["best"] == 150
    assert post(client, "best", {"settings": {"questions": 3, "borders": "off"}})["best"] is None


def test_submit_inside_the_borders_scores_full_accuracy(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    from app.games.pinpoint.location_data import point_in_polygons
    from app.shared.geo import load_country_polygons

    rid = begin(client, difficulty="hard")["round_id"]
    e = target(app, rid)
    polys = load_country_polygons().get(e.shape, [])
    # A point inside the outline as far as possible from the reference point: a polygon vertex nudged inwards
    # isn't guaranteed, so walk the outline's vertices and keep the farthest one that tests inside.
    candidates = [((y + e.lat * 0.02) / 1.02, (((x + 540) % 360 - 180) + e.lng * 0.02) / 1.02)
                  for poly in polys for x, y in poly[0][::5]]
    inside = [(la, lo) for la, lo in candidates if point_in_polygons(la, lo, polys)]
    assert inside, e.name
    lat, lng = max(inside, key=lambda p: L.haversine_km(p[0], p[1], e.lat, e.lng))
    clock.t += 2
    s = post(client, "submit", {"round_id": rid, "question_index": 0, "lat": round(lat, 4), "lng": round(lng, 4)})
    assert s["inside"] is True and s["accuracy"] == 100 and s["points"] == 100 and s["distance_km"] > 0


def test_paused_time_is_excluded(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = begin(client)["round_id"]
    e = target(app, rid)
    clock.t += 4
    assert post(client, "pause", {"round_id": rid})["paused"]
    clock.t += 300
    assert post(client, "resume", {"round_id": rid})["elapsed_ms"] == 4000
    clock.t += 13.5
    s = post(client, "submit", {"round_id": rid, "question_index": 0, "lat": e.lat, "lng": e.lng})
    assert s["elapsed_ms"] == 17500 and s["speed_factor"] == 0.75 and s["points"] == 75


def test_submit_while_paused_resumes_first(app: Flask, client: FlaskClient, clock: FakeClock) -> None:
    rid = begin(client)["round_id"]
    e = target(app, rid)
    clock.t += 2
    post(client, "pause", {"round_id": rid})
    clock.t += 100
    assert post(client, "submit", {"round_id": rid, "question_index": 0, "lat": e.lat, "lng": e.lng})["points"] == 100


@pytest.mark.parametrize("body", [
    {"lat": 91, "lng": 0}, {"lat": 0, "lng": -180.5}, {"lat": "10", "lng": 0}, {"lat": True, "lng": 0},
    {"lat": None, "lng": 0}, {"lng": 0}, {"lat": 0, "lng": 0, "timeout": "yes"},
    {"lat": 0, "lng": 0, "question_index": "0"}, {"lat": 0, "lng": 0, "question_index": 9},
])
def test_submit_validates_input(client: FlaskClient, body: dict[str, Any]) -> None:
    rid = begin(client)["round_id"]
    assert post(client, "submit", {"round_id": rid, "question_index": 0, **body}, status=400)["error"]["code"] \
        == "bad_request"


def test_submit_rejects_other_questions_and_bad_rounds(client: FlaskClient) -> None:
    rid = begin(client)["round_id"]
    assert post(client, "submit", {"round_id": rid, "question_index": 1, "lat": 0, "lng": 0},
                status=409)["error"]["code"] == "question_over"
    assert post(client, "submit", {"round_id": "gone", "question_index": 0, "lat": 0, "lng": 0},
                status=404)["error"]["code"] == "round_not_found"
    assert post(client, "submit", {"question_index": 0, "lat": 0, "lng": 0}, status=400)["error"]["code"] \
        == "bad_request"
    assert post(client, "next", {"round_id": rid, "from_index": 0}, status=409)["error"]["code"] == "question_active"


def test_invalid_settings(client: FlaskClient) -> None:
    for bad, field in (({"questions": 31}, "questions"), ({"seconds": 10}, "seconds"),
                       ({"difficulty": "x"}, "difficulty"), ({"units": "nm"}, "units"), ({"lives": 3}, "lives")):
        e = post(client, "start", {"settings": bad}, status=400)["error"]
        assert e["code"] == "invalid_settings" and field in e["details"]["fields"]
    e = post(client, "start", {"settings": {"region": "oceania", "questions": 20}}, status=400)["error"]
    assert "Oceania has 14 countries" in e["details"]["fields"]["questions"]


def test_page_renders(client: FlaskClient) -> None:
    html = client.get("/games/pinpoint/").get_data(as_text=True)
    assert "Pinpoint" in html and 'data-mode="page"' in html
    for label in ("Questions per round", "Time limit per question", "Difficulty", "Map view",
                  "Show country borders", "Distance units"):
        assert label in html
    assert "/static/js/map_view.js" in html and 'data-gesture-zone="map"' in html and "pp-crosshair" in html
