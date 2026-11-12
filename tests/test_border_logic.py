"""Border Bridge rules: correct set, options, pairs, scoring and settings."""
from __future__ import annotations

import random

import pytest

from app.games.border_bridge import logic as L
from app.games.border_bridge.graph import BorderGraph, Node, get_graph
from app.shared.settings import SettingsError, validate_settings


def make_graph(edges: str, coords: dict[str, tuple[float, float]] | None = None) -> BorderGraph:
    """edges like "A-X X-B"; node coordinates default to a line along the equator."""
    adj: dict[str, set[str]] = {}
    for e in edges.split():
        a, b = e.split("-")
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    coords = coords or {}
    nodes = {c: Node(c, c, c.lower(), c, "Europe", coords.get(c, (float(i), 0.0)))
             for i, c in enumerate(sorted(adj))}
    return BorderGraph(nodes=nodes, adj={k: frozenset(v) for k, v in adj.items()})


# --------------------------------------------------------------------------- correct set
def test_single_intermediate() -> None:
    g = make_graph("A-X X-B A-D")
    assert L.gap(g, {"A", "B"}, "A", "B") == 2
    assert L.correct_set(g, {"A", "B"}, "A", "B") == {"X"}


def test_multiple_simultaneous_correct() -> None:
    g = make_graph("A-X X-B A-Y Y-B A-D")
    assert L.correct_set(g, {"A", "B"}, "A", "B") == {"X", "Y"}


def test_shortest_path_reduction_from_both_ends() -> None:
    g = make_graph("A-P P-Q Q-B A-D D-E E-F F-B")
    assert L.gap(g, {"A", "B"}, "A", "B") == 3
    assert L.correct_set(g, {"A", "B"}, "A", "B") == {"P", "Q"}  # D and F lie on a longer route
    assert L.correct_set(g, {"A", "B", "P"}, "A", "B") == {"Q"}


def test_guess_that_bridges_both_solves() -> None:
    g = make_graph("A-P P-Q Q-B")
    r = {"A", "B", "P"}
    assert L.gap(g, r | {"Q"}, "A", "B") == 0
    assert L.correct_set(g, r | {"Q"}, "A", "B") == set()


def test_components() -> None:
    g = make_graph("A-P P-Q Q-B")
    ca, cb = L.components(g, {"A", "P", "B"}, "A", "B")
    assert ca == {"A", "P"} and cb == {"B"}
    ca, cb = L.components(g, {"A", "P", "Q", "B"}, "A", "B")
    assert ca is cb


# --------------------------------------------------------------------------- options
def test_padding_with_far_countries() -> None:
    # Only X borders the revealed set; F1..F4 are far away and F1 is closest.
    g = make_graph("A-X X-B F1-F2 F2-F3 F3-F4",
                   {"A": (0, 0), "X": (1, 0), "B": (2, 0), "F1": (5, 0), "F2": (9, 0), "F3": (20, 0), "F4": (40, 0)})
    opts = L.generate_options(g, ["A", "B"], "A", "B", set(), random.Random(0))
    assert sorted(opts) == ["F1", "F2", "F3", "X"]


def test_distractors_prefer_nearby_detours() -> None:
    g = make_graph("A-X X-B A-N1 A-N2 A-N3 A-N4",
                   {"A": (0, 0), "X": (1, 0), "B": (2, 0), "N1": (-1, 0), "N2": (-3, 0), "N3": (-6, 0), "N4": (-9, 0)})
    opts = L.generate_options(g, ["A", "B"], "A", "B", set(), random.Random(0))
    assert sorted(opts) == ["N1", "N2", "N3", "X"]
    opts = L.generate_options(g, ["A", "B"], "A", "B", {"N1"}, random.Random(0))
    assert sorted(opts) == ["N2", "N3", "N4", "X"]


def test_correct_capped_at_two_and_kept() -> None:
    g = make_graph("A-X X-B A-Y Y-B A-Z Z-B A-D")
    for seed in range(20):
        opts = L.generate_options(g, ["A", "B"], "A", "B", set(), random.Random(seed))
        assert len([o for o in opts if o in {"X", "Y", "Z"}]) == 2
    opts = L.generate_options(g, ["A", "B"], "A", "B", set(), random.Random(1), keep=["Z"])
    assert "Z" in opts


