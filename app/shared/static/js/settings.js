/* Per-game settings stores, driven by the schema the server embeds in
   <script type="application/json" data-game-settings>. Values persist in
   localStorage and are sent to the server (which re-validates them) when a
   round starts. A page can hold several games (the feed), so each game gets its
   own store: in templates use the $settings magic, which resolves the store of
   the nearest [data-game] ancestor; in JS use GeoArcade.settings(gameId). */
(function () {
  "use strict";
  const stores = {};

  function makeStore(gameId, schema) {
    const storageKey = `geo-arcade:settings:${gameId}`;
    const defaults = Object.fromEntries(schema.map((f) => [f.key, f.default]));

    /** An int's maximum given the other values: its own max, or less when it has cap_by and the
        choice picked there has a smaller count (e.g. questions <= countries in the region). */
    function capOf(f, values) {
      if (f.type !== "int" || !f.cap_by) return f.max;
      const src = schema.find((x) => x.key === f.cap_by);
      const choice = src && src.choices.find((c) => c.value === values[src.key]);
      return choice && Number.isInteger(choice.count) ? Math.max(f.min, Math.min(f.max, choice.count)) : f.max;
    }

    function capNoteOf(f, values) {
      const cap = capOf(f, values);
      if (cap >= f.max) return "";
      const src = schema.find((x) => x.key === f.cap_by);
      const choice = src.choices.find((c) => c.value === values[src.key]);
      return (f.cap_note || "{choice} allows at most {cap}.")
        .split("{choice}").join(choice.label).split("{cap}").join(String(cap));
    }

    function sanitize(values) {
      const out = {};
      // Choices first (a stored value that's no longer offered falls back to the default),
      // then ints, whose caps can depend on a choice.
      for (const f of schema) {
        if (f.type !== "choice") continue;
        const v = values[f.key];
        out[f.key] = f.choices.some((c) => c.value === v) ? v : f.default;
      }
      for (const f of schema) {
        if (f.type === "choice") continue;
        let v = values[f.key];
        if (f.type === "int") {
          v = Number(v);
          if (!Number.isFinite(v)) v = f.default;
          v = Math.min(capOf(f, out), Math.max(f.min, Math.round(v)));
        }
        out[f.key] = v;
      }
      // Keep the schema's key order (some callers iterate it).
      return Object.fromEntries(schema.map((f) => [f.key, out[f.key]]));
    }

    function load() {
      try {
        return sanitize({ ...defaults, ...JSON.parse(localStorage.getItem(storageKey) || "{}") });
      } catch (e) {
        return { ...defaults };
      }
    }

    function persist(values) {
      try { localStorage.setItem(storageKey, JSON.stringify(values)); } catch (e) { /* private mode */ }
    }

    const modal = () => document.querySelector(`[data-settings-modal="${gameId}"]`);

    return {
      gameId,
      schema,
      values: load(),
      draft: {},
      errors: {},
      open: false,
      locked: false,
      _returnFocus: null,

      openModal(trigger) {
        if (this.locked) return;
        this.draft = { ...this.values };
        this.errors = {};
        this._returnFocus = trigger || document.activeElement;
        this.open = true;
        setTimeout(() => {
          const first = modal() && modal().querySelector(".modal__panel input, .modal__panel select");
          first && first.focus();
        }, 50);
      },
      close() {
        this.open = false;
        const target = this._returnFocus;
        this._returnFocus = null;
        setTimeout(() => target && target.focus && target.focus(), 0);
      },
      validateDraft() {
        const errors = {};
        for (const f of schema) {
          const v = this.draft[f.key];
          if (f.type === "int" && (!Number.isInteger(v) || v < f.min || v > f.max)) {
            errors[f.key] = `${f.label} must be between ${f.min} and ${f.max}.`;
          } else if (f.type === "int" && v > capOf(f, this.draft)) {
            errors[f.key] = capNoteOf(f, this.draft);
          }
        }
        return errors;
      },
      save() {
        this.errors = this.validateDraft();
        if (Object.keys(this.errors).length) return;
        this.values = sanitize(this.draft);
        persist(this.values);
        this.close();
        window.dispatchEvent(new CustomEvent("settings-changed", { detail: { gameId, values: { ...this.values } } }));
      },
      /** The current maximum of an int setting in the draft (see capOf). */
      capFor(key) {
        const f = schema.find((x) => x.key === key);
        return f ? capOf(f, this.draft) : 0;
      },
      /** "Caribbean has 13 countries, so the maximum is 13 questions." while a cap applies, else "". */
      capNote(key) {
        const f = schema.find((x) => x.key === key);
        return f ? capNoteOf(f, this.draft) : "";
      },
      /** A choice changed: pull capped ints down to their new maximum. */
      clampCaps() {
        const errors = { ...this.errors };
        for (const f of schema) {
          if (f.type !== "int" || !f.cap_by) continue;
          const cap = capOf(f, this.draft);
          if (Number(this.draft[f.key]) > cap) this.draft[f.key] = cap;
          delete errors[f.key];
        }
        this.errors = errors;
      },
      reset() {
        this.draft = { ...defaults };
        this.errors = {};
      },
      /** Server rejected the settings: show its messages in the modal. */
      showServerErrors(fields) {
        this.errors = { ...fields };
        this.draft = { ...this.values };
        this.open = true;
      },
      /** Human-readable label for a value, e.g. region "europe" -> "Europe". */
      display(key) {
        const f = schema.find((x) => x.key === key);
        const v = this.values[key];
        if (!f) return String(v);
        if (f.type === "choice") return (f.choices.find((c) => c.value === v) || {}).label || v;
        return `${v}${f.unit || ""}`;
      },
    };
  }

  window.GeoArcade = window.GeoArcade || {};
  window.GeoArcade.settings = (gameId) => stores[gameId];

  document.addEventListener("alpine:init", () => {
    for (const el of document.querySelectorAll("script[data-game-settings]")) {
      const { gameId, schema } = JSON.parse(el.textContent);
      if (!stores[gameId]) stores[gameId] = Alpine.reactive(makeStore(gameId, schema));
    }
    Alpine.magic("settings", (el) => {
      const host = el.closest("[data-game]");
      return stores[host ? host.dataset.game : Object.keys(stores)[0]];
    });
  });
})();
