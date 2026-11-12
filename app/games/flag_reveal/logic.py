"""Flag Reveal game rules as pure, unit-testable functions.

No Flask, no I/O: randomness and token generation are injected.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.shared.countries import REGIONS, CountryIndex
from app.shared.scoring import points_for_stage
from app.shared.settings import ChoiceSetting, IntSetting, SettingsSchema


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class FlagRevealConfig:
    stage_points: tuple[int, ...] = (15, 10, 5)   # stage 1 (1/3 visible), 2 (2/3), 3 (full)
    stage_seconds: int = 5
    stage_seconds_range: tuple[int, int] = (2, 15)
    questions: int = 5
    questions_range: tuple[int, int] = (1, 50)
    feedback_ms: int = 1500                       # pause after a correct answer before moving on
    missed_feedback_ms: int = 2200
    horizontal_reveal_chance: float = 0.3
    options_shown: int = 8                        # multiple-choice answers per flag
    same_region_distractors: int = 4              # wrong options drawn from the answer's region first
    wrong_penalty: int = 5                        # per wrong pick; stage points floor at 0
    clock_grace_seconds: float = 0.75             # flag load time allowed on top of the server clock

    @property
    def stages(self) -> int:
        return len(self.stage_points)


CONFIG = FlagRevealConfig()

SETTINGS_SCHEMA: SettingsSchema = (
    IntSetting("questions", "Questions per round", CONFIG.questions, *CONFIG.questions_range,
               help="How many flags you'll see in one round."),
    IntSetting("stage_seconds", "Seconds per reveal stage", CONFIG.stage_seconds, *CONFIG.stage_seconds_range,
               help="Time before the next third of the flag is revealed.", unit="s"),
    ChoiceSetting("region", "Region", "all",
                  (("all", "Whole world"), *((r.lower(), r) for r in REGIONS)),
                  help="Only show flags from this part of the world."),
)

# Flags that are (near-)identical at a glance; naming either one counts.
LOOKALIKE_FLAGS: tuple[frozenset[str], ...] = (frozenset({"td", "ro"}), frozenset({"id", "mc"}))

Orientation = Literal["vertical", "horizontal"]
Status = Literal["active", "solved", "missed"]


# --------------------------------------------------------------------------- state
@dataclass(frozen=True)
class RevealPlan:
    orientation: Orientation
    order: tuple[int, int, int]   # slice indices (0,1,2) in reveal order

    def to_json(self) -> dict[str, Any]:
        return {"orientation": self.orientation, "order": list(self.order)}


@dataclass
class Question:
    code: str
    token: str
    reveal: RevealPlan
    options: list[str] = field(default_factory=list)   # country codes; an option's id is its index
    eliminated: set[int] = field(default_factory=set)
    stage: int = 1
    status: Status = "active"
    points: int = 0
    stage_points: int = 0
    speed_bonus: int = 0
    solved_stage: int | None = None
    wrong_guesses: int = 0
    served_at: float | None = None                    # server clock when the question became current


@dataclass
class Round:
    id: str
    settings: dict[str, Any]
    questions: list[Question]
    index: int = 0
    recorded: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def current(self) -> Question:
        return self.questions[self.index]

    @property
    def score(self) -> int:
        return sum(q.points for q in self.questions)

    @property
    def is_last(self) -> bool:
        return self.index == len(self.questions) - 1

    @property
    def finished(self) -> bool:
        return self.is_last and self.current.status != "active"

    @property
    def max_score(self) -> int:
        return max_score_for(len(self.questions), self.settings["stage_seconds"])


def max_score_for(questions: int, stage_seconds: int, config: FlagRevealConfig = CONFIG) -> int:
    """Best case per flag: first-stage points plus a full stage of speed bonus."""
    return questions * (max(config.stage_points) + stage_seconds)


# --------------------------------------------------------------------------- pure rules
def region_pool(index: CountryIndex, region: str) -> list[str]:
    """Country codes eligible for a region setting ('all' or lower-case region name)."""
    if region == "all":
        return [c.code for c in index.countries]
    return [c.code for c in index.countries if c.region.lower() == region]


def pick_questions(pool: Sequence[str], count: int, rng: random.Random) -> list[str]:
    """``count`` distinct codes from ``pool`` (never repeats within a round)."""
    unique = list(dict.fromkeys(pool))
    if count < 1:
        raise ValueError("A round needs at least one question.")
    if count > len(unique):
        raise ValueError(f"Only {len(unique)} countries are available for this region; "
                         f"choose {len(unique)} or fewer questions.")
    return rng.sample(unique, count)


def pick_reveal_plan(rng: random.Random, config: FlagRevealConfig = CONFIG) -> RevealPlan:
    orientation: Orientation = "horizontal" if rng.random() < config.horizontal_reveal_chance else "vertical"
    order = [0, 1, 2]
    rng.shuffle(order)
    return RevealPlan(orientation, (order[0], order[1], order[2]))


def pick_options(answer: str, pool: Sequence[str], index: CountryIndex, rng: random.Random,
                 config: FlagRevealConfig = CONFIG) -> list[str]:
    """The answer plus distinct wrong options, shuffled. Wrong options come from the
    answer's region first (harder), never include a lookalike flag, and stay within
    the round's pool so a regional round only offers that region's countries."""
    banned = {answer}.union(*(g for g in LOOKALIKE_FLAGS if answer in g))
    candidates = sorted(set(pool) - banned)
    need = min(config.options_shown - 1, len(candidates))
    region = index.by_code[answer].region
    near = [c for c in candidates if index.by_code[c].region == region]
    wrong = rng.sample(near, min(config.same_region_distractors, len(near), need))
    rest = [c for c in candidates if c not in wrong]
    wrong += rng.sample(rest, need - len(wrong))
    options = [answer, *wrong]
    rng.shuffle(options)
    return options


def new_round(round_id: str, settings: dict[str, Any], index: CountryIndex, rng: random.Random,
              make_token: Callable[[], str], now: float = 0.0) -> Round:
    pool = region_pool(index, settings["region"])
    codes = pick_questions(pool, settings["questions"], rng)
    questions = [Question(code=c, token=make_token(), reveal=pick_reveal_plan(rng),
                          options=pick_options(c, pool, index, rng)) for c in codes]
    questions[0].served_at = now
    return Round(id=round_id, settings=dict(settings), questions=questions)


def validate_stage(stage: Any, config: FlagRevealConfig = CONFIG) -> int:
    if isinstance(stage, bool) or not isinstance(stage, int) or not 1 <= stage <= config.stages:
        raise ValueError(f"stage must be an integer between 1 and {config.stages}")
    return stage


def is_correct(guess_code: str | None, answer_code: str) -> bool:
    if guess_code is None:
        return False
    if guess_code == answer_code:
        return True
    return any({guess_code, answer_code} <= group for group in LOOKALIKE_FLAGS)


def speed_bonus(stage: int, stage_seconds: int, client_remaining_ms: int, served_at: float | None, now: float,
                config: FlagRevealConfig = CONFIG) -> int:
    """Whole seconds left in the current stage (what the timer shows, rounded up).

    The client's timer (which pauses while the tab is hidden) is trusted only up
    to the server's own clock plus a small allowance for loading the flag."""
    remaining = min(client_remaining_ms / 1000, stage_seconds)
    if served_at is not None:
        server_remaining = stage * stage_seconds - (now - served_at) + config.clock_grace_seconds
        remaining = min(remaining, server_remaining)
    return max(0, min(stage_seconds, math.ceil(remaining)))


