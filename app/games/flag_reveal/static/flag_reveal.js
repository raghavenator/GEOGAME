/* Flag Reveal client. Presentation only: the server picks countries and options,
   checks picks and scores. This file runs the stage timer, drives the reveal and
   animations, and reports what the player did.

   Embed lifecycle (see GeoArcade.alpineEmbed in core.js):
     prepare()  create a round and preload the first flag's bytes (nothing shown) -> "ready"
     begin()    tell the server play started, reveal stage 1, start the timer -> "playing"
     pause()/resume()  hold timers (server shifts the paused time out of scoring)
   On the full page the Start button runs prepare() then begin(). */
(function () {
  "use strict";
  const GA = window.GeoArcade;
  const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const sleep = (ms) => new Promise((r) => setTimeout(r, GA.reducedMotion() ? 0 : ms));
  const STAGE_WORDS = ["One third", "Two thirds", "The whole flag"];
  const GAME_ID = "flag-reveal";

  async function preloadImage(url) {
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
    } catch (e) { /* the <img> will still try */ }
  }

  document.addEventListener("alpine:init", () => {
    Alpine.data("flagReveal", (cfg) => {
      const P = {};
      // Runtime objects live in P (per instance, outside Alpine's reactivity). Undeclared
      // `this.x` would land on the outermost scope, i.e. be shared with other games in the feed.
      return {
      // ---- screen state
      screen: cfg.mode === "feed" ? "play" : "start",   // start | play | summary
      phase: "idle",            // idle | ready | loading | question | result
      lifecycle: "idle",        // idle | preparing | ready | playing | paused | finished | error
      busy: false,
      error: "",
      best: null,
      maxScore: 0,
      skeletonCount: cfg.optionsShown,
      // ---- round state (mirrors the server; never contains the answer early)
      roundId: null,
      pending: null,            // first question, held back until begin()
      q: { index: 0, number: 0, total: 0 },
      flagUrl: "",
      reveal: { orientation: "vertical", order: [0, 1, 2] },
      options: [],              // [{ id, name, state: "" | "wrong" | "leaving" | "correct" | "answer" }]
      stage: 1,
      stageSeconds: 5,
      score: 0,
      result: null,
      summary: null,
      // ---- feedback
      feedback: "",
      feedbackKind: "",
      announcement: "",
      // ---- guards
      picking: false,
      advancing: false,
      autoNext: null,

      init() {
        P.root = this.$el;   // the game root (in methods, $el is whatever element fired the event)
        P.api = GA.api(cfg.apiBase);
        P.host = {};
        P.timer = new GA.StageTimer({
          onTick: (f, rem) => this.renderTimer(f, rem),
          onExpire: () => this.onStageExpired(),
        });
        this.refreshBest();
        P.onSettings = (e) => this.onSettingsChanged(e);
        P.onKey = (e) => this.onKey(e);
        window.addEventListener("settings-changed", P.onSettings);
        window.addEventListener("keydown", P.onKey);
        if (cfg.mode === "page") this.$nextTick(() => this.ref("startBtn") && this.ref("startBtn").focus());
      },

      settings() { return GA.settings(GAME_ID); },

      async refreshBest() {
        try {
          const r = await P.api.post("best", { settings: this.settings().values });
          this.best = r.best;
          this.maxScore = r.max_score;
        } catch (e) { /* not critical */ }
      },

      onSettingsChanged(e) {
        if (e.detail && e.detail.gameId !== GAME_ID) return;
        this.error = "";
        this.refreshBest();
        // Feed: a prepared (not yet started) round used the old settings; prepare a new one.
        if (cfg.mode === "feed" && ["ready", "error", "idle"].includes(this.lifecycle)) {
          this.prepare().catch(() => {});
        }
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
          this.autoNext && this.autoNext.cancel();
          this.autoNext = null;
          this.setLifecycle("preparing");
          this.error = "";
          try {
            const r = await P.api.post("start", { settings: this.settings().values });
            this.roundId = r.round_id;
            this.stageSeconds = r.stage_seconds;
            this.maxScore = r.max_score;
            this.best = r.best;
            this.score = 0;
            this.summary = null;
            this.result = null;
            this.ref("score").textContent = "0";
            await preloadImage(r.question.flag_url);   // bytes cached, but not rendered
            this.pending = r.question;
            this.flagUrl = "";
            this.options = [];
            this.setFeedback("", "");
            this.q = { index: 0, number: 1, total: r.question.total };
            this.stage = 1;
            this.renderTimer(0, this.stageSeconds * 1000);
            this.phase = "ready";
            if (cfg.mode === "feed") this.screen = "play";
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
          await this.loadQuestion(r.question);
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
          await this.prepare();
          await this.begin();
        } catch (e) { /* surfaced by handleError */ } finally {
          this.busy = false;
        }
      },

      pause() {
        if (this.lifecycle !== "playing") return;
        this.setLifecycle("paused");
        P.timer.hold();
        this.autoNext && this.autoNext.pause();
        P.api.post("pause", { round_id: this.roundId }).catch(() => {});
      },

      resume() {
        if (this.lifecycle !== "paused") return;
        this.setLifecycle("playing");
        P.api.post("resume", { round_id: this.roundId }).catch(() => {});
        P.timer.release();
        this.autoNext && this.autoNext.resume();
      },

      destroy() {
        P.timer.destroy();
        this.autoNext && this.autoNext.cancel();
        window.removeEventListener("settings-changed", P.onSettings);
        window.removeEventListener("keydown", P.onKey);
      },

      nextGame() { P.host.onNextGame && P.host.onNextGame(); },

      // ------------------------------------------------------------ questions
      async loadQuestion(question) {
        const loadId = (P.loadId = (P.loadId || 0) + 1);
        P.timer.stop();
        this.phase = "loading";            // hides slices instantly (no transition)
        this.result = null;
        this.setFeedback("", "");
        this.stage = question.stage;
        this.q = question;
        this.reveal = question.reveal;
        this.options = question.options.map((o) => ({ ...o, state: "" }));
        this.renderTimer(0, this.stageSeconds * 1000);
        await this.$nextTick();
        await nextFrame();                 // ensure the hidden state is painted before swapping src
        await preloadImage(question.flag_url);
        if (loadId !== P.loadId) return;   // superseded by a newer load
        this.flagUrl = question.flag_url;
        await this.$nextTick();
        await nextFrame();
        GA.replayAnimation(this.ref("card"), "animate-question-in");
        this.phase = "question";
        this.announce(`Question ${question.number} of ${question.total}. One third of the flag is showing. ` +
          `Options: ${this.options.map((o, i) => `${i + 1}, ${o.name}`).join("; ")}.`);
        this.focusFirstOption();
        P.timer.start(this.stageSeconds * 1000);
      },

      sliceVisible(i) {
        if (this.phase === "result") return true;
        if (this.phase !== "question") return false;
        return this.reveal.order.indexOf(i) < this.stage;
      },

      flagLabel() {
        if (this.phase === "result") return "The full flag";
        if (this.phase === "ready") return "Hidden flag. Tap play to start.";
        if (this.phase !== "question") return "Loading the next flag";
        return `Mystery flag: ${STAGE_WORDS[this.stage - 1].toLowerCase()} revealed`;
      },

      // ------------------------------------------------------------ timer & reveal
      renderTimer(fraction, remainingMs) {
        const ring = this.ref("ring");
        if (!ring) return;
        ring.style.setProperty("--progress", String(fraction));
        const secs = Math.ceil(remainingMs / 1000);
        this.ref("ringLabel").textContent = this.phase === "result" ? "" : String(secs);
        ring.dataset.urgency = fraction > 0.8 ? "danger" : fraction > 0.55 ? "warn" : "";
        cfg.stagePoints.forEach((_, i) => {
          const seg = this.ref(`seg${i + 1}`);
          if (!seg) return;
          const n = i + 1;
          const fill = n < this.stage ? 1 : n === this.stage ? fraction : 0;
          seg.style.setProperty("--fill", String(fill));
        });
      },

      async onStageExpired() {
        if (this.phase !== "question") return;
        const stage = this.stage;
        const index = this.q.index;
        let r;
        try {
          r = await P.api.post("timeout", { round_id: this.roundId, stage });
        } catch (e) {
          return this.handleError(e);
        }
        if (this.phase !== "question" || this.q.index !== index) return; // a pick won the race
        if (r.status === "solved") return;
        if (r.missed) {
          return this.endQuestion({ kind: "missed", name: r.country.name, points: 0, score: r.score,
                                    isLast: r.is_last, answerOption: r.answer_option });
        }
        this.stage = Math.max(this.stage, r.stage);
        this.announce(`${STAGE_WORDS[this.stage - 1]} of the flag ${this.stage === 3 ? "is" : "are"} now showing.`);
        P.timer.start(this.stageSeconds * 1000);
      },

      // ------------------------------------------------------------ picking
      async pick(opt) {
        if (!opt || opt.state || this.phase !== "question" || this.picking || this.lifecycle !== "playing") return;
        this.picking = true;
        const index = this.q.index;
        const remaining = Math.max(0, Math.min(this.stageSeconds * 1000, Math.round(P.timer.remaining())));
        try {
          const r = await P.api.post("pick", {
            round_id: this.roundId, option_id: opt.id, stage: this.stage, remaining_ms: remaining,
          });
          if (this.phase !== "question" || this.q.index !== index) return;
          if (r.correct) {
            opt.state = "correct";
            this.endQuestion({
              kind: "solved", name: r.country.name, points: r.points, stagePoints: r.stage_points,
              speed: r.speed_bonus, wrong: r.wrong, stage: r.stage, score: r.score, isLast: r.is_last,
            });
          } else {
            this.stage = Math.max(this.stage, r.stage);
            this.removeWrong(opt, r);
          }
        } catch (e) {
          if (e.code === "option_unavailable") this.setFeedback(e.message, "error");
          else if (e.code !== "question_over") this.handleError(e);
        } finally {
          this.picking = false;
        }
      },

      async removeWrong(opt, r) {
        opt.state = "wrong";
        const msg = `Not quite. It's not ${r.guessed}.`;
        this.setFeedback(`${msg} −${r.penalty}`, "error");
        this.announce(`${msg} Minus ${r.penalty} points.`);
        this.popPoints(`−${r.penalty}`, "bad");
        await sleep(420);
        opt.state = "leaving";
        await sleep(240);
        this.options = this.options.filter((o) => o !== opt);
        if (this.phase === "question") this.focusFirstOption();
      },

      endQuestion(result) {
        P.timer.stop();
        const before = this.score;
        this.score = result.score;
        this.result = result;
        this.phase = "result";
        this.renderTimer(1, 0); // fill the meter up to the stage it ended on
        GA.countUp(this.ref("score"), before, this.score);
        if (result.kind === "solved") {
          this.popPoints(`+${result.points}`, "good");
          const parts = [`+${result.stagePoints} for stage ${result.stage}`];
          if (result.wrong) parts.push(`${result.wrong} wrong`);
          parts.push(`+${result.speed} speed bonus`);
          this.setFeedback(parts.join(" · "), "success");
          this.announce(`Correct! It's ${result.name}. Plus ${result.points} points. Score ${this.score}.`);
        } else {
          const answer = this.options.find((o) => o.id === result.answerOption);
          if (answer) answer.state = "answer";
          this.setFeedback("No points this time.", "missed");
          this.announce(`Time's up. It was ${result.name}. No points.`);
        }
        this.$nextTick(() => this.ref("nextBtn") && this.isLive() && this.ref("nextBtn").focus({ preventScroll: true }));
        const delay = result.kind === "solved" ? cfg.feedbackMs : cfg.missedFeedbackMs;
        this.autoNext && this.autoNext.cancel();
        this.autoNext = GA.pausableDelay(delay, () => this.next());
        if (this.lifecycle === "paused") this.autoNext.pause();
      },

      async next() {
        if (this.advancing || this.phase !== "result") return;
        this.advancing = true;
        this.autoNext && this.autoNext.cancel();
        this.autoNext = null;
        try {
          const r = await P.api.post("next", { round_id: this.roundId, from_index: this.q.index });
          if (r.finished) await this.showSummary();
          else await this.loadQuestion(r.question);
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
        const pct = this.summary.score / this.summary.max_score;
        if (pct >= 0.8) return "Outstanding. You know your flags!";
        if (pct >= 0.55) return "Great round. Sharp eyes.";
        if (pct >= 0.3) return "Solid effort. Faster picks score more.";
        return "Every expert started somewhere. Go again!";
      },

      // ------------------------------------------------------------ helpers
      /** x-ref lookup from the game root ($refs resolves from whichever element fired the
          event, which may already be gone, e.g. an option button removed on solve). */
      ref(name) { return P.root.querySelector(`[x-ref="${name}"]`); },

      /** In the feed, only the active (unpaused) card reacts to keys and takes focus. */
      isLive() {
        return cfg.mode === "page" || (this.lifecycle !== "paused" && !P.root.closest("[inert]"));
      },

      onKey(e) {
        if (this.screen !== "play" || this.phase !== "question" || this.lifecycle !== "playing") return;
        if (!this.isLive() || this.settings().open || e.defaultPrevented) return;
        if (e.altKey || e.ctrlKey || e.metaKey || !/^[1-9]$/.test(e.key)) return;
        if (e.target.closest && e.target.closest("input, select, textarea, [contenteditable]")) return;
        const opt = this.options.filter((o) => o.state !== "leaving")[Number(e.key) - 1];
        if (opt) {
          e.preventDefault();
          this.pick(opt);
        }
      },

      focusFirstOption() {
        this.$nextTick(() => {
          if (!this.isLive()) return;
          const b = P.root.querySelector(".fr-option:not(:disabled)");
          b && b.focus({ preventScroll: true });
        });
      },

      popPoints(text, kind) {
        const pop = this.ref("pop");
        if (!pop) return;
        pop.textContent = text;
        pop.classList.toggle("is-bad", kind === "bad");
        GA.replayAnimation(pop, "is-popping");
      },

      setFeedback(text, kind) {
        this.feedback = text;
        this.feedbackKind = kind;
      },

      announce(text) {
        // Clear first so repeated identical messages are still read out.
        this.announcement = "";
        setTimeout(() => { this.announcement = text; }, 30);
      },

      handleError(e) {
        P.timer.stop();
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
