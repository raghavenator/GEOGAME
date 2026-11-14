"""Pinpoint rules. Pure functions and plain dataclasses, no Flask.

A country name appears; the player moves the map until the fixed crosshair sits on it
and submits. Accuracy is scored relative to the country's size, then scaled by speed.
Time is always the server's clock, counted from ``begin``/``next`` with paused time
shifted out. Tested in tests/test_pinpoint_logic.py.

Scoring (all constants in ``PinpointConfig``):
* d = great-circle distance (km) from the guess to the country's reference point
* r = sqrt(area / pi), the radius of a circle with the country's area
* tolerance = min(tol_factor * r, 1200 km); d_eff = max(0, d - tolerance)
* scale = clamp(scale_factor * r, 150 km, 1500 km)
* accuracy = round(100 * exp(-d_eff / scale)), or 100 for any guess inside the country's
  own borders (its Natural Earth 50m polygons, holes excluded)
* speed = 1.0 up to 5 s, then linear down to 0.5 at the time limit (never lower)
* points = round(accuracy * speed)
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.shared.countries import REGIONS
from app.shared.settings import ChoiceSetting, IntSetting, SettingsSchema

from .location_data import Location, Polygon, point_in_polygons

EARTH_RADIUS_KM = 6371.0
KM_PER_MILE = 1.609344


@dataclass(frozen=True)
class Difficulty:
    label: str
    tol_factor: float      # share of the country's radius that counts as a direct hit
    scale_factor: float    # share of the radius over which accuracy falls to ~37 %


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class PinpointConfig:
    max_points: int = 100
    tolerance_cap_km: float = 1200.0
    scale_min_km: float = 150.0
    scale_max_km: float = 1500.0
    full_speed_seconds: float = 5.0      # speed factor is 1.0 up to here
    min_speed_factor: float = 0.5        # ... and falls linearly to this at the time limit
    time_limit_seconds: int = 30
    time_limit_range: tuple[int, int] = (15, 120)
    questions: int = 5
    questions_range: tuple[int, int] = (1, 30)
    difficulties: tuple[tuple[str, Difficulty], ...] = (
        ("easy", Difficulty("Easy", 0.6, 1.5)),
        ("normal", Difficulty("Normal", 0.35, 1.0)),
        ("hard", Difficulty("Hard", 0.15, 0.6)),
    )
    default_difficulty: str = "normal"
    # The client auto-submits when its countdown ends. If the server's clock says there's
    # more than this much time left, the timeout is refused (the two clocks drift a little).
    early_timeout_tolerance_seconds: float = 1.0
    # Settings that change the challenge; map view and units are cosmetic.
    scored_settings: tuple[str, ...] = ("questions", "seconds", "difficulty", "borders", "region")

    def difficulty(self, key: str) -> Difficulty:
        return dict(self.difficulties)[key]


CONFIG = PinpointConfig()

SETTINGS_SCHEMA: SettingsSchema = (
    IntSetting("questions", "Questions per round", CONFIG.questions, *CONFIG.questions_range,
               help="How many countries you'll look for in one round."),
    IntSetting("seconds", "Time limit per question", CONFIG.time_limit_seconds, *CONFIG.time_limit_range,
               help="When it runs out, your crosshair position is submitted for you.", unit="s"),
    ChoiceSetting("difficulty", "Difficulty", CONFIG.default_difficulty,
                  tuple((k, d.label) for k, d in CONFIG.difficulties),
                  help="How close you need to be. Harder settings shrink the zone that counts as a direct hit."),
    ChoiceSetting("view", "Map view", "flat", (("flat", "Flat map"), ("globe", "Globe")),
                  help="Drag to move the map under the crosshair; pinch or scroll to zoom."),
    ChoiceSetting("borders", "Show country borders", "on", (("on", "On"), ("off", "Off (harder)")),
                  help="Off draws the land as one seamless mass."),
    ChoiceSetting("units", "Distance units", "km", (("km", "Kilometres"), ("mi", "Miles"))),
    ChoiceSetting("region", "Region", "all",
                  (("all", "Whole world"), *((r.lower(), r) for r in REGIONS)),
                  help="Only ask about countries from this part of the world."),
)


class QuestionOver(Exception):
    """The question has already been submitted."""


class QuestionActive(Exception):
    """Can't move on before the question is submitted."""


