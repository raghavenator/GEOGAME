/* Border Bridge client. Presentation only: the server picks pairs, knows which
   options are correct, validates picks and scores. This file drives the maps,
   options, chips, timer and animations.

   Embed lifecycle (see GeoArcade.alpineEmbed in core.js):
     prepare()  create a round, load the map data, show an idle turning globe -> "ready"
     begin()    tell the server play started, fly to the pair, show options -> "playing"
     pause()/resume()  stop/restart the clock and all map rendering (the card
                       scrolled away); the server shifts paused time out of scoring
   On the full page the Start button runs prepare() then begin(). */
(function () {
  "use strict";
  const GA = window.GeoArcade;
  const GAME_ID = "border-bridge";
  const FLY_IN_MS = 700;
  const sleep = (ms) => new Promise((r) => setTimeout(r, GA.reducedMotion() ? 0 : ms));

  function formatTime(ms) {
    const s = Math.max(0, ms) / 1000;
    if (s < 60) return `${s.toFixed(1)}s`;
    return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  }

  document.addEventListener("alpine:init", () => {
    Alpine.data("borderBridge", (cfg) => {
      const P = {};
      // Runtime objects live in P (per instance, outside Alpine's reactivity). Undeclared
      // `this.x` would land on the outermost scope, i.e. be shared with other games in the feed.
      return {
      screen: cfg.mode === "feed" ? "play" : "start",   // start | play | summary
      phase: "idle",          // idle | ready | playing | ended
      lifecycle: "idle",      // idle | preparing | ready | playing | paused | finished | error
      busy: false,
      error: "",
      mapError: "",
      mapsReady: false,
      best: null,
      maxScore: 0,
      total: 0,
      roundId: null,
      puzzle: null,
      options: [],            // [{ id, name, state: "" | "wrong" | "leaving" | "correct" }]
      trail: { a_side: [], b_side: [], remaining: 0 },
      found: [],
      wrong: 0,
      score: 0,
      elapsed: 0,
      ended: null,            // server result when a puzzle finishes
      summary: null,
      feedback: "",
      feedbackKind: "",
      announcement: "",
      toast: null,
      tab: "globe",
      picking: false,
      advancing: false,

      init() {
        P.root = this.$el;   // the game root (in methods, $el is whatever element fired the event)
        P.api = GA.api(cfg.apiBase);
        P.host = {};
        P.views = {};
        P.dormant = false;
        P.worldPromise = BorderMaps.loadWorld(cfg.topoUrl, cfg.metaUrl)
          .then((w) => { P.world = w; return w; })
          .catch((e) => { this.mapError = e.message || "Couldn't load the map."; throw e; });
        P.worldPromise.catch(() => {});
        this.refreshBest();
        P.onSettings = (e) => this.onSettingsChanged(e);
        P.onKey = (e) => this.onKey(e);
        window.addEventListener("settings-changed", P.onSettings);
        window.addEventListener("keydown", P.onKey);
        P.wide = window.matchMedia("(min-width: 720px)");
        P.onWide = () => this.applyViewSettings();
        P.wide.addEventListener("change", P.onWide);
        if (cfg.mode === "page") this.$nextTick(() => this.ref("startBtn") && this.ref("startBtn").focus());
      },

      settings() { return GA.settings(GAME_ID); },
      get view() { return this.settings().values.view; },

      async refreshBest() {
        try {
          const r = await P.api.post("best", { settings: this.settings().values });
          this.best = r.best;
        } catch (e) { /* not critical */ }
      },

      onSettingsChanged(e) {
        if (e.detail && e.detail.gameId !== GAME_ID) return;
        this.error = "";
        this.refreshBest();
        this.applyViewSettings();
        // Feed: a prepared (not yet started) round used the old settings; prepare a new one.
        if (cfg.mode === "feed" && ["ready", "error", "idle"].includes(this.lifecycle)) {
          this.prepare().catch(() => {});
        }
      },

      // ------------------------------------------------------------ maps
      async ensureViews() {
        const world = await P.worldPromise;
        if (!P.views.globe) {
          const onInteract = () => {};
          P.views.globe = new BorderMaps.MapView(this.ref("globe"), world, { kind: "globe", onInteract });
          P.views.flat = new BorderMaps.MapView(this.ref("flat"), world, { kind: "flat", onInteract });
          if (P.dormant) Object.values(P.views).forEach((v) => v.pause());
        }
        this.applyViewSettings();
        this.mapsReady = true;
      },

      applyViewSettings() {
        if (!P.views.globe) return;
        const borders = this.settings().values.borders === "on";
        Object.values(P.views).forEach((v) => v.setBorders(borders));
        if (this.phase === "ready") this.tab = "globe";        // the idle globe turns while you wait
        else if (this.view !== "both") this.tab = this.view;
        // Side by side only on a wide full page; a feed card shows one view at a time.
        const sideBySide = cfg.mode === "page" && this.view === "both" && P.wide.matches;
        P.views.globe.setFrameMax(sideBySide ? 1.8 : 6);
      },

      setTab(tab) { this.tab = tab; },

      mapState() {
        const p = this.puzzle;
        const names = {};
        const add = (c) => { if (c) names[c.shape] = c.name; };
        add(p.a); add(p.b);
        this.found.forEach(add);
        const added = (this.ended && this.ended.added) || [];
        added.forEach(add);
        return { a: p.a.shape, b: p.b.shape, found: this.found.map((c) => c.shape), added: added.map((c) => c.shape), names };
      },

      syncMaps({ fresh = false, animate = true, durationMs } = {}) {
        if (!P.views.globe || !this.puzzle) return;
        const state = this.mapState();
        const ids = [state.a, state.b, ...state.found, ...state.added];
        for (const v of Object.values(P.views)) {
          v.setState(state, { fresh });
          v.frame(ids, [state.a, state.b], animate, durationMs);
        }
      },

      idleMaps() {
        for (const v of Object.values(P.views)) {
          v.setState({ a: null, b: null, found: [], added: [], names: {} }, { fresh: true });
          v.idle();
        }
      },

      resetView() { Object.values(P.views).forEach((v) => v.resetView()); },
      /** Read-only access to the map renderers (debugging and browser tests). */
      mapViews() { return P.views; },
      zoomBy(f) { Object.values(P.views).forEach((v) => v.zoomBy(f)); },

      mapLabel(kind) {
        const name = kind === "globe" ? "Globe" : "Flat map";
        if (!this.puzzle) return `${name}. Tap play to reveal the puzzle.`;
        const found = this.found.map((c) => c.name).join(", ");
        return `${name} showing ${this.puzzle.a.name} and ${this.puzzle.b.name}` + (found ? `, connected through ${found}` : "");
      },

      // ------------------------------------------------------------ embed lifecycle
      attach(host) { P.host = host || {}; },

      setLifecycle(state, detail) {
        this.lifecycle = state;
        P.host.onState && P.host.onState(state, detail);
      },

      prepare() {
        if (P.preparing) return P.preparing;
        P.preparing = (async () => {
          this.stopClock();
          this.setLifecycle("preparing");
          this.error = "";
          try {
            const r = await P.api.post("start", { settings: this.settings().values });
            this.roundId = r.round_id;
            this.maxScore = r.max_score;
            this.best = r.best;
            this.total = r.puzzle.total;
            this.score = 0;
            this.summary = null;
            this.puzzle = null;          // held back: nothing about the pair is shown before the tap
            this.ended = null;
            this.found = [];
            this.trail = { a_side: [], b_side: [], remaining: 0 };
            this.options = [];
            this.elapsed = 0;
            this.setFeedback("", "");
            this.phase = "ready";
            if (cfg.mode === "feed") this.screen = "play";
            await this.$nextTick();
            await this.ensureViews();
            this.idleMaps();
            this.setLifecycle("ready");
          } catch (e) {
            this.handleError(e);
            this.setLifecycle("error", { message: e.message });
            throw e;
          }
        })().finally(() => { P.preparing = null; });
        return P.preparing;
      },

      async begin() {
        if (P.preparing) await P.preparing;
        if (this.lifecycle !== "ready" || P.beginning) return;
        P.beginning = true;
        try {
          let r;
          try {
            r = await P.api.post("begin", { round_id: this.roundId });
          } catch (e) {
            if (e.code !== "round_not_found") throw e;
            await this.prepare();       // the preloaded round expired while idle: get a fresh one
            r = await P.api.post("begin", { round_id: this.roundId });
          }
          this.settings().locked = true;
          this.screen = "play";
          this.setLifecycle("playing");
          this.loadPuzzle(r.puzzle, { durationMs: FLY_IN_MS });
        } catch (e) {
          this.handleError(e);
          if (cfg.mode === "feed") this.setLifecycle("error", { message: e.message });
        } finally {
          P.beginning = false;
        }
      },

      /** Full page Start / "Play again": prepare a round and start it straight away. */
      async play() {
        if (this.busy) return;
        this.busy = true;
        try {
          if (this.screen === "summary" && cfg.mode === "feed") this.screen = "play";
          await this.prepare();
          await this.begin();
        } catch (e) { /* surfaced by handleError */ } finally {
          this.busy = false;
        }
      },

      /** The host says this game is off screen: stop everything that ticks or draws. */
      pause() {
        if (P.dormant) return;
        P.dormant = true;
        Object.values(P.views).forEach((v) => v.pause());
        if (this.lifecycle !== "playing") return;
        this.setLifecycle("paused");
        this.pauseClock();
        P.api.post("pause", { round_id: this.roundId }).catch(() => {});
      },

      resume() {
        if (!P.dormant) return;
        P.dormant = false;
        Object.values(P.views).forEach((v) => v.resume());
        if (this.lifecycle !== "paused") return;
        this.setLifecycle("playing");
        P.api.post("resume", { round_id: this.roundId }).catch(() => {});
        if (this.phase === "playing") this.resumeClock();
      },

      destroy() {
        this.stopClock();
        Object.values(P.views).forEach((v) => v.destroy());
        window.removeEventListener("settings-changed", P.onSettings);
        window.removeEventListener("keydown", P.onKey);
        P.wide.removeEventListener("change", P.onWide);
      },

      nextGame() { P.host.onNextGame && P.host.onNextGame(); },

      // ------------------------------------------------------------ puzzles
      async loadPuzzle(pz, { durationMs } = {}) {
        this.puzzle = pz;
        this.ended = null;
        this.found = pz.found;
        this.trail = pz.trail;
        this.wrong = pz.wrong;
        this.options = pz.options.map((o) => ({ ...o, state: "" }));
        this.setFeedback("", "");
        const firstReveal = this.phase === "ready";
        this.phase = "playing";
        this.startClock();
        if (firstReveal) {
          // Switch from the idle globe to the chosen view and lay it out, so it flies in.
          this.applyViewSettings();
          await this.$nextTick();
          Object.values(P.views).forEach((v) => v.resize());
        }
        this.syncMaps({ fresh: true, durationMs });
        this.announce(`Puzzle ${pz.number} of ${pz.total}. Connect ${pz.a.name} to ${pz.b.name}. ` +
          `${pz.between} ${pz.between === 1 ? "country sits" : "countries sit"} between them. ` +
          `Options: ${pz.options.map((o, i) => `${i + 1}, ${o.name}`).join("; ")}.`);
        this.focusFirstOption();
      },

      // The elapsed clock excludes paused time, like the server's.
      startClock() {
        this.stopClock();
        P.clockBase = 0;
        this.elapsed = 0;
        if (this.lifecycle === "paused") return;
        this.resumeClock();
      },
      resumeClock() {
        clearInterval(P.clock);
        P.clockT0 = performance.now();
        P.clock = setInterval(() => { this.elapsed = P.clockBase + performance.now() - P.clockT0; }, 100);
      },
      pauseClock() {
        if (P.clock) P.clockBase += performance.now() - P.clockT0;
        clearInterval(P.clock);
        P.clock = null;
      },
      stopClock() {
        clearInterval(P.clock);
        P.clock = null;
      },
      /** Countdown from the time limit; the whole seconds left when solved are bonus points. */
      secondsLeft() {
        const ms = this.ended ? this.ended.elapsed_ms : this.elapsed;
        return Math.max(0, Math.ceil(cfg.timeLimit - ms / 1000));
      },
      timeBonusLive() { return this.phase === "playing" && this.secondsLeft() > 0; },

      // ------------------------------------------------------------ picking
      async pick(opt) {
        if (!opt || opt.state || this.phase !== "playing" || this.picking || this.lifecycle !== "playing") return;
        this.picking = true;
        const index = this.puzzle.index;
        try {
          const r = await P.api.post("pick", { round_id: this.roundId, puzzle_index: index, option_id: opt.id });
          if (this.phase !== "playing" || this.puzzle.index !== index) return;
          this.score = r.score;
          if (r.result === "correct") await this.onCorrect(opt, r);
          else await this.onWrong(opt, r);
        } catch (e) {
          if (e.code === "option_unavailable") this.setFeedback(e.message, "error");
          else if (e.code !== "puzzle_over") this.handleError(e);
        } finally {
          this.picking = false;
        }
      },

      async onCorrect(opt, r) {
        opt.state = "correct";
        this.found = r.found;
        this.trail = r.trail;
        this.setFeedback(r.message, "success");
        this.announce(`Correct. ${r.message}${r.solved ? "" : ` ${r.trail.remaining} to go.`}`);
        if (r.solved) {
          this.endPuzzle(r);
          return;
        }
        this.syncMaps();
        const index = this.puzzle.index;
        await sleep(380);
        if (this.phase !== "playing" || !this.puzzle || this.puzzle.index !== index) return;
        const keep = new Set(this.options.map((o) => o.id));
        this.options = r.options.map((o) => ({ ...o, state: "", fresh: !keep.has(o.id) }));
        this.focusFirstOption();
      },

      async onWrong(opt, r) {
        opt.state = "wrong";
        this.wrong = r.wrong;
        this.showToast(`−${r.penalty}`, "bad");
        this.setFeedback(r.message, "error");
        this.announce(`${r.message} Minus ${r.penalty} points.`);
        const index = this.puzzle.index;
        await sleep(420);
        opt.state = "leaving";
        await sleep(260);
        if (this.phase !== "playing" || !this.puzzle || this.puzzle.index !== index) return;
        const states = new Map(this.options.map((o) => [o.id, o.state]));
        this.options = r.options.map((o) => ({ ...o, state: states.get(o.id) || "" }));
        this.focusFirstOption();
      },

      async giveUp() {
        if (this.phase !== "playing" || this.picking) return;
        this.picking = true;
        try {
          const r = await P.api.post("giveup", { round_id: this.roundId, puzzle_index: this.puzzle.index });
          this.score = r.score;
          this.endPuzzle(r);
        } catch (e) {
          if (e.code !== "puzzle_over") this.handleError(e);
        } finally {
          this.picking = false;
        }
      },

      endPuzzle(r) {
        this.stopClock();
        this.ended = r;
        this.phase = "ended";
        this.score = r.score;
        this.options = [];
        this.syncMaps();
        const chain = r.chain.map((c) => c.name).join(", then ");
        if (r.result === "gave_up") {
          this.setFeedback("The shortest chain is shown on the map.", "missed");
          this.announce(`Gave up. The shortest chain was ${chain}. No points.`);
        } else {
          this.showToast(`+${r.points}`, "good", "total");
          this.announce(`Solved in ${formatTime(r.elapsed_ms)}: ${chain}. ${r.points} points.`);
        }
        this.$nextTick(() => this.ref("nextBtn") && this.isLive() && this.ref("nextBtn").focus({ preventScroll: true }));
      },

      chainChips() {
        if (!this.puzzle) return [];
        const p = this.puzzle;
        if (this.ended) {
          const added = new Set(this.ended.added.map((c) => c.id));
          return this.ended.chain.map((c, i, all) => ({
            key: c.id, name: c.name,
            role: i === 0 ? "a" : i === all.length - 1 ? "b" : added.has(c.id) ? "revealed" : "found",
          }));
        }
        const slots = Array.from({ length: this.trail.remaining }, (_, i) => ({ key: `?${i}`, name: "?", role: "slot" }));
        return [
          { key: p.a.id, name: p.a.name, role: "a" },
          ...this.trail.a_side.map((c) => ({ key: c.id, name: c.name, role: "found" })),
          ...slots,
          ...this.trail.b_side.map((c) => ({ key: c.id, name: c.name, role: "found" })),
          { key: p.b.id, name: p.b.name, role: "b" },
        ];
      },

      breakdown() {
        const r = this.ended;
        if (!r || r.result === "gave_up") return [];
        const bonus = Object.values(r.bonuses).reduce((a, b) => a + b, 0);
        const lost = cfg.solvePoints - (r.points - bonus);  // wrong-pick penalty actually applied (floored)
        const rows = [{ label: "Puzzle solved", value: `+${cfg.solvePoints}` }];
        if (lost > 0) rows.push({ label: `Wrong picks (${this.wrong})`, value: `−${lost}` });
        if (r.bonuses.perfect) rows.push({ label: "No wrong picks", value: `+${r.bonuses.perfect}` });
        rows.push({ label: `Time left (${this.secondsLeft()}s)`, value: `+${r.bonuses.time || 0}` });
        return rows;
      },

      async next() {
        if (this.advancing || this.phase !== "ended") return;
        this.advancing = true;
        try {
          const r = await P.api.post("next", { round_id: this.roundId, from_index: this.puzzle.index });
          if (r.finished) await this.showSummary();
          else this.loadPuzzle(r.puzzle);
        } catch (e) {
          this.handleError(e);
        } finally {
          this.advancing = false;
        }
      },

      async showSummary() {
        const s = await P.api.post("summary", { round_id: this.roundId });
        this.summary = s;
        this.best = s.best;
        this.phase = "idle";
        this.screen = "summary";
        this.settings().locked = false;
        this.setLifecycle("finished");
        P.host.onRoundFinished && P.host.onRoundFinished(s);
        this.announce(`Round complete. You scored ${s.score} out of ${s.max_score}.${s.is_new_best && s.score > 0 ? " New best!" : ""}`);
        await this.$nextTick();
        GA.countUp(this.ref("totalScore"), 0, s.score, 900);
        if (this.isLive()) this.ref("againBtn") && this.ref("againBtn").focus({ preventScroll: true });
        if (cfg.mode === "page") window.scrollTo({ top: 0, behavior: GA.reducedMotion() ? "auto" : "smooth" });
      },

      verdict() {
        if (!this.summary) return "";
        const pct = this.summary.score / Math.max(1, this.summary.max_score);
        if (pct >= 0.85) return "Master cartographer. Barely a wrong turn.";
        if (pct >= 0.6) return "Great routes. You know your neighbours.";
        if (pct >= 0.3) return "Solid. Detours cost points, so think one border ahead.";
        return "Every route starts somewhere. Go again!";
      },

      formatTime,

      // ------------------------------------------------------------ helpers
      /** x-ref lookup from the game root ($refs resolves from whichever element fired the
          event, which may already be gone, e.g. an option button removed on solve). */
      ref(name) { return P.root.querySelector(`[x-ref="${name}"]`); },

      /** In the feed, only the active (unpaused) card reacts to keys and takes focus. */
      isLive() {
        return cfg.mode === "page" || (!P.dormant && !P.root.closest("[inert]"));
      },

      focusFirstOption() {
        this.$nextTick(() => {
          if (!this.isLive()) return;
          const b = P.root.querySelector(".bb-option:not(:disabled)");
          b && b.focus({ preventScroll: true });
        });
      },

      onKey(e) {
        if (this.screen !== "play" || !this.isLive() || this.settings().open || e.defaultPrevented) return;
        if (e.target.closest && e.target.closest("input, select, textarea, [contenteditable]")) return;
        if (e.altKey || e.ctrlKey || e.metaKey) return;
        if (this.phase === "playing" && this.lifecycle === "playing" && /^[1-4]$/.test(e.key)) {
          const opt = this.options.filter((o) => o.state !== "leaving")[Number(e.key) - 1];
          if (opt) { e.preventDefault(); this.pick(opt); }
        } else if (this.phase === "ended" && e.key === "Enter" && e.target === document.body) {
          e.preventDefault();
          this.next();
        }
      },

      showToast(text, kind, variant = "") {
        this.toast = { text, kind, variant, id: (this.toast ? this.toast.id : 0) + 1 };
        this.$nextTick(() => GA.replayAnimation(this.ref("toast"), "is-showing"));
      },

      setFeedback(text, kind) {
        this.feedback = text;
        this.feedbackKind = kind;
      },

      announce(text) {
        this.announcement = "";
        setTimeout(() => { this.announcement = text; }, 30);
      },

      handleError(e) {
        this.stopClock();
        const store = this.settings();
        if (e.code === "invalid_settings") store.showServerErrors((e.details && e.details.fields) || {});
        if (cfg.mode === "feed") {
          store.locked = false;
          this.error = e.message;
          if (this.lifecycle === "playing" || this.lifecycle === "paused") this.setFeedback(e.message, "error");
          return;
        }
        if (this.screen !== "start" && (e.code === "round_not_found" || e.code === "invalid_settings" || e.status === 0)) {
          this.screen = "start";
          this.phase = "idle";
          store.locked = false;
        }
        if (this.screen === "start") this.error = e.message;
        else this.setFeedback(e.message, "error");
      },
    };
    });
  });
})();