def test_far_elimination_returns_when_it_becomes_correct() -> None:
    g = make_graph("A-X X-Y Y-Z Z-B")
    p = L.Puzzle("A", "B", 3, ["A", "B"])
    p.options = L.generate_options(g, p.revealed, "A", "B", set(), random.Random(0))
    assert "Y" in p.options  # padding: Y doesn't border A or B
    out = L.apply_pick(g, p, "Y", random.Random(0), 0.0)
    assert out.kind == "far" and "Y" not in p.options and "Y" in p.eliminated
    assert L.apply_pick(g, p, "X", random.Random(0), 1.0).kind == "correct"
    assert "Y" in p.options  # now borders X and is on the shortest path


def test_option_invariants_on_real_graph() -> None:
    g = get_graph()
    rng = random.Random(42)
    pool = L.pair_pool(g, "hard", "all")
    for pair in rng.sample(pool, 120):
        p = L.Puzzle(pair.a, pair.b, pair.between, [pair.a, pair.b])
        p.options = L.generate_options(g, p.revealed, p.a, p.b, p.eliminated, rng)
        steps = 0
        while p.status == "active":
            correct = L.correct_set(g, p.revealed, p.a, p.b)
            opts = p.options
            assert len(opts) == len(set(opts))
            assert not set(opts) & set(p.revealed)
            assert not (set(opts) & p.eliminated) - correct
            n_correct = len(set(opts) & correct)
            assert 1 <= n_correct <= 2
            if steps == 0:
                assert len(opts) == 4
            wrong = [o for o in opts if o not in correct]
            if wrong and rng.random() < 0.4:
                L.apply_pick(g, p, rng.choice(wrong), rng, 0.0)
            else:
                L.apply_pick(g, p, rng.choice([o for o in opts if o in correct]), rng, 0.0)
                if p.status == "active":
                    assert len(p.options) == 4
            steps += 1
        assert p.status == "solved"
        assert p.chain[0] == p.a and p.chain[-1] == p.b and len(p.chain) == pair.between + 2
        for x, y in zip(p.chain, p.chain[1:]):
            assert y in g.neighbours(x)


def test_wrong_pick_feedback_kinds() -> None:
    g = make_graph("A-X X-B A-D F1-F2", {"A": (0, 0), "X": (1, 0), "B": (2, 0), "D": (-1, 0), "F1": (4, 0), "F2": (30, 0)})
    p = L.Puzzle("A", "B", 1, ["A", "B"])
    p.options = L.generate_options(g, p.revealed, "A", "B", set(), random.Random(0))
    assert sorted(p.options) == ["D", "F1", "F2", "X"]
    d = L.apply_pick(g, p, "D", random.Random(0), 0.0)
    assert (d.kind, d.via) == ("detour", "A")
    f = L.apply_pick(g, p, "F1", random.Random(0), 0.0)
    assert (f.kind, f.via) == ("far", None)
    assert sorted(p.options) == ["F2", "X"]  # no replacements after wrong picks
    with pytest.raises(L.OptionUnavailable):
        L.apply_pick(g, p, "D", random.Random(0), 0.0)
    assert L.apply_pick(g, p, "X", random.Random(0), 0.0).solved
    with pytest.raises(L.PuzzleOver):
        L.apply_pick(g, p, "F2", random.Random(0), 0.0)


def test_give_up_reveals_shortest_chain() -> None:
    g = make_graph("A-P P-Q Q-B A-D")
    p = L.Puzzle("A", "B", 2, ["A", "B", "P"], picks=[L.Pick("P", "correct", None)], started_at=0.0)
    added = L.give_up(g, p, 5.0)
    assert added == ["Q"] and p.chain == ["A", "P", "Q", "B"] and p.points == 0 and p.status == "gave_up"


def test_trail_orders_picks_by_side() -> None:
    g = make_graph("A-P P-Q Q-R R-B")
    p = L.Puzzle("A", "B", 3, ["A", "B", "R", "P"])
    assert L.trail(g, p) == (["P"], ["R"])
    p = L.Puzzle("A", "B", 3, ["A", "B", "Q", "R"])
    assert L.trail(g, p) == ([], ["Q", "R"])


# --------------------------------------------------------------------------- pairs
@pytest.mark.parametrize("key", ["easy", "normal", "hard"])
def test_pair_pools_respect_difficulty(key: str) -> None:
    g = get_graph()
    diff = L.CONFIG.difficulty(key)
    pool = L.pair_pool(g, key, "all")
    assert len(pool) > 100
    for p in pool:
        assert p.b not in g.neighbours(p.a)
        assert g.distance(p.a, p.b) == p.between + 1
        assert diff.min_between <= p.between <= diff.max_between
        assert p.km <= diff.max_km
    assert {p.between for p in pool} == set(range(diff.min_between, diff.max_between + 1))


