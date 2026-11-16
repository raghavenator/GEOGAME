# Geo Arcade

A small, polished website of browser geography games, built with **Flask**, Jinja2 and a little Alpine.js.
The homepage is a vertical, Shorts-style feed: swipe or scroll from one game to the next, each preloaded and one tap from playing.

- **Flag Reveal**: a flag is revealed one third at a time. Pick the country from 8 options; answering earlier and faster scores more.
- **Border Bridge**: two countries light up on a globe and a flat map. Connect them by picking the countries in between, one land border at a time.
- **Capital Quest**: a country lights up on the map. Pick its capital from up to 8 options before the points drain away.
- **Pinpoint**: a country is named. Move the map until the fixed crosshair sits on it and lock in your guess. Closer and faster scores more.
- **Currency Quest**: a country is shown by name and flag. Pick the currency it uses from up to 8 options before the points drain away.

## Run it

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
flask run                 # http://127.0.0.1:5000 (the feed); /games lists every game
pytest                    # run the test suite
```

`flask run` finds the app through `.flaskenv` (`FLASK_APP=app`). Add `--debug` for auto-reload.
High scores are stored in `instance/geo_arcade.sqlite3`, which is created on first run.

Everything is served locally:
- fonts (Inter), icons (Lucide), Alpine.js, d3 and topojson-client are vendored under `app/shared/static/vendor/`;
- the flag SVGs (flag-icons) are under `app/shared/static/flags/`;
- the world map data (Natural Earth 50m) is in Border Bridge's `static/data/`, shared through `app/shared/geo.py`.

There's no npm, no bundler and no CDN at runtime.

## Project layout

```
app/
  __init__.py              app factory: registers every game's blueprint; / is the feed, /games the hub
  feed.py                  the homepage feed: ordered card config + validation against the registry
  games/
    registry.py            the list of games (playable + "coming soon")
    flag_reveal/
      __init__.py          GAME metadata + blueprint
      logic.py             pure rules: config, settings schema, scoring, stages, question picking
      routes.py            page, JSON API (start/begin/pause/resume/pick/...), tokenised flag endpoint
      templates/flag_reveal/_embed.html   the game's markup as macros (full page + feed card)
      templates/flag_reveal/play.html     full page: renders the embed panel in page mode
      static/              flag_reveal.css, flag_reveal.js (component), embed.js (registers the game)
    border_bridge/
      __init__.py          GAME metadata + blueprint
      graph.py             land-border graph: build rules, exclusion lists, BFS, data report
      logic.py             pure rules: config, settings, correct set, options, pairs, scoring
      routes.py            page + JSON API (start/begin/pause/resume/pick/...)
      data/                trimmed mledoze/countries borders (+ licence)
      templates/border_bridge/_embed.html, play.html
      static/              border_bridge.css/.js, embed.js,
                           data/countries-50m.json (Natural Earth via world-atlas), data/geo_meta.json
                           (the world map data both map games use, see shared/geo.py)
    capital_quest/
      __init__.py          GAME metadata + blueprint
      capital_data.py      capital override / confirmation / exclusion tables + the data join
      logic.py             pure rules: config, settings, options, scoring, play clock, state
      routes.py            page + JSON API (start/begin/pick/timeout/next/pause/resume/summary)
      data/capitals.json   the eligible pool, built by scripts/build_capitals.py
      templates/capital_quest/_embed.html, play.html
      static/              capital_quest.css/.js, embed.js
    pinpoint/
      __init__.py          GAME metadata + blueprint
      location_data.py     reference-point override table, point-in-polygon check + the data join
      logic.py             pure rules: haversine, size-relative scoring, speed factor, play clock, state
      routes.py            page + JSON API (start/begin/submit/next/pause/resume/summary)
      data/locations.json  the eligible pool, built by scripts/build_locations.py
      templates/pinpoint/_embed.html, play.html
      static/              pinpoint.css/.js, embed.js
    currency_quest/
      __init__.py          GAME metadata + blueprint
      currency_data.py     correction / primary-override / ISO-review tables, the data join, the ISO 4217 check
      logic.py             pure rules: config, settings, distractors by difficulty, unique-currency rounds, labels
      routes.py            page + JSON API (start/begin/pick/timeout/next/pause/resume/summary)
      data/currencies.json the eligible pool + reverse index, built by scripts/build_currencies.py
      templates/currency_quest/_embed.html, play.html
      static/              currency_quest.css/.js, embed.js
  shared/
    countries.py           country loader + forgiving answer matcher (case/accents/aliases/typos)
    settings.py            declarative settings schemas, validation, high-score keys
    scoring.py             generic scoring helpers, incl. the draining-points rules (Capital Quest, Currency Quest)
    timed_choice.py        timed multiple choice where wrong options disappear: question/round state, play clock
    highscores.py          SQLite best scores per (game, settings)
    round_store.py         thread-safe in-memory store for live rounds
    api.py                 JSON error helpers
    game.py                GameSpec: the contract every game fulfils (incl. embed template/context, map_zone)
    geo.py                 where the world map data lives, the UN-member pool rule, its browser URLs
    data/countries.json    195 countries (UN members + observers) with regions and aliases
    templates/             base.html, feed.html, hub.html, styleguide, settings modal, UI macros
    static/css/tokens.css  design tokens (the only place raw colours live)
    static/css/base.css    shared components (incl. the points meter);  static/css/feed.css  the feed
    static/js/             core.js (API, timers, embed contract), settings.js (per-game settings stores),
                           feed.js (feed controller), map_view.js (d3 canvas globe/flat map renderer,
                           window.GeoMaps; window.BorderMaps is the same object)
    static/flags/          sanitised flag SVGs
