from __future__ import annotations

import itertools
import random

import pytest

from app.games.flag_reveal import logic
from app.games.flag_reveal.logic import CONFIG, SETTINGS_SCHEMA, Question, RevealPlan
from app.shared.countries import get_index
from app.shared.scoring import max_score, points_for_stage
from app.shared.settings import SettingsError, settings_key, validate_settings


OPTIONS = ["de", "fr", "it", "es", "be", "nl", "ch", "at"]   # "fr" is option 1


def make_question(code: str = "fr") -> Question:
    return Question(code=code, token="t", reveal=RevealPlan("vertical", (0, 1, 2)), options=list(OPTIONS))


def pick(q: Question, option: int, stage: int = 1, remaining_ms: int = 0, now: float = 0.0) -> logic.PickOutcome:
    return logic.apply_pick(q, option, stage, remaining_ms, 5, now)


def make_round(n: int = 3, seed: int = 0) -> logic.Round:
    counter = itertools.count()
    return logic.new_round("r1", {"questions": n, "stage_seconds": 5, "region": "all"},
                           get_index(), random.Random(seed), lambda: f"tok{next(counter)}")


# ------------------------------------------------------------------ scoring
@pytest.mark.parametrize(("stage", "points"), [(1, 15), (2, 10), (3, 5)])
def test_points_per_stage(stage: int, points: int) -> None:
    assert points_for_stage(CONFIG.stage_points, stage) == points


@pytest.mark.parametrize("stage", [0, 4, -1])
def test_points_for_invalid_stage(stage: int) -> None:
    with pytest.raises(ValueError):
        points_for_stage(CONFIG.stage_points, stage)


def test_max_score() -> None:
    assert max_score(5, CONFIG.stage_points) == 75
    assert make_round(7).max_score == 7 * (15 + 5)      # first-stage points + a full stage of speed bonus
    assert logic.max_score_for(5, 10) == 125


@pytest.mark.parametrize(("stage", "points"), [(1, 15), (2, 10), (3, 5)])
def test_correct_pick_awards_stage_points_plus_seconds_left(stage: int, points: int) -> None:
    q = make_question()
    out = pick(q, 1, stage, remaining_ms=2300)
    assert out.correct and out.stage_points == points and out.speed_bonus == 3 and out.points == points + 3
    assert q.status == "solved" and q.solved_stage == stage and q.points == points + 3


def test_wrong_pick_eliminates_option_and_costs_points() -> None:
    q = make_question()
    out = pick(q, 0)
    assert not out.correct and out.code == "de"
    assert q.status == "active" and q.wrong_guesses == 1 and q.eliminated == {0}
    with pytest.raises(logic.OptionUnavailable):
        pick(q, 0)                                   # not offered again
    out = pick(q, 1, remaining_ms=4100)
    assert out.stage_points == 10 and out.speed_bonus == 5 and out.points == 15


def test_penalties_floor_stage_points_at_zero() -> None:
    q = make_question()
    for option in (0, 2, 3, 4):
        pick(q, option)
    out = pick(q, 1, stage=2, remaining_ms=1000)
    assert out.stage_points == 0 and out.points == 1    # 10 - 20 floors at 0, speed bonus still counts


@pytest.mark.parametrize("option", [-1, 8, 99])
def test_unknown_option_rejected(option: int) -> None:
    with pytest.raises(logic.OptionUnavailable):
        pick(make_question(), option)


def test_client_cannot_claim_an_earlier_stage() -> None:
    q = make_question()
    logic.apply_timeout(q, 1)
    logic.apply_timeout(q, 2)
    assert q.stage == 3
    assert pick(q, 1, stage=1).stage_points == 5


def test_pick_after_question_ended_raises() -> None:
    q = make_question()
    pick(q, 1)
    with pytest.raises(logic.QuestionOver):
        pick(q, 2)


