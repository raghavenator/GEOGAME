/* Geo Arcade shared browser helpers. Plain JS, no build step.
   Exposes window.GeoArcade = { api, normalize, StageTimer, Autocomplete, countUp, reducedMotion }. */
(function () {
  "use strict";

  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ------------------------------------------------------------------ API
     JSON POST/GET with one in-flight queue per client, so requests reach the
     server in the order the player caused them (guess before timeout, etc.). */
  class ApiError extends Error {
    constructor(status, payload) {
      const err = (payload && payload.error) || {};
      super(err.message || `Request failed (${status})`);
      this.status = status;
      this.code = err.code || "network";
      this.details = err.details || {};
    }
  }

  function api(base) {
    let queue = Promise.resolve();
    async function request(method, path, body) {
      let resp;
      try {
        resp = await fetch(`${base}/${path}`, {
          method,
          headers: body ? { "Content-Type": "application/json" } : {},
          body: body ? JSON.stringify(body) : undefined,
          credentials: "same-origin",
        });
      } catch (e) {
        throw new ApiError(0, { error: { code: "network", message: "Can't reach the server. Check your connection." } });
      }
      let data = null;
      try { data = await resp.json(); } catch (e) { /* non-JSON */ }
      if (!resp.ok) throw new ApiError(resp.status, data);
      return data;
    }
    return {
      get: (path) => request("GET", path),
      /** Serialised POST: waits for earlier calls to settle first. */
      post(path, body) {
        const p = queue.then(() => request("POST", path, body));
        queue = p.catch(() => {});
        return p;
      },
    };
  }

  /* ------------------------------------------------------------------ text
     Mirrors app/shared/countries.py:normalize so suggestions feel identical. */
  function normalize(text) {
    let t = (text || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
    t = t.replace(/&/g, " and ").replace(/['.\p{Pi}\p{Pf}]/gu, "").replace(/[^\p{L}\p{N}\s]/gu, " ");
    t = t.replace(/\s+/g, " ").trim();
    if (t.startsWith("the ")) t = t.slice(4);
    if (t.startsWith("st ")) t = "saint " + t.slice(3);
    return t;
  }

  /* ------------------------------------------------------------------ timer
     rAF-driven countdown. Pauses while the tab is hidden, can only expire once
     per start(), and start() always cancels any previous run (no double timers). */
  class StageTimer {
    constructor({ onTick = () => {}, onExpire = () => {} } = {}) {
      this.onTick = onTick;
      this.onExpire = onExpire;
      this.running = false;
      this.paused = false;
      this._frame = this._frame.bind(this);
      this._onVisibility = () => (document.hidden ? this.pause() : this.resume());
      document.addEventListener("visibilitychange", this._onVisibility);
    }
    start(durationMs) {
      this.stop();
      this.duration = Math.max(1, durationMs);
      this.banked = 0;
      this.startedAt = performance.now();
      this.running = true;
      this.paused = false;
      this.generation = (this.generation || 0) + 1;
      if (document.hidden) this.pause();
      else this._raf = requestAnimationFrame(this._frame);
    }
    elapsed() {
      return this.banked + (this.running && !this.paused ? performance.now() - this.startedAt : 0);
    }
    remaining() { return Math.max(0, this.duration - this.elapsed()); }
    _frame() {
      if (!this.running || this.paused) return;
      const fraction = Math.min(1, this.elapsed() / this.duration);
      this.onTick(fraction, this.remaining());
      if (fraction >= 1) {
        this.running = false;
        const gen = this.generation;
        this.onExpire(gen);
        return;
      }
      this._raf = requestAnimationFrame(this._frame);
    }
    pause() {
      if (!this.running || this.paused) return;
      this.banked += performance.now() - this.startedAt;
      this.paused = true;
      cancelAnimationFrame(this._raf);
    }
    resume() {
      if (!this.running || !this.paused || document.hidden) return;
      this.paused = false;
      this.startedAt = performance.now();
      this._raf = requestAnimationFrame(this._frame);
    }
    stop() {
      this.running = false;
      this.paused = false;
      cancelAnimationFrame(this._raf);
    }
    destroy() {
      this.stop();
      document.removeEventListener("visibilitychange", this._onVisibility);
    }
  }

  /** setTimeout that also pauses while the tab is hidden. */
  function pausableDelay(ms, fn) {
    const t = new StageTimer({ onExpire: () => { t.destroy(); fn(); } });
    t.start(ms);
    return { cancel: () => t.destroy() };
  }

  /* ------------------------------------------------------------------ autocomplete
     Accessible combobox: arrow keys move, Enter picks the highlighted option,
     Escape closes. Items: [{ name, aliases }]. */
  class Autocomplete {
    constructor(input, listbox, { onPick, max = 6 } = {}) {
      this.input = input;
      this.listbox = listbox;
      this.onPick = onPick;
      this.max = max;
      this.items = [];
      this.results = [];
      this.active = -1;
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-controls", listbox.id);
      listbox.setAttribute("role", "listbox");
      input.addEventListener("input", () => this.update());
      input.addEventListener("keydown", (e) => this.onKey(e));
      input.addEventListener("blur", () => setTimeout(() => this.close(), 120));
      listbox.addEventListener("mousedown", (e) => e.preventDefault()); // keep focus in input
      listbox.addEventListener("click", (e) => {
        const li = e.target.closest("[data-index]");
        if (li) this.pick(Number(li.dataset.index));
      });
    }
    setItems(items) {
      this.items = items.map((it) => ({
        name: it.name,
        keys: [it.name, ...(it.aliases || [])].map((k) => ({ raw: k, norm: normalize(k) })),
      }));
    }
    search(query) {
      const q = normalize(query);
      if (!q) return [];
      const scored = [];
      for (const item of this.items) {
        let best = null;
        for (const key of item.keys) {
          let score = null;
          if (key.norm === q) score = 0;
          else if (key.norm.startsWith(q)) score = 1;
          else if (key.norm.split(" ").some((w) => w.startsWith(q))) score = 2;
          else if (q.length >= 3 && key.norm.includes(q)) score = 3;
          if (score !== null) {
            const isAlias = key.raw !== item.name;
            const s = score + (isAlias ? 0.5 : 0);
            if (!best || s < best.score) best = { score: s, via: isAlias ? key.raw : null };
          }
        }
        if (best) scored.push({ name: item.name, via: best.via, score: best.score });
      }
      scored.sort((a, b) => a.score - b.score || a.name.length - b.name.length || a.name.localeCompare(b.name));
      return scored.slice(0, this.max);
    }
    update() {
      this.results = this.search(this.input.value);
      this.active = this.results.length ? 0 : -1;
      this.render();
    }
    render() {
      const open = this.results.length > 0;
      this.listbox.hidden = !open;
      this.input.setAttribute("aria-expanded", String(open));
      this.listbox.replaceChildren(
        ...this.results.map((r, i) => {
          const li = document.createElement("li");
          li.id = `${this.listbox.id}-opt-${i}`;
          li.className = "suggestion" + (i === this.active ? " is-active" : "");
          li.setAttribute("role", "option");
          li.setAttribute("aria-selected", String(i === this.active));
          li.dataset.index = String(i);
          const name = document.createElement("span");
          name.textContent = r.name;
          li.append(name);
          if (r.via) {
            const via = document.createElement("span");
            via.className = "suggestion__via";
            via.textContent = r.via;
            li.append(via);
          }
          return li;
        })
      );
      if (open && this.active >= 0) this.input.setAttribute("aria-activedescendant", `${this.listbox.id}-opt-${this.active}`);
      else this.input.removeAttribute("aria-activedescendant");
    }
    onKey(e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (!this.results.length) { this.update(); if (!this.results.length) return; }
        e.preventDefault();
        const n = this.results.length;
        this.active = e.key === "ArrowDown" ? (this.active + 1) % n : (this.active - 1 + n) % n;
        this.render();
      } else if (e.key === "Enter" && this.results.length && this.active >= 0) {
        e.preventDefault();
        this.pick(this.active);
      } else if (e.key === "Escape" && this.results.length) {
        e.preventDefault();
        e.stopPropagation();
        this.close();
      }
    }
    pick(i) {
      const r = this.results[i];
      if (!r) return;
      this.input.value = r.name;
      this.close();
      this.onPick && this.onPick(r.name);
    }
    close() {
      this.results = [];
      this.active = -1;
      this.render();
    }
  }

  /* ------------------------------------------------------------------ count-up */
  function countUp(el, from, to, ms = 600) {
    if (!el) return;
    if (reducedMotion() || from === to) { el.textContent = String(to); return; }
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = String(Math.round(from + (to - from) * eased));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /** Restart a CSS animation class on an element. */
  function replayAnimation(el, cls) {
    if (!el || reducedMotion()) return;
    el.classList.remove(cls);
    void el.offsetWidth; // reflow
    el.classList.add(cls);
    el.addEventListener("animationend", function done(e) {
      if (e.target !== el) return;
      el.classList.remove(cls);
      el.removeEventListener("animationend", done);
    });
  }

  window.GeoArcade = { api, ApiError, normalize, StageTimer, pausableDelay, Autocomplete, countUp, replayAnimation, reducedMotion };
})();
