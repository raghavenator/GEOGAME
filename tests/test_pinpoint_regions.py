"""Pinpoint regions: the regions.json config, region pools, the questions cap, unknown ids and
backwards compatibility of stored settings and best-score keys."""
from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.games.pinpoint import logic as L
from app.games.pinpoint.location_data import load_locations
from app.games.pinpoint.regions import (REGIONS_FILE, RegionConfigError, check_against_pool, load_regions,
                                        parse_regions)
from app.shared.settings import SettingsError, settings_key, validate_settings

POOL = load_locations()
BY_CODE = {e.code: e for e in POOL}
CFG = load_regions()
LEGACY_IDS = ("all", "africa", "americas", "asia", "europe", "oceania")   # values stored before regions.json
REQUIRED = ("caribbean", "central-america", "eastern-europe", "sub-saharan-africa", "mena")
DEFAULTS = {"questions": 5, "seconds": 30, "difficulty": "normal", "view": "flat", "borders": "on", "units": "km",
            "region": "all"}


def members(rid: str) -> set[str]:
    return {e.code for e in CFG.get(rid).pool(POOL)}


def names(rid: str) -> set[str]:
    return {BY_CODE[c].name for c in members(rid)}


# --------------------------------------------------------------------------- the config file
def test_every_region_is_valid_and_non_empty() -> None:
    assert check_against_pool(CFG, BY_CODE) == []
    assert CFG.default == "all" and CFG.get("all").is_world and len(members("all")) == len(POOL)
    raw = json.loads(REGIONS_FILE.read_text(encoding="utf-8"))
    for r in raw["regions"]:
        if r["members"] != "all":
            assert len(r["members"]) == len(set(r["members"])), f"{r['id']} has duplicate members"
            assert set(r["members"]) <= set(BY_CODE), f"{r['id']} has members outside the eligible pool"
    for r in CFG.regions:
        assert members(r.id), r.id
        w, s, e, n = r.box
        assert -180 <= w <= 180 and -180 <= e <= 180 and -90 <= s < n <= 90, r.id
        if r.id != "oceania":   # the only box allowed to cross the antimeridian
            assert w < e, f"{r.id}: west must be < east"
        if not r.is_world:
            assert r.lon_span < 300, f"{r.id}: a box this wide would get the world camera"


def test_reference_points_are_inside_their_box() -> None:
    """A cheap server-side guard; the browser check projects them through the real camera."""
    for r in CFG.regions:
        if r.is_world:
            continue
        w, s, e, n = r.box
        for code in members(r.id) - set(r.framing_exempt):
            loc = BY_CODE[code]
            lng = loc.lng if e > w or loc.lng >= w else loc.lng + 360
            east = e if e > w else e + 360
            assert w <= lng <= east and s <= loc.lat <= n, f"{r.id}: {loc.name} ({loc.lat}, {loc.lng}) is outside {r.box}"


def test_required_regions_come_first_among_subregions() -> None:
    subs = [r.id for r in CFG.regions if r.group == "subregions"]
    assert set(subs[:5]) == set(REQUIRED)
    assert [g for g, _ in CFG.groups] == ["world", "continents", "subregions"]


def test_membership_rulings() -> None:
    assert names("caribbean") == {
        "Antigua and Barbuda", "Bahamas", "Barbados", "Cuba", "Dominica", "Dominican Republic", "Grenada", "Haiti",
        "Jamaica", "Saint Kitts and Nevis", "Saint Lucia", "Saint Vincent and the Grenadines", "Trinidad and Tobago"}
    assert names("central-america") == {"Belize", "Costa Rica", "El Salvador", "Guatemala", "Honduras", "Nicaragua",
                                        "Panama"}
    assert "mx" not in members("central-america") and "mx" in members("north-america")
    ee = members("eastern-europe")
    assert "ru" not in ee
    assert {"by", "bg", "cz", "hu", "md", "pl", "ro", "sk", "ua"} <= ee        # UN Eastern Europe minus Russia
    assert {"ee", "lv", "lt"} <= ee                                             # Baltics
    assert {"al", "ba", "hr", "me", "mk", "rs", "si"} <= ee                     # Balkans
    mena = members("mena")
    assert {e.code for e in POOL if e.subregion == "Northern Africa"} - {"sd"} <= mena
    assert {e.code for e in POOL if e.subregion == "Western Asia"} - {"am", "az", "ge"} <= mena
    assert "ir" in mena and not {"am", "az", "ge", "sd"} & mena
    africa = {e.code for e in POOL if e.region == "Africa"}
    ssa = members("sub-saharan-africa")
    assert ssa == africa - mena and "sd" in ssa
    assert members("americas") == members("north-america") | members("south-america")
    assert not members("north-america") & members("south-america")
    assert members("europe") == {e.code for e in POOL if e.region == "Europe"}     # unchanged pool
    assert set(CFG.get("europe").framing_exempt) == {"ru"}


