/* Pinpoint client. Presentation only: the server picks countries, keeps the clock and
   scores. This file drives the map under the fixed crosshair, the countdown, the result
   phase (pins, line, camera fit) and the summary map.

   The guess is the [lon, lat] under the middle of the map zone, where the crosshair is:
   MapView.centerLonLat() inverts the projection there, which works for both views.

   Embed lifecycle (see GeoArcade.alpineEmbed in core.js):
     prepare()  create a round, load the map, show the idle map -> "ready". The server
                sends nothing about the questions yet.
     begin()    tell the server play started (its clock starts now); it answers with the
                first country's name -> show it and start the countdown
     pause()/resume()  hold/release the countdown and map rendering (card scrolled away or
                       tab hidden); the server shifts paused time out of scoring
   On the full page the Start button runs prepare() then begin(). */
(function () {
  "use strict";
  const GA = window.GeoArcade;
  const GAME_ID = "pinpoint";
  const FIT_MS = 900;
  const KM_MAX = 24;                       // the 50m shapes hold up to about this zoom
  const FAR_ON_GLOBE = (80 * Math.PI) / 180;   // pins further apart than this can't be framed closer on a globe
  const START_CAMERA = { flat: { lon: 10, lat: 0 }, globe: { lon: 10, lat: 20 } };
  // Each question starts the flat map just tall enough to overflow the map zone, so it can be dragged
  // up and down as easily as sideways (at zoom 1 the whole map fits and stays centred). The globe
  // already turns freely at zoom 1.
  const START_FILL = 1.1;

  /** The same view for every question (it never hints at the answer). */
  function startCamera(kind, view) {
    const k = kind === "flat" ? Math.min(view.limits.kMax, view.fillZoom() * START_FILL) : 1;
    return { ...START_CAMERA[kind], k };
  }

  function formatTime(ms) {
    return `${(Math.max(0, ms) / 1000).toFixed(1)}s`;
  }

  document.addEventListener("alpine:init", () => {
    Alpine.data("pinpoint", (cfg) => {
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
        q: null,                // the active question: { index, number, total, name, ... }
        score: 0,
        secondsLeft: 0,
        speed: 1,
        timeLevel: "high",
        result: null,
        summary: null,
        feedback: "",
        feedbackKind: "",
        announcement: "",
        submitting: false,
        advancing: false,

        init() {
          P.root = this.$el;
          P.api = GA.api(cfg.apiBase);
          P.host = {};
          P.views = {};
          P.dormant = false;
          P.base = 0;
          P.limitMs = 30000;
          P.timer = new GA.StageTimer({ onTick: () => this.tick(), onExpire: () => this.submit(true) });
          P.worldPromise = GeoMaps.loadWorld(cfg.topoUrl, cfg.metaUrl)
            .then((w) => { P.world = w; return w; })
            .catch((e) => { this.mapError = e.message || "Couldn't load the map."; throw e; });
          P.worldPromise.catch(() => {});
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
          this.applyBorders();
          if (cfg.mode === "feed" && ["ready", "error", "idle"].includes(this.lifecycle)) this.prepare().catch(() => {});
        },

        // ------------------------------------------------------------ map
        async ensureViews() {
          const world = await P.worldPromise;
          if (!P.views.flat) {
            for (const kind of ["flat", "globe"]) {
              P.views[kind] = new GeoMaps.MapView(this.ref(kind), world,
                // tinyArea -1: no small-country marker ring; the pins already mark the spot.
                { kind, kMax: KM_MAX, inertia: true, clampToWorld: true, tinyArea: -1 });
              if (P.dormant) P.views[kind].pause();
            }
          }
          this.applyBorders();
          this.mapReady = true;
        },

        activeView() { return P.views[this.view]; },

        applyBorders() {
          const on = this.settings().values.borders === "on";
          Object.values(P.views).forEach((v) => v.setBorders(on));
        },

        /** No names, no highlights: just the map, at the same start view for every question. */
        clearMap({ idle = false } = {}) {
          for (const [kind, v] of Object.entries(P.views)) {
            v.setState({}, { fresh: true });
            if (idle) v.idle();
            else v.show(startCamera(kind, v), !GA.reducedMotion(), 500);
          }
        },

        resetView() {
          if (this.phase === "result") Object.values(P.views).forEach((v) => v.resetView());
          else for (const [kind, v] of Object.entries(P.views)) v.show(startCamera(kind, v), true, 500);
        },
        zoomBy(f) { Object.values(P.views).forEach((v) => { v.stopMotion(); v.zoomBy(f); }); },
        /** Read-only access to the map renderers (debugging and browser tests). */
        mapViews() { return P.views; },

        mapLabel() {
          const name = this.view === "globe" ? "Globe" : "Flat map";
          if (this.phase === "result" && this.result) {
            return `${name} showing your guess and ${this.result.target.name}, ${this.formatDistance(this.result)} apart.`;
          }
          return `${name} under a fixed crosshair. Arrow keys pan, plus and minus zoom, Enter submits.`;
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
            P.timer.stop();
            this.setLifecycle("preparing");
            this.error = "";
            try {
              const r = await P.api.post("start", { settings: this.settings().values });
              this.roundId = r.round_id;
              this.maxScore = r.max_score;
              this.best = r.best;
              this.total = r.total;
              P.limitMs = r.scoring.time_limit_ms;
              this.score = 0;
              this.summary = null;
              this.q = null;               // the first name is held back until the tap
              this.result = null;
              this.setTime(P.limitMs, 1);
              this.setFeedback("", "");
              this.phase = "ready";
              if (cfg.mode === "feed") this.screen = "play";
              await this.$nextTick();
              await this.ensureViews();
              this.clearMap({ idle: true });
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
            if (r.result) this.showResult({ ...r.result, score: r.score, is_last: r.question.number === r.question.total });
          } catch (e) {
            this.handleError(e);
            if (cfg.mode === "feed") this.setLifecycle("error", { message: e.message });
          } finally {
            P.beginning = false;
          }
        },

        /** Full page Start / "Play again" / the restart button: a fresh round, started at once. */
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

        pause() {
          if (P.dormant) return;
          P.dormant = true;
          Object.values(P.views).forEach((v) => v.pause());
          if (P.summaryView) P.summaryView.pause();
          P.timer.hold();
          if (this.lifecycle !== "playing") return;
          this.setLifecycle("paused");
          this.serverPause();
        },

        resume() {
          if (!P.dormant) return;
          P.dormant = false;
          Object.values(P.views).forEach((v) => v.resume());
          if (P.summaryView) P.summaryView.resume();
          if (this.lifecycle !== "paused") { P.timer.release(); return; }
          this.setLifecycle("playing");
          if (!document.hidden) this.serverResume();
        },

        /** A hidden tab pauses the countdown (StageTimer does that itself); pause the server too. */
        onVisibility() {
          if (this.lifecycle !== "playing") return;
          if (document.hidden) this.serverPause();
          else this.serverResume();
        },

        serverPause() { P.api.post("pause", { round_id: this.roundId }).catch(() => {}); },

        async serverResume() {
          const index = this.q && this.q.index;
          try {
            const r = await P.api.post("resume", { round_id: this.roundId });
            if (this.phase === "question" && this.q && this.q.index === index) this.restartTimer(r.elapsed_ms);
          } catch (e) { /* the next submit resumes the server anyway */ }
          P.timer.release();
        },

        destroy() {
          P.timer.destroy();
          Object.values(P.views).forEach((v) => v.destroy());
          if (P.summaryView) P.summaryView.destroy();
          window.removeEventListener("settings-changed", P.onSettings);
          window.removeEventListener("keydown", P.onKey);
          document.removeEventListener("visibilitychange", P.onVisibility);
        },

        nextGame() { P.host.onNextGame && P.host.onNextGame(); },

        // ------------------------------------------------------------ countdown
        restartTimer(elapsedMs) {
          P.base = Math.max(0, elapsedMs);
          P.timer.start(Math.max(1, P.limitMs - P.base));
          this.tick();
        },

        elapsedMs() { return P.base + P.timer.elapsed(); },

        /** Display only; the server measures time itself. Same formula as logic.speed_factor. */
        tick() {
          if (this.phase !== "question") return;
          const e = Math.min(this.elapsedMs(), P.limitMs);
          const span = Math.max(1, P.limitMs - cfg.fullSpeedMs);
          const speed = e <= cfg.fullSpeedMs ? 1 : 1 - (1 - cfg.minSpeed) * Math.min(1, (e - cfg.fullSpeedMs) / span);
          this.setTime(P.limitMs - e, speed);
        },

        setTime(remainingMs, speed) {
          const secs = Math.ceil(Math.max(0, remainingMs) / 1000);
          if (secs !== this.secondsLeft) this.secondsLeft = secs;
          const rounded = Math.round(speed * 100) / 100;
          if (rounded !== this.speed) this.speed = rounded;
          const frac = Math.max(0, Math.min(1, remainingMs / P.limitMs));
          const level = frac > 0.5 ? "high" : frac > 0.2 ? "mid" : "low";
          if (level !== this.timeLevel) this.timeLevel = level;
          const bar = this.ref("timeBar");
          if (bar) bar.style.setProperty("--fill", String(frac));
        },

        // ------------------------------------------------------------ questions
        loadQuestion(q) {
          this.q = q;
          this.result = null;
          this.setFeedback("", "");
          this.phase = "question";
          P.limitMs = q.time_limit_ms;
          this.clearMap();
          this.restartTimer(q.elapsed_ms);
          this.announce(`Question ${q.number} of ${q.total}. Find ${q.name}. ` +
            "Move the map so the crosshair is on it, then press Submit.");
          this.$nextTick(() => {
            if (!this.isLive()) return;
            const canvas = this.ref(this.view);
            canvas && canvas.focus({ preventScroll: true });
          });
        },

        /** Lock in the crosshair. timeout: the countdown ran out (the server checks its own clock). */
        async submit(timeout = false) {
          if (this.phase !== "question" || this.submitting) return;
          if (!timeout && this.lifecycle !== "playing") return;
          const view = this.activeView();
          if (!view) return;
          this.submitting = true;
          view.stopMotion();
          const [lng, lat] = view.centerLonLat();
          const index = this.q.index;
          if (!timeout) P.timer.stop();
          try {
            const r = await P.api.post("submit", { round_id: this.roundId, question_index: index,
              lat: Math.round(lat * 1e4) / 1e4, lng: Math.round(lng * 1e4) / 1e4, timeout });
            if (this.phase === "question" && this.q.index === index) this.showResult(r);
          } catch (e) {
            if (e.code === "too_early") this.restartTimer(P.limitMs - e.details.remaining_ms);
            else if (e.code !== "question_over") { this.handleError(e); if (!timeout) this.restartTimer(this.elapsedMs()); }
          } finally {
            this.submitting = false;
          }
        },

        showResult(r) {
          P.timer.stop();
          this.result = r;
          this.phase = "result";
          this.score = r.score;
          const guess = [r.guess.lng, r.guess.lat];
          const target = [r.target.lng, r.target.lat];
          const shape = r.target.shape;
          for (const v of Object.values(P.views)) {
            v.setState({ found: shape ? [shape] : [],
                         pins: [{ id: "guess", lonlat: guess, kind: "guess" }, { id: "target", lonlat: target, kind: "target" }],
                         links: [{ from: guess, to: target }] }, { fresh: true });
            if (v.kind === "globe" && d3.geoDistance(guess, target) > FAR_ON_GLOBE) {
              // Far apart: face the midpoint of the line with the whole globe in view, which puts
              // both pins (less than 180° apart) on the visible hemisphere.
              const [lon, lat] = d3.geoInterpolate(guess, target)(0.5);
              v.show({ lon, lat, k: 1 }, true, FIT_MS);
            } else {
              v.frame(shape ? [shape] : [], shape ? [shape] : [], true, FIT_MS,
                { points: [guess, target], clipDeg: 80, frameMax: KM_MAX, zoomOut: 0.85 });
            }
          }
          this.$nextTick(() => {
            GA.countUp(this.ref("resultPoints"), 0, r.points, 700);
            if (this.isLive()) this.ref("nextBtn") && this.ref("nextBtn").focus({ preventScroll: true });
          });
          this.announce(`${r.timed_out ? "Time's up. " : ""}` +
            (r.inside ? `Inside ${r.target.name}, full accuracy. ` : `${this.formatDistance(r)} from ${r.target.name}. `) +
            `${r.accuracy} accuracy times ${r.speed_factor.toFixed(2)} speed: ${r.points} points.`);
        },

        verdictFor(r) {
          if (r.inside) return `Inside ${r.target.name}!`;
          if (r.accuracy === 100) return "Spot on!";
          if (r.accuracy >= 75) return "Very close";
          if (r.accuracy >= 40) return "Not bad";
          if (r.accuracy > 0) return "A bit off";
          return "Way off";
        },

        formatDistance(r) {
          const mi = this.settings().values.units === "mi";
          const v = mi ? r.distance_mi : r.distance_km;
          return `${v < 10 ? v.toFixed(1) : Math.round(v).toLocaleString()} ${mi ? "mi" : "km"}`;
        },

        async next() {
          if (this.advancing || this.phase !== "result") return;
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
          this.drawSummaryMap(s);
          if (this.isLive()) this.ref("againBtn") && this.ref("againBtn").focus({ preventScroll: true });
          if (cfg.mode === "page") window.scrollTo({ top: 0, behavior: GA.reducedMotion() ? "auto" : "smooth" });
        },

        /** A small static flat map of every guess/actual pair in the round. */
        drawSummaryMap(s) {
          const canvas = this.ref("summaryMap");
          if (!canvas || !P.world) return;
          if (P.summaryView) P.summaryView.destroy();
          const v = new GeoMaps.MapView(canvas, P.world, { kind: "flat", tinyArea: -1 });
          P.summaryView = v;
          v.setBorders(this.settings().values.borders === "on");
          const pins = [], links = [], points = [];
          s.questions.forEach((row, i) => {
            const g = [row.guess.lng, row.guess.lat], t = [row.target.lng, row.target.lat];
            pins.push({ id: `g${i}`, lonlat: g, kind: "guess" }, { id: `t${i}`, lonlat: t, kind: "target" });
            links.push({ from: g, to: t });
            points.push(g, t);
          });
          v.setState({ pins, links }, { fresh: true });
          v.frame([], [], false, 0, { points, clipDeg: 180, frameMax: 6, zoomOut: 0.8 });
          if (P.dormant) v.pause();
        },

        verdict() {
          if (!this.summary) return "";
          const pct = this.summary.score / Math.max(1, this.summary.max_score);
          if (pct >= 0.8) return "Human GPS. You knew exactly where to look.";
          if (pct >= 0.55) return "Sharp eyes. A little faster and you'd be untouchable.";
          if (pct >= 0.3) return "Good instincts. Zoom in before you lock it in.";
          return "The world is big. Go again!";
        },

        formatTime,

        // ------------------------------------------------------------ helpers
        ref(name) { return P.root.querySelector(`[x-ref="${name}"]`); },

        /** In the feed, only the active (unpaused) card reacts to keys and takes focus. */
        isLive() {
          return cfg.mode === "page" || (!P.dormant && !P.root.closest("[inert]"));
        },

        onKey(e) {
          if (this.screen !== "play" || !this.isLive() || this.settings().open || e.defaultPrevented) return;
          if (e.target.closest && e.target.closest("input, select, textarea, [contenteditable]")) return;
          if (e.altKey || e.ctrlKey || e.metaKey) return;
          const onButton = e.target.closest && e.target.closest("button, a");
          if (this.phase === "question" && (e.key === "Enter" || e.key === " ") && !onButton) {
            e.preventDefault();
            this.submit();
          } else if (this.phase === "result" && e.key === "Enter" && !onButton) {
            e.preventDefault();
            this.next();
          }
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
          const store = this.settings();
          if (e.code === "invalid_settings") store.showServerErrors((e.details && e.details.fields) || {});
          if (cfg.mode === "feed") {
            store.locked = false;
            this.error = e.message;
            if (this.lifecycle === "playing" || this.lifecycle === "paused") this.setFeedback(e.message, "error");
            return;
          }
          if (this.screen !== "start" && (e.code === "round_not_found" || e.code === "invalid_settings" || e.status === 0)) {
            P.timer.stop();
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
