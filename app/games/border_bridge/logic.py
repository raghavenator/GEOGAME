"""Border Bridge rules. Pure functions and plain dataclasses, no Flask.

Definitions (tested in tests/test_border_logic.py):

* G is the land-border graph, R the revealed set (A, B and correct picks).
* CA and CB are the connected components of A and B in the subgraph induced by R.
* ``gap(R)`` is 0 when CA == CB (solved), otherwise the shortest hop distance in G
  between any node of CA and any node of CB.
* A frontier country c (borders R, not in R) is *correct* iff gap(R + c) < gap(R).
  Every other frontier country is a *detour*. Countries off the frontier are *far*.
"""
from __future__ import annotations

import math
import random
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.shared.countries import REGIONS
from app.shared.settings import ChoiceSetting, IntSetting, SettingsSchema

from .graph import BorderGraph, great_circle_km


@dataclass(frozen=True)
class Difficulty:
    label: str
    min_between: int   # intermediate countries on the shortest path
    max_between: int
    max_km: float      # great-circle distance cap between A and B mainland centroids


@dataclass(frozen=True)
class BorderBridgeConfig:
    solve_points: int = 10          # per solved puzzle, however many countries it took
    wrong_penalty: int = 5
    perfect_bonus: int = 5
    time_limit_seconds: int = 15    # countdown; each whole second left when solved = 1 point
    options_shown: int = 4
    max_correct_options: int = 2
    puzzles: int = 5
    puzzles_range: tuple[int, int] = (1, 30)
    difficulties: tuple[tuple[str, Difficulty], ...] = (
        ("easy", Difficulty("Easy", 1, 1, 2500.0)),
        ("normal", Difficulty("Normal", 1, 2, 2500.0)),
        ("hard", Difficulty("Hard", 2, 4, 4000.0)),
    )
    default_difficulty: str = "normal"
    # Settings that change the challenge; view mode is cosmetic and excluded from high-score keys.
    scored_settings: tuple[str, ...] = ("difficulty", "puzzles", "region", "borders")

    def difficulty(self, key: str) -> Difficulty:
        return dict(self.difficulties)[key]


CONFIG = BorderBridgeConfig()

# Regions with land borders between member states (Oceania has none).
PAIR_REGIONS: tuple[str, ...] = tuple(r for r in REGIONS if r != "Oceania")

SETTINGS_SCHEMA: SettingsSchema = (
    ChoiceSetting(
        "difficulty", "Difficulty", CONFIG.default_difficulty,
        tuple((k, f"{d.label}: {d.min_between}" + (f"–{d.max_between}" if d.max_between != d.min_between else "")
               + (" country" if d.max_between == 1 else " countries") + " between")
              for k, d in CONFIG.difficulties),
        help="How many countries sit between the two you need to connect.",
    ),
    IntSetting("puzzles", "Puzzles per round", CONFIG.puzzles, *CONFIG.puzzles_range,
               help="How many pairs of countries to connect."),
    ChoiceSetting("view", "View", "flat",
                  (("both", "Globe and flat map"), ("globe", "Globe"), ("flat", "Flat map")),
                  help="On a phone, “both” shows tabs to switch views."),
    ChoiceSetting("borders", "Show country borders", "on",
                  (("on", "On"), ("off", "Off (harder)")),
                  help="Off hides the borders between background countries."),
    ChoiceSetting("region", "Region", "all",
                  (("all", "Whole world"), *((r.lower(), r) for r in PAIR_REGIONS)),
                  help="Both countries in each pair come from this region."),
)


class PuzzleOver(Exception):
    """The puzzle is already solved or given up."""


class PuzzleActive(Exception):
    """Can't advance while the puzzle is still being played."""


class OptionUnavailable(Exception):
    """The picked option isn't currently offered."""


# --------------------------------------------------------------------------- graph rules
def components(graph: BorderGraph, revealed: Iterable[str], a: str, b: str) -> tuple[set[str], set[str]]:
    inside = set(revealed)

    def grow(start: str) -> set[str]:
        seen, stack = {start}, [start]
        while stack:
            for n in graph.neighbours(stack.pop()):
                if n in inside and n not in seen:
                    seen.add(n)
                    stack.append(n)
        return seen

    ca = grow(a)
    return ca, (ca if b in ca else grow(b))


