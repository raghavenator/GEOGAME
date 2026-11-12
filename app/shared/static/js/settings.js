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

    function sanitize(values) {
      const out = {};
      for (const f of schema) {
        let v = values[f.key];
        if (f.type === "int") {
          v = Number(v);
          if (!Number.isFinite(v)) v = f.default;
          v = Math.min(f.max, Math.max(f.min, Math.round(v)));
        } else if (f.type === "choice") {
          if (!f.choices.some((c) => c.value === v)) v = f.default;
        }
        out[f.key] = v;
      }
      return out;
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
