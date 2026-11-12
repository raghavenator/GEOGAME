/* Border Bridge client. Presentation only: the server picks pairs, knows which
   options are correct, validates picks and scores. This file drives the maps,
   options, chips, timer and animations. */
(function () {
  "use strict";
  const GA = window.GeoArcade;
  const sleep = (ms) => new Promise((r) => setTimeout(r, GA.reducedMotion() ? 0 : ms));

  function formatTime(ms) {
    const s = Math.max(0, ms) / 1000;
    if (s < 60) return `${s.toFixed(1)}s`;
    return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  }

  document.addEventListener("alpine:init", () => {
    Alpine.data("borderBridge", (cfg) => ({
      screen: "start",        // start | play | summary
      phase: "idle",          // idle | playing | ended
      busy: false,
      error: "",
      mapError: "",
      mapsReady: false,
      best: null,
      maxScore: 0,
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
        this.api = GA.api(cfg.apiBase);
        this.views = {};
        this.worldPromise = BorderMaps.loadWorld(cfg.topoUrl, cfg.metaUrl)
          .then((w) => { this.world = w; return w; })
          .catch((e) => { this.mapError = e.message || "Couldn't load the map."; throw e; });
        this.refreshBest();
        window.addEventListener("settings-changed", () => {
          this.error = "";
          this.refreshBest();
          this.applyViewSettings();
        });
        window.addEventListener("keydown", (e) => this.onKey(e));
        this.wide = window.matchMedia("(min-width: 720px)");
        this.wide.addEventListener("change", () => this.applyViewSettings());
        this.$nextTick(() => this.$refs.startBtn && this.$refs.startBtn.focus());
      },

      settings() { return Alpine.store("settings").values; },
      get view() { return this.settings().view; },

      async refreshBest() {
        try {
          const r = await this.api.post("best", { settings: this.settings() });
          this.best = r.best;
        } catch (e) { /* not critical */ }
      },

      // ------------------------------------------------------------ maps
      async ensureViews() {
        const world = await this.worldPromise;
        if (!this.views.globe) {
          const onInteract = () => {};
          this.views.globe = new BorderMaps.MapView(this.$refs.globe, world, { kind: "globe", onInteract });
          this.views.flat = new BorderMaps.MapView(this.$refs.flat, world, { kind: "flat", onInteract });
        }
        this.applyViewSettings();
        this.mapsReady = true;
      },

      applyViewSettings() {
        if (!this.views.globe) return;
        const borders = this.settings().borders === "on";
        Object.values(this.views).forEach((v) => v.setBorders(borders));
        if (this.view !== "both") this.tab = this.view;
        const sideBySide = this.view === "both" && this.wide.matches;
        this.views.globe.setFrameMax(sideBySide ? 1.8 : 6);
      },

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

      syncMaps({ fresh = false, animate = true } = {}) {
        if (!this.views.globe || !this.puzzle) return;
        const state = this.mapState();
        const ids = [state.a, state.b, ...state.found, ...state.added];
        for (const v of Object.values(this.views)) {
          v.setState(state, { fresh });
          v.frame(ids, [state.a, state.b], animate);
        }
      },

      resetView() { Object.values(this.views).forEach((v) => v.resetView()); },

      mapLabel(kind) {
        if (!this.puzzle) return `${kind === "globe" ? "Globe" : "Flat map"}`;
        const found = this.found.map((c) => c.name).join(", ");
        return `${kind === "globe" ? "Globe" : "Flat map"} showing ${this.puzzle.a.name} and ${this.puzzle.b.name}` +
          (found ? `, connected through ${found}` : "");
      },

      // ------------------------------------------------------------ round lifecycle
      async start() {
        if (this.busy) return;
        this.busy = true;
        this.error = "";
        try {
          const r = await this.api.post("start", { settings: this.settings() });
          this.roundId = r.round_id;
          this.maxScore = r.max_score;
          this.best = r.best;
          this.score = 0;
          this.summary = null;
          Alpine.store("settings").locked = true;
          this.screen = "play";
          await this.$nextTick();
          await this.ensureViews();
          this.loadPuzzle(r.puzzle);
        } catch (e) {
          this.handleError(e);
        } finally {
          this.busy = false;
        }
      },

      loadPuzzle(pz) {
        this.puzzle = pz;
        this.ended = null;
        this.found = pz.found;
        this.trail = pz.trail;
        this.wrong = pz.wrong;
        this.options = pz.options.map((o) => ({ ...o, state: "" }));
        this.setFeedback("", "");
        this.phase = "playing";
        this.startClock();
        this.syncMaps({ fresh: true });
        this.announce(`Puzzle ${pz.number} of ${pz.total}. Connect ${pz.a.name} to ${pz.b.name}. ` +
          `${pz.between} ${pz.between === 1 ? "country sits" : "countries sit"} between them. ` +
          `Options: ${pz.options.map((o, i) => `${i + 1}, ${o.name}`).join("; ")}.`);
        this.focusFirstOption();
      },

      startClock() {
        this.stopClock();
        const t0 = performance.now();
        this.elapsed = 0;
        this._clock = setInterval(() => { this.elapsed = performance.now() - t0; }, 100);
      },
      stopClock() { clearInterval(this._clock); },
      /** Countdown from the time limit; the whole seconds left when solved are bonus points. */
      secondsLeft() {
        const ms = this.ended ? this.ended.elapsed_ms : this.elapsed;
        return Math.max(0, Math.ceil(cfg.timeLimit - ms / 1000));
      },
      timeBonusLive() { return this.phase === "playing" && this.secondsLeft() > 0; },

      // ------------------------------------------------------------ picking
      async pick(opt) {
        if (!opt || opt.state || this.phase !== "playing" || this.picking) return;
        this.picking = true;
        const index = this.puzzle.index;
        try {
          const r = await this.api.post("pick", { round_id: this.roundId, puzzle_index: index, option_id: opt.id });
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
        await sleep(380);
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
        await sleep(420);
        opt.state = "leaving";
        await sleep(260);
        const states = new Map(this.options.map((o) => [o.id, o.state]));
        this.options = r.options.map((o) => ({ ...o, state: states.get(o.id) || "" }));
        this.focusFirstOption();
      },

      async giveUp() {
        if (this.phase !== "playing" || this.picking) return;
        this.picking = true;
        try {
          const r = await this.api.post("giveup", { round_id: this.roundId, puzzle_index: this.puzzle.index });
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
        this.$nextTick(() => this.$refs.nextBtn && this.$refs.nextBtn.focus({ preventScroll: true }));
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
          const r = await this.api.post("next", { round_id: this.roundId, from_index: this.puzzle.index });
          if (r.finished) await this.showSummary();
          else this.loadPuzzle(r.puzzle);
        } catch (e) {
          this.handleError(e);
        } finally {
          this.advancing = false;
        }
      },

      async showSummary() {
        const s = await this.api.post("summary", { round_id: this.roundId });
        this.summary = s;
        this.best = s.best;
        this.phase = "idle";
        this.screen = "summary";
        Alpine.store("settings").locked = false;
        this.announce(`Round complete. You scored ${s.score} out of ${s.max_score}.${s.is_new_best && s.score > 0 ? " New best!" : ""}`);
        await this.$nextTick();
        GA.countUp(this.$refs.totalScore, 0, s.score, 900);
        this.$refs.againBtn && this.$refs.againBtn.focus({ preventScroll: true });
        window.scrollTo({ top: 0, behavior: GA.reducedMotion() ? "auto" : "smooth" });
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
      focusFirstOption() {
        this.$nextTick(() => {
          const b = document.querySelector(".bb-option:not(:disabled)");
          b && b.focus({ preventScroll: true });
        });
      },

      onKey(e) {
        if (this.screen !== "play" || Alpine.store("settings").open) return;
        if (e.target.closest && e.target.closest("input, select, textarea")) return;
        if (e.altKey || e.ctrlKey || e.metaKey) return;
        if (this.phase === "playing" && /^[1-4]$/.test(e.key)) {
          const opt = this.options.filter((o) => o.state !== "leaving")[Number(e.key) - 1];
          if (opt) { e.preventDefault(); this.pick(opt); }
        } else if (this.phase === "ended" && e.key === "Enter" && e.target === document.body) {
          e.preventDefault();
          this.next();
        }
      },

      showToast(text, kind, variant = "") {
        this.toast = { text, kind, variant, id: (this.toast ? this.toast.id : 0) + 1 };
        this.$nextTick(() => GA.replayAnimation(this.$refs.toast, "is-showing"));
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
        if (e.code === "invalid_settings") {
          Alpine.store("settings").showServerErrors((e.details && e.details.fields) || {});
        }
        if (this.screen !== "start" && (e.code === "round_not_found" || e.code === "invalid_settings" || e.status === 0)) {
          this.screen = "start";
          this.phase = "idle";
          Alpine.store("settings").locked = false;
        }
        if (this.screen === "start") this.error = e.message;
        else this.setFeedback(e.message, "error");
      },
    }));
  });
})();
