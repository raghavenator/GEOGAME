from __future__ import annotations

import itertools
import random

import pytest

from app.games.flag_reveal import logic
from app.games.flag_reveal.logic import CONFIG, SETTINGS_SCHEMA, Question, RevealPlan
from app.shared.countries import get_index
from app.shared.scoring import max_score, points_for_stage
from app.shared.settings import SettingsError, settings_key, validate_settings


def make_question(code: str = "fr") -> Question:
    return Question(code=code, token="t", reveal=RevealPlan("vertical", (0, 1, 2)))


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
    assert make_round(7).max_score == 105


@pytest.mark.parametrize(("stage", "points"), [(1, 15), (2, 10), (3, 5)])
def test_correct_guess_awards_stage_points(stage: int, points: int) -> None:
    q = make_question()
    out = logic.apply_guess(q, "fr", stage)
    assert out.correct and out.points == points
    assert q.status == "solved" and q.solved_stage == stage and q.points == points


def test_wrong_guess_keeps_question_open() -> None:
    q = make_question()
    out = logic.apply_guess(q, "de", 1)
    assert not out.correct and out.recognised
    assert q.status == "active" and q.wrong_guesses == 1
    out = logic.apply_guess(q, None, 1)
    assert not out.recognised
    assert logic.apply_guess(q, "fr", 1).points == 15


def test_client_cannot_claim_an_earlier_stage() -> None:
    q = make_question()
    logic.apply_timeout(q, 1)
    logic.apply_timeout(q, 2)
    assert q.stage == 3
    assert logic.apply_guess(q, "fr", 1).points == 5


def test_guess_after_question_ended_raises() -> None:
    q = make_question()
    logic.apply_guess(q, "fr", 1)
    with pytest.raises(logic.QuestionOver):
        logic.apply_guess(q, "fr", 1)


@pytest.mark.parametrize("stage", [0, 4, "1", 1.0, True, None])
def test_invalid_stage_rejected(stage: object) -> None:
    with pytest.raises(ValueError):
        logic.apply_guess(make_question(), "fr", stage)  # type: ignore[arg-type]


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
    logic.apply_guess(q, "fr", 1)
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
    logic.apply_guess(rnd.current, rnd.current.code, 1)
    assert logic.advance(rnd, 0) is True
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