def test_legacy_continent_pools_are_unchanged() -> None:
    """Old best scores were earned on these pools; they mustn't change under them."""
    for rid in LEGACY_IDS[1:]:
        assert members(rid) == {e.code for e in POOL if e.region.lower() == rid}, rid


def test_parse_rejects_bad_configs() -> None:
    raw = json.loads(REGIONS_FILE.read_text(encoding="utf-8"))

    def broken(**change: Any) -> dict[str, Any]:
        r = json.loads(json.dumps(raw))
        r["regions"][8].update(change)          # caribbean
        return r

    for bad in (broken(members=["cu", "cu"]), broken(members=[]), broken(box=[10, 20, 30]),
                broken(box=[-86, 27, -58, 8]), broken(box=[-86, 8, -86, 27]), broken(box=[-200, 8, -58, 27]),
                broken(group="planets"), broken(id="a|b"), broken(id="africa"),
                broken(framing_exempt={"fr": "not a member"})):
        with pytest.raises(RegionConfigError):
            parse_regions(bad)
    unknown = parse_regions(broken(members=["cu", "xx"]))
    assert check_against_pool(unknown, BY_CODE) == ["caribbean: not in the eligible pool: xx"]


# --------------------------------------------------------------------------- pools and validation
def test_questions_come_only_from_the_region_and_never_repeat() -> None:
    for r in CFG.regions:
        size = len(members(r.id))
        n = min(size, 30)
        for seed in range(3):
            rnd = L.new_round("r", {**DEFAULTS, "region": r.id, "questions": n}, POOL, random.Random(seed))
            codes = [q.code for q in rnd.questions]
            assert len(codes) == len(set(codes)) == n
            assert set(codes) <= members(r.id)
    rnd = L.new_round("r", {**DEFAULTS, "region": "central-america", "questions": 7}, POOL, random.Random(1))
    assert {q.code for q in rnd.questions} == members("central-america")


def test_questions_are_capped_at_the_pool_size() -> None:
    assert validate_settings(L.SETTINGS_SCHEMA, {"region": "caribbean", "questions": 13})["questions"] == 13
    with pytest.raises(SettingsError) as exc:
        validate_settings(L.SETTINGS_SCHEMA, {"region": "caribbean", "questions": 14})
    assert exc.value.errors == {"questions": "Caribbean has 13 countries, so the maximum is 13 questions."}
    with pytest.raises(SettingsError) as exc:
        validate_settings(L.SETTINGS_SCHEMA, {"region": "central-america", "questions": 8})
    assert "Central America has 7 countries" in exc.value.errors["questions"]
    # Regions with 30+ countries keep the usual 1-30 range.
    assert validate_settings(L.SETTINGS_SCHEMA, {"region": "africa", "questions": 30})["questions"] == 30


def test_schema_json_carries_groups_counts_and_cap() -> None:
    q, region = L.SETTINGS_SCHEMA[0].to_json(), L.SETTINGS_SCHEMA[-1].to_json()
    assert q["cap_by"] == "region" and "{cap}" in q["cap_note"]
    counts = {c["value"]: c["count"] for c in region["choices"]}
    assert counts["caribbean"] == 13 and counts["central-america"] == 7 and counts["all"] == len(POOL)
    assert [g["label"] for g in region["groups"]] == ["World", "Continents", "Sub-regions"]
    assert sorted(v for g in region["groups"] for v in g["values"]) == sorted(counts)


def test_unknown_region_is_rejected() -> None:
    for bad in ("mars", "", "Europe", 3, None):
        with pytest.raises(SettingsError) as exc:
            validate_settings(L.SETTINGS_SCHEMA, {"region": bad})
        assert "Region must be one of" in exc.value.errors["region"]