def gap(graph: BorderGraph, revealed: Iterable[str], a: str, b: str) -> int:
    ca, cb = components(graph, revealed, a, b)
    if ca is cb:
        return 0
    dist = graph.bfs(ca)
    return min((dist[x] for x in cb if x in dist), default=math.inf)  # type: ignore[return-value]


def correct_set(graph: BorderGraph, revealed: Iterable[str], a: str, b: str) -> set[str]:
    r = set(revealed)
    d = gap(graph, r, a, b)
    if d == 0:
        return set()
    return {c for c in graph.frontier(r) if gap(graph, r | {c}, a, b) < d}


def spherical_centre(points: Iterable[tuple[float, float]]) -> tuple[float, float]:
    x = y = z = 0.0
    for lon, lat in points:
        lo, la = math.radians(lon), math.radians(lat)
        x += math.cos(la) * math.cos(lo)
        y += math.cos(la) * math.sin(lo)
        z += math.sin(la)
    return math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))


def generate_options(
    graph: BorderGraph,
    revealed: Sequence[str],
    a: str,
    b: str,
    eliminated: Iterable[str],
    rng: random.Random,
    keep: Iterable[str] = (),
    cfg: BorderBridgeConfig = CONFIG,
) -> list[str]:
    """Options for the current state: 1..max_correct correct ones (still-correct ``keep``
    entries first), then the nearest detours, then the nearest far countries as padding."""
    r = set(revealed)
    out = set(eliminated)
    correct = correct_set(graph, r, a, b)
    n_correct = min(cfg.max_correct_options, len(correct))
    chosen = [c for c in keep if c in correct][:n_correct]
    rest = sorted(correct - set(chosen))
    rng.shuffle(rest)
    chosen += rest[: n_correct - len(chosen)]

    centre = spherical_centre(graph.nodes[c].centroid for c in r)

    def nearest(pool: Iterable[str]) -> list[str]:
        return sorted(pool, key=lambda c: (great_circle_km(graph.nodes[c].centroid, centre), c))

    need = cfg.options_shown - len(chosen)
    frontier = graph.frontier(r)
    wrong = nearest(frontier - correct - out)[:need]
    if len(wrong) < need:
        wrong += nearest(set(graph.nodes) - r - frontier - out)[: need - len(wrong)]
    options = chosen + wrong
    rng.shuffle(options)
    return options


# --------------------------------------------------------------------------- pairs
@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    between: int
    km: float
    needs_padding: bool

    @property
    def key(self) -> frozenset[str]:
        return frozenset({self.a, self.b})


def needs_padding(graph: BorderGraph, a: str, b: str, cfg: BorderBridgeConfig = CONFIG) -> bool:
    r = {a, b}
    correct = correct_set(graph, r, a, b)
    detours = graph.frontier(r) - correct
    return len(detours) < cfg.options_shown - min(cfg.max_correct_options, len(correct))


def build_pair_pool(graph: BorderGraph, difficulty: Difficulty, region: str = "all",
                    cfg: BorderBridgeConfig = CONFIG) -> list[Pair]:
    """Every unordered (A, B) that isn't adjacent, has min..max countries between them
    on the shortest path, and whose mainland centroids are within the distance cap."""
    codes = sorted(c for c in graph.nodes if region == "all" or graph.nodes[c].region.lower() == region)
    pool = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            d = graph.distance(a, b)
            if d is None or not difficulty.min_between <= d - 1 <= difficulty.max_between:
                continue
            km = graph.km(a, b)
            if km <= difficulty.max_km:
                pool.append(Pair(a, b, d - 1, km, needs_padding(graph, a, b, cfg)))
    return pool


def pair_pool(graph: BorderGraph, difficulty: str, region: str) -> tuple[Pair, ...]:
    key = ("pairs", difficulty, region)
    if key not in graph.cache:
        graph.cache[key] = tuple(build_pair_pool(graph, CONFIG.difficulty(difficulty), region))
    return graph.cache[key]


def warm_pair_pools(graph: BorderGraph) -> None:
    for key, _ in CONFIG.difficulties:
        for region in ("all", *(r.lower() for r in PAIR_REGIONS)):
            pair_pool(graph, key, region)


