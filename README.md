# Geo Arcade

A small, polished website of browser geography games, built with **Flask**, Jinja2 and a little Alpine.js.

- **Flag Reveal**: a flag is revealed one third at a time, and naming the country earlier scores more points (15 / 10 / 5).
- **Border Bridge**: two countries light up on a globe and a flat map. Connect them by picking the countries in between, one land border at a time.

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

Everything is served locally:
- fonts (Inter), icons (Lucide), Alpine.js, d3 and topojson-client are vendored under `app/shared/static/vendor/`;
- the flag SVGs (flag-icons) are under `app/shared/static/flags/`;
- Border Bridge's map data is in its own `static/data/`.

There's no npm, no bundler and no CDN at runtime.

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
    border_bridge/
      __init__.py          GAME metadata + blueprint
      graph.py             land-border graph: build rules, exclusion lists, BFS, data report
      logic.py             pure rules: config, settings, correct set, options, pairs, scoring
      routes.py            page + JSON API
      data/                trimmed mledoze/countries borders (+ licence)
      templates/border_bridge/play.html
      static/              border_bridge.css/.js, map_view.js (d3 canvas renderer),
                           data/countries-50m.json (Natural Earth via world-atlas), data/geo_meta.json
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
scripts/fetch_border_data.py   download + derive Border Bridge data and libraries
scripts/verify_border_data.py  join report: shapes vs border graph vs countries.json
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

## Border Bridge

Two countries A and B are highlighted. You get four options. Each option borders A, B or a country you've already found.

- **Correct pick**: the country joins the chain, animates onto the map and a fresh set of options appears.
- **Wrong pick** (−5): that option disappears and isn't replaced. The feedback tells you why, for example "Italy borders France, but it's a detour."
- **Solved** (+10 per puzzle, however long the chain): the countries linked to A and the countries linked to B touch.
- **Give up**: shows the shortest chain and scores 0.
- **Time**: a clock counts down from 15 s. Every whole second left when you solve the puzzle is a bonus point.
- **Bonuses**: wrong picks can only cancel the 10 solve points, never more. Solving with no wrong picks earns +5. A perfect, instant solve is worth 30.
- All values live in `BorderBridgeConfig` in `app/games/border_bridge/logic.py`.

**What counts as correct** (`logic.correct_set`, tested in `tests/test_border_logic.py`):
- R is the revealed set. CA and CB are the components of A and B in the subgraph induced by R.
- gap(R) is the shortest hop distance in the border graph between CA and CB, or 0 when they're the same component.
- A country bordering R is correct when adding it reduces the gap. Every other neighbour is a detour.

**Options:**
- Each set shows 1–2 of the currently correct countries.
- The rest are the detours nearest the revealed set's centre, by centroid distance.
- If there aren't enough detours, the nearest non-bordering countries fill in.
- Eliminated countries don't come back unless they've since become correct.

**Pairs:**
- A and B are never neighbours.
- The number of countries between them depends on difficulty: Easy 1, Normal 1–2, Hard 2–4.
- Their mainland centroids are at most 2500 km apart (4000 km on Hard).
- All valid pairs are computed and cached at startup.
- Pairs don't repeat within a round, and the same country isn't reused as A or B too often.

**Settings:**
- View: flat map (default), globe or both. With "both", a phone shows tabs.
- Show country borders: when off, background land is drawn as one seamless mass. Highlighted countries keep their outlines.
- Difficulty, puzzles per round (1–30) and region.
- The view setting doesn't affect high scores.

**Rendering:**
- Both views use d3-geo on a `<canvas>`: an orthographic globe with gradient ocean, atmosphere glow and shading, and a Natural Earth flat map. There's no WebGL.
- Background land is split into small pieces that are culled to the view and pre-simplified into four levels of detail. While the camera moves it draws a coarse level, then redraws at full detail once it stops.
- The camera auto-frames each country's *mainland* (French Guiana, Alaska, Hawaii, Svalbard and the Canaries are drawn but ignored for framing). It clips huge countries to a circle around the pair, so Russia as an intermediate doesn't zoom out to the whole world.
- With reduced motion the camera jumps instead of flying.

### Data pipeline

```bash
python scripts/fetch_border_data.py     # download (once) + derive; --refresh to re-download
python scripts/verify_border_data.py    # join report
```

- **Shapes:** Natural Earth 1:50m admin-0 countries, from the [`world-atlas`](https://github.com/topojson/world-atlas) 2.0.2 `countries-50m.json` TopoJSON.
  `geo_meta.json` is derived from it and holds each country's mainland polygons and centroid. Rings that cross the antimeridian are unwrapped first.
- **Borders:** [`mledoze/countries`](https://github.com/mledoze/countries), via its npm release `world-countries` 5.1.0.
  It's joined to the shapes by ISO numeric code (`ccn3`) and to `countries.json` by ISO alpha-2. `countries.json` gained `cca3`/`ccn3` fields; the change is additive.

**Graph rules** (`graph.py`):
- Borders are made symmetric, and any asymmetries are reported.
- Only UN member states can be puzzle countries. Territories and disputed areas are drawn as background, including Gibraltar, Kosovo, Western Sahara, Palestine, Northern Cyprus, Somaliland, French Guiana and Hong Kong.
- Overseas-only borders are excluded: France–Brazil, France–Suriname, France–Netherlands.
- Data fixes are excluded too:
  - India–Sri Lanka: one-sided in the source and not a land border.
  - Djibouti–Somalia: runs through Somaliland, which is drawn separately.
- Island countries are background only.
- Spain–Morocco (Ceuta/Melilla) and Russia–Poland/Lithuania (Kaliningrad) are kept on purpose.

**Current report:**
- 155 countries and 307 land borders.
- No graph node is missing a shape or a name.
- The only asymmetry in the source is LKA→IND, which is excluded.
- 80 shapes are background only: 42 territories, 37 island states and Sri Lanka.
- Five shapes have no ISO id (Kosovo, N. Cyprus, Somaliland, Siachen Glacier, Indian Ocean Ter.). They are drawn but never referenced.
- Tuvalu has no 50m shape.
- Ashmore and Cartier Is. reuse Australia's id `036`, so the larger shape is kept.

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
- Licences:
  - flag-icons (MIT), Lucide (ISC), Inter (OFL), d3 and topojson-client (ISC).
  - world-atlas (ISC), with Natural Earth data in the public domain.
  - mledoze/countries (ODbL 1.0; attribution: Mohammed Le Doze).
  - Each licence file is next to its vendored files.
