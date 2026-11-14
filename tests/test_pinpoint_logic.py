"""Pinpoint rules: distances, size-relative scoring, speed, the play clock and round flow."""
from __future__ import annotations

import math
import random

import pytest

from app.games.pinpoint import logic as L
from app.games.pinpoint.location_data import Location, load_locations
from app.shared.settings import SettingsError, validate_settings

POOL = load_locations()
BY_CODE = {e.code: e for e in POOL}
NORMAL = L.CONFIG.difficulty("normal")
DEFAULTS = {"questions": 5, "seconds": 30, "difficulty": "normal", "view": "flat", "borders": "on", "units": "km",
            "region": "all"}


def offset(e: Location, km: float, bearing_deg: float = 90.0) -> tuple[float, float]:
    """The point ``km`` from e's reference point along a bearing (spherical destination formula)."""
    d = km / L.EARTH_RADIUS_KM
    p1, l1, b = math.radians(e.lat), math.radians(e.lng), math.radians(bearing_deg)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), L.normalize_lng(math.degrees(l2))


# --------------------------------------------------------------------------- distance
def test_haversine_known_distances() -> None:
    assert L.haversine_km(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(344, abs=2)     # London-Paris
    assert L.haversine_km(0, 0, 0, 180) == pytest.approx(20015, abs=2)                        # antipodes
    assert L.haversine_km(90, 0, -90, 0) == pytest.approx(20015, abs=2)
    # Across the antimeridian: Fiji (178.4E) to Samoa (171.8W) is ~1150 km, not ~39000.
    assert L.haversine_km(-18.1, 178.4, -13.8, -171.8) == pytest.approx(1150, abs=30)
    assert L.haversine_km(10, 179.5, 10, -179.5) == pytest.approx(109.5, abs=1)
    assert L.haversine_km(10, 20, 10, 20) == 0


def test_normalize_lng() -> None:
    assert L.normalize_lng(190) == -170 and L.normalize_lng(-190) == 170 and L.normalize_lng(45) == 45


# --------------------------------------------------------------------------- accuracy
def test_accuracy_bounds_and_monotonic() -> None:
    for code in ("lu", "at", "fr", "ru", "va", "kw"):
        area = BY_CODE[code].area_km2
        pts = [L.accuracy_points(d, area, NORMAL) for d in range(0, 6000, 25)]
        assert all(0 <= p <= 100 for p in pts)
        assert all(a >= b for a, b in zip(pts, pts[1:])), code
        assert pts[0] == 100 and pts[-1] < pts[0]


def test_inside_tolerance_is_a_direct_hit() -> None:
    fr = BY_CODE["fr"]
    tol = L.tolerance_km(fr.area_km2, NORMAL)
    assert tol == pytest.approx(0.35 * math.sqrt(fr.area_km2 / math.pi))
    assert L.accuracy_points(tol - 1, fr.area_km2, NORMAL) == 100
    assert L.accuracy_points(tol + 50, fr.area_km2, NORMAL) < 100
    # The tolerance is capped at 1200 km whatever the size.
    assert L.tolerance_km(1e9, L.CONFIG.difficulty("easy")) == 1200


def test_tolerance_and_scale_grow_with_size() -> None:
    lu, ru = BY_CODE["lu"], BY_CODE["ru"]
    assert L.tolerance_km(lu.area_km2, NORMAL) < 15 < 500 < L.tolerance_km(ru.area_km2, NORMAL)
    assert L.scale_km(lu.area_km2, NORMAL) == 150           # floor
    assert L.scale_km(ru.area_km2, NORMAL) == 1500          # ceiling
    # The same 300 km miss is far worse for Luxembourg than for Russia.
    assert L.accuracy_points(300, lu.area_km2, NORMAL) < 20 < 95 < L.accuracy_points(300, ru.area_km2, NORMAL)


def test_difficulty_presets_order() -> None:
    easy, hard = L.CONFIG.difficulty("easy"), L.CONFIG.difficulty("hard")
    for code in ("lu", "at", "fr", "ru"):
        area = BY_CODE[code].area_km2
        for d in (50, 200, 500, 1500):
            e, n, h = (L.accuracy_points(d, area, x) for x in (easy, NORMAL, hard))
            assert e >= n >= h, (code, d)
    assert L.accuracy_points(500, BY_CODE["fr"].area_km2, easy) > L.accuracy_points(500, BY_CODE["fr"].area_km2, hard)


# --------------------------------------------------------------------------- speed
def test_speed_factor() -> None:
    assert L.speed_factor(0, 30) == L.speed_factor(5, 30) == 1.0
    assert L.speed_factor(17.5, 30) == pytest.approx(0.75)
    assert L.speed_factor(30, 30) == pytest.approx(0.5)
    assert L.speed_factor(500, 30) == pytest.approx(0.5)            # never below 0.5
    assert L.speed_factor(10, 15) == pytest.approx(0.75)            # halfway from 5 s to a 15 s limit
    assert L.speed_factor(15, 15) == pytest.approx(0.5)
    vals = [L.speed_factor(t / 10, 30) for t in range(0, 400)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))