class TimeoutTooEarly(Exception):
    def __init__(self, remaining_s: float) -> None:
        super().__init__(f"{remaining_s:.2f}s left")
        self.remaining_s = remaining_s


# --------------------------------------------------------------------------- geometry
def normalize_lng(lng: float) -> float:
    """Longitude in [-180, 180)."""
    return (lng + 180.0) % 360.0 - 180.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance. Longitudes are differences, so the antimeridian needs no care."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def km_to_miles(km: float) -> float:
    return km / KM_PER_MILE


# --------------------------------------------------------------------------- scoring
def round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def equivalent_radius_km(area_km2: float) -> float:
    return math.sqrt(max(0.0, area_km2) / math.pi)


def tolerance_km(area_km2: float, diff: Difficulty, cfg: PinpointConfig = CONFIG) -> float:
    return min(cfg.tolerance_cap_km, diff.tol_factor * equivalent_radius_km(area_km2))


def scale_km(area_km2: float, diff: Difficulty, cfg: PinpointConfig = CONFIG) -> float:
    return min(cfg.scale_max_km, max(cfg.scale_min_km, diff.scale_factor * equivalent_radius_km(area_km2)))


def accuracy_points(distance_km: float, area_km2: float, diff: Difficulty, cfg: PinpointConfig = CONFIG) -> int:
    """0–100: full marks inside the tolerance, then exponential decay over the scale."""
    d_eff = max(0.0, distance_km - tolerance_km(area_km2, diff, cfg))
    return round_half_up(cfg.max_points * math.exp(-d_eff / scale_km(area_km2, diff, cfg)))


def speed_factor(elapsed_s: float, limit_s: float, cfg: PinpointConfig = CONFIG) -> float:
    """1.0 up to full_speed_seconds, then linear down to min_speed_factor at the limit."""
    if elapsed_s <= cfg.full_speed_seconds:
        return 1.0
    span = max(1e-9, limit_s - cfg.full_speed_seconds)
    t = min(1.0, (elapsed_s - cfg.full_speed_seconds) / span)
    return 1.0 - (1.0 - cfg.min_speed_factor) * t


@dataclass(frozen=True)
class Score:
    distance_km: float
    accuracy: int
    speed: float
    points: int
    inside: bool = False   # the guess is within the country's borders (full accuracy)


def score_guess(lat: float, lng: float, target: Location, elapsed_s: float, limit_s: float, diff: Difficulty,
                timed_out: bool = False, polygons: Sequence[Polygon] = (), cfg: PinpointConfig = CONFIG) -> Score:
    """``polygons``: the target country's outline; a guess inside it gets full accuracy. Countries
    without a shape (Tuvalu at 50m) are scored on distance alone. Speed still applies either way."""
    d = haversine_km(lat, lng, target.lat, target.lng)
    inside = bool(polygons) and point_in_polygons(lat, lng, polygons)
    acc = cfg.max_points if inside else accuracy_points(d, target.area_km2, diff, cfg)
    speed = cfg.min_speed_factor if timed_out else speed_factor(elapsed_s, limit_s, cfg)
    return Score(distance_km=d, accuracy=acc, speed=speed, points=round_half_up(acc * speed), inside=inside)


def max_score_for(questions: int, cfg: PinpointConfig = CONFIG) -> int:
    return questions * cfg.max_points


# --------------------------------------------------------------------------- pools
def region_pool(pool: Sequence[Location], region: str) -> list[Location]:
    return [e for e in pool if region == "all" or e.region.lower() == region]


def pick_targets(pool: Sequence[Location], count: int, rng: random.Random) -> list[Location]:
    """``count`` distinct countries (never repeats within a round)."""
    unique = list({e.code: e for e in pool}.values())
    if count < 1:
        raise ValueError("A round needs at least one question.")
    if count > len(unique):
        raise ValueError(f"Only {len(unique)} countries are available for this region; "
                         f"choose {len(unique)} or fewer questions.")
    return rng.sample(unique, count)


# --------------------------------------------------------------------------- state
Status = Literal["active", "scored"]


