"""Timed multiple choice with options that disappear when wrong.

The state machine shared by Capital Quest and Currency Quest (pure functions and plain
dataclasses, no Flask). A question offers a list of option keys; exactly one is right.
A wrong pick eliminates that option for the rest of the question and the clock keeps
running. Points drain while you think (``app.shared.scoring.drain_*``). Time is always the
server's clock, counted from ``begin`` (the player's tap) with paused time shifted out.

``cfg`` is the game's config dataclass. It needs the DrainScoring fields plus
``late_pick_grace_seconds`` and ``early_timeout_tolerance_seconds``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .scoring import DrainScoring, drain_correct_points, drain_potential_points

Status = Literal["active", "correct", "timeout"]


class TimedChoiceConfig(DrainScoring, Protocol):
    # The client posts its pick a moment after it sees the clock; this much network time
    # after the limit still counts as in time (worth min_correct_points if correct).
    late_pick_grace_seconds: float
    # A timeout the client sends this much earlier than the server's clock is accepted
    # (the two clocks drift a little); anything earlier is rejected as too early.
    early_timeout_tolerance_seconds: float


class QuestionOver(Exception):
    """The question has already ended (answered or timed out)."""


class QuestionActive(Exception):
    """Can't move on while the question is still being played."""


class OptionUnavailable(Exception):
    """The picked option doesn't exist or was already eliminated."""


class TimeoutTooEarly(Exception):
    def __init__(self, remaining_s: float) -> None:
        super().__init__(f"{remaining_s:.2f}s left")
        self.remaining_s = remaining_s


# --------------------------------------------------------------------------- state
@dataclass
class Question:
    code: str                                # the correct option's key
    options: list[str]                       # option keys; an option's id is its index
    eliminated: set[int] = field(default_factory=set)
    wrong_picks: list[int] = field(default_factory=list)
    status: Status = "active"
    started_at: float | None = None          # server clock when the question was shown (after begin)
    ended_at: float | None = None
    points: int = 0
    extra: dict[str, Any] = field(default_factory=dict)   # game-specific data (e.g. the country asked about)

    @property
    def answer_option(self) -> int:
        return self.options.index(self.code)

    @property
    def wrong(self) -> int:
        return len(self.wrong_picks)

    def live_options(self) -> list[int]:
        return [i for i in range(len(self.options)) if i not in self.eliminated]

    def elapsed(self, now: float) -> float:
        if self.started_at is None:
            return 0.0
        return max(0.0, (self.ended_at if self.ended_at is not None else now) - self.started_at)


@dataclass
class Round:
    id: str
    settings: dict[str, Any]                 # must include "seconds" (the time limit)
    questions: list[Question]
    index: int = 0
    finished: bool = False
    recorded: bool = False
    begun: bool = False                      # set by begin(): the player tapped play
    paused_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    points_per_question: int = 20            # the round maximum is questions x this

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
        return len(self.questions) * self.points_per_question


# --------------------------------------------------------------------------- play clock
# The feed prepares rounds long before they're played, so a question's time starts at
# begin() (the tap), and pause()/resume() shift paused time out of the active question.
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
@dataclass(frozen=True)
class PickOutcome:
    kind: Literal["correct", "wrong", "timeout"]
    option_id: int
    points: int        # awarded (correct/timeout) or 0 (wrong)
    potential: int     # what a correct pick would score now (after this pick)


def _finish(q: Question, status: Status, now: float, points: int) -> None:
    q.status = status
    q.ended_at = now
    q.points = points


def is_overdue(q: Question, now: float, limit_s: float, cfg: TimedChoiceConfig) -> bool:
    return q.status == "active" and q.elapsed(now) > limit_s + cfg.late_pick_grace_seconds


def expire_if_overdue(q: Question, now: float, limit_s: float, cfg: TimedChoiceConfig) -> bool:
    """End an active question whose time (plus grace) is up with 0 points. Returns True if it did."""
    if not is_overdue(q, now, limit_s, cfg):
        return False
    _finish(q, "timeout", q.started_at + limit_s if q.started_at is not None else now, 0)
    return True


def apply_pick(q: Question, option_id: int, now: float, limit_s: float, cfg: TimedChoiceConfig) -> PickOutcome:
    if q.status != "active":
        raise QuestionOver
    if not 0 <= option_id < len(q.options) or option_id in q.eliminated:
        raise OptionUnavailable
    if expire_if_overdue(q, now, limit_s, cfg):
        return PickOutcome("timeout", option_id, 0, 0)
    elapsed = q.elapsed(now)
    if option_id == q.answer_option:
        points = drain_correct_points(elapsed, q.wrong, limit_s, cfg)
        _finish(q, "correct", now, points)
        return PickOutcome("correct", option_id, points, points)
    q.eliminated.add(option_id)
    q.wrong_picks.append(option_id)
    return PickOutcome("wrong", option_id, 0, drain_potential_points(elapsed, q.wrong, limit_s, cfg))


def apply_timeout(q: Question, now: float, limit_s: float, cfg: TimedChoiceConfig) -> bool:
    """The client's countdown ran out. Ends the question with 0 points.

    Idempotent: returns False if the question had already ended (a double-fired timer).
    Raises TimeoutTooEarly if the server's clock says there's clearly time left.
    """
    if q.status != "active":
        return False
    remaining = limit_s - q.elapsed(now)
    if remaining > cfg.early_timeout_tolerance_seconds:
        raise TimeoutTooEarly(remaining)
    _finish(q, "timeout", now, 0)
    return True


def advance(rnd: Round, from_index: int, now: float, cfg: TimedChoiceConfig) -> None:
    """Move past question ``from_index``. Idempotent: repeats for an old index are no-ops.
    A question whose time ran out on the server is closed as a timeout first."""
    if rnd.finished or from_index != rnd.index:
        return
    expire_if_overdue(rnd.current, now, rnd.limit, cfg)
    if rnd.current.status == "active":
        raise QuestionActive
    if rnd.is_last:
        rnd.finished = True
        return
    rnd.index += 1
    rnd.current.started_at = now if rnd.paused_at is None else rnd.paused_at
