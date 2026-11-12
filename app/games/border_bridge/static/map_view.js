/* Border Bridge map renderer: d3-geo on a 2D canvas (no WebGL).
   One MapView per view ("globe" = orthographic, "flat" = Natural Earth). Both share
   a camera model { lon, lat, k } so framing, animation and input work the same way.
   Exposes window.BorderMaps = { loadWorld, MapView }. */
(function () {
  "use strict";
  const DEG = 180 / Math.PI;
  // frameMax caps auto-framing zoom (manual zoom can go up to kMax). setFrameMax()
  // lowers it for the globe when the flat map is beside it, so the globe keeps its
  // curved edge and gives context while the flat map gives detail.
  const LIMITS = {
    globe: { kMin: 0.85, kMax: 32, frameMax: 6, fit: [0.7, 0.62] },
    flat: { kMin: 1, kMax: 60, frameMax: 60, fit: [0.8, 0.72] },
  };
  const PALETTE_KEYS = [
    "ocean", "ocean-light", "ocean-deep", "land", "land-border", "coast", "graticule", "atmosphere", "shade",
    "sheen", "label", "label-halo", "a", "a-stroke", "b", "b-stroke", "found", "found-stroke", "revealed",
    "revealed-stroke",
  ];
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const wrapLon = (lon) => ((lon + 540) % 360) - 180;

  function readPalette() {
    const css = getComputedStyle(document.documentElement);
    return Object.fromEntries(PALETTE_KEYS.map((k) => [k, css.getPropertyValue(`--map-${k}`).trim()]));
  }

  function cssMs(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const n = parseFloat(v);
    return Number.isFinite(n) ? (v.endsWith("ms") ? n : n * 1000) : fallback;
  }

  /** Mean of [lon, lat] points on the sphere. */
  function sphericalMean(points) {
    let x = 0, y = 0, z = 0;
    for (const [lon, lat] of points) {
      const l = lon / DEG, p = lat / DEG;
      x += Math.cos(p) * Math.cos(l);
      y += Math.cos(p) * Math.sin(l);
      z += Math.sin(p);
    }
    return [Math.atan2(y, x) * DEG, Math.atan2(z, Math.hypot(x, y)) * DEG];
  }

  /* ------------------------------------------------------------------ data
     Background geometry is split into small cullable pieces (one per polygon or
     line) with a bounding circle, and pre-simplified into levels of detail so a
     camera flight only projects what's on screen at a resolution that's visible. */
  const LOD_TOLERANCES = [0, 0.04, 0.12, 0.35];  // degrees

  function simplifyLine(pts, tol, closed) {
    if (!tol || pts.length < 4) return pts;
    const out = [pts[0]];
    let [lx, ly] = pts[0];
    for (let i = 1; i < pts.length - 1; i++) {
      const [x, y] = pts[i];
      const dx = (x - lx) * Math.cos(y / DEG);
      if (dx * dx + (y - ly) * (y - ly) >= tol * tol) { out.push(pts[i]); lx = x; ly = y; }
    }
    out.push(pts[pts.length - 1]);
    return closed && out.length < 4 ? null : out;
  }

  function boundingCircle(points) {
    const c = d3.geoCentroid({ type: "MultiPoint", coordinates: points });
    let r = 0;
    const step = Math.max(1, Math.floor(points.length / 200));
    for (let i = 0; i < points.length; i += step) r = Math.max(r, d3.geoDistance(c, points[i]));
    return { c, r: r + 0.01, v: unitVector(c) };
  }

  function unitVector([lon, lat]) {
    const l = lon / DEG, p = lat / DEG;
    return [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)];
  }

  function buildLevels(features, coast, borders) {
    const polys = [];
    for (const f of features) {
      const g = f.geometry;
      if (!g) continue;
      for (const poly of g.type === "Polygon" ? [g.coordinates] : g.coordinates) {
        polys.push({ rings: poly, ...boundingCircle(poly[0]) });
      }
    }
    const lines = (mesh) => mesh.coordinates.map((l) => ({ line: l, ...boundingCircle(l) }));
    const coastLines = lines(coast), borderLines = lines(borders);
    return LOD_TOLERANCES.map((tol) => ({
      tol,
      land: polys.map((p) => {
        const rings = p.rings.map((r) => simplifyLine(r, tol, true)).filter(Boolean);
        return rings.length ? { geom: { type: "Polygon", coordinates: rings }, v: p.v, r: p.r } : null;
      }).filter(Boolean),
      coast: coastLines.map((l) => ({ geom: { type: "LineString", coordinates: simplifyLine(l.line, tol, false) }, v: l.v, r: l.r })),
      borders: borderLines.map((l) => ({ geom: { type: "LineString", coordinates: simplifyLine(l.line, tol, false) }, v: l.v, r: l.r })),
    }));
  }

  async function loadWorld(topoUrl, metaUrl) {
    const [topo, meta] = await Promise.all([topoUrl, metaUrl].map((u) => fetch(u).then((r) => {
      if (!r.ok) throw new Error(`Couldn't load map data (${r.status})`);
      return r.json();
    })));
    const features = topojson.feature(topo, topo.objects.countries).features;
    const coast = topojson.mesh(topo, topo.objects.countries, (a, b) => a === b);
    const borders = topojson.mesh(topo, topo.objects.countries, (a, b) => a !== b);
    const levels = buildLevels(features, coast, borders);
    const shapes = {};
    for (const [id, m] of Object.entries(meta)) {
      const feature = features[m.index];
      const g = feature.geometry;
      const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
      // Sample the mainland outline for camera framing (overseas pieces are drawn, not framed).
      const samples = [];
      for (const i of m.mainland) {
        const ring = (polys[i] || [])[0] || [];
        const step = Math.max(1, Math.floor(ring.length / 300));
        for (let j = 0; j < ring.length; j += step) samples.push(ring[j]);
      }
      shapes[id] = { feature, centroid: m.centroid, samples };
    }
    return { levels, shapes };
  }

  /* ------------------------------------------------------------------ view */
  class MapView {
    constructor(canvas, world, { kind, onInteract = () => {} }) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.world = world;
      this.kind = kind;
      this.limits = { ...LIMITS[kind] };
      this.onInteract = onInteract;
      this.projection = this.makeProjection();
      this.camera = kind === "globe" ? { lon: 10, lat: 25, k: 1 } : { lon: 10, lat: 15, k: 1 };
      this.colors = readPalette();
      this.state = { a: null, b: null, found: [], added: [], names: {} };
      this.showBorders = true;
      this.appear = new Map();
      this.pulses = [];
      this.base = document.createElement("canvas");
      this.baseKey = "";
      this._tiny = new Set();
      this._tinyKey = "";
      this.level = 0;
      this.w = 0;
      this.h = 0;
      this.dpr = 1;
      this.target = null;
      this.anim = null;
      this.raf = 0;
      this.bindInput();
      this.paused = false;
      this.autoRotate = false;
      this.ro = new ResizeObserver(() => this.resize());
      this.ro.observe(canvas);
      this.resize();
    }

    makeProjection() {
      const p = this.kind === "globe" ? d3.geoOrthographic().clipAngle(90) : d3.geoNaturalEarth1();
      return p.precision(0.6);
    }

    baseScale() {
      if (this.kind === "globe") return Math.max(10, Math.min(this.w, this.h) / 2 - 14);
      return d3.geoNaturalEarth1().fitSize([this.w, this.h], { type: "Sphere" }).scale();
    }

    applyCamera(p, cam) {
      p.scale(this._baseScale * cam.k).translate([this.w / 2, this.h / 2]);
      if (this.kind === "globe") p.rotate([-cam.lon, -cam.lat]);
      else p.rotate([-cam.lon, 0]).center([0, cam.lat]);
      return p;
    }

    resize() {
      // Layout size, not getBoundingClientRect: a feed card may be scaled by a CSS
      // transform while it animates, and the canvas must not be sized to that.
      const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
      if (!w || !h) { this.w = this.h = 0; return; }
      if (w === this.w && h === this.h) return;
      this.w = w;
      this.h = h;
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      for (const c of [this.canvas, this.base]) {
        c.width = Math.round(w * this.dpr);
        c.height = Math.round(h * this.dpr);
      }
      this._baseScale = this.baseScale();
      this.baseKey = "";
      if (this.target) this.camera = this.computeFrame(this.target);  // was hidden: jump, don't fly
      this.request();
    }

    /* ---------------------------------------------------------------- state */
    setState({ a, b, found = [], added = [], names = {} }, { fresh = false } = {}) {
      const now = performance.now();
      const before = new Set(fresh ? [] : [this.state.a, this.state.b, ...this.state.found, ...this.state.added]);
      if (fresh) { this.appear.clear(); this.pulses = []; }
      for (const id of [a, b, ...found, ...added]) {
        if (id && !before.has(id) && !reducedMotion()) {
          this.appear.set(id, now);
          if (!fresh) this.pulses.push({ id, t0: now });
        }
      }
      this.state = { a, b, found, added, names };
      this._tinyKey = "";
      this.request();
    }

    setBorders(on) {
      this.showBorders = on;
      this.baseKey = "";
      this.request();
    }

    /** Fly the camera so the given shapes fit, framed around the core pair. */
    frame(ids, core, animate = true, durationMs) {
      this.autoRotate = false;
      this.target = { ids: ids.filter((id) => this.world.shapes[id]), core };
      if (!this.w) return;  // hidden; resize() will jump there when shown
      this.flyTo(this.computeFrame(this.target), animate, durationMs);
    }

    /** Ready state: no puzzle, whole-world view, the globe slowly turning. */
    idle() {
      this.target = null;
      this.anim = null;
      this.camera = this.kind === "globe" ? { lon: this.camera.lon, lat: 18, k: 1 } : { lon: 10, lat: 15, k: 1 };
      this.autoRotate = this.kind === "globe" && !reducedMotion();
      this.request();
    }

    /** Stop all rendering and animation (card scrolled away); resume() picks up again. */
    pause() {
      this.paused = true;
      cancelAnimationFrame(this.raf);
      this.raf = 0;
      clearTimeout(this._refine);
    }

    resume() {
      if (!this.paused) return;
      this.paused = false;
      this._lastFrame = 0;
      if (this.anim) this.anim.t0 = performance.now() - this.anim.elapsed;
      this.request();
    }

    zoomBy(factor) {
      this.anim = null;
      this.autoRotate = false;
      this.zoom(factor);
    }

    setFrameMax(k) {
      if (k === this.limits.frameMax) return;
      this.limits.frameMax = k;
      this.resetView();
    }

    resetView() {
      if (this.target) this.flyTo(this.computeFrame(this.target), true);
    }

    computeFrame({ ids, core }) {
      const shapes = this.world.shapes;
      const centres = core.map((id) => shapes[id].centroid);
      const centre = sphericalMean(centres);
      // Clip to a circle around the pair so huge countries (Russia, Canada) don't
      // drag the camera out to a whole-world view.
      // Endpoints keep a generous circle; other countries (often big intermediates
      // such as Russia) only count where they're close to the pair.
      const radius = Math.max(...centres.map((c) => d3.geoDistance(centre, c))) * DEG;
      const coreClip = clamp(radius * 1.6 + 7, 12, 80) / DEG;
      const otherClip = clamp(radius * 1.15 + 4, 6, 70) / DEG;
      const pts = [];
      for (const id of ids) {
        const clip = core.includes(id) ? coreClip : otherClip;
        for (const p of shapes[id].samples) if (d3.geoDistance(centre, p) <= clip) pts.push(p);
        if (d3.geoDistance(centre, shapes[id].centroid) <= clip) pts.push(shapes[id].centroid);
      }
      let cam = { lon: centre[0], lat: centre[1], k: 1 };
      const probe = this.makeProjection();
      for (let pass = 0; pass < 2; pass++) {
        this.applyCamera(probe, { ...cam, k: 1 });
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const p of pts) {
          if (this.kind === "globe" && d3.geoDistance([cam.lon, cam.lat], p) > Math.PI / 2 - 0.02) continue;
          const xy = probe(p);
          if (!xy) continue;
          x0 = Math.min(x0, xy[0]); x1 = Math.max(x1, xy[0]);
          y0 = Math.min(y0, xy[1]); y1 = Math.max(y1, xy[1]);
        }
        if (!Number.isFinite(x0)) break;
        const mid = probe.invert([(x0 + x1) / 2, (y0 + y1) / 2]);
        const [fx, fy] = this.limits.fit;
        const k = Math.min((this.w * fx) / Math.max(x1 - x0, 1), (this.h * fy) / Math.max(y1 - y0, 1));
        cam = {
          lon: mid ? wrapLon(mid[0]) : cam.lon,
          lat: clamp(mid ? mid[1] : cam.lat, -75, 75),
          k: clamp(k, this.limits.kMin, this.limits.frameMax),
        };
      }
      return cam;
    }

    flyTo(target, animate, durationMs) {
      if (!animate || reducedMotion() || !this.w) {
        this.anim = null;
        this.camera = target;
        this.request();
        return;
      }
      const from = { ...this.camera };
      const to = { lon: from.lon + wrapLon(target.lon - from.lon), lat: target.lat, k: target.k };
      const hop = d3.geoDistance([from.lon, from.lat], [target.lon, target.lat]);
      this.anim = { t0: performance.now(), elapsed: 0, dur: durationMs || cssMs("--dur-camera", 1100), from, to,
                    bump: durationMs ? 0 : Math.min(0.55, hop / 2) };
      this.request();
    }

    stepAnim(now) {
      const a = this.anim;
      a.elapsed = now - a.t0;
      const t = clamp(a.elapsed / a.dur, 0, 1);
      const e = d3.easeCubicInOut(t);
      const logK = Math.log(a.from.k) + (Math.log(a.to.k) - Math.log(a.from.k)) * e;
      this.camera = {
        lon: wrapLon(a.from.lon + (a.to.lon - a.from.lon) * e),
        lat: a.from.lat + (a.to.lat - a.from.lat) * e,
        k: Math.max(this.limits.kMin * 0.9, Math.exp(logK) * (1 - a.bump * Math.sin(Math.PI * t))),
      };
      if (t >= 1) this.anim = null;
      return t < 1;
    }

    /* ---------------------------------------------------------------- input */
    bindInput() {
      const c = this.canvas;
      const pointers = new Map();
      let pinch = 0;
      const interact = () => { this.anim = null; this.autoRotate = false; this.onInteract(); };
      c.addEventListener("pointerdown", (e) => {
        c.setPointerCapture(e.pointerId);
        pointers.set(e.pointerId, [e.clientX, e.clientY]);
        if (pointers.size === 2) pinch = this.pinchDistance(pointers);
        c.classList.add("is-dragging");
      });
      c.addEventListener("pointermove", (e) => {
        if (!pointers.has(e.pointerId)) return;
        const prev = pointers.get(e.pointerId);
        pointers.set(e.pointerId, [e.clientX, e.clientY]);
        interact();
        if (pointers.size === 1) this.pan(e.clientX - prev[0], e.clientY - prev[1]);
        else if (pointers.size === 2) {
          const d = this.pinchDistance(pointers);
          if (pinch) this.zoom(d / pinch);
          pinch = d;
        }
      });
      const up = (e) => {
        pointers.delete(e.pointerId);
        pinch = pointers.size === 2 ? this.pinchDistance(pointers) : 0;
        if (!pointers.size) c.classList.remove("is-dragging");
      };
      c.addEventListener("pointerup", up);
      c.addEventListener("pointercancel", up);
      c.addEventListener("wheel", (e) => {
        e.preventDefault();
        interact();
        // Ctrl+wheel is a trackpad pinch: small deltas, so scale them up.
        this.zoom(Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : e.ctrlKey ? 0.01 : 0.0015)));
      }, { passive: false });
      c.addEventListener("keydown", (e) => {
        const moves = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] };
        if (moves[e.key]) { interact(); this.pan(...moves[e.key]); }
        else if (e.key === "+" || e.key === "=") { interact(); this.zoom(1.25); }
        else if (e.key === "-" || e.key === "_") { interact(); this.zoom(0.8); }
        else return;
        e.preventDefault();
      });
    }

    pinchDistance(pointers) {
      const [p, q] = [...pointers.values()];
      return Math.hypot(p[0] - q[0], p[1] - q[1]);
    }

    pan(dx, dy) {
      this.lastMove = performance.now();
      const s = this._baseScale * this.camera.k;
      this.camera = {
        ...this.camera,
        lon: wrapLon(this.camera.lon - (dx / s) * DEG),
        lat: clamp(this.camera.lat + (dy / s) * DEG, this.kind === "globe" ? -85 : -75, this.kind === "globe" ? 85 : 75),
      };
      this.request();
    }

    zoom(factor) {
      this.lastMove = performance.now();
      this.camera = { ...this.camera, k: clamp(this.camera.k * factor, this.limits.kMin, this.limits.kMax) };
      this.request();
    }

    /* ---------------------------------------------------------------- render */
    request() {
      if (this.paused) return;
      if (!this.raf) this.raf = requestAnimationFrame((t) => { this.raf = 0; this.draw(t); });
    }

    /** Coarsest level of detail whose error stays under ~1 px (2 px while moving). */
    pickLevel(moving) {
      const pxPerDeg = this.projection.scale() / DEG;
      const budget = moving ? 2.2 : 0.7;
      let level = 0;
      this.world.levels.forEach((l, i) => { if (l.tol * pxPerDeg <= budget) level = i; });
      return level;
    }

    /** Visible part of the sphere as a circle (centre, angular radius), for culling. */
    viewCircle() {
      const s = this.projection.scale();
      const half = Math.hypot(this.w, this.h) / 2;
      const r = this.kind === "globe" ? Math.asin(Math.min(1, half / s)) : Math.min(Math.PI, (half / s) * 1.35);
      return { v: unitVector([this.camera.lon, this.camera.lat]), r: Math.min(Math.PI, r + 0.05) };
    }

    /** Bounding circles overlap: angle between centres <= sum of radii (via dot product). */
    inView(item) {
      const sum = this._view.r + item.r;
      if (sum >= Math.PI) return true;
      const a = this._view.v, b = item.v;
      return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] >= Math.cos(sum);
    }

    draw(now) {
      if (!this.w) return;
      let again = this.anim ? this.stepAnim(now) : false;
      if (this.autoRotate) {
        const dt = this._lastFrame ? Math.min(100, now - this._lastFrame) : 0;
        this.camera = { ...this.camera, lon: wrapLon(this.camera.lon - dt * 0.006) };
        again = true;
      }
      this._lastFrame = now;
      this.applyCamera(this.projection, this.camera);
      const cam = this.camera;
      const moving = !!this.anim || this.autoRotate || now - (this.lastMove || 0) < 140;
      const level = this.pickLevel(moving);
      const key = `${cam.lon.toFixed(3)},${cam.lat.toFixed(3)},${cam.k.toFixed(4)},${this.w},${this.h},${this.showBorders},${level},${moving}`;
      if (key !== this.baseKey) {
        this.level = level;
        this.moving = moving;
        // Adaptive resampling is invisible mid-flight; skip it to keep frames cheap.
        this.projection.precision(moving ? 0 : 0.6);
        this._view = this.viewCircle();
        this.drawBase();
        this.baseKey = key;
        this._tinyKey = "";
      }
      // Once movement stops, redraw at full detail.
      clearTimeout(this._refine);
      if (moving && !this.anim) this._refine = setTimeout(() => this.request(), 160);
      const ctx = this.ctx;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      ctx.drawImage(this.base, 0, 0);
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      again = this.drawHighlights(now) || again;
      if (this.kind === "globe") this.drawShading();
      again = this.drawMarkers(now) || again;
      this.drawLabels();
      if (again) this.request();
    }

    drawBase() {
      const ctx = this.base.getContext("2d");
      const col = this.colors;
      const path = d3.geoPath(this.projection, ctx);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, this.base.width, this.base.height);
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      const [cx, cy] = this.projection.translate();
      const r = this.projection.scale();

      if (this.kind === "globe") {
        const glow = ctx.createRadialGradient(cx, cy, r * 0.96, cx, cy, r * 1.09);
        glow.addColorStop(0, col.atmosphere);
        glow.addColorStop(1, "transparent");
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(cx, cy, r * 1.09, 0, Math.PI * 2);
        ctx.fill();
        const sea = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.4, r * 0.05, cx, cy, r * 1.05);
        sea.addColorStop(0, col["ocean-light"]);
        sea.addColorStop(0.55, col.ocean);
        sea.addColorStop(1, col["ocean-deep"]);
        ctx.fillStyle = sea;
      } else {
        ctx.fillStyle = col.ocean;
      }
      ctx.beginPath();
      path({ type: "Sphere" });
      ctx.fill();

      ctx.beginPath();
      path(d3.geoGraticule10());
      ctx.strokeStyle = col.graticule;
      ctx.lineWidth = 0.6;
      ctx.stroke();

      // One Path2D per layer: every visible country polygon goes into a single
      // fill so the land reads as one seamless mass when borders are hidden.
      const level = this.world.levels[this.level];
      const layer = (items) => {
        const p2 = new Path2D();
        const draw = d3.geoPath(this.projection, p2);
        for (const it of items) if (this.inView(it)) draw(it.geom);
        return p2;
      };
      ctx.lineJoin = "round";
      ctx.fillStyle = col.land;
      ctx.fill(layer(level.land));
      if (this.showBorders) {
        ctx.strokeStyle = col["land-border"];
        ctx.lineWidth = 0.7;
        ctx.stroke(layer(level.borders));
      }
      if (!this.moving) {  // the fill already shows the coast; the crisp outline is added at rest
        ctx.strokeStyle = col.coast;
        ctx.lineWidth = 0.6;
        ctx.stroke(layer(level.coast));
      }

      if (this.kind === "flat") {
        ctx.beginPath();
        path({ type: "Sphere" });
        ctx.strokeStyle = col.coast;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }

    roles() {
      const s = this.state;
      const out = [];
      if (s.a) out.push([s.a, "a"]);
      if (s.b) out.push([s.b, "b"]);
      for (const id of s.found) out.push([id, "found"]);
      for (const id of s.added) out.push([id, "revealed"]);
      return out.filter(([id]) => this.world.shapes[id]);
    }

    visible(lonlat) {
      if (this.kind === "globe" && d3.geoDistance([this.camera.lon, this.camera.lat], lonlat) > Math.PI / 2 - 0.05) return null;
      const xy = this.projection(lonlat);
      if (!xy || xy[0] < -40 || xy[1] < -40 || xy[0] > this.w + 40 || xy[1] > this.h + 40) return null;
      return xy;
    }

    drawHighlights(now) {
      const ctx = this.ctx;
      const path = d3.geoPath(this.projection, ctx);
      let again = false;
      for (const [id, role] of this.roles()) {
        const shape = this.world.shapes[id];
        ctx.save();
        const t0 = this.appear.get(id);
        if (t0 !== undefined) {
          const t = clamp((now - t0) / 650, 0, 1);
          if (t >= 1) this.appear.delete(id);
          else {
            again = true;
            const xy = this.projection(shape.centroid);
            const back = 1.70158;
            const s = 0.6 + 0.4 * (1 + (back + 1) * Math.pow(t - 1, 3) + back * Math.pow(t - 1, 2));
            ctx.globalAlpha = t;
            if (xy) {
              ctx.translate(xy[0], xy[1]);
              ctx.scale(s, s);
              ctx.translate(-xy[0], -xy[1]);
            }
          }
        }
        ctx.beginPath();
        path(shape.feature);
        ctx.fillStyle = this.colors[role];
        ctx.fill();
        ctx.lineWidth = 1.2;
        ctx.lineJoin = "round";
        ctx.strokeStyle = this.colors[`${role}-stroke`];
        ctx.stroke();
        ctx.restore();
      }
      return again;
    }

    drawShading() {
      const ctx = this.ctx;
      const [cx, cy] = this.projection.translate();
      const r = this.projection.scale();
      // Limb shading only makes sense while the globe's edge is on screen.
      ctx.globalAlpha = clamp((4.5 - this.camera.k) / 2.3, 0.15, 1);
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      const sheen = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.4, 0, cx - r * 0.35, cy - r * 0.4, r * 0.75);
      sheen.addColorStop(0, this.colors.sheen);
      sheen.addColorStop(1, "transparent");
      ctx.fillStyle = sheen;
      ctx.fill();
      const limb = ctx.createRadialGradient(cx, cy, r * 0.9, cx, cy, r);
      limb.addColorStop(0, "transparent");
      limb.addColorStop(1, this.colors.shade);
      ctx.fillStyle = limb;
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    /** Pulses on new countries + rings around highlighted countries too small to see. */
    drawMarkers(now) {
      const ctx = this.ctx;
      const path = d3.geoPath(this.projection);
      const still = reducedMotion();
      let again = false;
      this.pulses = this.pulses.filter((p) => now - p.t0 < 1400);
      for (const p of this.pulses) {
        const xy = this.visible(this.world.shapes[p.id].centroid);
        if (!xy) continue;
        const role = this.roles().find(([id]) => id === p.id);
        for (const delay of [0, 280]) {
          const t = clamp((now - p.t0 - delay) / 1000, 0, 1);
          if (t <= 0 || t >= 1) continue;
          ctx.beginPath();
          ctx.arc(xy[0], xy[1], 10 + 46 * t, 0, Math.PI * 2);
          ctx.strokeStyle = this.colors[role ? role[1] : "found"];
          ctx.globalAlpha = 1 - t;
          ctx.lineWidth = 2.5;
          ctx.stroke();
          ctx.globalAlpha = 1;
        }
        again = true;
      }
      if (!this._tinyKey) {  // recomputed only when the camera or highlights change
        this._tiny = new Set(this.roles().map(([id]) => id).filter((id) => path.area(this.world.shapes[id].feature) <= 36));
        this._tinyKey = "ok";
      }
      for (const [id, role] of this.roles()) {
        if (!this._tiny.has(id)) continue;
        const xy = this.visible(this.world.shapes[id].centroid);
        if (!xy) continue;
        const wobble = still ? 0 : Math.sin(now / 320) * 2;
        ctx.beginPath();
        ctx.arc(xy[0], xy[1], 9 + wobble, 0, Math.PI * 2);
        ctx.lineWidth = 2;
        ctx.strokeStyle = this.colors[role];
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(xy[0], xy[1], 3, 0, Math.PI * 2);
        ctx.fillStyle = this.colors[role];
        ctx.fill();
        if (!still) again = true;
      }
      return again;
    }

    drawLabels() {
      const ctx = this.ctx;
      const placed = [];
      ctx.font = "600 12px Inter, ui-sans-serif, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";
      for (const [id, role] of this.roles()) {
        const name = this.state.names[id];
        const xy = name && this.visible(this.world.shapes[id].centroid);
        if (!xy) continue;
        const w = ctx.measureText(name).width + 8;
        // Try below/above the anchor (tiny countries: clear of the ring), then further out.
        const d = this._tiny.has(id) ? 20 : 0;
        const x = xy[0];
        let y = null;
        for (const dy of d ? [d, -d, d + 16, -d - 16] : [0, 16, -16, 32]) {
          const box = [x - w / 2, xy[1] + dy - 9, x + w / 2, xy[1] + dy + 9];
          if (!placed.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) {
            y = xy[1] + dy;
            placed.push(box);
            break;
          }
        }
        if (y === null) {
          if (role !== "a" && role !== "b") continue;  // crowded: the start and goal labels win
          y = xy[1] + d;
        }
        ctx.strokeStyle = this.colors["label-halo"];
        ctx.lineWidth = 4;
        ctx.strokeText(name, x, y);
        ctx.fillStyle = this.colors.label;
        ctx.fillText(name, x, y);
      }
    }

    destroy() {
      this.ro.disconnect();
      cancelAnimationFrame(this.raf);
    }
  }

  window.BorderMaps = { loadWorld, MapView };
})();
