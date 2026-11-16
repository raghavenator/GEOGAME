"""Currency Quest rules. Pure functions and plain dataclasses, no Flask.

A country is shown (name and flag) and the player picks the currency it uses from 4, 6 or
8 options. Options are currencies, never countries: every option is a distinct ISO code,
exactly one is right (the country's primary currency), and none of the country's other
currencies is ever offered as a distractor. The scoring and the question/round state
machine are Capital Quest's, shared through app/shared/scoring.py and
app/shared/timed_choice.py. Tested in tests/test_currency_logic.py.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.shared import scoring as _scoring
from app.shared import timed_choice as _tc
from app.shared.countries import REGIONS
from app.shared.settings import ChoiceSetting, IntSetting, SettingsSchema
from app.shared.timed_choice import (  # noqa: F401  (re-exported for routes and tests)
    OptionUnavailable, PickOutcome, Question, QuestionActive, QuestionOver, Round, TimeoutTooEarly,
    begin, pause, resume,
)

from .currency_data import CountryCurrencies, CurrencyData, name_key


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class CurrencyQuestConfig:
    start_points: int = 20                  # available points when the question appears
    wrong_penalty: int = 3                  # taken off the available points per wrong pick
    min_correct_points: int = 2             # a correct answer before the timeout is never worth less
    time_limit_seconds: int = 20            # points drain to 0 over this many seconds
    time_limit_range: tuple[int, int] = (10, 60)
    questions: int = 5
    questions_range: tuple[int, int] = (1, 30)
    option_counts: tuple[int, ...] = (4, 6, 8)
    default_options: int = 8
    result_ms: int = 2500                   # the result stays up this long before the next question
    late_pick_grace_seconds: float = 0.5    # see app/shared/timed_choice.py
    early_timeout_tolerance_seconds: float = 1.0
    also_used_in_max: int = 6               # names listed in "Also used in: ...", then "+N more"
    # Settings that change the challenge. The map locator is a visual aid and not part of the key.
    scored_settings: tuple[str, ...] = ("questions", "seconds", "options", "difficulty", "region")


CONFIG = CurrencyQuestConfig()

DIFFICULTIES = (("easy", "Easy"), ("normal", "Normal"), ("hard", "Hard"))

SETTINGS_SCHEMA: SettingsSchema = (
    IntSetting("questions", "Questions per round", CONFIG.questions, *CONFIG.questions_range,
               help="How many countries you'll see in one round. A currency never repeats in a round."),
    IntSetting("seconds", "Time per question", CONFIG.time_limit_seconds, *CONFIG.time_limit_range,
               help="Points drain to zero over this time.", unit="s"),
    ChoiceSetting("options", "Number of options", str(CONFIG.default_options),
                  tuple((str(n), f"{n} options") for n in CONFIG.option_counts),
                  help="Fewer options is easier."),
    ChoiceSetting("difficulty", "Difficulty", "normal", DIFFICULTIES,
                  help="Easy: wrong options from anywhere. Normal: about half from the same region. "
                       "Hard: look-alikes (the same symbol, or another dollar or franc) and neighbours."),
    ChoiceSetting("locator", "Show map locator", "on", (("on", "On"), ("off", "Off")),
                  help="A small globe with the country highlighted, for when you don't know where it is."),
    ChoiceSetting("region", "Region", "all",
                  (("all", "Whole world"), *((r.lower(), r) for r in REGIONS)),
                  help="Only ask about countries from this part of the world."),
)


# --------------------------------------------------------------------------- scoring
# Capital Quest's scoring, from app/shared/scoring.py, bound to this game's CONFIG.
def available_points(elapsed_s: float, limit_s: float, cfg: CurrencyQuestConfig = CONFIG) -> float:
    return _scoring.drain_available_points(elapsed_s, limit_s, cfg)


def potential_points(elapsed_s: float, wrong: int, limit_s: float, cfg: CurrencyQuestConfig = CONFIG) -> int:
    return _scoring.drain_potential_points(elapsed_s, wrong, limit_s, cfg)


def correct_points(elapsed_s: float, wrong: int, limit_s: float, cfg: CurrencyQuestConfig = CONFIG) -> int:
    return _scoring.drain_correct_points(elapsed_s, wrong, limit_s, cfg)


def max_score_for(questions: int, cfg: CurrencyQuestConfig = CONFIG) -> int:
    return questions * cfg.start_points


# --------------------------------------------------------------------------- pools
def region_pool(pool: Sequence[CountryCurrencies], region: str) -> list[CountryCurrencies]:
    """Eligible targets for a region setting ('all' or a lower-case continent)."""
    return [e for e in pool if region == "all" or e.continent.lower() == region]


def distinct_currencies(pool: Iterable[CountryCurrencies]) -> int:
    return len({e.primary for e in pool})


def pick_targets(pool: Sequence[CountryCurrencies], count: int, rng: random.Random) -> list[CountryCurrencies]:
    """``count`` countries, none repeated, and no two with the same primary currency.

    Countries are drawn uniformly at random; a country whose currency is already in the
    round is skipped (so the euro can come up once per round, not five times)."""
    if count < 1:
        raise ValueError("A round needs at least one question.")
    available = distinct_currencies(pool)
    if count > available:
        raise ValueError(f"Only {available} different currencies are available for this region; "
                         f"choose {available} or fewer questions.")
    order = list({e.code: e for e in pool}.values())
    rng.shuffle(order)
    out: list[CountryCurrencies] = []
    used: set[str] = set()
    for e in order:
        if e.primary not in used:
            used.add(e.primary)
            out.append(e)
            if len(out) == count:
                break
    return out


# --------------------------------------------------------------------------- options
def _regional(data: CurrencyData, codes_in: Callable[[CountryCurrencies], bool]) -> list[str]:
    """Currencies that are the primary currency of some country matching ``codes_in``."""
    return sorted({e.primary for e in data.countries if codes_in(e)})


def generate_options(target: CountryCurrencies, data: CurrencyData, count: int, difficulty: str,
                     rng: random.Random, cfg: CurrencyQuestConfig = CONFIG) -> list[str]:
    """ISO codes offered: the target's primary currency plus ``count - 1`` distractors, shuffled.

    Distractors are other countries' primary currencies, all distinct, never any currency in
    the target's correct set (``target.currencies``), and never named like an option already
    chosen. By difficulty:
      easy    random currencies from anywhere
      normal  about half (rounded up) from the target's subregion, topped up from its
              continent; the rest random
      hard    about half look-alikes (the same symbol, like "$" or "Fr", or the same name
              family: another dollar, franc, dinar...), nearest first; the rest from the subregion, then the continent; random only
              if the region runs out
    """
    if count not in cfg.option_counts:
        raise ValueError(f"options must be one of {cfg.option_counts}")
    if difficulty not in {d for d, _ in DIFFICULTIES}:
        raise ValueError(f"unknown difficulty {difficulty!r}")
    info = data.currencies
    want = count - 1
    banned = set(target.currencies)
    used_names = {name_key(info[target.primary].name)}
    chosen: list[str] = []

    def take(group: Iterable[str], n: int) -> None:
        group = [c for c in group if c not in banned and c not in chosen]
        rng.shuffle(group)
        added = 0
        for c in group:
            if added >= n:
                break
            key = name_key(info[c].name)
            if key in used_names:
                continue
            used_names.add(key)
            chosen.append(c)
            added += 1

    anywhere = _regional(data, lambda e: True)
    subregion = _regional(data, lambda e: e.subregion == target.subregion)
    continent = _regional(data, lambda e: e.continent == target.continent)
    half = math.ceil(want / 2)
    if difficulty == "normal":
        take(subregion, half)
        take(continent, half - len(chosen))
    elif difficulty == "hard":
        ans = info[target.primary]
        looks = [c for c in anywhere if info[c].family == ans.family or (ans.symbol and info[c].symbol == ans.symbol)]
        take([c for c in looks if c in continent], half)
        take(looks, half - len(chosen))
        take(subregion, want - len(chosen))
        take(continent, want - len(chosen))
    take(anywhere, want - len(chosen))
    if len(chosen) < want:
        raise ValueError("Not enough distinct currencies to build the options.")
    options = [target.primary, *chosen]
    rng.shuffle(options)
    return options


def option_labels(codes: Sequence[str], data: CurrencyData) -> list[dict[str, Any]]:
    """What the player sees per option while the question runs: the ISO code and the symbol.

    Never the name: "Yemeni rial" would give the answer away. Codes are unique, so two
    options never read the same. Names are revealed once the question ends."""
    return [{"code": c, "symbol": data.currencies[c].symbol or None} for c in codes]


def also_used_in(currency: str, exclude: str, data: CurrencyData,
                 limit: int = CONFIG.also_used_in_max) -> tuple[list[str], int]:
    """Other countries using a currency (those whose primary it is first, then the rest),
    by display name: (up to ``limit`` names, how many more)."""
    cur = data.currencies[currency]
    by_code = data.by_code
    primary = [c for c in cur.primary_for if c != exclude]
    secondary = [c for c in cur.countries if c != exclude and c not in cur.primary_for]
    names = [by_code[c].name for c in primary] + [by_code[c].name for c in secondary]
    return names[:limit], max(0, len(names) - limit)


# --------------------------------------------------------------------------- rounds
def new_round(round_id: str, settings: dict[str, Any], data: CurrencyData, rng: random.Random,
              cfg: CurrencyQuestConfig = CONFIG, pool: Sequence[CountryCurrencies] | None = None) -> Round:
    """Targets come from the region (``pool`` overrides the eligible countries, for tests);
    distractors from every eligible country's currency."""
    targets = pick_targets(region_pool(pool if pool is not None else data.countries, settings["region"]),
                           settings["questions"], rng)
    n = int(settings["options"])
    questions = [Question(code=t.primary, options=generate_options(t, data, n, settings["difficulty"], rng, cfg),
                          extra={"country": t.code})
                 for t in targets]
    return Round(id=round_id, settings=settings, questions=questions, points_per_question=cfg.start_points)