def test_final_points_are_whole_and_speed_only_scales_accuracy() -> None:
    fr = BY_CODE["fr"]
    for km, t in ((0, 1), (120, 7.3), (480, 13.1), (2500, 29.9), (9000, 2)):
        lat, lng = offset(fr, km)
        s = L.score_guess(lat, lng, fr, t, 30, NORMAL)
        assert isinstance(s.points, int) and 0 <= s.points <= s.accuracy <= 100
        assert s.points == L.round_half_up(s.accuracy * s.speed)
        assert s.distance_km == pytest.approx(km, abs=0.5)
    far = L.score_guess(*offset(fr, 9000), fr, 0, 30, NORMAL)
    assert far.points == 0                                           # fast but far earns nothing


# --------------------------------------------------------------------------- state
def make_round(seed: int = 0, **settings: object) -> L.Round:
    return L.new_round("r", {**DEFAULTS, **settings}, POOL, random.Random(seed))


def test_questions_never_repeat() -> None:
    for seed in range(20):
        rnd = make_round(seed, questions=30)
        assert len({q.code for q in rnd.questions}) == 30
    oceania = make_round(0, questions=14, region="oceania")
    assert {BY_CODE[q.code].region for q in oceania.questions} == {"Oceania"}
    with pytest.raises(ValueError, match="Only 14"):
        make_round(0, questions=15, region="oceania")


def test_submit_scores_with_server_time_and_pause_is_excluded() -> None:
    rnd = make_round()
    q = rnd.current
    target = BY_CODE[q.code]
    assert L.begin(rnd, 100.0) and not L.begin(rnd, 101.0)
    L.pause(rnd, 104.0)
    L.resume(rnd, 404.0)                                   # five minutes away
    s = L.apply_submit(q, target.lat, target.lng, target, 407.0, rnd.limit, NORMAL)
    assert q.elapsed(999) == 7.0 and s.speed == pytest.approx(L.speed_factor(7, 30)) and s.accuracy == 100
    with pytest.raises(L.QuestionOver):                    # no double submit
        L.apply_submit(q, 0, 0, target, 408.0, rnd.limit, NORMAL)


def test_timeout_auto_submit_uses_the_minimum_speed_factor() -> None:
    rnd = make_round()
    q = rnd.current
    target = BY_CODE[q.code]
    L.begin(rnd, 0.0)
    with pytest.raises(L.TimeoutTooEarly):
        L.apply_submit(q, target.lat, target.lng, target, 20.0, 30, NORMAL, timeout=True)
    assert q.status == "active"
    s = L.apply_submit(q, target.lat, target.lng, target, 29.4, 30, NORMAL, timeout=True)
    assert s.speed == 0.5 and s.points == 50 and q.timed_out and q.elapsed(99) == 29.4
    # A plain submit that arrives after the limit is scored the same way.
    rnd = make_round(1)
    L.begin(rnd, 0.0)
    q = rnd.current
    s = L.apply_submit(q, 0, 0, BY_CODE[q.code], 45.0, 30, NORMAL)
    assert s.speed == 0.5 and q.timed_out and q.elapsed(99) == 30


