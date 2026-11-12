# Geo Arcade

A small, polished website of browser geography games, built with **Flask**, Jinja2 and a little Alpine.js.
The first game is **Flag Reveal**: a flag is revealed one third at a time, and naming the country earlier scores more points (15 / 10 / 5).

## Run it

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
flask run                 # http://127.0.0.1:5000
pytest                    # run the test suite
```

`flask run` finds the app through `.flaskenv` (`FLASK_APP=app`). Add `--debug` for auto-reload.
High scores are stored in `instance/geo_arcade.sqlite3`, which is created on first run.

Everything is served locally: fonts (Inter), icons (Lucide), Alpine.js and flag SVGs (flag-icons) are vendored under `app/shared/static/`.
There's no npm, no bundler and no CDN.

## Project layout

```
app/
  __init__.py              app factory: registers every game's blueprint from the registry
  games/
    registry.py            the list of games (playable + "coming soon")
    flag_reveal/
      __init__.py          GAME metadata + blueprint
      logic.py             pure rules: config, settings schema, scoring, stages, question picking
      routes.py            page, JSON API, tokenised flag endpoint
      templates/flag_reveal/play.html
      static/              flag_reveal.css, flag_reveal.js
  shared/
    countries.py           country loader + forgiving answer matcher (case/accents/aliases/typos)
    settings.py            declarative settings schemas, validation, high-score keys
    scoring.py             generic scoring helpers
    highscores.py          SQLite best scores per (game, settings)
    round_store.py         thread-safe in-memory store for live rounds
    api.py                 JSON error helpers
    game.py                GameSpec: the contract every game fulfils
    data/countries.json    195 countries (UN members + observers) with regions and aliases
    templates/             base.html, hub, styleguide, settings modal, UI macros
    static/css/tokens.css  design tokens (the only place raw colours live)
    static/css/base.css    shared components
    static/js/             core.js (API, timer, autocomplete), settings.js (settings store)
    static/flags/          sanitised flag SVGs
scripts/vendor_flags.py    re-import flags from the flag-icons package
tests/                     pytest: matching, game logic, settings, API flow, hub wiring
```

Visit `/styleguide` to see every shared component.

## How Flag Reveal stays honest

- The **server** picks non-repeating countries, matches answers, scores and stores high scores.
  The client never receives the answer until the question ends.
- Flags are served from `/games/flag-reveal/flag/<random-token>`, not `/flags/fr.svg`.
  The SVGs are also stripped of ids that would reveal the country.
- The client reports which stage it's on. The server only lets the stage move forward, so a client can't claim an earlier stage to win more points.
  Timeouts and "next" are idempotent, so a double-fired timer or a double-clicked button can't skip anything.
- The **client** only handles presentation: the stage timer (paused while the tab is hidden), the clip-path reveal, animations and autocomplete.

All tunables (points per stage, default durations, feedback delays) live in `FlagRevealConfig` in `app/games/flag_reveal/logic.py`.

## How to add a new game

1. **Create a package** at `app/games/<your_game>/`:
   ```python
   # app/games/capital_quest/__init__.py
   from flask import Blueprint
   from app.shared.game import GameSpec
   from app.shared.settings import IntSetting

   bp = Blueprint("capital_quest", __name__, template_folder="templates",
                  static_folder="static", static_url_path="/static")
   from . import routes  # defines a view named `play` at "/"

   GAME = GameSpec(
       id="capital-quest",                    # URL: /games/capital-quest/
       title="Capital Quest",
       description="Match countries with their capitals.",
       icon="landmark",                       # any SVG in shared/static/vendor/lucide
       blueprint=bp,
       settings_schema=(IntSetting("questions", "Questions per round", 10, 1, 30),),
   )
   ```
2. **Register it.** In `app/games/registry.py`, import `GAME` and add it to `GAMES`, replacing its "coming soon" placeholder if there is one.
   The hub card, the `/games/<id>/` routes and the settings modal are all generated from that entry.
3. **Build the page.** Put it in `templates/<your_game>/play.html`, extend `base.html` and pass `game=...` so the header and settings button appear.
   Use the components from `/styleguide` and the tokens from `tokens.css`, with no hard-coded colours.
4. **Reuse the shared code:**
   - `get_index().match(text)` for forgiving country answers
   - `validate_settings(schema, raw)` for validating settings sent by the client
   - `highscores.record_score(...)` for best scores
   - `RoundStore` for round state
   - `GeoArcade.StageTimer`, `GeoArcade.Autocomplete` and `GeoArcade.api` in the browser
5. **Test it.** Put the rules in a pure `logic.py` and unit-test them in `tests/`.
   `tests/test_site.py` already checks that every registry game appears on the hub and has its routes registered.

## Notes

- Live rounds are kept in memory, which is fine for `flask run` (one process). To run several workers, put `RoundStore` behind Redis or SQLite.
- Flags whose designs are effectively identical (Chad/Romania, Indonesia/Monaco) accept either answer.
- To refresh flags: download the `flag-icons` npm package, extract it, and run `python scripts/vendor_flags.py <extracted>/package/flags/4x3`.
- Licences: flag-icons (MIT), Lucide (ISC), Inter (OFL). Each licence file is next to its vendored files.
