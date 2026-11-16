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
from dataclasses import dataclass
from typing import Any

from app.shared import scoring as _scoring
from app.shared import timed_choice as _tc
from app.shared.countries import REGIONS
from app.shared.settings import ChoiceSetting, IntSetting, SettingsSchema
# The question/round state machine and the drain scoring are shared with Currency Quest
# (app/shared/timed_choice.py, app/shared/scoring.py); re-exported here unchanged.
from app.shared.scoring import round_half_up
from app.shared.timed_choice import (  # noqa: F401  (re-exported)
    OptionUnavailable, PickOutcome, Question, QuestionActive, QuestionOver, Round, Status, TimeoutTooEarly,
    begin, pause, resume,
)

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

# --------------------------------------------------------------------------- scoring
# Thin wrappers binding the shared drain scoring to this game's CONFIG.
def available_points(elapsed_s: float, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> float:
    """Points before penalties: start_points at 0 s, falling linearly to 0 at the limit."""
    return _scoring.drain_available_points(elapsed_s, limit_s, cfg)


def potential_points(elapsed_s: float, wrong: int, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> int:
    """What a correct pick right now would score (the live meter's number)."""
    return _scoring.drain_potential_points(elapsed_s, wrong, limit_s, cfg)


def correct_points(elapsed_s: float, wrong: int, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> int:
    """max(min_correct_points, available - penalty x wrong), as a whole number."""
    return _scoring.drain_correct_points(elapsed_s, wrong, limit_s, cfg)


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
def new_round(round_id: str, settings: dict[str, Any], pool: Sequence[CapitalEntry], rng: random.Random,
              cfg: CapitalQuestConfig = CONFIG) -> Round:
    """Targets come from the region; distractors from the whole eligible pool."""
    targets = pick_targets(region_pool(pool, settings["region"]), settings["questions"], rng)
    n = int(settings["options"])
    questions = [Question(code=t.code, options=generate_options(t, pool, n, rng, cfg)) for t in targets]
    return Round(id=round_id, settings=settings, questions=questions, points_per_question=cfg.start_points)


# --------------------------------------------------------------------------- play clock and answering
# begin / pause / resume are re-exported from app.shared.timed_choice (see the imports).
# These wrappers bind the shared rules to this game's CONFIG.
def is_overdue(q: Question, now: float, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> bool:
    return _tc.is_overdue(q, now, limit_s, cfg)


def expire_if_overdue(q: Question, now: float, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> bool:
    """End an active question whose time (plus grace) is up with 0 points. Returns True if it did."""
    return _tc.expire_if_overdue(q, now, limit_s, cfg)


def apply_pick(q: Question, option_id: int, now: float, limit_s: float,
               cfg: CapitalQuestConfig = CONFIG) -> PickOutcome:
    return _tc.apply_pick(q, option_id, now, limit_s, cfg)


def apply_timeout(q: Question, now: float, limit_s: float, cfg: CapitalQuestConfig = CONFIG) -> bool:
    """The client's countdown ran out. Ends the question with 0 points (idempotent).
    Raises TimeoutTooEarly if the server's clock says there's clearly time left."""
    return _tc.apply_timeout(q, now, limit_s, cfg)


def advance(rnd: Round, from_index: int, now: float, cfg: CapitalQuestConfig = CONFIG) -> None:
    """Move past question ``from_index`` (idempotent; closes an overdue question as a timeout first)."""
    _tc.advance(rnd, from_index, now, cfg)


def scored_settings(settings: dict[str, Any], cfg: CapitalQuestConfig = CONFIG) -> dict[str, Any]:
    return {k: settings[k] for k in cfg.scored_settings}