def test_advance() -> None:
    rnd = make_round(questions=2)
    L.begin(rnd, 0.0)
    with pytest.raises(L.QuestionActive):
        L.advance(rnd, 0, 5.0)
    L.apply_submit(rnd.current, 0, 0, BY_CODE[rnd.current.code], 2.0, 30, NORMAL)
    L.advance(rnd, 0, 6.0)
    L.advance(rnd, 0, 7.0)                                  # idempotent
    assert rnd.index == 1 and rnd.current.started_at == 6.0
    L.apply_submit(rnd.current, 0, 0, BY_CODE[rnd.current.code], 8.0, 30, NORMAL)
    L.advance(rnd, 1, 9.0)
    assert rnd.finished and rnd.max_score == 200


@pytest.mark.parametrize("lat,lng", [(91, 0), (-90.5, 0), (0, 180.01), (0, -181), ("1", 2), (True, 0),
                                     (None, 0), (float("nan"), 0), (0, float("inf"))])
def test_invalid_coordinates(lat: object, lng: object) -> None:
    with pytest.raises(ValueError):
        L.validate_coords(lat, lng)


def test_valid_coordinates() -> None:
    assert L.validate_coords(90, -180) == (90.0, -180.0)
    assert L.validate_coords(-12.5, 179.99) == (-12.5, 179.99)


def test_settings_validation() -> None:
    assert validate_settings(L.SETTINGS_SCHEMA, {}) == DEFAULTS
    for bad in ({"questions": 0}, {"questions": 31}, {"seconds": 14}, {"seconds": 121}, {"difficulty": "brutal"},
                {"view": "3d"}, {"borders": "maybe"}, {"units": "nm"}, {"region": "mars"}, {"extra": 1}):
        with pytest.raises(SettingsError):
            validate_settings(L.SETTINGS_SCHEMA, bad)
    assert set(L.scored_settings(DEFAULTS)) == {"questions", "seconds", "difficulty", "borders", "region"}


# --------------------------------------------------------------------------- inside the borders
def polygons(code: str):
    from app.shared.geo import load_country_polygons
    return load_country_polygons().get(BY_CODE[code].shape, [])


@pytest.mark.parametrize("code,lat,lng", [
    ("ru", 66.0, -173.0),     # Chukotka, across the antimeridian, ~3,600 km from Russia's reference point
    ("ru", 54.7, 20.5),       # the Kaliningrad exclave
    ("cl", -18.5, -70.0),     # Arica, the far north of a very long country
    ("us", 21.3, -157.8),     # Honolulu, Hawaii
    ("lu", 49.6, 6.1),        # Luxembourg City: a tiny country's own outline counts
])
def test_a_guess_inside_the_borders_gets_full_accuracy(code: str, lat: float, lng: float) -> None:
    e = BY_CODE[code]
    far = L.score_guess(lat, lng, e, 3, 30, L.CONFIG.difficulty("hard"), polygons=polygons(code))
    assert far.inside and far.accuracy == 100 and far.points == 100
    # Without the outline it would have been scored on distance (and lost points for the far ones).
    assert L.score_guess(lat, lng, e, 3, 30, L.CONFIG.difficulty("hard")).inside is False


def test_holes_and_neighbours_are_not_inside() -> None:
    maseru = (-29.32, 27.5)                                    # Lesotho, a hole in South Africa's outline
    assert L.score_guess(*maseru, BY_CODE["ls"], 3, 30, NORMAL, polygons=polygons("ls")).inside
    assert not L.score_guess(*maseru, BY_CODE["za"], 3, 30, NORMAL, polygons=polygons("za")).inside
    brussels = (50.85, 4.35)                                   # just over the border from Luxembourg
    s = L.score_guess(*brussels, BY_CODE["lu"], 3, 30, NORMAL, polygons=polygons("lu"))
    assert not s.inside and s.accuracy < 100


def test_inside_still_pays_for_speed_and_shapeless_countries_use_distance() -> None:
    e = BY_CODE["fr"]
    slow = L.score_guess(46.0, 2.0, e, 17.5, 30, NORMAL, polygons=polygons("fr"))
    assert slow.inside and slow.accuracy == 100 and slow.points == 75
    tv = BY_CODE["tv"]
    assert polygons("tv") == []
    s = L.score_guess(tv.lat, tv.lng, tv, 3, 30, NORMAL, polygons=polygons("tv"))
    assert not s.inside and s.accuracy == 100                 # distance 0 -> 100 the usual way
