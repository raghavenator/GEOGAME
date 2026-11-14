"""Capital Quest rules. Pure functions and plain dataclasses, no Flask.

A country is highlighted on the map and the player picks its capital from 4, 6 or 8
options. Points drain linearly from ``start_points`` to 0 over the question's time
limit, each wrong pick costs ``wrong_penalty``, and a correct answer is worth at least
``min_correct_points``. Time is always the server's clock, counted from ``begin`` (the
player's tap) with paused time shifted out. Tested in tests/test_capital_logic.py.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.shared.countries import REGIONS
from app.shared.settings import ChoiceSetting, IntSetting, SettingsSchema

from .capital_data import CapitalEntry, name_key


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class CapitalQuestConfig:
    start_points: int = 20                  # available points when the question appears
    wrong_penalty: int = 3                  # taken off the available points per wrong pick
    min_correct_points: int = 2             # a correct answer before the timeout is never worth less
    time_limit_seconds: int = 20            # points drain to 0 over this many seconds
    time_limit_range: tuple[int, int] = (10, 60)
    questions: int = 5
    questions_range: tuple[int, int] = (1, 30)
    option_counts: tuple[int, ...] = (4, 6, 8)
    default_options: int = 8
    result_ms: int = 1500                   # how long the client shows a result before moving on
    # The client posts its pick a moment after it sees the clock; this much network time
    # after the limit still counts as in time (worth min_correct_points if correct).
    late_pick_grace_seconds: float = 0.5
    # A timeout the client sends this much earlier than the server's clock is accepted
    # (the two clocks drift a little); anything earlier is rejected as too early.
    early_timeout_tolerance_seconds: float = 1.0
    # Settings that change the challenge. Map view is cosmetic and not part of the high-score key.
    scored_settings: tuple[str, ...] = ("questions", "seconds", "options", "framing", "region")


CONFIG = CapitalQuestConfig()

SETTINGS_SCHEMA: SettingsSchema = (
    IntSetting("questions", "Questions per round", CONFIG.questions, *CONFIG.questions_range,
               help="How many countries you'll see in one round."),
    IntSetting("seconds", "Time per question", CONFIG.time_limit_seconds, *CONFIG.time_limit_range,
               help="Points drain to zero over this time.", unit="s"),
    ChoiceSetting("options", "Number of options", str(CONFIG.default_options),
                  tuple((str(n), f"{n} options") for n in CONFIG.option_counts),
                  help="Fewer options is easier."),
    ChoiceSetting("view", "Map view", "flat", (("flat", "Flat map"), ("globe", "Globe")),
                  help="Drag, pinch or scroll to move around either view."),
    ChoiceSetting("framing", "Map framing", "region",
                  (("world", "World view"), ("region", "Zoom to region (easier)")),
                  help="World view shows the whole map; zoom to region frames the country and its neighbours."),
    ChoiceSetting("region", "Region", "all",
                  (("all", "Whole world"), *((r.lower(), r) for r in REGIONS)),
                  help="Only ask about countries from this part of the world."),
)

Status = Literal["active", "correct", "timeout"]


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


# --------------------------------------------------------------------------- scoring
def round_half_up(x: float) -> int:
    """Whole points, rounding .5 up (Python's round() would send 2.5 to 2)."""
    return math.floor(x + 0.5)


def available_points(elapsed_s: float, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> float:
    """Points before penalties: start_points at 0 s, falling linearly to 0 at the limit."""
    if limit_s <= 0:
        return 0.0
    return cfg.start_points * min(1.0, max(0.0, 1.0 - elapsed_s / limit_s))


def potential_points(elapsed_s: float, wrong: int, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> int:
    """What a correct pick right now would score (the live meter's number)."""
    if elapsed_s >= limit_s:
        return 0
    return correct_points(elapsed_s, wrong, limit_s, cfg)


def correct_points(elapsed_s: float, wrong: int, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> int:
    """max(min_correct_points, available - penalty x wrong), as a whole number."""
    raw = available_points(elapsed_s, limit_s, cfg) - cfg.wrong_penalty * wrong
    return max(cfg.min_correct_points, round_half_up(raw))


def max_score_for(questions: int, cfg: CapitalQuestConfig = CONFIG) -> int:
    return questions * cfg.start_points


# --------------------------------------------------------------------------- pools and options
def region_pool(pool: Sequence[CapitalEntry], region: str) -> list[CapitalEntry]:
    """Eligible targets for a region setting ('all' or a lower-case continent)."""
    return [e for e in pool if region == "all" or e.continent.lower() == region]


def pick_targets(pool: Sequence[CapitalEntry], count: int, rng: random.Random) -> list[CapitalEntry]:
    """``count`` distinct countries (never repeats within a round)."""
    unique = list({e.code: e for e in pool}.values())
    if count < 1:
        raise ValueError("A round needs at least one question.")
    if count > len(unique):
        raise ValueError(f"Only {len(unique)} countries are available for this region; "
                         f"choose {len(unique)} or fewer questions.")
    return rng.sample(unique, count)


def generate_options(target: CapitalEntry, pool: Sequence[CapitalEntry], count: int, rng: random.Random,
                     cfg: CapitalQuestConfig = CONFIG) -> list[str]:
    """Country codes whose capitals are offered: the target plus ``count - 1`` distractors.

    About half the distractors (rounded up) come from the target's subregion, topped up from
    the rest of its continent; the others come from other continents. Capital names are
    unique (case and accents ignored) and never equal the answer. The order is shuffled.
    """
    if count not in cfg.option_counts:
        raise ValueError(f"options must be one of {cfg.option_counts}")
    want = count - 1
    used = {name_key(target.capital)}
    others = [e for e in pool if e.code != target.code]

    def take(group: Sequence[CapitalEntry], n: int) -> list[str]:
        group = list(group)
        rng.shuffle(group)
        out: list[str] = []
        for e in group:
            if len(out) >= n:
                break
            key = name_key(e.capital)
            if key not in used:
                used.add(key)
                out.append(e.code)
        return out

    near_n = math.ceil(want / 2)
    near = take([e for e in others if e.subregion == target.subregion], near_n)
    near += take([e for e in others if e.continent == target.continent and e.subregion != target.subregion],
                 near_n - len(near))
    far = take([e for e in others if e.continent != target.continent], want - len(near))
    chosen = near + far
    if len(chosen) < want:  # tiny pools only (tests); never in the real data
        chosen += take([e for e in others if e.code not in chosen], want - len(chosen))
    if len(chosen) < want:
        raise ValueError("Not enough distinct capitals to build the options.")
    options = [target.code, *chosen]
    rng.shuffle(options)
    return options


# --------------------------------------------------------------------------- state
@dataclass
class Question:
    code: str
    options: list[str]                       # country codes; an option's id is its index
    eliminated: set[int] = field(default_factory=set)
    wrong_picks: list[int] = field(default_factory=list)
    status: Status = "active"
    started_at: float | None = None          # server clock when the question was shown (after begin)
    ended_at: float | None = None
    points: int = 0

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
    settings: dict[str, Any]
    questions: list[Question]
    index: int = 0
    finished: bool = False
    recorded: bool = False
    begun: bool = False                      # set by begin(): the player tapped play
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


def new_round(round_id: str, settings: dict[str, Any], pool: Sequence[CapitalEntry], rng: random.Random,
              cfg: CapitalQuestConfig = CONFIG) -> Round:
    """Targets come from the region; distractors from the whole eligible pool."""
    targets = pick_targets(region_pool(pool, settings["region"]), settings["questions"], rng)
    n = int(settings["options"])
    questions = [Question(code=t.code, options=generate_options(t, pool, n, rng, cfg)) for t in targets]
    return Round(id=round_id, settings=settings, questions=questions)


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


def is_overdue(q: Question, now: float, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> bool:
    return q.status == "active" and q.elapsed(now) > limit_s + cfg.late_pick_grace_seconds


def expire_if_overdue(q: Question, now: float, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> bool:
    """End an active question whose time (plus grace) is up with 0 points. Returns True if it did."""
    if not is_overdue(q, now, limit_s, cfg):
        return False
    _finish(q, "timeout", q.started_at + limit_s if q.started_at is not None else now, 0)  # type: ignore[operator]
    return True


def apply_pick(q: Question, option_id: int, now: float, limit_s: float,
               cfg: CapitalQuestConfig = CONFIG) -> PickOutcome:
    if q.status != "active":
        raise QuestionOver
    if not 0 <= option_id < len(q.options) or option_id in q.eliminated:
        raise OptionUnavailable
    if expire_if_overdue(q, now, limit_s, cfg):
        return PickOutcome("timeout", option_id, 0, 0)
    elapsed = q.elapsed(now)
    if option_id == q.answer_option:
        points = correct_points(elapsed, q.wrong, limit_s, cfg)
        _finish(q, "correct", now, points)
        return PickOutcome("correct", option_id, points, points)
    q.eliminated.add(option_id)
    q.wrong_picks.append(option_id)
    return PickOutcome("wrong", option_id, 0, potential_points(elapsed, q.wrong, limit_s, cfg))


def apply_timeout(q: Question, now: float, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> bool:
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


def advance(rnd: Round, from_index: int, now: float, cfg: CapitalQuestConfig = CONFIG) -> None:
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


def scored_settings(settings: dict[str, Any], cfg: CapitalQuestConfig = CONFIG) -> dict[str, Any]:
    return {k: settings[k] for k in cfg.scored_settings}