@pytest.mark.parametrize("stage", [0, 4, "1", 1.0, True, None])
def test_invalid_stage_rejected(stage: object) -> None:
    with pytest.raises(ValueError):
        logic.apply_pick(make_question(), 1, stage, 0, 5, 0.0)  # type: ignore[arg-type]


@pytest.mark.parametrize(("remaining_ms", "served_at", "now", "bonus"), [
    (5000, None, 0, 5),      # full stage left
    (4001, None, 0, 5),      # rounds up: the timer shows "5"
    (4000, None, 0, 4),
    (1, None, 0, 1),
    (0, None, 0, 0),
    (9000, None, 0, 5),      # never more than a stage
    (5000, 0.0, 0.2, 5),     # honest client, flag took a moment to load
    (5000, 0.0, 4.0, 2),     # client claims more than the server clock allows (5 - 4 + 0.75)
    (5000, 0.0, 9.0, 0),     # stage 1 long over on the server
])
def test_speed_bonus(remaining_ms: int, served_at: float | None, now: float, bonus: int) -> None:
    assert logic.speed_bonus(1, 5, remaining_ms, served_at, now) == bonus


def test_speed_bonus_uses_stage_on_server_clock() -> None:
    # 7 s in with 5 s stages: 3 s of stage 2 left on the server (+0.75 grace).
    assert logic.speed_bonus(2, 5, 4800, 0.0, 7.0) == 4


def test_lookalike_flags_accepted() -> None:
    assert logic.is_correct("ro", "td")
    assert logic.is_correct("mc", "id")
    assert not logic.is_correct("fr", "td")
    assert not logic.is_correct(None, "td")


# ------------------------------------------------------------------ timeouts
def test_timeouts_progress_to_zero_points() -> None:
    q = make_question()
    assert logic.apply_timeout(q, 1).stage == 2
    assert logic.apply_timeout(q, 2).stage == 3
    out = logic.apply_timeout(q, 3)
    assert out.missed and q.status == "missed" and q.points == 0


def test_duplicate_timeout_is_idempotent() -> None:
    q = make_question()
    logic.apply_timeout(q, 1)
    out = logic.apply_timeout(q, 1)     # double-fired timer
    assert out.stale and q.stage == 2


def test_timeout_after_solve_is_stale() -> None:
    q = make_question()
    pick(q, 1)
    out = logic.apply_timeout(q, 1)
    assert out.stale and q.status == "solved" and q.points == 15


# ------------------------------------------------------------------ question selection
def test_pick_questions_never_repeats() -> None:
    pool = [c.code for c in get_index().countries]
    for seed in range(50):
        picked = logic.pick_questions(pool, 50, random.Random(seed))
        assert len(picked) == len(set(picked)) == 50


def test_pick_questions_dedupes_pool_and_limits() -> None:
    assert sorted(logic.pick_questions(["a", "a", "b"], 2, random.Random(0))) == ["a", "b"]
    with pytest.raises(ValueError, match="Only 2"):
        logic.pick_questions(["a", "b"], 3, random.Random(0))
    with pytest.raises(ValueError):
        logic.pick_questions(["a"], 0, random.Random(0))


def test_region_pool() -> None:
    oceania = logic.region_pool(get_index(), "oceania")
    assert len(oceania) == 14 and "au" in oceania and "fr" not in oceania
    assert len(logic.region_pool(get_index(), "all")) == 195


def test_new_round_has_unique_countries_and_tokens() -> None:
    rnd = make_round(20, seed=3)
    assert len({q.code for q in rnd.questions}) == 20
    assert len({q.token for q in rnd.questions}) == 20
    assert all(len(q.options) == 8 and q.code in q.options for q in rnd.questions)


class _FakeCountry:
    region = "X"


class _FakeIndex:
    by_code = {"a": _FakeCountry, "b": _FakeCountry, "c": _FakeCountry}


