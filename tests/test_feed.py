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
    assert [c.item.id for c in cards] == ["flag-reveal", "border-bridge", "coming-soon"]
    assert [c.item.kind for c in cards] == ["game", "game", "placeholder"]
    assert cards[0].game is SPECS["flag-reveal"] and cards[2].game is None
    assert cards[2].title == "More games on the way"


@pytest.mark.parametrize("items,message", [
    ((), "at least one"),
    ((game("flag-reveal"), game("flag-reveal")), "Duplicate"),
    ((game("atlantis"),), "unknown game"),
    ((game("capital-quest"),), "can't be embedded"),              # a coming-soon registry entry
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
    assert ids == ["flag-reveal", "border-bridge", "coming-soon"]
    # Each game is mounted from the same embed markup the full pages use, in feed mode.
    assert 'data-game-root="flag-reveal" data-game="flag-reveal" data-mode="feed"' in html
    assert 'data-game-root="border-bridge" data-game="border-bridge" data-mode="feed"' in html
    assert html.count("data-tap-to-play=") == 2
    assert html.count("data-game-settings") == 2          # one settings store + modal per game
    assert 'data-gesture-zone="map"' in html
    assert "You're all caught up" in html
    for script in ("js/feed.js", "flag_reveal/static/embed.js", "border-bridge/static/embed.js"):
        assert script.split("/")[-1] in html


def test_full_pages_use_page_mode(client: FlaskClient) -> None:
    for gid in ("flag-reveal", "border-bridge"):
        html = client.get(f"/games/{gid}/").get_data(as_text=True)
        assert f'data-game-root="{gid}" data-game="{gid}" data-mode="page"' in html
        assert "embed.js" in html


def test_hub_moved_to_games(client: FlaskClient) -> None:
    html = client.get("/games").get_data(as_text=True)
    assert "Flag Reveal" in html and "Border Bridge" in html and "Coming soon" in html
    assert 'class="feed-item"' not in html