scripts/vendor_flags.py    re-import flags from the flag-icons package
scripts/fetch_border_data.py   download + derive Border Bridge data and libraries
scripts/verify_border_data.py  join report: shapes vs border graph vs countries.json
scripts/build_capitals.py      build Capital Quest's capitals.json + print its review report
scripts/build_locations.py     build Pinpoint's locations.json + print its review report
scripts/build_currencies.py    build Currency Quest's currencies.json, cross-check ISO 4217, print the report
scripts/mledoze_source.py      the pinned mledoze/countries file the build scripts read
tests/                     pytest: matching, game logic, settings, API flow, play clock, feed, hub wiring
```

Visit `/styleguide` to see every shared component.

## The feed (homepage)

`/` is a full-screen feed of cards that snap one at a time (CSS scroll snap, `dvh` units), in the order set by
`FEED` in `app/feed.py`: Flag Reveal, Border Bridge, Capital Quest, Pinpoint, Currency Quest, then a "Coming soon" card. It doesn't loop.

- **Preloaded, one tap to start.** On load every game card runs `prepare()` in parallel: it creates a round on the
  server and loads what it needs (Flag Reveal fetches the first flag's bytes without rendering it; Border Bridge
  loads the map and shows an idle, slowly turning globe). Each card shows a skeleton while preparing, a Retry button
  if that fails, and a dimmed "Tap to play" overlay when ready. Nothing about the question is visible and no timer
  runs before the tap.
- **Only the active card is live.** An `IntersectionObserver` picks the card that's at least 60% visible. Every
  other card is `inert` and its game is paused (timers held, map rendering stopped), and resumed when you come back.
- **Navigation.** Swipe (native snap on touch), wheel/trackpad (one card per gesture, so momentum can't skip
  cards), progress dots, up/down buttons on desktop, and keys ArrowUp/ArrowDown, PageUp/PageDown and J/K, which are
  ignored while a text field or the map has focus. The URL hash follows the active card (`#border-bridge`) and is
  honoured on load. A "Swipe up for next game" hint shows until your first scroll (remembered in localStorage).
- **Pull to refresh.** At the top of the first card, pulling down (touch) or scrolling up past the top (wheel)
  shows an indicator and, past a threshold, reloads with fresh rounds. It never starts from a map, while a text
  field is focused, or while the first game is mid-round.
- **Gesture zones.** Currency Quest has none: its small locator globe is decorative (`pointer-events: none`), so a
  swipe anywhere on that card, even one starting on an option button, changes card (the browser doesn't fire a click
  for a touch that became a scroll).
  In Border Bridge, Capital Quest and Pinpoint the map is a fully interactive zone (`data-gesture-zone="map"`,
  `touch-action: none`, pointer capture): dragging, rotating, panning, pinching and wheel/trackpad zoom there never
  move the feed. Everything else on the card (header strip, chips, options, margins) swipes as normal. The map sits
  in the top half of the portrait card with a clear border, and a one-time hint says "Swipe outside the map to
  change game". +/−/reset buttons, arrow buttons and keys are the non-gesture alternatives.
