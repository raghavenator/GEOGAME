"""Generic scoring helpers reusable across games."""
from __future__ import annotations

from collections.abc import Sequence


def points_for_stage(stage_points: Sequence[int], stage: int) -> int:
    """Points for a correct answer at 1-based ``stage``."""
    if not 1 <= stage <= len(stage_points):
        raise ValueError(f"stage must be between 1 and {len(stage_points)}")
    return stage_points[stage - 1]


def max_score(question_count: int, stage_points: Sequence[int]) -> int:
    return question_count * max(stage_points)


def percent(score: int, maximum: int) -> int:
    return round(100 * score / maximum) if maximum else 0