# --------------------------------------------------------------------------- play clock and answering
# begin / pause / resume come from app.shared.timed_choice (see the imports).
def is_overdue(q: Question, now: float, limit_s: float, cfg: CurrencyQuestConfig = CONFIG) -> bool:
    return _tc.is_overdue(q, now, limit_s, cfg)


def expire_if_overdue(q: Question, now: float, limit_s: float, cfg: CurrencyQuestConfig = CONFIG) -> bool:
    return _tc.expire_if_overdue(q, now, limit_s, cfg)


def apply_pick(q: Question, option_id: int, now: float, limit_s: float,
               cfg: CurrencyQuestConfig = CONFIG) -> PickOutcome:
    return _tc.apply_pick(q, option_id, now, limit_s, cfg)


def apply_timeout(q: Question, now: float, limit_s: float, cfg: CurrencyQuestConfig = CONFIG) -> bool:
    return _tc.apply_timeout(q, now, limit_s, cfg)


def advance(rnd: Round, from_index: int, now: float, cfg: CurrencyQuestConfig = CONFIG) -> None:
    _tc.advance(rnd, from_index, now, cfg)


def scored_settings(settings: dict[str, Any], cfg: CurrencyQuestConfig = CONFIG) -> dict[str, Any]:
    return {k: settings[k] for k in cfg.scored_settings}
