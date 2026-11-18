"""Pinpoint's selectable regions: who is in them and where the camera starts.

Everything lives in ``regions.json`` (edit that file, not this one; see the README,
"How to add or edit a Pinpoint region"). Each region has:

* ``id``      the settings value, part of the best-score key, so never rename one that
              has shipped (the original ids: all, africa, americas, asia, europe, oceania)
* ``label``   shown in the settings menu and the region chip
* ``group``   one of the file's ``groups`` (World / Continents / Sub-regions)
* ``members`` ISO alpha-2 codes from the eligible pool (locations.json), or "all"
* ``box``     the start framing ``[west, south, east, north]`` in degrees. Both the flat
              map and the globe are fitted to it with ``padding`` (a share of the map zone
              kept clear on each side). West > east means the box crosses the antimeridian.
              A box spanning 300° of longitude or more is "the whole world" and keeps the
              world start camera. Boxes are hand-tuned, not derived from the members' shapes
              (Russia, France and the United States have far-flung parts).
* ``framing_exempt`` optional ``{code: why}``: members whose reference point may be outside
              the start framing (each reason is shown in the README and checked by tests).

Pure functions, no Flask; tested in tests/test_pinpoint_regions.py.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

REGIONS_FILE = Path(__file__).parent / "regions.json"
WORLD_SPAN_DEG = 300.0


class RegionConfigError(ValueError):
    """regions.json is malformed or disagrees with the eligible pool."""


@dataclass(frozen=True)
class Region:
    id: str
    label: str
    group: str
    box: tuple[float, float, float, float]       # west, south, east, north
    members: tuple[str, ...] | None              # None = every eligible country
    framing_exempt: Mapping[str, str] = field(default_factory=dict)

    @property
    def lon_span(self) -> float:
        w, _, e, _ = self.box
        return e - w if e > w else e - w + 360.0

    @property
    def is_world(self) -> bool:
        return self.members is None

    def pool(self, pool: Iterable[Any]) -> list[Any]:
        """The members of ``pool`` (anything with a ``.code``) in this region, in pool order."""
        if self.members is None:
            return list(pool)
        wanted = set(self.members)
        return [e for e in pool if e.code in wanted]

    def to_client(self) -> dict[str, Any]:
        return {"label": self.label, "box": list(self.box), "world": self.lon_span >= WORLD_SPAN_DEG}


@dataclass(frozen=True)
class RegionConfig:
    padding: float
    default: str
    groups: tuple[tuple[str, str], ...]          # (id, label), in display order
    regions: tuple[Region, ...]                  # in display order

    def get(self, region_id: str) -> Region:
        for r in self.regions:
            if r.id == region_id:
                return r
        raise KeyError(region_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.regions)

    def resolve(self, region_id: Any) -> str:
        """A stored value that's no longer (or never was) a region falls back to the default (World)."""
        return region_id if isinstance(region_id, str) and region_id in self.ids() else self.default


def _box(raw: Any, rid: str) -> tuple[float, float, float, float]:
    if (not isinstance(raw, list) or len(raw) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw)):
        raise RegionConfigError(f"{rid}: box must be [west, south, east, north] numbers.")
    w, s, e, n = (float(v) for v in raw)
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s < n <= 90):
        raise RegionConfigError(f"{rid}: box {raw} is out of range or has south >= north.")
    if w == e:
        raise RegionConfigError(f"{rid}: box {raw} has no width.")
    return w, s, e, n


def parse_regions(raw: Mapping[str, Any]) -> RegionConfig:
    """Parse and check the file's own consistency (not its members against the pool)."""
    padding = raw.get("padding")
    if not isinstance(padding, (int, float)) or not 0 <= padding < 0.5:
        raise RegionConfigError("padding must be a number in [0, 0.5).")
    groups = tuple((g["id"], g["label"]) for g in raw.get("groups", []))
    group_ids = {g for g, _ in groups}
    regions: list[Region] = []
    for r in raw.get("regions", []):
        rid = r.get("id")
        if not isinstance(rid, str) or not rid or any(c in rid for c in "|= "):
            raise RegionConfigError(f"Bad region id {rid!r} (it's part of the best-score key).")
        if r.get("group") not in group_ids:
            raise RegionConfigError(f"{rid}: unknown group {r.get('group')!r}.")
        members = r.get("members")
        if members == "all":
            parsed: tuple[str, ...] | None = None
        elif isinstance(members, list) and members and all(isinstance(m, str) for m in members):
            if len(set(members)) != len(members):
                dupes = sorted({m for m in members if members.count(m) > 1})
                raise RegionConfigError(f"{rid}: duplicate members {dupes}.")
            parsed = tuple(members)
        else:
            raise RegionConfigError(f"{rid}: members must be \"all\" or a non-empty list of ISO codes.")
        exempt = dict(r.get("framing_exempt") or {})
        if parsed is not None and not set(exempt) <= set(parsed):
            raise RegionConfigError(f"{rid}: framing_exempt lists non-members {sorted(set(exempt) - set(parsed))}.")
        regions.append(Region(id=rid, label=str(r.get("label") or rid), group=r["group"],
                              box=_box(r.get("box"), rid), members=parsed, framing_exempt=exempt))
    ids = [r.id for r in regions]
    if len(set(ids)) != len(ids):
        raise RegionConfigError("Duplicate region ids.")
    default = raw.get("default")
    if default not in ids:
        raise RegionConfigError(f"default {default!r} isn't a region.")
    return RegionConfig(padding=float(padding), default=default, groups=groups, regions=tuple(regions))


def check_against_pool(cfg: RegionConfig, pool_codes: Iterable[str]) -> list[str]:
    """Problems with the members against the eligible pool (empty = fine)."""
    eligible = set(pool_codes)
    problems = []
    for r in cfg.regions:
        if r.members is None:
            continue
        unknown = sorted(set(r.members) - eligible)
        if unknown:
            problems.append(f"{r.id}: not in the eligible pool: {', '.join(unknown)}")
    return problems


@lru_cache(maxsize=1)
def load_regions(path: Path = REGIONS_FILE) -> RegionConfig:
    return parse_regions(json.loads(path.read_text(encoding="utf-8")))


def pool_sizes(cfg: RegionConfig, pool: Sequence[Any]) -> dict[str, int]:
    return {r.id: len(r.pool(pool)) for r in cfg.regions}
