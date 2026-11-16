/* Capital Quest client. Presentation only: the server picks countries and options,
   knows which option is correct, keeps the clock and scores. This file drives the map,
   the options, the live points meter (an estimate; the server's number is the score)
   and the animations.

   Embed lifecycle (see GeoArcade.alpineEmbed in core.js):
     prepare()  create a round, load the map, show the plain world map -> "ready".
                The server sends nothing about the questions yet.
     begin()    tell the server play started (its clock starts now); it answers with the
                first question -> highlight the country, show options, start the timer
     pause()/resume()  hold/release the timer and the map rendering (card scrolled away
                       or tab hidden); the server shifts paused time out of scoring
   On the full page the Start button runs prepare() then begin(). */
(function () {
  "use strict";
  const GA = window.GeoArcade;
  const GAME_ID = "capital-quest";
  const FLY_MS = 750;
  const MISSED_MS = 2600;        // a timeout's reveal stays up a little longer than a correct answer
  const REGION_ZOOM_OUT = 0.4;   // "Zoom to region": fit the country, then pull back to show its neighbours
  const sleep = (ms) => new Promise((r) => setTimeout(r, GA.reducedMotion() ? 0 : ms));

  function formatTime(ms) {
    return `${(Math.max(0, ms) / 1000).toFixed(1)}s`;
  }

  document.addEventListener("alpine:init", () => {
    Alpine.data("capitalQuest", (cfg) => {
      // Runtime objects live in P (per instance, outside Alpine's reactivity).
      const P = {};
      return {
        screen: cfg.mode === "feed" ? "play" : "start",   // start | play | summary
        phase: "idle",          // idle | ready | question | result
        lifecycle: "idle",      // idle | preparing | ready | playing | paused | finished | error
        busy: false,
        error: "",
        mapError: "",
        mapReady: false,
        best: null,
        maxScore: 0,
        total: 0,
        roundId: null,
        q: null,                // the active question (no answer in it)
        options: [],            // [{ id, name, state: "" | "wrong" | "leaving" | "correct" | "answer" | "dim" }]
        optionCount: 8,
        wrong: 0,
        score: 0,
        potential: cfg.startPoints,
        level: "high",          // meter colour: high | mid | low
        result: null,           // server payload when a question ends
        summary: null,
        feedback: "",
        feedbackKind: "",
        announcement: "",
        toast: null,
        picking: false,
        advancing: false,

        init() {
          P.root = this.$el;
          P.api = GA.api(cfg.apiBase);
          P.host = {};
          P.views = {};
          P.dormant = false;
          P.base = 0;           // ms already elapsed (server) when the timer last started
          P.timer = new GA.StageTimer({ onTick: () => this.tick(), onExpire: () => this.onExpire() });
          P.worldPromise = GeoMaps.loadWorld(cfg.topoUrl, cfg.metaUrl)
            .then((w) => { P.world = w; return w; })
            .catch((e) => { this.mapError = e.message || "Couldn't load the map."; throw e; });
          P.worldPromise.catch(() => {});
          this.optionCount = Number(this.settings().values.options);
          this.refreshBest();
          P.onSettings = (e) => this.onSettingsChanged(e);
          P.onKey = (e) => this.onKey(e);
          P.onVisibility = () => this.onVisibility();
          window.addEventListener("settings-changed", P.onSettings);
          window.addEventListener("keydown", P.onKey);
          document.addEventListener("visibilitychange", P.onVisibility);
          if (cfg.mode === "page") this.$nextTick(() => this.ref("startBtn") && this.ref("startBtn").focus());
        },

        settings() { return GA.settings(GAME_ID); },
        get view() { return this.settings().values.view; },
        get skeletons() { return Number(this.settings().values.options); },

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
          // Feed: a prepared (not yet started) round used the old settings; prepare a new one.
          if (cfg.mode === "feed" && ["ready", "error", "idle"].includes(this.lifecycle)) this.prepare().catch(() => {});
          else if (this.phase === "ready") this.idleMap();
        },

        // ------------------------------------------------------------ map
        async ensureViews() {
          const world = await P.worldPromise;
          if (!P.views.flat) {
            for (const kind of ["flat", "globe"]) {
              P.views[kind] = new GeoMaps.MapView(this.ref(kind), world, { kind, tinyArea: 48 });
              if (P.dormant) P.views[kind].pause();
            }
          }
          this.mapReady = true;
        },

        activeView() { return P.views[this.view]; },

        idleMap() {
          for (const v of Object.values(P.views)) {
            v.setState({}, { fresh: true });
            v.idle();
          }
        },

        /** Highlight the target with a fade-and-pulse and frame it per the "Map framing" setting. */
        highlight(shape, { names = {}, fly = true } = {}) {
          const shapeInfo = P.world && P.world.shapes[shape];
          if (!shapeInfo) return;
          const region = this.settings().values.framing === "region";
          for (const v of Object.values(P.views)) {
            v.setState({ target: shape, names }, { fresh: fly, pulse: fly });
            if (!fly) continue;
            if (region) v.frame([shape], [shape], true, FLY_MS, { clipDeg: 80, zoomOut: REGION_ZOOM_OUT, frameMax: v.limits.kMax });
            else v.show(v.worldCamera(shapeInfo.centroid), true, FLY_MS);
          }
        },

        resetView() { Object.values(P.views).forEach((v) => v.resetView()); },
        zoomBy(f) { Object.values(P.views).forEach((v) => v.zoomBy(f)); },
        /** Read-only access to the map renderers (debugging and browser tests). */
        mapViews() { return P.views; },

        mapLabel() {
          const name = this.view === "globe" ? "Globe" : "Flat map";
          if (!this.q) return `${name}. Tap play to reveal the country.`;
          if (this.result) return `${name} showing ${this.result.answer.country}, capital ${this.result.answer.capital}.`;
          return `${name} with one country highlighted. Drag to pan, scroll or pinch to zoom.`;
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
            this.stopTimer();
            this.setLifecycle("preparing");
            this.error = "";
            try {
              const r = await P.api.post("start", { settings: this.settings().values });
              this.roundId = r.round_id;
              this.maxScore = r.max_score;
              this.best = r.best;
              this.total = r.total;
              this.optionCount = Number(r.settings.options);
              P.limitMs = r.scoring.time_limit_ms;
              this.score = 0;
              this.summary = null;
              this.q = null;
              this.result = null;
              this.options = [];
              this.wrong = 0;
              this.setPotential(cfg.startPoints, 1);
              this.setFeedback("", "");
              this.phase = "ready";
              if (cfg.mode === "feed") this.screen = "play";
              await this.$nextTick();
              await this.ensureViews();
              this.idleMap();
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
            this.setLifecycle(P.dormant ? "paused" : "playing");
            if (P.dormant) P.api.post("pause", { round_id: this.roundId }).catch(() => {});
            await this.$nextTick();
            Object.values(P.views).forEach((v) => v.resize());
            this.loadQuestion(r.question);
            if (r.ended) this.endQuestion(r.ended);
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
          P.timer.hold();
          P.auto && P.auto.pause();
          if (this.lifecycle !== "playing") return;
          this.setLifecycle("paused");
          this.serverPause();
        },

        resume() {
          if (!P.dormant) return;
          P.dormant = false;
          Object.values(P.views).forEach((v) => v.resume());
          if (this.lifecycle !== "paused") {
            // Not mid-question (ready, result, summary): nothing to sync with the server.
            P.timer.release();
            P.auto && P.auto.resume();
            return;
          }
          this.setLifecycle("playing");
          if (!document.hidden) this.serverResume();
        },

        /** A hidden tab pauses the timer (StageTimer does that itself); pause the server too. */
        onVisibility() {
          if (this.lifecycle !== "playing") return;
          if (document.hidden) this.serverPause();
          else this.serverResume();
        },

        serverPause() {
          P.api.post("pause", { round_id: this.roundId }).catch(() => {});
        },

        async serverResume() {
          const index = this.q && this.q.index;
          try {
            const r = await P.api.post("resume", { round_id: this.roundId });
            if (this.phase === "question" && this.q && this.q.index === index) this.restartTimer(r.elapsed_ms);
          } catch (e) { /* the next pick resumes the server anyway */ }
          P.timer.release();
          P.auto && P.auto.resume();
        },

        destroy() {
          this.stopTimer();
          P.timer.destroy();
          Object.values(P.views).forEach((v) => v.destroy());
          window.removeEventListener("settings-changed", P.onSettings);
          window.removeEventListener("keydown", P.onKey);
          document.removeEventListener("visibilitychange", P.onVisibility);
        },

        nextGame() { P.host.onNextGame && P.host.onNextGame(); },

        // ------------------------------------------------------------ timer + meter
        /** Server-measured elapsed time when the local timer (re)started, plus local time since. */
        elapsedMs() { return P.base + P.timer.elapsed(); },

        restartTimer(elapsedMs) {
          P.base = Math.max(0, elapsedMs);
          P.timer.start(Math.max(1, P.limitMs - P.base));
          this.tick();
        },

        stopTimer() {
          P.timer && P.timer.stop();
          P.auto && P.auto.cancel();
          P.auto = null;
        },

        /** Same formula as logic.correct_points (GA.drainMeter); display only. */
        tick() {
          if (this.phase !== "question") return;
          const m = GA.drainMeter(this.elapsedMs(), P.limitMs, this.wrong, cfg);
          this.setPotential(m.points, m.fill);
        },

        setPotential(points, fill) {
          if (points !== this.potential) this.potential = points;
          const level = GA.meterLevel(points, cfg.startPoints);
          if (level !== this.level) this.level = level;
          const bar = this.ref("meterBar");
          if (bar) bar.style.setProperty("--fill", String(Math.max(0, Math.min(1, fill))));
        },

        async onExpire() {
          if (this.phase !== "question") return;
          const index = this.q.index;
          try {
            const r = await P.api.post("timeout", { round_id: this.roundId, question_index: index });
            if (this.phase === "question" && this.q.index === index) this.endQuestion(r);
          } catch (e) {
            if (e.code === "too_early") {
              // Our clock ran ahead of the server's (a paused stretch, say): wait out the rest.
              this.restartTimer(P.limitMs - e.details.remaining_ms);
            } else if (e.code !== "question_over") this.handleError(e);
          }
        },

        // ------------------------------------------------------------ questions
        loadQuestion(q) {
          this.q = q;
          this.result = null;
          this.wrong = q.wrong;
          this.optionCount = q.options.length;
          this.options = q.options.map((o) => ({ ...o, state: "" }));
          this.setFeedback("", "");
          this.phase = "question";
          P.limitMs = q.time_limit_ms;
          this.highlight(q.shape);
          this.restartTimer(q.elapsed_ms);
          this.announce(`Question ${q.number} of ${q.total}. What's the capital of the highlighted country? ` +
            `Options: ${q.options.map((o, i) => `${i + 1}, ${o.name}`).join("; ")}.`);
          this.focusFirstOption();
        },

        async pick(opt) {
          if (!opt || opt.state || this.phase !== "question" || this.picking || this.lifecycle !== "playing") return;
          this.picking = true;
          const index = this.q.index;
          try {
            const r = await P.api.post("pick", { round_id: this.roundId, question_index: index, option_id: opt.id });
            if (this.phase !== "question" || this.q.index !== index) return;
            if (r.result === "wrong") this.onWrong(opt, r);
            else this.endQuestion(r, opt);
          } catch (e) {
            if (e.code === "option_unavailable") this.setFeedback(e.message, "error");
            else if (e.code !== "question_over") this.handleError(e);
          } finally {
            this.picking = false;
          }
        },

        async onWrong(opt, r) {
          opt.state = "wrong";
          this.wrong = r.wrong;
          this.tick();
          this.showToast(`−${r.penalty}`, "bad");
          this.setFeedback(`Not ${opt.name}. −${r.penalty} points.`, "error");
          this.announce(`${opt.name} is wrong. Minus ${r.penalty} points. ${r.potential} points left.`);
          const index = this.q.index;
          await sleep(420);
          opt.state = "leaving";
          await sleep(240);
          if (this.phase !== "question" || this.q.index !== index) return;
          const live = new Set(r.options);
          this.options = this.options.filter((o) => live.has(o.id));
          this.focusFirstOption();
        },

        endQuestion(r, picked) {
          P.timer.stop();
          this.result = r;
          this.phase = "result";
          this.score = r.score;
          this.wrong = r.wrong;
          this.setPotential(r.points, r.points / cfg.startPoints);
          const answerId = r.answer.option_id;
          for (const o of this.options) {
            if (o.id === answerId) o.state = r.status === "correct" ? "correct" : "answer";
            else if (o.state !== "leaving") o.state = "dim";
          }
          if (!this.options.some((o) => o.id === answerId)) {
            this.options.push({ id: answerId, name: r.answer.capital, state: "answer" });
          }
          // Reveal on the map too: the answer's capital labels the country now that it's over.
          this.highlight(r.answer.shape, { names: { [r.answer.shape]: r.answer.capital }, fly: false });
          if (r.status === "correct") {
            this.showToast(`+${r.points}`, "good");
            this.setFeedback("", "");
            this.announce(`Correct! ${r.answer.capital} is the capital of ${r.answer.country}. ${r.points} points.`);
          } else {
            this.setFeedback("", "");
            this.announce(`Time's up. The capital of ${r.answer.country} is ${r.answer.capital}. No points.`);
          }
          P.auto && P.auto.cancel();
          P.auto = GA.pausableDelay(r.status === "correct" ? cfg.resultMs : MISSED_MS, () => this.next());
          if (P.dormant) P.auto.pause();
          this.$nextTick(() => this.isLive() && this.ref("nextBtn") && this.ref("nextBtn").focus({ preventScroll: true }));
        },

        async next() {
          if (this.advancing || this.phase !== "result") return;
          P.auto && P.auto.cancel();
          P.auto = null;
          this.advancing = true;
          try {
            const r = await P.api.post("next", { round_id: this.roundId, from_index: this.q.index });
            if (r.finished) await this.showSummary();
            else this.loadQuestion(r.question);
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
          if (pct >= 0.8) return "Lightning fast. You know your capitals.";
          if (pct >= 0.55) return "Great round. Speed is where the points are.";
          if (pct >= 0.3) return "Solid. Trust your first instinct and go faster.";
          return "Every capital counts. Go again!";
        },

        formatTime,

        // ------------------------------------------------------------ helpers
        /** x-ref lookup from the game root ($refs resolves from whichever element fired the event). */
        ref(name) { return P.root.querySelector(`[x-ref="${name}"]`); },

        /** In the feed, only the active (unpaused) card reacts to keys and takes focus. */
        isLive() {
          return cfg.mode === "page" || (!P.dormant && !P.root.closest("[inert]"));
        },

        focusFirstOption() {
          this.$nextTick(() => {
            if (!this.isLive()) return;
            const b = P.root.querySelector(".cq-option:not(:disabled)");
            b && b.focus({ preventScroll: true });
          });
        },

        onKey(e) {
          if (this.screen !== "play" || !this.isLive() || this.settings().open || e.defaultPrevented) return;
          if (e.target.closest && e.target.closest("input, select, textarea, [contenteditable]")) return;
          if (e.altKey || e.ctrlKey || e.metaKey) return;
          if (this.phase === "question" && this.lifecycle === "playing" && /^[1-8]$/.test(e.key)) {
            const opt = this.options[Number(e.key) - 1];
            if (opt) { e.preventDefault(); this.pick(opt); }
          } else if (this.phase === "result" && e.key === "Enter" && e.target === document.body) {
            e.preventDefault();
            this.next();
          }
        },

        showToast(text, kind) {
          this.toast = { text, kind, id: (this.toast ? this.toast.id : 0) + 1 };
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
          this.stopTimer();
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
