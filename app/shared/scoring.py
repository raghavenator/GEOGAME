"""Generic scoring helpers reusable across games."""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol


def points_for_stage(stage_points: Sequence[int], stage: int) -> int:
    """Points for a correct answer at 1-based ``stage``."""
    if not 1 <= stage <= len(stage_points):
        raise ValueError(f"stage must be between 1 and {len(stage_points)}")
    return stage_points[stage - 1]


def max_score(question_count: int, stage_points: Sequence[int]) -> int:
    return question_count * max(stage_points)


def percent(score: int, maximum: int) -> int:
    return round(100 * score / maximum) if maximum else 0


# --------------------------------------------------------------------------- draining points
# "Points drain while you think": a question starts at ``start_points``, drains linearly to 0
# over the time limit, each wrong pick costs ``wrong_penalty`` and a correct answer before the
# limit is worth at least ``min_correct_points``. Used by Capital Quest and Currency Quest;
# ``cfg`` is any object with those three attributes (each game's config dataclass).
class DrainScoring(Protocol):
    start_points: int
    wrong_penalty: int
    min_correct_points: int


def round_half_up(x: float) -> int:
    """Whole points, rounding .5 up (Python's round() would send 2.5 to 2)."""
    return math.floor(x + 0.5)


def drain_available_points(elapsed_s: float, limit_s: float, cfg: DrainScoring) -> float:
    """Points before penalties: start_points at 0 s, falling linearly to 0 at the limit."""
    if limit_s <= 0:
        return 0.0
    return cfg.start_points * min(1.0, max(0.0, 1.0 - elapsed_s / limit_s))


def drain_correct_points(elapsed_s: float, wrong: int, limit_s: float, cfg: DrainScoring) -> int:
    """max(min_correct_points, available - penalty x wrong), as a whole number."""
    raw = drain_available_points(elapsed_s, limit_s, cfg) - cfg.wrong_penalty * wrong
    return max(cfg.min_correct_points, round_half_up(raw))


def drain_potential_points(elapsed_s: float, wrong: int, limit_s: float, cfg: DrainScoring) -> int:
    """What a correct pick right now would score (the live meter's number); 0 once time is up."""
    if elapsed_s >= limit_s:
        return 0
    return drain_correct_points(elapsed_s, wrong, limit_s, cfg)