def test_pick_options() -> None:
    index = get_index()
    pool = logic.region_pool(index, "all")
    rng = random.Random(5)
    for answer in pool:
        opts = logic.pick_options(answer, pool, index, rng)
        assert len(opts) == len(set(opts)) == 8 and answer in opts
        region = index.by_code[answer].region
        assert sum(index.by_code[c].region == region for c in opts if c != answer) >= 4
    for answer, lookalike in (("td", "ro"), ("ro", "td"), ("id", "mc"), ("mc", "id")):
        for _ in range(30):
            assert lookalike not in logic.pick_options(answer, pool, index, rng)
    oceania = logic.region_pool(index, "oceania")
    assert set(logic.pick_options("fj", oceania, index, rng)) <= set(oceania)
    assert sorted(logic.pick_options("a", ["a", "b", "c"], _FakeIndex(), rng)) == ["a", "b", "c"]


def test_reveal_plan_is_a_permutation() -> None:
    rng = random.Random(9)
    orientations = set()
    for _ in range(100):
        plan = logic.pick_reveal_plan(rng)
        assert sorted(plan.order) == [0, 1, 2]
        orientations.add(plan.orientation)
    assert orientations == {"vertical", "horizontal"}


# ------------------------------------------------------------------ round progression
def test_advance_requires_answered_question_and_is_idempotent() -> None:
    rnd = make_round(2)
    with pytest.raises(logic.QuestionActive):
        logic.advance(rnd, 0)
    logic.apply_pick(rnd.current, rnd.current.options.index(rnd.current.code), 1, 0, 5, 0.0)
    assert logic.advance(rnd, 0, now=12.0) is True and rnd.current.served_at == 12.0
    assert logic.advance(rnd, 0) is False   # double "Next" doesn't skip
    assert rnd.index == 1 and not rnd.finished
    logic.apply_timeout(rnd.current, 1)
    logic.apply_timeout(rnd.current, 2)
    logic.apply_timeout(rnd.current, 3)
    assert rnd.finished and rnd.score == 15
    assert [logic.stage_label(q) for q in rnd.questions] == ["Stage 1", "Missed"]


# ------------------------------------------------------------------ settings validation
def test_settings_defaults() -> None:
    assert validate_settings(SETTINGS_SCHEMA, {}) == {"questions": 5, "stage_seconds": 5, "region": "all"}
    assert validate_settings(SETTINGS_SCHEMA, None)["questions"] == 5


def test_settings_accepts_valid_values() -> None:
    clean = validate_settings(SETTINGS_SCHEMA, {"questions": "50", "stage_seconds": 2, "region": "europe"})
    assert clean == {"questions": 50, "stage_seconds": 2, "region": "europe"}


@pytest.mark.parametrize(
    ("raw", "bad_field"),
    [
        ({"questions": 0}, "questions"),
        ({"questions": 51}, "questions"),
        ({"questions": 2.5}, "questions"),
        ({"questions": True}, "questions"),
        ({"questions": "ten"}, "questions"),
        ({"stage_seconds": 1}, "stage_seconds"),
        ({"stage_seconds": 16}, "stage_seconds"),
        ({"region": "atlantis"}, "region"),
        ({"region": 3}, "region"),
        ({"lives": 3}, "lives"),
    ],
)
def test_settings_rejects_invalid(raw: dict[str, object], bad_field: str) -> None:
    with pytest.raises(SettingsError) as info:
        validate_settings(SETTINGS_SCHEMA, raw)
    assert bad_field in info.value.errors


def test_settings_must_be_object() -> None:
    with pytest.raises(SettingsError):
        validate_settings(SETTINGS_SCHEMA, [1, 2])


def test_settings_key_is_order_independent() -> None:
    a = settings_key({"questions": 5, "region": "all", "stage_seconds": 5})
    b = settings_key({"stage_seconds": 5, "questions": 5, "region": "all"})
    assert a == b == "questions=5|region=all|stage_seconds=5"