@dataclass
class Question:
    code: str
    status: Status = "active"
    started_at: float | None = None          # server clock when the question was shown (begin/next)
    ended_at: float | None = None
    guess: tuple[float, float] | None = None  # (lat, lng)
    timed_out: bool = False
    score: Score | None = None

    @property
    def points(self) -> int:
        return self.score.points if self.score else 0

    def elapsed(self, now: float) -> float:
        if self.started_at is None:
            return 0.0
        return max(0.0, (self.ended_at if self.ended_at is not None else now) - self.started_at)


@dataclass
class Round:
    id: str
    settings: dict[str, Any]
    questions: list[Question]
    index: int = 0
    finished: bool = False
    recorded: bool = False
    begun: bool = False
    paused_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def current(self) -> Question:
        return self.questions[self.index]

    @property
    def is_last(self) -> bool:
        return self.index == len(self.questions) - 1

    @property
    def limit(self) -> int:
        return int(self.settings["seconds"])

    @property
    def score(self) -> int:
        return sum(q.points for q in self.questions)

    @property
    def max_score(self) -> int:
        return max_score_for(len(self.questions))


def new_round(round_id: str, settings: dict[str, Any], pool: Sequence[Location], rng: random.Random) -> Round:
    targets = pick_targets(region_pool(pool, settings["region"]), settings["questions"], rng)
    return Round(id=round_id, settings=settings, questions=[Question(code=t.code) for t in targets])


# --------------------------------------------------------------------------- play clock
def begin(rnd: Round, now: float) -> bool:
    """Start play. Idempotent: returns False if the round had already begun."""
    if rnd.begun:
        return False
    rnd.begun = True
    rnd.current.started_at = now
    return True


def pause(rnd: Round, now: float) -> bool:
    if not rnd.begun or rnd.paused_at is not None:
        return False
    rnd.paused_at = now
    return True


def resume(rnd: Round, now: float) -> bool:
    if rnd.paused_at is None:
        return False
    q = rnd.current
    if q.status == "active" and q.started_at is not None:
        q.started_at += max(0.0, now - rnd.paused_at)
    rnd.paused_at = None
    return True


# --------------------------------------------------------------------------- answering
def validate_coords(lat: Any, lng: Any) -> tuple[float, float]:
    """Raise ValueError unless lat is in [-90, 90] and lng in [-180, 180] (finite numbers)."""
    out = []
    for name, v, lim in (("lat", lat, 90.0), ("lng", lng, 180.0)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"'{name}' must be a number.")
        if not -lim <= v <= lim:
            raise ValueError(f"'{name}' must be between {-lim:g} and {lim:g}.")
        out.append(float(v))
    return out[0], out[1]


def apply_submit(q: Question, lat: float, lng: float, target: Location, now: float, limit_s: float,
                 diff: Difficulty, timeout: bool = False, polygons: Sequence[Polygon] = (),
                 cfg: PinpointConfig = CONFIG) -> Score:
    """Score the crosshair position. ``timeout`` is the client's auto-submit when its countdown
    ends: it's checked against the server clock and scored with the minimum speed factor
    (as is any submit the server receives after the limit)."""
    if q.status != "active":
        raise QuestionOver
    elapsed = q.elapsed(now)
    if timeout and limit_s - elapsed > cfg.early_timeout_tolerance_seconds:
        raise TimeoutTooEarly(limit_s - elapsed)
    timed_out = timeout or elapsed >= limit_s
    q.guess = (lat, lng)
    q.timed_out = timed_out
    q.ended_at = now
    if timed_out and q.started_at is not None:
        q.ended_at = min(now, q.started_at + limit_s)   # time taken never shows more than the limit
    q.score = score_guess(lat, lng, target, q.elapsed(now), limit_s, diff, timed_out, polygons, cfg)
    q.status = "scored"
    return q.score


def advance(rnd: Round, from_index: int, now: float) -> None:
    """Move past question ``from_index``. Idempotent: repeats for an old index are no-ops."""
    if rnd.finished or from_index != rnd.index:
        return
    if rnd.current.status == "active":
        raise QuestionActive
    if rnd.is_last:
        rnd.finished = True
        return
    rnd.index += 1
    rnd.current.started_at = now if rnd.paused_at is None else rnd.paused_at


def scored_settings(settings: dict[str, Any], cfg: PinpointConfig = CONFIG) -> dict[str, Any]:
    return {k: settings[k] for k in cfg.scored_settings}