@dataclass(frozen=True)
class PickOutcome:
    correct: bool
    code: str
    stage: int
    points: int = 0
    stage_points: int = 0
    speed_bonus: int = 0


def apply_pick(q: Question, option_id: int, reported_stage: int, client_remaining_ms: int, stage_seconds: int,
               now: float, config: FlagRevealConfig = CONFIG) -> PickOutcome:
    """Score a multiple-choice pick. The stage only moves forward (a client can't
    claim an earlier stage than the server has seen). A wrong option is eliminated
    for good and costs ``wrong_penalty``; a correct pick scores stage points minus
    penalties (floored at 0) plus the speed bonus."""
    if q.status != "active":
        raise QuestionOver()
    stage = max(validate_stage(reported_stage, config), q.stage)
    if not 0 <= option_id < len(q.options) or option_id in q.eliminated:
        raise OptionUnavailable()
    q.stage = stage
    code = q.options[option_id]
    if is_correct(code, q.code):
        q.status, q.solved_stage = "solved", stage
        q.stage_points = max(0, points_for_stage(config.stage_points, stage) - q.wrong_guesses * config.wrong_penalty)
        q.speed_bonus = speed_bonus(stage, stage_seconds, client_remaining_ms, q.served_at, now, config)
        q.points = q.stage_points + q.speed_bonus
        return PickOutcome(True, code, stage, q.points, q.stage_points, q.speed_bonus)
    q.eliminated.add(option_id)
    q.wrong_guesses += 1
    return PickOutcome(False, code, stage)


@dataclass(frozen=True)
class TimeoutOutcome:
    stage: int
    missed: bool
    stale: bool            # duplicate/late timeout that changed nothing


def apply_timeout(q: Question, expired_stage: int, config: FlagRevealConfig = CONFIG) -> TimeoutOutcome:
    """The client's timer for ``expired_stage`` ran out. Idempotent for repeats."""
    expired_stage = validate_stage(expired_stage, config)
    if q.status != "active" or expired_stage < q.stage:
        return TimeoutOutcome(q.stage, q.status == "missed", stale=True)
    if expired_stage >= config.stages:
        q.stage, q.status, q.points = config.stages, "missed", 0
        return TimeoutOutcome(q.stage, True, stale=False)
    q.stage = expired_stage + 1
    return TimeoutOutcome(q.stage, False, stale=False)


def advance(rnd: Round, from_index: int, now: float = 0.0) -> bool:
    """Move to the next question. Returns True if it moved.

    Idempotent: a repeated call with an old ``from_index`` is a no-op, so a
    double-clicked "Next" (or Next + auto-advance) can't skip a question.
    """
    if from_index != rnd.index or rnd.is_last:
        return False
    if rnd.current.status == "active":
        raise QuestionActive()
    rnd.index += 1
    rnd.current.served_at = now
    return True


def stage_label(q: Question) -> str:
    wrong = [f"{q.wrong_guesses} wrong"] if q.wrong_guesses else []
    if q.status != "solved":
        return " · ".join(["Missed", *wrong])
    speed = [f"+{q.speed_bonus} speed"] if q.speed_bonus else []
    return " · ".join([f"Stage {q.solved_stage}", *wrong, *speed])


class QuestionOver(Exception):
    pass


class QuestionActive(Exception):
    pass


class OptionUnavailable(Exception):
    pass
