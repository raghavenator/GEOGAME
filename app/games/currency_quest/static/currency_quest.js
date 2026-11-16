/* Currency Quest client. Presentation only: the server picks countries and options,
   knows which option is correct, keeps the clock and scores. This file shows the country,
   the options, the optional locator globe and the live points meter (an estimate; the
   server's number is the score), and runs the animations.

   Embed lifecycle (see GeoArcade.alpineEmbed in core.js):
     prepare()  create a round -> "ready" (skeletons only: the client knows nothing about
                the questions yet, so there's nothing to peek at)
     begin()    tell the server play started (its clock starts now); it answers with the
                first question -> show the country and options, start the timer
     pause()/resume()  hold/release the timer and the locator (card scrolled away or tab
                       hidden); the server shifts paused time out of scoring
   On the full page the Start button runs prepare() then begin().

   Gestures: the card has no gesture zone, so a vertical swipe anywhere, including one
   that starts on an option, scrolls the feed natively; the browser doesn't fire a click
   for a touch that turned into a scroll. Nothing in the play area scrolls on its own. */
(function () {
  "use strict";
  const GA = window.GeoArcade;
  const GAME_ID = "currency-quest";
  const MISSED_MS = 3000;        // a timeout's reveal stays up a little longer than a correct answer
  const LOCATOR_FLY_MS = 650;
  const sleep = (ms) => new Promise((r) => setTimeout(r, GA.reducedMotion() ? 0 : ms));

  function formatTime(ms) {
    return `${(Math.max(0, ms) / 1000).toFixed(1)}s`;
  }

  document.addEventListener("alpine:init", () => {
    Alpine.data("currencyQuest", (cfg) => {
      // Runtime objects live in P (per instance, outside Alpine's reactivity).
      const P = {};
      return {
        screen: cfg.mode === "feed" ? "play" : "start",   // start | play | summary
        phase: "idle",          // idle | ready | question | result
        lifecycle: "idle",      // idle | preparing | ready | playing | paused | finished | error
        busy: false,
        error: "",
        best: null,
        maxScore: 0,
        total: 0,
        roundId: null,
        q: null,                // the active question (no answer in it)
        options: [],            // [{ id, code, symbol, name (only once revealed), state: "" | "wrong" | "leaving" | "correct" | "answer" | "dim" }]
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
        locatorShown: false,    // the globe has drawn the current country
        locatorFailed: false,

        init() {
          P.root = this.$el;
          P.api = GA.api(cfg.apiBase);
          P.host = {};
          P.dormant = false;
          P.base = 0;           // ms already elapsed (server) when the timer last started
          P.timer = new GA.StageTimer({ onTick: () => this.tick(), onExpire: () => this.onExpire() });
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
        get skeletons() { return Number(this.settings().values.options); },

        /** The locator shows (as a skeleton until the tap) when it's on and the country has a shape. */
        get showLocator() {
          if (this.settings().values.locator !== "on" || this.locatorFailed) return false;
          return !this.q || !!this.q.country.shape;
        },

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
          else if (this.q && this.showLocator) this.locate(this.q.country.shape, false);
        },

        // ------------------------------------------------------------ locator (decorative globe)
        async ensureLocator() {
          if (P.locator) return P.locator;
          if (!P.worldPromise) {
            P.worldPromise = GeoMaps.loadWorld(cfg.topoUrl, cfg.metaUrl).then((w) => { P.world = w; return w; });
          }
          const world = await P.worldPromise;
          if (!P.locator) {
            P.locator = new GeoMaps.MapView(this.ref("locator"), world, { kind: "globe", interactive: false, tinyArea: 60 });
            if (P.dormant) P.locator.pause();
          }
          return P.locator;
        },

        /** Turn the globe to the country and highlight it. */
        async locate(shape, animate = true) {
          if (!shape || this.settings().values.locator !== "on") return;
          let v;
          try {
            v = await this.ensureLocator();
          } catch (e) {
            this.locatorFailed = true;   // the locator is a nice-to-have: hide it, keep playing
            return;
          }
          if (!this.q || this.q.country.shape !== shape || !P.world.shapes[shape]) return;
          await this.$nextTick();
          v.resize();
          v.setState({ target: shape }, { fresh: true, pulse: true });
          v.show(v.worldCamera(P.world.shapes[shape].centroid), animate && this.locatorShown, LOCATOR_FLY_MS);
          this.locatorShown = true;
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
              this.locatorShown = false;
              this.setPotential(cfg.startPoints, 1);
              this.setFeedback("", "");
              this.phase = "ready";
              if (cfg.mode === "feed") this.screen = "play";
              // Warm the map data in the background so the first question's locator is instant.
              if (this.settings().values.locator === "on") this.ensureLocator().catch(() => { this.locatorFailed = true; });
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
          P.locator && P.locator.pause();
          P.timer.hold();
          P.auto && P.auto.pause();
          if (this.lifecycle !== "playing") return;
          this.setLifecycle("paused");
          this.serverPause();
        },

        resume() {
          if (!P.dormant) return;
          P.dormant = false;
          P.locator && P.locator.resume();
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
          P.locator && P.locator.destroy();
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
          this.options = q.options.map((o) => ({ ...o, name: "", state: "" }));
          this.setFeedback("", "");
          this.phase = "question";
          P.limitMs = q.time_limit_ms;
          this.restartTimer(q.elapsed_ms);
          this.locate(q.country.shape);
          this.announce(`Question ${q.number} of ${q.total}. Which currency is used in ${q.country.name}? ` +
            `Options: ${q.options.map((o, i) => `${i + 1}, ${this.optionLabel(o)}`).join("; ")}.`);
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
          opt.name = r.name;
          this.wrong = r.wrong;
          this.tick();
          this.showToast(`−${r.penalty}`, "bad");
          this.setFeedback(`Not ${opt.code}, the ${r.name}. −${r.penalty} points.`, "error");
          this.announce(`${opt.code}, the ${r.name}, is wrong. Minus ${r.penalty} points. ${r.potential} points left.`);
          const index = this.q.index;
          await sleep(420);
          opt.state = "leaving";
          await sleep(240);
          if (this.phase !== "question" || this.q.index !== index) return;
          const live = new Set(r.options);
          this.options = this.options.filter((o) => live.has(o.id));
          this.focusFirstOption();
        },

        endQuestion(r) {
          P.timer.stop();
          this.result = r;
          this.phase = "result";
          this.score = r.score;
          this.wrong = r.wrong;
          this.setPotential(r.points, r.points / cfg.startPoints);
          const answerId = r.answer.option_id;
          const names = r.option_names || {};
          for (const o of this.options) {
            o.name = names[o.id] || o.name;
            if (o.id === answerId) o.state = r.status === "correct" ? "correct" : "answer";
            else if (o.state !== "leaving") o.state = "dim";
          }
          if (!this.options.some((o) => o.id === answerId)) {
            const c = r.answer.currency;
            this.options.push({ id: answerId, code: c.code, symbol: c.symbol, name: c.name, state: "answer" });
          }
          const cur = r.answer.currency;
          const also = r.answer.also_used_in.length ? ` ${this.alsoUsedIn(r.answer)}.` : "";
          this.setFeedback("", "");
          if (r.status === "correct") {
            this.showToast(`+${r.points}`, "good");
            this.announce(`Correct! ${r.answer.country} uses the ${cur.name}, ${cur.code}. ${r.points} points.${also}`);
          } else {
            this.announce(`Time's up. ${r.answer.country} uses the ${cur.name}, ${cur.code}. No points.${also}`);
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
          if (pct >= 0.8) return "Rich in knowledge. You know your money.";
          if (pct >= 0.55) return "Great round. Speed is where the points are.";
          if (pct >= 0.3) return "Solid. Trust your first instinct and go faster.";
          return "Every currency counts. Go again!";
        },

        /** "Also used in: Austria, Belgium, … +20 more" */
        alsoUsedIn(answer) {
          const more = answer.also_more ? ` +${answer.also_more} more` : "";
          return `Also used in: ${answer.also_used_in.join(", ")}${more}`;
        },

        /** Spoken label for an option: "EUR, symbol €" (plus the name once it's revealed). */
        optionLabel(o) {
          return [o.code, o.symbol && `symbol ${o.symbol}`, o.name].filter(Boolean).join(", ");
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
            const b = P.root.querySelector(".cur-option:not(:disabled)");
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