def pick_pairs(pool: Sequence[Pair], n: int, rng: random.Random) -> list[Pair]:
    """n distinct pairs, spreading countries out (least-used endpoints first) and
    preferring pairs whose first options need no padding. Orientation is random."""
    if n > len(pool):
        raise ValueError(f"Only {len(pool)} puzzles are available with these settings. Choose {len(pool)} or fewer.")
    usage: Counter[str] = Counter()
    used: set[frozenset[str]] = set()
    chosen: list[Pair] = []
    for _ in range(n):
        avail = [p for p in pool if p.key not in used]
        rank = {id(p): (usage[p.a] + usage[p.b], p.needs_padding) for p in avail}
        best = min(rank.values())
        p = rng.choice([p for p in avail if rank[id(p)] == best])
        if rng.random() < 0.5:
            p = Pair(p.b, p.a, p.between, p.km, p.needs_padding)
        used.add(p.key)
        usage.update((p.a, p.b))
        chosen.append(p)
    return chosen


# --------------------------------------------------------------------------- state
@dataclass
class Pick:
    cca3: str
    kind: str          # correct | detour | far
    via: str | None    # a revealed neighbour (for detour feedback)


@dataclass
class Puzzle:
    a: str
    b: str
    between: int
    revealed: list[str]
    options: list[str] = field(default_factory=list)
    eliminated: set[str] = field(default_factory=set)
    picks: list[Pick] = field(default_factory=list)
    status: str = "active"           # active | solved | gave_up
    started_at: float | None = None
    ended_at: float | None = None
    points: int = 0
    bonuses: dict[str, int] = field(default_factory=dict)
    chain: list[str] = field(default_factory=list)

    @property
    def correct_picks(self) -> int:
        return sum(p.kind == "correct" for p in self.picks)

    @property
    def wrong_picks(self) -> int:
        return sum(p.kind != "correct" for p in self.picks)

    def elapsed(self, now: float) -> float:
        if self.started_at is None:
            return 0.0
        return (self.ended_at if self.ended_at is not None else now) - self.started_at


@dataclass
class Round:
    id: str
    settings: dict[str, Any]
    puzzles: list[Puzzle]
    index: int = 0
    finished: bool = False
    recorded: bool = False
    begun: bool = False                  # set by begin(): the player tapped play
    paused_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def current(self) -> Puzzle:
        return self.puzzles[self.index]

    @property
    def is_last(self) -> bool:
        return self.index == len(self.puzzles) - 1

    @property
    def score(self) -> int:
        return sum(p.points for p in self.puzzles if p.status != "active")

    @property
    def max_score(self) -> int:
        return sum(max_points(p.between) for p in self.puzzles)


def max_points(between: int, cfg: BorderBridgeConfig = CONFIG) -> int:
    return cfg.solve_points + cfg.perfect_bonus + cfg.time_limit_seconds


def time_points(elapsed_s: float, cfg: BorderBridgeConfig = CONFIG) -> int:
    """Seconds left on the countdown, rounded up (what the timer showed), 0 once it runs out."""
    return max(0, math.ceil(cfg.time_limit_seconds - elapsed_s))


def final_points(p: Puzzle, elapsed_s: float, cfg: BorderBridgeConfig = CONFIG) -> tuple[int, dict[str, int]]:
    """Solve points minus wrong picks (floored at 0), plus the perfect and time bonuses."""
    if p.status != "solved":
        return 0, {}
    bonuses = {}
    if p.wrong_picks == 0:
        bonuses["perfect"] = cfg.perfect_bonus
    if time_points(elapsed_s, cfg):
        bonuses["time"] = time_points(elapsed_s, cfg)
    base = max(0, cfg.solve_points - p.wrong_picks * cfg.wrong_penalty)
    return base + sum(bonuses.values()), bonuses


def new_round(round_id: str, settings: dict[str, Any], graph: BorderGraph, rng: random.Random,
              clock: Callable[[], float] = time.monotonic) -> Round:
    pool = pair_pool(graph, settings["difficulty"], settings["region"])
    pairs = pick_pairs(pool, settings["puzzles"], rng)
    puzzles = [Puzzle(a=p.a, b=p.b, between=p.between, revealed=[p.a, p.b]) for p in pairs]
    rnd = Round(id=round_id, settings=settings, puzzles=puzzles)
    _deal(graph, rnd.current, rng)
    return rnd