def test_regional_pools() -> None:
    g = get_graph()
    for p in L.pair_pool(g, "normal", "europe"):
        assert g.nodes[p.a].region == g.nodes[p.b].region == "Europe"


def test_pick_pairs_never_repeats_and_spreads_countries() -> None:
    g = get_graph()
    pool = L.pair_pool(g, "normal", "europe")
    for seed in range(10):
        pairs = L.pick_pairs(pool, 30, random.Random(seed))
        assert len({p.key for p in pairs}) == 30
        counts: dict[str, int] = {}
        for p in pairs:
            for c in (p.a, p.b):
                counts[c] = counts.get(c, 0) + 1
        assert max(counts.values()) <= 3
        assert not any(p.needs_padding for p in pairs[:5])


def test_pick_pairs_too_many() -> None:
    pool = L.pair_pool(get_graph(), "easy", "americas")
    with pytest.raises(ValueError, match=str(len(pool))):
        L.pick_pairs(pool, len(pool) + 1, random.Random(0))


def test_new_round_puzzles_are_valid() -> None:
    g = get_graph()
    settings = {"difficulty": "easy", "puzzles": 8, "region": "all", "view": "both", "borders": "on"}
    rnd = L.new_round("r", settings, g, random.Random(3), lambda: 100.0)
    assert len(rnd.puzzles) == 8 and rnd.current.started_at is None   # clock starts at begin()
    assert L.begin(rnd, 100.0) and rnd.current.started_at == 100.0
    assert all(p.between == 1 for p in rnd.puzzles)
    assert rnd.max_score == 8 * (10 + 5 + 15)
    assert len(rnd.current.options) == 4


# --------------------------------------------------------------------------- scoring
def _solved(correct: int, wrong: int) -> L.Puzzle:
    picks = [L.Pick("W", "detour", "A")] * wrong + [L.Pick("C", "correct", None)] * correct
    return L.Puzzle("A", "B", correct, ["A", "B"], picks=picks, status="solved")


def test_scoring_rules() -> None:
    # 10 per solved puzzle (not per pick), -5 per wrong pick (floored at 0),
    # +5 for no wrong picks, + whole seconds left on the 15 s countdown.
    assert L.final_points(_solved(2, 0), 4.7) == (10 + 5 + 11, {"perfect": 5, "time": 11})
    assert L.final_points(_solved(3, 0), 4.7)[0] == 26            # chain length doesn't matter
    assert L.final_points(_solved(2, 0), 0.2) == (30, {"perfect": 5, "time": 15})
    assert L.final_points(_solved(2, 0), 14.9) == (16, {"perfect": 5, "time": 1})
    assert L.final_points(_solved(2, 0), 15.0) == (15, {"perfect": 5})
    assert L.final_points(_solved(2, 1), 30.0) == (5, {})         # 10 - 5
    assert L.final_points(_solved(1, 3), 30.0) == (0, {})         # floored at 0
    assert L.final_points(_solved(1, 3), 10.0) == (5, {"time": 5})
    gave_up = _solved(1, 0)
    gave_up.status = "gave_up"
    assert L.final_points(gave_up, 1.0) == (0, {})


def test_time_points_and_max() -> None:
    assert [L.time_points(t) for t in (0, 0.5, 1.0, 7.2, 14.99, 15, 60)] == [15, 15, 14, 8, 1, 0, 0]
    assert L.max_points(1) == L.max_points(4) == 30


# --------------------------------------------------------------------------- settings
def test_settings_defaults_and_validation() -> None:
    clean = validate_settings(L.SETTINGS_SCHEMA, {})
    assert clean == {"difficulty": "normal", "puzzles": 5, "view": "flat", "borders": "on", "region": "all"}
    assert validate_settings(L.SETTINGS_SCHEMA, {"puzzles": 30, "difficulty": "hard", "region": "europe"})["puzzles"] == 30
    for bad in ({"puzzles": 0}, {"puzzles": 31}, {"difficulty": "insane"}, {"view": "3d"},
                {"borders": True}, {"region": "oceania"}, {"colour": "red"}):
        with pytest.raises(SettingsError):
            validate_settings(L.SETTINGS_SCHEMA, bad)


def test_view_setting_not_part_of_score_key() -> None:
    a = L.scored_settings({"difficulty": "easy", "puzzles": 5, "region": "all", "view": "globe", "borders": "on"})
    b = L.scored_settings({"difficulty": "easy", "puzzles": 5, "region": "all", "view": "flat", "borders": "on"})
    assert a == b
