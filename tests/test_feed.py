"""Homepage feed: config ordering and validation, rendering, and the moved hub."""
from __future__ import annotations

import re
from dataclasses import replace

import pytest
from flask.testing import FlaskClient

from app.feed import FEED, FeedError, FeedItem, game, placeholder, resolve_feed
from app.games.registry import GAMES

SPECS = {g.id: g for g in GAMES}


def test_feed_order() -> None:
    cards = resolve_feed(FEED, SPECS)
    assert [c.item.id for c in cards] == ["flag-reveal", "border-bridge", "capital-quest", "pinpoint", "currency-quest",
                                          "coming-soon"]
    assert [c.item.kind for c in cards] == ["game", "game", "game", "game", "game", "placeholder"]
    assert cards[0].game is SPECS["flag-reveal"] and cards[3].game is SPECS["pinpoint"]
    assert cards[-1].game is None and cards[-1].title == "More games on the way"


@pytest.mark.parametrize("items,message", [
    ((), "at least one"),
    ((game("flag-reveal"), game("flag-reveal")), "Duplicate"),
    ((game("atlantis"),), "unknown game"),
    ((game("shape-shifter"),), "can't be embedded"),              # a coming-soon registry entry
    ((game("flag-reveal", tint="neon"),), "unknown tint"),
    ((placeholder("Bad Id", "x", "y"),), "slug"),
    ((placeholder("soon", "", "y"),), "needs a title"),
    ((FeedItem(id="x", kind="video"),), "unknown kind"),           # type: ignore[arg-type]
])
def test_feed_validation(items: tuple[FeedItem, ...], message: str) -> None:
    with pytest.raises(FeedError, match=message):
        resolve_feed(items, SPECS)


def test_game_without_embed_template_is_rejected() -> None:
    specs = {**SPECS, "flag-reveal": replace(SPECS["flag-reveal"], embed_template="")}
    with pytest.raises(FeedError, match="can't be embedded"):
        resolve_feed((game("flag-reveal"),), specs)


def test_feed_page_renders_cards_in_order(client: FlaskClient) -> None:
    html = client.get("/").get_data(as_text=True)
    ids = re.findall(r'<section class="feed-item" id="([a-z-]+)"', html)
    assert ids == ["flag-reveal", "border-bridge", "capital-quest", "pinpoint", "currency-quest", "coming-soon"]
    # Each game is mounted from the same embed markup the full pages use, in feed mode.
    assert 'data-game-root="flag-reveal" data-game="flag-reveal" data-mode="feed"' in html
    assert 'data-game-root="border-bridge" data-game="border-bridge" data-mode="feed"' in html
    assert 'data-game-root="capital-quest" data-game="capital-quest" data-mode="feed"' in html
    assert 'data-game-root="pinpoint" data-game="pinpoint" data-mode="feed"' in html
    assert 'data-game-root="currency-quest" data-game="currency-quest" data-mode="feed"' in html
    assert html.count("data-tap-to-play=") == 5
    assert html.count("data-game-settings") == 5          # one settings store + modal per game
    assert html.count('data-gesture-zone="map"') == 3     # Border Bridge, Capital Quest and Pinpoint maps
                                                          # (Currency Quest's locator is decorative, no zone)
    assert html.count("feed-hint--map") == 3              # the shared one-time map hint
    assert html.count("vendor/d3/d3.min.js") == 1         # shared libraries load once
    assert html.count("js/map_view.js") == 1
    assert "You're all caught up" in html
    for script in ("js/feed.js", "flag_reveal/static/embed.js", "border-bridge/static/embed.js",
                   "capital-quest/static/embed.js", "pinpoint/static/embed.js", "currency-quest/static/embed.js"):
        assert script.split("/")[-1] in html


def test_full_pages_use_page_mode(client: FlaskClient) -> None:
    for gid in ("flag-reveal", "border-bridge", "capital-quest", "pinpoint", "currency-quest"):
        html = client.get(f"/games/{gid}/").get_data(as_text=True)
        assert f'data-game-root="{gid}" data-game="{gid}" data-mode="page"' in html
        assert "embed.js" in html


def test_hub_moved_to_games(client: FlaskClient) -> None:
    html = client.get("/games").get_data(as_text=True)
    for title in ("Flag Reveal", "Border Bridge", "Capital Quest", "Pinpoint", "Currency Quest", "Coming soon"):
        assert title in html
    assert 'href="/games/capital-quest/"' in html and 'href="/games/pinpoint/"' in html
    assert 'class="feed-item"' not in html


def test_restart_buttons_and_ready_overlay(client: FlaskClient) -> None:
    html = client.get("/").get_data(as_text=True)
    assert html.count("feed-card__restart") == 5                 # one per game card, next to its gear
    assert "feed-bar__restart" in html                           # mobile top bar
    # The ready overlay follows a class, not x-show with a leave transition: saving settings flips
    # ready -> preparing -> ready quickly, and an interrupted leave transition left it hidden.
    ready = re.findall(r'<div class="feed-overlay feed-overlay--ready"[^>]*>', html)
    assert len(ready) == 5 and all("x-show" not in tag and "showReady(" in tag for tag in ready)
    for gid in ("flag-reveal", "border-bridge", "capital-quest", "pinpoint", "currency-quest"):
        page = client.get(f"/games/{gid}/").get_data(as_text=True)
        assert f"GeoArcade.restart('{gid}')" in page
