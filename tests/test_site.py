"""Hub, styleguide and registry wiring."""
from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from app.games.registry import GAMES, get_game, playable_games


def test_hub_lists_every_registry_game(client: FlaskClient) -> None:
    html = client.get("/").get_data(as_text=True)
    for game in GAMES:
        assert game.title in html
    assert html.count("Coming soon") >= 1
    assert 'href="/games/flag-reveal/"' in html


def test_routes_generated_from_registry(app: Flask) -> None:
    rules = {r.rule for r in app.url_map.iter_rules()}
    for game in playable_games():
        assert f"/games/{game.id}/" in rules
        assert f"/games/{game.id}/api/start" in rules


def test_game_page_and_redirect(client: FlaskClient) -> None:
    resp = client.get("/games/flag-reveal/")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "Flag Reveal" in html
    assert "Questions per round" in html  # settings schema rendered
    assert client.get("/games/flag-reveal").status_code in (301, 302, 308)
    assert client.get("/games/capital-quest").status_code == 404
    assert client.get("/games/nope").status_code == 404


def test_styleguide(client: FlaskClient) -> None:
    html = client.get("/styleguide").get_data(as_text=True)
    for word in ("Buttons", "Cards", "Inputs", "Badges", "Progress ring"):
        assert word in html


def test_no_external_assets(client: FlaskClient) -> None:
    for url in ("/", "/styleguide", "/games/flag-reveal/", "/games/border-bridge/"):
        html = client.get(url).get_data(as_text=True)
        assert "http://" not in html.replace("http://www.w3.org", "")
        assert "https://" not in html.replace("https://www.w3.org", "")


def test_get_game() -> None:
    assert get_game("flag-reveal") is not None
    assert get_game("missing") is None