- **Settings and summaries.** The gear on a card (or in the mobile top bar) opens that game's settings; saving
  them discards the prepared round and prepares a new one. A finished round shows its summary in the card with
  "Play again" and "Next game ↓".
- **Mobile keyboard.** While a text field in a card is focused, feed scrolling and key navigation are locked and
  the feed is sized from `visualViewport`, so the keyboard can't make the snap jump to another card.

On desktop each card is a 9:16 portrait card with the logo on the left and the dots/arrows on the right, over a
soft gradient tinted per card. On narrow screens the card fills the viewport under a slim floating top bar.

### Embed contract

Every game registers itself in the browser with `GeoArcade.games[id] = { mount(container, options) }` (each game's
`static/embed.js`; the shared adapter is `GeoArcade.alpineEmbed` in `core.js`). `mount` returns an instance:

| Method | What it does |
| --- | --- |
| `prepare()` | Create a round on the server, load data and assets, render the locked **ready** state. Resolves when ready. |
| `start()` | Begin play (call it from the player's tap). Tells the server, which starts the scoring clock. |
| `pause()` / `resume()` | Card left / came back: hold and release timers, animation loops and rendering. |
| `restart()` | Throw away the current round and start a fresh one with the current settings (the restart button). |
| `destroy()` | Remove listeners and stop everything. |

`options` = `{ mode: "feed" | "page", onState(state, detail), onRoundFinished(summary), onNextGame() }`, where state
is one of `idle | preparing | ready | playing | paused | finished | error`.

The same code serves both places: each game's markup lives in `templates/<game>/_embed.html` as macros
(`styles()`, `scripts()`, `panel(ctx, mode)`). The full page (`/games/<id>/`) renders `panel(..., mode="page")` with
its start screen, whose Start button runs `prepare()` then `start()`; the feed renders `mode="feed"` (compact
portrait layout, no start screen). Settings are per game: templates use the `$settings` magic (the store of the
nearest `[data-game]`), JS uses `GeoArcade.settings(id)`.

### Play clock (server)

A prepared round can sit idle for a long time, so scoring time starts at `POST api/begin`, not at `api/start`.
`api/pause` and `api/resume` shift paused time out of the current question or puzzle (Flag Reveal's speed bonus,
Border Bridge's countdown). Picks, timeouts and "next" before `begin` return `409 not_started`; acting while paused
resumes first. If a prepared round expired from the `RoundStore` (2 h TTL), `begin` returns `404 round_not_found`
and the client silently prepares a fresh round and begins that one.

### How to add a game to the feed

1. Make the game embeddable: put its markup in `templates/<game>/_embed.html` with macros `styles()`, `scripts()`
   and `panel(ctx, mode)`; give its Alpine component `attach/prepare/begin/pause/resume/destroy` (copy the pattern
   in `flag_reveal.js`); add `static/embed.js` with `GeoArcade.games["<id>"] = GeoArcade.alpineEmbed("<id>")`; add
   `api/begin`, `api/pause` and `api/resume` and start its clock at `begin`.
2. Set `embed_template="<game>/_embed.html"` and `embed_context=routes.embed_context` on its `GameSpec`.
3. Add `game("<id>", tint="amber")` to `FEED` in `app/feed.py` (tints: blue, green, amber, violet). The app checks
   the feed against the registry at startup and refuses unknown, unplayable or duplicate entries.
4. If the game has a zone with its own gestures (like a map), mark it `data-gesture-zone="map"`, keep it to
   about half of the card and set `map_zone=True` on its `GameSpec` (the feed then shows the one-time
   "Swipe outside the map" hint on that card). Don't put `overscroll-behavior: contain` on scrollable parts of the
   card outside the map, or swipes there can't change card.

## Flag Reveal scoring

You pick the country from 8 options. The wrong options come from the same region where possible, and a lookalike flag (Chad/Romania, Indonesia/Monaco) is never offered alongside its twin.

- **Stage points:** 15 / 10 / 5, depending on how much of the flag is showing when you answer.
- **Wrong pick:** −5. The option disappears and isn't replaced. Stage points never drop below 0.
- **Speed bonus:** every whole second left in the current stage when you answer correctly is +1, so a 5-second stage can add up to 5.
- **Time out:** 0, and the correct option is highlighted.
- Multiple-choice best scores are stored separately from the old typed-answer ones.

## How Flag Reveal stays honest

- The **server** picks non-repeating countries and the options, checks picks, scores and stores high scores.
  The client never learns which option is correct until the question ends.
- The speed bonus uses the client's timer, which pauses while the tab is hidden. The server caps it at what its own clock allows, plus a 0.75 s allowance for loading the flag.
- Flags are served from `/games/flag-reveal/flag/<random-token>`, not `/flags/fr.svg`.
  The SVGs are also stripped of ids that would reveal the country.
- The client reports which stage it's on. The server only lets the stage move forward, so a client can't claim an earlier stage to win more points.
  Timeouts and "next" are idempotent, so a double-fired timer or a double-clicked button can't skip anything.
- The **client** only handles presentation: the stage timer (paused while the tab is hidden), the clip-path reveal, animations and the option buttons (keys 1–8).

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

## Capital Quest

A country is highlighted on the map (no names or labels). Pick its capital from 8 options (4 or 6 in settings).

- **Points drain:** each question starts at **20** and drains linearly to 0 over the time limit (20 s by default).
- **Wrong pick:** −3 from the points available. That option disappears and the clock keeps running.
- **Correct:** `max(2, available − 3 × wrong picks)`, rounded half up to a whole number. A right answer before the
  timeout is never worth less than 2.
- **Time out:** 0, and the capital is revealed in the options and on the map.
- The round maximum is questions × 20. The summary lists country, capital, time, wrong picks and points per question.
  Best scores are kept per questions, time, options, framing and region. Map view doesn't split them.
- All values live in `CapitalQuestConfig` in `app/games/capital_quest/logic.py`. The scoring functions and the
  question/round state machine are shared with Currency Quest (`app/shared/scoring.py` `drain_*`,
  `app/shared/timed_choice.py`); `logic.py` binds them to its config and keeps the same function names.

**How it stays honest:**
- `start` creates the round but returns nothing about its questions. `begin` (the tap) starts the server clock and
  returns the first question: the shape id to highlight and the option names, with opaque ids.
- Which option is correct is only sent when the question ends (correct pick, timeout or a late pick).
- Elapsed time is the server's own clock from `begin` / `next`, with paused time shifted out (the card scrolled
  away or the tab was hidden). The client's points meter is only an estimate for display.
- A pick up to 0.5 s after the limit still counts (network grace). Later picks end the question as a timeout.
  A `timeout` more than 1 s earlier than the server's clock is refused with `too_early` and the remaining time.

**Options:** the answer plus distractors from other eligible capitals. About half come from the target's UN
subregion, topped up from its continent, and the rest from other continents. Names are unique (accents and case
ignored), never equal the answer, and the order is shuffled.

**Settings:** questions (1–30), time per question (10–60 s), options (4/6/8), map view (flat/globe), map framing
(zoom to region by default, which is easier, or world view) and region. In world view, small countries (Malta, Singapore,
Liechtenstein…) get a pulsing marker ring.

**Rendering:** the shared `map_view.js` renderer (moved from Border Bridge; its behaviour there is unchanged)
with a `target` highlight role (`--map-target`). The highlight fades and pulses in. World view turns the globe to
the country or keeps the flat map whole. Zoom to region frames the country's mainland and pulls back to show its
neighbours.

### Capitals data

```bash
python scripts/build_capitals.py           # rebuild data/capitals.json and print the review report
```

- **Capitals and subregions:** [`mledoze/countries`](https://github.com/mledoze/countries) `capital` and
  `subregion` fields (npm `world-countries` 5.1.0, the same pinned file Border Bridge uses).
- **Joined to** the shared `countries.json` (ids, English names, continents) and the 50m shapes (by `ccn3`).
- **Pool:** the same rule as Border Bridge, independent UN member states (`app/shared/geo.py:is_un_state`). Palestine,
  Kosovo, Taiwan and Western Sahara are not in it. Every country also needs a shape (Tuvalu has none at 50m) and
  exactly one agreed capital.
- **Tables** in `app/games/capital_quest/capital_data.py`:
  - `CAPITAL_OVERRIDES` gives a single answer or fixes a spelling: South Africa → Pretoria, Eswatini → Mbabane,
    Washington, D.C., Ulaanbaatar, Reykjavík, St. John's.
  - `CONFIRMED_SINGLE_CAPITALS` keeps the source's single value on purpose: Netherlands (Amsterdam), Côte d'Ivoire,
    Benin, Malaysia, Tanzania, Switzerland, Nauru, Montenegro, Yemen.
  - `EXCLUDED_CAPITALS` lists Bolivia (Sucre / La Paz), Sri Lanka (Sri Jayawardenepura Kotte / Colombo) and Israel
    (Jerusalem's status is disputed).
  - Any other country with several or no capitals in the source is excluded automatically.
- **Current report:** 190 eligible countries (Africa 54, Americas 34, Asia 44, Europe 45, Oceania 13). No
  duplicate names and no empty capitals. Note that the source flags Vatican City as a UN member, so it is in the
  pool for both map games.

## Pinpoint

A country's name appears (nothing else: no labels, names or highlights on the map). Move the map, or turn the globe,
until the large fixed crosshair in the middle of the map zone sits on it, then press **Submit** (Enter or Space).

- **The guess** is the point under the crosshair: `MapView.centerLonLat()` inverts the projection at the centre of the
  map zone, which works for the flat map and the globe. Longitude is normalised to [-180, 180].
- **The map moves, the crosshair doesn't.** Drag (with inertia), pinch, wheel, +/− and Reset; arrow keys pan and
  +/− zoom. Zoom runs from the whole world to 24×. The flat map is clamped so the crosshair can never leave the map
  past a pole, and it stays centred while the whole map fits.
- **The result** drops a guess pin and an actual pin, draws a dashed great-circle line between them, highlights the
  country and flies the camera to fit all three (on the globe, pins more than 80° apart are shown by facing the
  midpoint of the line with the whole globe in view). It shows the distance (km or miles), accuracy, speed factor and
  points. **Next** moves on; there's no auto-advance.
- **Time:** a bar drains to the limit (30 s by default) with the seconds and the live speed factor beside it. When it
  runs out, the crosshair position is submitted for you with the minimum speed factor.

**Scoring** (`PinpointConfig` in `app/games/pinpoint/logic.py`): a country's reference point is mledoze's `latlng`
(roughly its geographic centre, not its capital), and the score is relative to the country's size so huge countries
aren't scored on their centre alone.

- `d` = great-circle (haversine) distance from the guess to the reference point.
- `r = sqrt(area / π)`: the radius of a circle with the country's area.
- `tolerance = min(tol_factor × r, 1200 km)` counts as a direct hit; `d_eff = max(0, d − tolerance)`.
- `scale = clamp(scale_factor × r, 150 km, 1500 km)`.
- **Inside the borders:** a guess anywhere within the country's own outline (its Natural Earth 50m polygons, holes
  such as Lesotho excluded, exclaves such as Kaliningrad included) gets full accuracy, 100, however far it is from
  the reference point. Tuvalu has no 50m shape, so it's scored on distance alone.
- **Otherwise, accuracy** = `round(100 × exp(−d_eff / scale))`.
- **Speed factor** = 1.0 up to 5 s, then linear down to 0.5 at the time limit. It only scales accuracy, so a fast,
  far-off guess earns nothing extra.
- **Points** = `round(accuracy × speed)`, up to 100 per question.
- Difficulty: Easy (0.6, 1.5), **Normal (0.35, 1.0)**, Hard (0.15, 0.6) for (`tol_factor`, `scale_factor`).

Example points on Normal with a 3-second guess that lands outside the country (inside it would be 100):

| Country | r | Tolerance | Scale | 50 km | 200 km | 500 km | 1500 km |
|---|---:|---:|---:|---:|---:|---:|---:|
| Luxembourg | 29 km | 10 km | 150 km | 77 | 28 | 4 | 0 |
| Austria | 163 km | 57 km | 163 km | 100 | 42 | 7 | 0 |
| France | 419 km | 147 km | 419 km | 100 | 88 | 43 | 4 |
| Russia | 2,333 km | 817 km | 1,500 km | 100 | 100 | 100 | 63 |

**How it stays honest:**
- `start` creates the round but says nothing about its questions. `begin` (the tap) and `next` start the server
  clock and send only the country's display name: no coordinates, id, code or shape.
- `submit` takes the crosshair's `lat`/`lng` (validated: finite, [-90, 90] and [-180, 180]) and returns the true point,
  the country id (for the highlight), the distance, whether it was inside the borders, and the points. The border
  test runs on the server against the same 50m shapes the map draws (`app/shared/geo.py:load_country_polygons`,
  decoded once at startup). A second submit for the same question is refused.
- Elapsed time is the server's clock with paused time shifted out. The client's countdown sends `timeout: true` when
  it ends; the server refuses it with `too_early` if its own clock has more than 1 s left, and scores it with the
  minimum speed factor otherwise (as it does any submit that arrives after the limit).

**Settings:** questions (1–30), time limit (15–120 s), difficulty, map view (flat/globe), country borders (on/off),
distance units (km/miles) and region. Best scores are kept per questions, time, difficulty, borders and region.

### Locations data

```bash
python scripts/build_locations.py          # rebuild data/locations.json and print the review report
```

- **Reference point and size:** [`mledoze/countries`](https://github.com/mledoze/countries) `latlng` and `area`
  (plus `subregion`), from the same pinned `world-countries` 5.1.0 file as the other games.
- **Pool:** the same independent UN member states as Border Bridge and Capital Quest (`is_un_state`): 194 countries.
  None is missing a `latlng` or `area`. Tuvalu has no 50m shape, so it's playable but its answer isn't highlighted.
- **Sanity check:** every reference point is tested against its own Natural Earth polygons (holes included,
  antimeridian-aware). The report lists the ones outside and how far.
- **`LOCATION_OVERRIDES`** in `app/games/pinpoint/location_data.py` replaces points that are clearly wrong or
  misleading, with a point inside the country (its mainland centroid where that works):
  - Kuwait (29.5, 45.75) → (29.334, 47.587): the source point is ~90 km west, in Iraq / Saudi Arabia.
  - San Marino (43.767, 12.417) → (43.942, 12.459): the source point is ~20 km south, in Italy.
  - Israel (31.47, 35.13) → (31.0, 34.85): the source point (and the polygon centroid) falls in the West Bank.
  - Cuba (21.5, −80.0) → (21.616, −79.024): the source point is in the sea south of the island.
- **Outside their polygon, kept on purpose** (19): archipelagos and island groups (Fiji 239 km, Marshall Islands 199,
  Tonga 123, São Tomé and Príncipe 73, Cabo Verde 63, Bahamas 48, Maldives 42, Philippines 23, Trinidad and Tobago 20,
  Seychelles 19, Vanuatu 18, Kiribati 7, Comoros 3, Palau 3), narrow Gambia (4), and tiny countries whose 50m outline is
  coarse (Malta 3, Monaco 2, Liechtenstein 1, Vatican City 1).

## Currency Quest

A country is shown: its name, its flag and (optionally) a small globe with it highlighted. Pick the **currency** it
uses from 8 options (4 or 6 in settings). Options are currencies, never countries.

- **Scoring** is Capital Quest's, from the same shared code: 20 points draining linearly to 0 over the time limit
  (20 s by default), −3 per wrong pick, a correct answer is worth `max(2, available − 3 × wrong picks)` (whole
  points, rounded half up), and a timeout scores 0. The round maximum is questions × 20.
- **Wrong pick:** that option disappears for the rest of the question, the option shakes, the meter floats "−3",
  and the clock keeps running.
- **Result:** the currency's name, ISO code and symbol, and "Also used in: …" (up to 6 other countries, those whose
  own currency it is first, then "+N more"). The next question comes after 2.5 s (3 s after a timeout), or on Next.
- The summary lists country, currency, time, wrong picks and points per question. Best scores are kept per
  questions, time, options, difficulty and region. The locator doesn't split them.
- All values live in `CurrencyQuestConfig` in `app/games/currency_quest/logic.py`.

**One right answer only** (`logic.generate_options`, tested in `tests/test_currency_logic.py`):
- Each country has a *correct set* (every currency the data lists for it) and one *primary* currency, which is the
  answer shown. No other currency of the correct set is ever a distractor, so Panama (PAB) never sees the US dollar
  and Lesotho (LSL) never sees the rand.
- Every option is a distinct ISO code, and a shared currency like the euro appears once. Distractors are other
  countries' primary currencies, never named like another option.
- **Options show only the ISO code and the symbol** (large, e.g. "YER ﷼"), never the name, because "Yemeni rial"
  would give the answer away. The server doesn't send names during the question. A wrong pick's name comes back
  with its result ("Not MWK, the Malawian kwacha"), and every option's name is shown once the question ends.
  Codes are unique, so two options never read the same. Many codes still start with the country's own code
  (YER, JPY), which is part of the puzzle.
- **A currency never repeats in a round.** Countries are drawn uniformly at random, never repeated, and a country
  whose currency is already in the round is skipped, so the euro (26 countries) comes up at most once.
- **Difficulty** only changes the distractors. Easy: random. Normal: about half from the target's UN subregion,
  topped up from its continent, the rest random. Hard: about half look-alikes, nearest first, the rest from the
  region. A look-alike has the same symbol ("$", "Fr", "£", "kr", "Sh", "Rs"…) or the same name family (dollar,
  franc, peso, pound, dinar/denar, rupee/rupiah, dirham, krone/krona/koruna, rial/riyal, shilling, ruble, lira,
  leu/lev, won, kwacha, manat, som).

**How it stays honest:** the same as Capital Quest. `start` says nothing about the questions. `begin` (the tap)
starts the server clock and sends the country (name, flag URL, shape id) and the options' display data under
opaque ids. Which option is right is only sent when the question ends. Elapsed time is the server's clock with
paused time shifted out, with the same late-pick grace and early-timeout checks.

**Settings:** questions (1–30), time per question (10–60 s), options (4/6/8), difficulty (Easy/Normal/Hard), show
map locator (on/off) and region.

**Layout:** a header strip (question, score, points meter), the country block (flag and locator on top, the name
below; about a third of a feed card), then the options as a 2-column grid of large buttons (keys 1–8). The result
takes the question's place in the country block. The locator is the shared `MapView` globe with
`interactive: false`. Its map data loads in the background while the round is prepared, and only when the locator
is on.

### Currency data

```bash
python scripts/build_currencies.py           # rebuild data/currencies.json, check ISO 4217, print the review report
python scripts/build_currencies.py --no-iso  # offline: skip the ISO 4217 cross-check
```

- **Currencies:** [`mledoze/countries`](https://github.com/mledoze/countries) `currencies` (ISO 4217 code → name,
  symbol) and `subregion`, from the same pinned `world-countries` 5.1.0 file as the other games, joined to the shared
  `countries.json`.
- **Pool:** the same independent UN member states as the other games (`is_un_state`), with at least one currency and
  exactly one primary: **194 countries, 142 currencies**. Palestine is the only `countries.json` entry left out (not
  a UN member state). No multi-currency country is excluded, because each has an override. Tuvalu has no 50m shape,
  so it has no locator.
- **Cross-check:** the build downloads the ISO 4217 active currency list ("list one", published by SIX for the ISO
  4217 maintenance agency; git-ignored under `data/raw/`) and compares every country's final list with its ISO entry.
  It fails on any difference that isn't reviewed, any country it can't match, and any code that isn't an active ISO
  code. Against the list published 2026-09-17, 187 of 194 countries match exactly and the other 7 are reviewed below.
- **Tables** in `app/games/currency_quest/currency_data.py`:
  - `CURRENCY_CORRECTIONS` fixes source lists that are out of date or incomplete:
    - Bulgaria: BGN → EUR (euro since 1 January 2026).
    - Sierra Leone: SLL → SLE (2022 redenomination).
    - Cuba: drops CUC (withdrawn 2021).
    - Zimbabwe: ZWB → ZWG (ZiG since April 2024); its multi-currency basket stays in the correct set.
    - Kiribati and Tuvalu: drop their non-ISO local coins (KID, TVD).
    - Micronesia: gets USD (the source has none).
    - Haiti: adds USD, as ISO lists it.
  - `PRIMARY_OVERRIDES` (the answer for a country with several currencies): Panama PAB, Bahamas BSD, Brunei BND,
    Bhutan BTN, Cambodia KHR, Eswatini SZL, Lesotho LSL, Namibia NAD, Haiti HTG, Zimbabwe ZWG.
  - `ISO_DIFFERENCES_REVIEWED` lists the remaining differences from ISO, kept on purpose:
    - Cambodia and Bahamas keep USD as a second currency, and Brunei keeps SGD.
    - Eswatini keeps the rand, and Zimbabwe keeps its basket.
    - El Salvador leaves out the colón (ISO still lists SVC, which hasn't circulated since 2001).
    - Venezuela leaves out VED (the "digital" bolívar, never offered).
  - `CURRENCY_INFO` sets display names and symbols:
    - the two new currencies (SLE, ZWG);
    - names that don't say whose: "lari", "dalasi" and "denar" become Georgian lari, Gambian dalasi and Macedonian denar;
    - a stray script in the Sri Lankan rupee's symbol;
    - source rows that disagree (CHF "Fr"/"Fr.", ZAR "R"/"Rs");
    - "Venezuelan bolívar".
- **Report:** no currency lacks a symbol, and no display names collide. Most shared currencies (countries listing
  it / using it as their own):

  | Currency | Countries |
  | --- | --- |
  | Euro | 27 / 26 |
  | US dollar | 12 / 7 |
  | West African CFA franc | 8 |
  | Central African CFA franc | 6 |
  | Eastern Caribbean dollar | 6 |
  | South African rand | 5 / 1 |
  | Australian dollar | 4 |

## How to add a new game

1. **Create a package** at `app/games/<your_game>/`:
   ```python
   # app/games/your_game/__init__.py
   from flask import Blueprint
   from app.shared.game import GameSpec
   from app.shared.settings import IntSetting

   bp = Blueprint("your_game", __name__, template_folder="templates",
                  static_folder="static", static_url_path="/static")
   from . import routes  # defines a view named `play` at "/"

   GAME = GameSpec(
       id="your-game",                        # URL: /games/your-game/
       title="Your Game",
       description="What the player does.",
       icon="shapes",                         # any SVG in shared/static/vendor/lucide
       blueprint=bp,
       settings_schema=(IntSetting("questions", "Questions per round", 10, 1, 30),),
   )
   ```
2. **Register it.** In `app/games/registry.py`, import `GAME` and add it to `GAMES`, replacing its "coming soon" placeholder if there is one.
   The hub card, the `/games/<id>/` routes and the settings modal are all generated from that entry.
3. **Build the page.** Put the markup in `templates/<your_game>/_embed.html` (see "Embed contract" above) and a
   thin `play.html` that extends `base.html`, passes `game=...` and renders the panel in page mode.
   Use the components from `/styleguide` and the tokens from `tokens.css`, with no hard-coded colours.
   To put it in the homepage feed, follow "How to add a game to the feed".
4. **Reuse the shared code:**
   - `get_index().match(text)` for forgiving country answers
   - `validate_settings(schema, raw)` for validating settings sent by the client
   - `highscores.record_score(...)` for best scores
   - `RoundStore` for round state
   - `app/shared/timed_choice.py` + `scoring.drain_*` for timed multiple choice with disappearing wrong options
     (Capital Quest and Currency Quest), and `ui.points_meter()` / `GeoArcade.drainMeter` for its live meter
   - `GeoArcade.StageTimer` (with `hold()`/`release()`), `GeoArcade.api`, `GeoArcade.settings(id)` and `GeoArcade.alpineEmbed` in the browser
5. **Test it.** Put the rules in a pure `logic.py` and unit-test them in `tests/`.
   `tests/test_site.py` already checks that every registry game appears on the hub and has its routes registered.

## Notes

- Live rounds are kept in memory, which is fine for `flask run` (one process). To run several workers, put `RoundStore` behind Redis or SQLite.
- Flags whose designs are effectively identical (Chad/Romania, Indonesia/Monaco) are never offered as options together.
- To refresh flags: download the `flag-icons` npm package, extract it, and run `python scripts/vendor_flags.py <extracted>/package/flags/4x3`.
- Licences:
  - flag-icons (MIT), Lucide (ISC), Inter (OFL), d3 and topojson-client (ISC).
  - world-atlas (ISC), with Natural Earth data in the public domain.
  - mledoze/countries (ODbL 1.0; attribution: Mohammed Le Doze), for Border Bridge's borders, Capital Quest's
    capitals and subregions, Pinpoint's reference points and areas, and Currency Quest's currencies
    (`capitals.json`, `locations.json` and `currencies.json` are derived databases under the same licence).
  - The ISO 4217 list is only read by `scripts/build_currencies.py` to check the data; it isn't redistributed.
  - Each licence file is next to its vendored files.