# --------------------------------------------------------------------------- backwards compatibility
def test_old_stored_values_still_validate_and_keep_their_keys() -> None:
    for rid in LEGACY_IDS:
        clean = validate_settings(L.SETTINGS_SCHEMA, {**DEFAULTS, "region": rid})
        # The exact key format rows were stored under before regions.json existed.
        assert settings_key(L.scored_settings(clean)) == \
            f"borders=on|difficulty=normal|questions=5|region={rid}|seconds=30"


def test_unknown_stored_values_fall_back_to_world() -> None:
    assert CFG.resolve("europe") == "europe" and CFG.resolve("caribbean") == "caribbean"
    for gone in ("mars", "Europe", "", None, 7):
        assert CFG.resolve(gone) == "all"
    # The browser does the same with what it stored (settings.js sanitize: unknown choice -> default).
    assert L.SETTINGS_SCHEMA[-1].default == "all"


# --------------------------------------------------------------------------- API
@pytest.fixture()
def app(tmp_path: Path) -> Iterator[Flask]:
    yield create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.sqlite3"), "PINPOINT_SEED": 3})


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def post(client: FlaskClient, path: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    r = client.post(f"/games/pinpoint/api/{path}", json=body)
    assert r.status_code == status, r.get_json()
    return r.get_json()


def test_api_rejects_unknown_region_and_over_cap(client: FlaskClient) -> None:
    e = post(client, "start", {"settings": {"region": "atlantis"}}, status=400)["error"]
    assert e["code"] == "invalid_settings" and "region" in e["details"]["fields"]
    e = post(client, "start", {"settings": {"region": "caribbean", "questions": 20}}, status=400)["error"]
    assert e["details"]["fields"] == {"questions": "Caribbean has 13 countries, so the maximum is 13 questions."}
    e = post(client, "best", {"settings": {"region": "caribbean", "questions": 20}}, status=400)["error"]
    assert e["code"] == "invalid_settings"


def test_api_round_in_a_new_region(client: FlaskClient) -> None:
    from app.games.pinpoint.location_data import locations_by_code

    r = post(client, "start", {"settings": {"region": "central-america", "questions": 7}})
    assert r["total"] == 7 and r["settings"]["region"] == "central-america"
    rid = r["round_id"]
    seen = []
    q = post(client, "begin", {"round_id": rid})["question"]
    for i in range(7):
        res = post(client, "submit", {"round_id": rid, "question_index": i, "lat": 12.0, "lng": -85.0})
        seen.append(res["target"]["code"])
        nxt = post(client, "next", {"round_id": rid, "from_index": i})
        if not nxt["finished"]:
            q = nxt["question"]
    assert q["number"] == 7
    assert sorted(seen) == sorted(members("central-america"))
    assert all(locations_by_code()[c].subregion == "Central America" for c in seen)
    s = post(client, "summary", {"round_id": rid})
    assert s["settings"]["region"] == "central-america"
    assert post(client, "best", {"settings": {"region": "central-america", "questions": 7}})["best"] == s["score"]


def test_api_old_best_score_rows_still_load(app: Flask, client: FlaskClient) -> None:
    with sqlite3.connect(app.config["DATABASE"]) as db:
        for rid, score in (("europe", 321), ("all", 123)):
            db.execute("INSERT INTO high_scores (game_id, settings_key, best_score, max_score, plays) "
                       "VALUES ('pinpoint', ?, ?, 500, 1)",
                       (f"borders=on|difficulty=normal|questions=5|region={rid}|seconds=30", score))
    assert post(client, "best", {"settings": {"region": "europe"}})["best"] == 321
    assert post(client, "best", {"settings": {}})["best"] == 123
    assert post(client, "best", {"settings": {"region": "eastern-europe"}})["best"] is None


def test_page_embeds_framing_and_grouped_settings(client: FlaskClient) -> None:
    html = client.get("/games/pinpoint/").get_data(as_text=True)
    for label in ("Caribbean", "Central America", "Eastern Europe", "Middle East &amp; North Africa",
                  "Sub-Saharan Africa", "Sub-regions", "Continents"):
        assert label in html
    assert "<optgroup" in html and "regionPadding" in html