def _deal(graph: BorderGraph, p: Puzzle, rng: random.Random) -> None:
    p.options = generate_options(graph, p.revealed, p.a, p.b, p.eliminated, rng)


# --------------------------------------------------------------------------- play clock
# Rounds can be prepared long before they're played (the feed preloads them).
# Puzzle time starts at begin(); paused time is shifted out of the active puzzle.
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
    p = rnd.current
    if p.status == "active" and p.started_at is not None:
        p.started_at += max(0.0, now - rnd.paused_at)
    rnd.paused_at = None
    return True


@dataclass(frozen=True)
class PickOutcome:
    kind: str
    cca3: str
    via: str | None
    solved: bool


def apply_pick(graph: BorderGraph, p: Puzzle, cca3: str, rng: random.Random, now: float,
               cfg: BorderBridgeConfig = CONFIG) -> PickOutcome:
    if p.status != "active":
        raise PuzzleOver
    if cca3 not in p.options:
        raise OptionUnavailable
    if cca3 in correct_set(graph, p.revealed, p.a, p.b):
        p.revealed.append(cca3)
        p.picks.append(Pick(cca3, "correct", None))
        if gap(graph, p.revealed, p.a, p.b) == 0:
            _finish(graph, p, "solved", now, cfg)
            return PickOutcome("correct", cca3, None, True)
        keep = [o for o in p.options if o != cca3]
        p.options = generate_options(graph, p.revealed, p.a, p.b, p.eliminated, rng, keep=keep, cfg=cfg)
        return PickOutcome("correct", cca3, None, False)

    touching = [r for r in reversed(p.revealed) if r in graph.neighbours(cca3)]
    kind = "detour" if touching else "far"
    via = touching[0] if touching else None
    p.options.remove(cca3)
    p.eliminated.add(cca3)
    p.picks.append(Pick(cca3, kind, via))
    return PickOutcome(kind, cca3, via, False)


def give_up(graph: BorderGraph, p: Puzzle, now: float, cfg: BorderBridgeConfig = CONFIG) -> list[str]:
    """End the puzzle with 0 points; returns the countries added to complete the chain."""
    if p.status != "active":
        raise PuzzleOver
    ca, cb = components(graph, p.revealed, p.a, p.b)
    bridge = graph.shortest_path(ca, cb)
    added = [c for c in bridge if c not in p.revealed]
    _finish(graph, p, "gave_up", now, cfg, extra=added)
    return added


def _finish(graph: BorderGraph, p: Puzzle, status: str, now: float, cfg: BorderBridgeConfig,
            extra: Iterable[str] = ()) -> None:
    p.status = status
    p.ended_at = now
    p.options = []
    p.chain = graph.shortest_path([p.a], [p.b], within={*p.revealed, *extra})
    p.points, p.bonuses = final_points(p, p.elapsed(now), cfg)


def advance(graph: BorderGraph, rnd: Round, from_index: int, rng: random.Random, now: float) -> None:
    """Move past puzzle ``from_index``. Idempotent: repeats for an old index are no-ops."""
    if rnd.finished or from_index != rnd.index:
        return
    if rnd.current.status == "active":
        raise PuzzleActive
    if rnd.is_last:
        rnd.finished = True
        return
    rnd.index += 1
    _deal(graph, rnd.current, rng)
    rnd.current.started_at = now if rnd.paused_at is None else rnd.paused_at


def trail(graph: BorderGraph, p: Puzzle) -> tuple[list[str], list[str]]:
    """Correct picks in display order: those linked to A (nearest A first) and those
    linked to B (nearest B last), so chips read A, ..., ?, ..., B."""
    ca, cb = components(graph, p.revealed, p.a, p.b)

    def ordered(start: str, comp: set[str]) -> list[str]:
        dist = BorderGraph(nodes=graph.nodes, adj={c: graph.neighbours(c) & comp for c in comp}).bfs([start])
        return sorted((c for c in p.revealed[2:] if c in comp), key=lambda c: (dist.get(c, 99), c))

    if ca is cb:
        return [c for c in p.chain[1:-1]], []
    return ordered(p.a, ca), ordered(p.b, cb)[::-1]


def scored_settings(settings: dict[str, Any], cfg: BorderBridgeConfig = CONFIG) -> dict[str, Any]:
    return {k: settings[k] for k in cfg.scored_settings}
