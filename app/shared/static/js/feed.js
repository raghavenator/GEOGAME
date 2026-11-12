/* Geo Arcade homepage feed: full-screen cards that snap one at a time.

   - Mounts every game card through the embed contract (GeoArcade.games[id]) and
     prepares them all in parallel, so each is ready before you reach it.
   - Only the active card (>= 60% visible) is live: the others are paused and inert.
   - Navigation: swipe/scroll (CSS scroll snap), dots, up/down buttons, keys
     (ArrowUp/Down, PageUp/Down, J/K; never while typing), URL hash.
   - Gesture zones: anything inside [data-gesture-zone="map"] belongs to the map;
     wheel/touch there never moves the feed or starts pull-to-refresh.
   - Pull to refresh at the top of the first card (touch drag or wheel past the top). */
(function () {
  "use strict";
  const GA = window.GeoArcade;
  const HINTS_KEY = "geo-arcade:feed:hints";
  const MAP_ZONE = "[data-gesture-zone='map']";
  const TYPING = "input, textarea, select, [contenteditable='true']";
  const ACTIVE_RATIO = 0.6;

  function loadHints() {
    try { return { next: false, map: false, ...JSON.parse(localStorage.getItem(HINTS_KEY) || "{}") }; }
    catch (e) { return { next: false, map: false }; }
  }
  function saveHints(h) {
    try { localStorage.setItem(HINTS_KEY, JSON.stringify(h)); } catch (e) { /* private mode */ }
  }

  document.addEventListener("alpine:init", () => {
    const instances = new Map();   // game id -> embed instance (kept out of Alpine's reactivity)

    Alpine.data("geoFeed", (cfg) => ({
      items: cfg.items,
      activeIndex: 0,
      states: Object.fromEntries(cfg.items.filter((i) => i.kind === "game").map((i) => [i.gameId, "preparing"])),
      errors: {},
      announcement: "",
      hints: loadHints(),
      showMapHint: false,
      pull: 0,
      pullThreshold: 72,
      refreshing: false,
      locked: false,

      init() {
        this.feed = this.$refs.feed;
        this.sections = [...this.feed.querySelectorAll(".feed-item")];
        const fromHash = this.items.findIndex((it) => `#${it.id}` === location.hash);
        this.activeIndex = Math.max(0, fromHash);
        this.applyInert();
        document.addEventListener("alpine:initialized", () => this.start(fromHash));
      },

      activeItem() { return this.items[this.activeIndex]; },

      start(fromHash) {
        if (fromHash > 0) this.feed.scrollTop = this.sections[fromHash].offsetTop;
        this.mountGames();
        this.observe();
        this.bindKeys();
        this.bindGestures();
        this.bindTypingLock();
        this.activate(this.activeIndex, { initial: true });
      },

      // ------------------------------------------------------------ games
      mountGames() {
        this.items.forEach((item, index) => {
          if (item.kind !== "game") return;
          const embed = GA.games[item.gameId];
          const section = this.sections[index];
          if (!embed) { this.states[item.gameId] = "error"; return; }
          const instance = embed.mount(section.querySelector("[data-game-root]"), {
            mode: "feed",
            onState: (state, detail) => this.onState(item.gameId, state, detail),
            onRoundFinished: () => {},
            onNextGame: () => this.go(index + 1),
          });
          instances.set(item.gameId, instance);
          if (index !== this.activeIndex) instance.pause();
        });
        // Prepare every game at once so each card is ready before you reach it.
        for (const [id, instance] of instances) instance.prepare().catch(() => { this.states[id] = "error"; });
      },

      onState(gameId, state, detail) {
        this.states[gameId] = state;
        if (state === "error") this.errors[gameId] = detail && detail.message;
      },

      tapToPlay(gameId) {
        if (this.states[gameId] !== "ready") return;
        this.states[gameId] = "starting";
        instances.get(gameId).start();
      },

      retry(gameId) {
        instances.get(gameId).prepare().catch(() => {});
      },

      openSettings(gameId, trigger) {
        const store = GA.settings(gameId);
        if (store && !store.locked) store.openModal(trigger);
      },

      settingsLocked(gameId) {
        const store = GA.settings(gameId);
        return !store || store.locked;
      },

      // ------------------------------------------------------------ active card
      observe() {
        const io = new IntersectionObserver((entries) => {
          for (const e of entries) {
            if (e.isIntersecting && e.intersectionRatio >= ACTIVE_RATIO) this.activate(Number(e.target.dataset.index));
          }
        }, { root: this.feed, threshold: [ACTIVE_RATIO] });
        this.sections.forEach((s) => io.observe(s));
      },

      activate(index, { initial = false } = {}) {
        if (index === this.activeIndex && !initial) return;
        const prev = this.activeIndex;
        this.activeIndex = index;
        this.applyInert();
        const item = this.items[index];
        // Only the active card's game runs timers, loops and canvas rendering.
        for (const [id, instance] of instances) {
          if (id === item.gameId) instance.resume();
          else instance.pause();
        }
        if (!initial) {
          history.replaceState(null, "", `#${item.id}`);
          this.announce(`${item.title}, ${index + 1} of ${this.items.length}`);
          if (!this.hints.next) { this.hints.next = true; saveHints(this.hints); }
          if (this.showMapHint && prev !== index) {
            this.showMapHint = false;
            this.hints.map = true;
            saveHints(this.hints);
          }
          this.atTopSince = performance.now();
        }
        if (item.gameId === "border-bridge" && !this.hints.map) this.showMapHint = true;
      },

      applyInert() {
        this.sections.forEach((s, i) => { s.inert = i !== this.activeIndex; });
      },

      go(index) {
        if (this.locked) return;
        const i = Math.max(0, Math.min(this.items.length - 1, index));
        this.feed.scrollTo({ top: this.sections[i].offsetTop, behavior: GA.reducedMotion() ? "auto" : "smooth" });
      },

      announce(text) {
        this.announcement = "";
        setTimeout(() => { this.announcement = text; }, 30);
      },

      // ------------------------------------------------------------ keyboard
      bindKeys() {
        window.addEventListener("keydown", (e) => {
          if (e.defaultPrevented || this.locked || e.altKey || e.ctrlKey || e.metaKey) return;
          if (this.items.some((it) => it.gameId && GA.settings(it.gameId) && GA.settings(it.gameId).open)) return;
          const t = e.target;
          if (t.closest && (t.closest(TYPING) || t.closest(MAP_ZONE))) return;
          const step = { ArrowDown: 1, PageDown: 1, j: 1, J: 1, ArrowUp: -1, PageUp: -1, k: -1, K: -1 }[e.key];
          if (!step) return;
          e.preventDefault();
          this.go(this.activeIndex + step);
        });
      },

      // ------------------------------------------------------------ gestures
      inMapZone(target) { return !!(target && target.closest && target.closest(MAP_ZONE)); },

      canPull(target) {
        if (this.activeIndex !== 0 || this.locked || this.refreshing || this.feed.scrollTop > 0) return false;
        if (this.inMapZone(target)) return false;
        const a = document.activeElement;
        if (a && a.matches && a.matches(TYPING)) return false;
        const id = this.items[0].gameId;
        return !id || !["playing", "paused", "starting"].includes(this.states[id]);  // not mid-game
      },

      bindGestures() {
        const feed = this.feed;
        this.atTopSince = -Infinity;
        feed.addEventListener("scroll", () => {
          if (feed.scrollTop <= 0 && this._lastTop > 0) this.atTopSince = performance.now();
          this._lastTop = feed.scrollTop;
        }, { passive: true });

        // Wheel/trackpad: one card per gesture. Native snapping would bounce a single
        // mouse notch back to the same card, and trackpad momentum could skip cards.
        // The wheel over a map zooms the map (map_view.js) and never scrolls the feed.
        feed.addEventListener("wheel", (e) => {
          if (this.inMapZone(e.target) || this.locked) { e.preventDefault(); return; }
          if (e.ctrlKey || this.innerScrollable(e.target, e.deltaY)) return;
          e.preventDefault();
          if (e.deltaY < 0 && this.activeIndex === 0) this.onWheelPull(e);
          else this.onWheelPage(e);
        }, { passive: false, capture: true });

        // Touch: pull to refresh from the top of the first card.
        let startY = null;
        feed.addEventListener("touchstart", (e) => {
          startY = e.touches.length === 1 && this.canPull(e.target) ? e.touches[0].clientY : null;
        }, { passive: true });
        feed.addEventListener("touchmove", (e) => {
          if (startY === null) return;
          const dy = e.touches[0].clientY - startY;
          if (dy <= 0 || feed.scrollTop > 0) { this.pull = 0; return; }
          e.preventDefault();
          this.pull = Math.min(this.pullThreshold * 1.6, dy * 0.5);
        }, { passive: false });
        const end = () => {
          if (startY === null) return;
          startY = null;
          if (this.pull >= this.pullThreshold) this.refresh();
          else this.pull = 0;
        };
        feed.addEventListener("touchend", end);
        feed.addEventListener("touchcancel", () => { startY = null; this.pull = 0; });
      },

      /** A scrollable area inside the card (e.g. a long summary) that can still scroll that way. */
      innerScrollable(target, dy) {
        for (let el = target; el && el !== this.feed; el = el.parentElement) {
          if (!(el instanceof Element)) continue;
          const oy = getComputedStyle(el).overflowY;
          if ((oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight + 1) {
            if (dy > 0 && el.scrollTop + el.clientHeight < el.scrollHeight - 1) return true;
            if (dy < 0 && el.scrollTop > 0) return true;
          }
        }
        return false;
      },

      onWheelPage(e) {
        const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
        clearTimeout(this._wheelIdle);
        // The gesture (including trackpad momentum) ends once events pause briefly.
        this._wheelIdle = setTimeout(() => { this._wheelAcc = 0; this._wheelSpent = false; }, 180);
        if (this._wheelSpent) return;
        this._wheelAcc = (this._wheelAcc || 0) + dy;
        if (Math.abs(this._wheelAcc) < 24) return;
        this._wheelSpent = true;
        this._wheelAcc = 0;
        this.go(this.activeIndex + Math.sign(dy));
      },

      onWheelPull(e) {
        if (e.deltaY >= 0 || !this.canPull(e.target)) {
          if (e.deltaY > 0) this.pull = 0;
          return;
        }
        // Ignore the tail of a fling that just carried us back to the top.
        if (performance.now() - this.atTopSince < 450) return;
        this._wheelPull = (this._wheelPull || 0) - e.deltaY * (e.deltaMode === 1 ? 16 : 1);
        this.pull = Math.min(this.pullThreshold * 1.6, this._wheelPull * 0.3);
        clearTimeout(this._wheelTimer);
        if (this.pull >= this.pullThreshold) { this.refresh(); return; }
        this._wheelTimer = setTimeout(() => { this._wheelPull = 0; if (!this.refreshing) this.pull = 0; }, 280);
      },

      refresh() {
        if (this.refreshing) return;
        this.refreshing = true;
        this.pull = this.pullThreshold;
        history.replaceState(null, "", location.pathname);
        setTimeout(() => location.reload(), GA.reducedMotion() ? 0 : 250);
      },

      // ------------------------------------------------------------ mobile keyboard
      // A focused text field opens the on-screen keyboard, which shrinks the
      // viewport; lock the feed so snapping can't jump to another card meanwhile.
      bindTypingLock() {
        const vv = window.visualViewport;
        const size = () => {
          if (this.locked && vv) document.documentElement.style.setProperty("--feed-vh", `${vv.height}px`);
        };
        this.feed.addEventListener("focusin", (e) => {
          if (!e.target.matches(TYPING)) return;
          this._lockTop = this.feed.scrollTop;
          this.locked = true;
          size();
        });
        this.feed.addEventListener("focusout", (e) => {
          if (!e.target.matches(TYPING)) return;
          this.locked = false;
          document.documentElement.style.removeProperty("--feed-vh");
          this.feed.scrollTop = this._lockTop;
        });
        if (vv) vv.addEventListener("resize", size);
      },
    }));
  });
})();
