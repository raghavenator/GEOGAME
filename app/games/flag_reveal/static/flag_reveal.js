/* Flag Reveal client. Presentation only: the server picks countries and options,
   checks picks and scores. This file runs the stage timer, drives the reveal and
   animations, and reports what the player did. */
(function () {
  "use strict";
  const GA = window.GeoArcade;
  const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const sleep = (ms) => new Promise((r) => setTimeout(r, GA.reducedMotion() ? 0 : ms));
  const STAGE_WORDS = ["One third", "Two thirds", "The whole flag"];

  document.addEventListener("alpine:init", () => {
    Alpine.data("flagReveal", (cfg) => ({
      // ---- screen state
      screen: "start",          // start | play | summary
      phase: "idle",            // idle | loading | question | result
      busy: false,
      error: "",
      best: null,
      maxScore: 0,
      // ---- round state (mirrors the server; never contains the answer early)
      roundId: null,
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
        this.api = GA.api(cfg.apiBase);
        this.timer = new GA.StageTimer({
          onTick: (f, rem) => this.renderTimer(f, rem),
          onExpire: () => this.onStageExpired(),
        });
        this.refreshBest();
        window.addEventListener("settings-changed", () => { this.error = ""; this.refreshBest(); });
        window.addEventListener("keydown", (e) => this.onKey(e));
        this.$nextTick(() => this.$refs.startBtn && this.$refs.startBtn.focus());
      },

      settings() { return Alpine.store("settings").values; },

      async refreshBest() {
        try {
          const r = await this.api.post("best", { settings: this.settings() });
          this.best = r.best;
          this.maxScore = r.max_score;
        } catch (e) { /* not critical */ }
      },

      // ------------------------------------------------------------ round lifecycle
      async start() {
        if (this.busy) return;
        this.busy = true;
        this.error = "";
        try {
          const r = await this.api.post("start", { settings: this.settings() });
          this.roundId = r.round_id;
          this.stageSeconds = r.stage_seconds;
          this.maxScore = r.max_score;
          this.best = r.best;
          this.score = 0;
          this.summary = null;
          this.$refs.score.textContent = "0";
          Alpine.store("settings").locked = true;
          this.screen = "play";
          await this.loadQuestion(r.question);
        } catch (e) {
          this.handleError(e);
        } finally {
          this.busy = false;
        }
      },

      async loadQuestion(question) {
        const loadId = (this._loadId = (this._loadId || 0) + 1);
        this.timer.stop();
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
        try {
          const img = new Image();
          img.src = question.flag_url;
          await img.decode();
        } catch (e) { /* the <img> will still try */ }
        if (loadId !== this._loadId) return;   // superseded by a newer load
        this.flagUrl = question.flag_url;
        await this.$nextTick();
        await nextFrame();
        GA.replayAnimation(this.$refs.card, "animate-question-in");
        this.phase = "question";
        this.announce(`Question ${question.number} of ${question.total}. One third of the flag is showing. ` +
          `Options: ${this.options.map((o, i) => `${i + 1}, ${o.name}`).join("; ")}.`);
        this.focusFirstOption();
        this.timer.start(this.stageSeconds * 1000);
      },

      sliceVisible(i) {
        if (this.phase === "result") return true;
        if (this.phase !== "question") return false;
        return this.reveal.order.indexOf(i) < this.stage;
      },

      flagLabel() {
        if (this.phase === "result") return "The full flag";
        if (this.phase !== "question") return "Loading the next flag";
        return `Mystery flag: ${STAGE_WORDS[this.stage - 1].toLowerCase()} revealed`;
      },

      // ------------------------------------------------------------ timer & reveal
      renderTimer(fraction, remainingMs) {
        const ring = this.$refs.ring;
        if (!ring) return;
        ring.style.setProperty("--progress", String(fraction));
        const secs = Math.ceil(remainingMs / 1000);
        this.$refs.ringLabel.textContent = this.phase === "result" ? "" : String(secs);
        ring.dataset.urgency = fraction > 0.8 ? "danger" : fraction > 0.55 ? "warn" : "";
        cfg.stagePoints.forEach((_, i) => {
          const seg = this.$refs[`seg${i + 1}`];
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
          r = await this.api.post("timeout", { round_id: this.roundId, stage });
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
        this.timer.start(this.stageSeconds * 1000);
      },

      // ------------------------------------------------------------ picking
      async pick(opt) {
        if (!opt || opt.state || this.phase !== "question" || this.picking) return;
        this.picking = true;
        const index = this.q.index;
        const remaining = Math.max(0, Math.min(this.stageSeconds * 1000, Math.round(this.timer.remaining())));
        try {
          const r = await this.api.post("pick", {
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
        this.timer.stop();
        const before = this.score;
        this.score = result.score;
        this.result = result;
        this.phase = "result";
        this.renderTimer(1, 0); // fill the meter up to the stage it ended on
        GA.countUp(this.$refs.score, before, this.score);
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
        this.$nextTick(() => this.$refs.nextBtn && this.$refs.nextBtn.focus({ preventScroll: true }));
        const delay = result.kind === "solved" ? cfg.feedbackMs : cfg.missedFeedbackMs;
        this.autoNext && this.autoNext.cancel();
        this.autoNext = GA.pausableDelay(delay, () => this.next());
      },

      async next() {
        if (this.advancing || this.phase !== "result") return;
        this.advancing = true;
        this.autoNext && this.autoNext.cancel();
        this.autoNext = null;
        try {
          const r = await this.api.post("next", { round_id: this.roundId, from_index: this.q.index });
          if (r.finished) await this.showSummary();
          else await this.loadQuestion(r.question);
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
        const pct = this.summary.score / this.summary.max_score;
        if (pct >= 0.8) return "Outstanding. You know your flags!";
        if (pct >= 0.55) return "Great round. Sharp eyes.";
        if (pct >= 0.3) return "Solid effort. Faster picks score more.";
        return "Every expert started somewhere. Go again!";
      },

      // ------------------------------------------------------------ helpers
      onKey(e) {
        if (this.screen !== "play" || this.phase !== "question" || Alpine.store("settings").open) return;
        if (e.altKey || e.ctrlKey || e.metaKey || !/^[1-9]$/.test(e.key)) return;
        const opt = this.options.filter((o) => o.state !== "leaving")[Number(e.key) - 1];
        if (opt) {
          e.preventDefault();
          this.pick(opt);
        }
      },

      focusFirstOption() {
        this.$nextTick(() => {
          const b = document.querySelector(".fr-option:not(:disabled)");
          b && b.focus({ preventScroll: true });
        });
      },

      popPoints(text, kind) {
        const pop = this.$refs.pop;
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
        this.timer.stop();
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
