// =====================================================================
// OBSIDIAN NEXUS (v2) — 80-fragment swarm orb with mic-reactive audio
// analysis. Pure canvas-2D pseudo-3D, no WebGL, no deps.
//
// Public API
//   const orb = new ObsidianNexus(canvas, { theme: 'dark' });
//   orb.setMode('idle' | 'listening' | 'thinking' | 'speaking');
//   orb.setTheme('dark' | 'light');
//   orb.attachStream(MediaStream);      // wires mic analyser → reactive bands
//   orb.detachStream();
//   orb.getBands();                     // { low, mid, high, amp } 0..1
//   orb.pause(); orb.resume(); orb.size();
//
// Mode reactions
//   idle      — fragments breathe; subtle drift; core slow heartbeat
//   listening — fragments contract; satellites slow; core brightens
//   thinking  — fragments puff outward chaotically; core flickers
//   speaking  — fragments push outward proportional to band energy;
//               core flares with the high band; satellites accelerate
// =====================================================================

(function () {
  "use strict";

  const TWO_PI = Math.PI * 2;
  const OR  = "255,122,0";   // signal orange
  const HIc = "255,179,71";  // hot highlight

  function hexPath(ctx, x, y, r, rot) {
    ctx.beginPath();
    for (let k = 0; k < 6; k++) {
      const a = rot + k * Math.PI / 3;
      const X = x + Math.cos(a) * r;
      const Y = y + Math.sin(a) * r;
      k ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
    }
    ctx.closePath();
  }

  function rot3(p, ay, ax) {
    const cY = Math.cos(ay), sY = Math.sin(ay);
    const x = p.x * cY - p.z * sY;
    const z = p.x * sY + p.z * cY;
    const cX = Math.cos(ax), sX = Math.sin(ax);
    const yy = p.y * cX - z * sX;
    const z2 = p.y * sX + z * cX;
    return { x, y: yy, z: z2 };
  }

  class ObsidianNexus {
    constructor(canvas, opts) {
      opts = opts || {};
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);

      this.theme = opts.theme || "dark";
      this.mode = "idle";
      this.frame = 0;
      this.lastT = 0;
      this._running = false;

      // Drag state — pointer drags rotate the orb
      this.dragX = 0.35; this.dragY = 0.5;
      this._down = false; this._mpx = 0; this._mpy = 0; this._moved = false;

      // Audio analyser
      this.analyser = null;
      this.freq = null;
      this.bands = { low: 0, mid: 0, high: 0, amp: 0 };
      this.micActive = false;
      this._actx = null;

      // Fragments (Fibonacci-sphere distribution, 80 chips)
      const N = 80;
      this.N = N;
      this.frags = [];
      for (let i = 0; i < N; i++) {
        const y = 1 - (i / (N - 1)) * 2;
        const rr = Math.sqrt(Math.max(0, 1 - y * y));
        const th = i * 2.399963;
        const dir = { x: Math.cos(th) * rr, y, z: Math.sin(th) * rr };
        this.frags.push({
          dir,
          cur: { x: dir.x * 100, y: dir.y * 100, z: dir.z * 100 },
          size: 0.16 + Math.random() * 0.12,
          spin: Math.random() * TWO_PI,
          spinV: (Math.random() - 0.5) * 0.004,
          heat: 0.5,
          np: Math.random() * 1000,
        });
      }

      // Satellites — 6 orbiters
      this.sats = [];
      for (let s = 0; s < 6; s++) {
        this.sats.push({
          ang: (s / 6) * TWO_PI,
          tilt: (Math.random() - 0.5) * 1.2,
          rad: 1.55 + Math.random() * 0.3,
          sp: 0.003 + Math.random() * 0.004,
          spin: Math.random() * 6,
        });
      }

      this._bindPointer();
      this._sizeBound = () => this.size();
      window.addEventListener("resize", this._sizeBound);
      if (window.ResizeObserver) {
        this._ro = new ResizeObserver(this._sizeBound);
        this._ro.observe(canvas.parentElement || canvas);
      }
      this.size();
    }

    size() {
      const parent = this.canvas.parentElement || this.canvas;
      const W = parent.clientWidth || 440;
      const H = parent.clientHeight || 440;
      this.W = W; this.H = H;
      this.canvas.width = Math.max(1, W * this.dpr);
      this.canvas.height = Math.max(1, H * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.cx = W / 2; this.cy = H / 2;
      this.R = Math.max(60, Math.min(W, H) * 0.30);
    }

    setTheme(t) { this.theme = (t === "light") ? "light" : "dark"; }
    setMode(m) { this.mode = m; }
    getBands() { return this.bands; }

    attachStream(stream) {
      try {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        this._actx = new AC();
        const src = this._actx.createMediaStreamSource(stream);
        this.analyser = this._actx.createAnalyser();
        this.analyser.fftSize = 128;
        this.analyser.smoothingTimeConstant = 0.8;
        src.connect(this.analyser);
        this.freq = new Uint8Array(this.analyser.frequencyBinCount);
        this.micActive = true;
      } catch (e) {
        this.analyser = null; this.freq = null; this.micActive = false;
      }
    }

    detachStream() {
      this.micActive = false;
      this.analyser = null;
      this.freq = null;
      this.bands = { low: 0, mid: 0, high: 0, amp: 0 };
      if (this._actx) { try { this._actx.close(); } catch {} this._actx = null; }
    }

    _bindPointer() {
      const cv = this.canvas;
      const start = (x, y) => { this._down = true; this._mpx = x; this._mpy = y; this._moved = false; };
      const move  = (x, y) => {
        if (!this._down) return;
        if (Math.abs(x - this._mpx) + Math.abs(y - this._mpy) > 4) this._moved = true;
        this.dragY += (x - this._mpx) * 0.007;
        this.dragX += (y - this._mpy) * 0.007;
        this._mpx = x; this._mpy = y;
      };
      const end = () => { this._down = false; };
      cv.addEventListener("mousedown", (e) => start(e.clientX, e.clientY));
      window.addEventListener("mousemove", (e) => move(e.clientX, e.clientY));
      window.addEventListener("mouseup", end);
      cv.addEventListener("touchstart", (e) => { if (e.touches[0]) start(e.touches[0].clientX, e.touches[0].clientY); }, { passive: true });
      cv.addEventListener("touchmove",  (e) => { if (e.touches[0]) move(e.touches[0].clientX, e.touches[0].clientY); }, { passive: true });
      cv.addEventListener("touchend", end);
      // Click-without-drag forwards to onTap callback if set
      cv.addEventListener("click", () => { if (!this._moved && this.onTap) this.onTap(); });
    }

    pause() {
      if (!this._running) return;
      this._running = false;
      if (this._raf) cancelAnimationFrame(this._raf);
      this._raf = null;
    }

    resume() {
      if (this._running) return;
      this._running = true;
      this.lastT = 0;
      this._raf = requestAnimationFrame((tt) => this._tick(tt));
    }

    _readAudio() {
      if (!this.micActive || !this.analyser) return;
      this.analyser.getByteFrequencyData(this.freq);
      const n = this.freq.length;
      let lo = 0, mi = 0, hi = 0;
      for (let i = 0; i < n; i++) {
        const v = this.freq[i] / 255;
        if (i < n * 0.18) lo += v;
        else if (i < n * 0.55) mi += v;
        else hi += v;
      }
      this.bands.low  = lo / (n * 0.18);
      this.bands.mid  = mi / (n * 0.37);
      this.bands.high = hi / (n * 0.45);
      this.bands.amp  = (this.bands.low + this.bands.mid + this.bands.high) / 3;
    }

    // ---- drawing ----
    _drawCore(scale, bright) {
      const ctx = this.ctx;
      const r = this.R * 0.34 * scale;
      ctx.save();
      ctx.translate(this.cx, this.cy);
      ctx.lineWidth = 2;
      ctx.strokeStyle = `rgba(${OR},${0.55 * bright})`;
      ctx.shadowColor = "#ff7a00";
      ctx.shadowBlur = 18 * bright;
      for (let k = 0; k < 6; k++) {
        const a = k * Math.PI / 3 - Math.PI / 2;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r);
        ctx.stroke();
      }
      ctx.lineWidth = 3;
      ctx.strokeStyle = `rgba(${HIc},${0.85 * bright})`;
      hexPath(ctx, 0, 0, r, -Math.PI / 2);
      ctx.stroke();
      ctx.lineWidth = 2.4;
      hexPath(ctx, 0, 0, r * 0.6, -Math.PI / 2);
      ctx.strokeStyle = `rgba(${OR},${0.9 * bright})`;
      ctx.stroke();
      ctx.shadowBlur = 26 * bright;
      hexPath(ctx, 0, 0, r * 0.26, -Math.PI / 2);
      const g = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 0.26);
      g.addColorStop(0, `rgba(255,242,221,${bright})`);
      g.addColorStop(1, `rgba(${OR},${0.7 * bright})`);
      ctx.fillStyle = g;
      ctx.fill();
      ctx.restore();
    }

    _drawSat(s, ay, ax) {
      const ctx = this.ctx;
      const p = rot3({ x: Math.cos(s.ang) * this.R * s.rad, y: s.tilt * this.R * 0.5, z: Math.sin(s.ang) * this.R * s.rad }, ay, ax);
      const fz = 520; let dn = fz - p.z; if (dn < 60) dn = 60;
      const sc = fz / dn;
      const x = this.cx + p.x * sc;
      const y = this.cy + p.y * sc;
      const rr = 6 * sc;
      const dep = (p.z + this.R * 1.8) / (this.R * 3.6);
      if (!isFinite(x) || !isFinite(y)) return;
      ctx.save();
      ctx.translate(x, y);
      ctx.globalAlpha = 0.4 + Math.max(0, Math.min(1, dep)) * 0.6;
      ctx.strokeStyle = `rgba(${OR},.5)`;
      ctx.lineWidth = 1.4;
      for (let k = 0; k < 4; k++) {
        const a = s.spin + k * Math.PI / 2;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(Math.cos(a) * rr * 1.8, Math.sin(a) * rr * 1.8);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(Math.cos(a) * rr * 1.8, Math.sin(a) * rr * 1.8, rr * 0.42, 0, TWO_PI);
        ctx.fillStyle = "#ffb347";
        ctx.shadowColor = "#ff7a00";
        ctx.shadowBlur = 8;
        ctx.fill();
      }
      ctx.shadowBlur = 0;
      ctx.beginPath();
      ctx.arc(0, 0, rr * 0.8, 0, TWO_PI);
      const g = ctx.createRadialGradient(-rr * 0.3, -rr * 0.3, 0, 0, 0, rr * 0.8);
      g.addColorStop(0, "#39393e");
      g.addColorStop(1, "#0c0c10");
      ctx.fillStyle = g;
      ctx.fill();
      ctx.restore();
    }

    _drawFrag(pp) {
      const ctx = this.ctx;
      const f = pp.f, x = pp.x, y = pp.y, sc = pp.sc;
      const r = this.R * f.size * sc;
      let depth = (pp.z + this.R * 1.6) / (this.R * 3.2);
      if (!isFinite(depth)) depth = 0.5;
      depth = Math.max(0, Math.min(1, depth));
      if (!isFinite(x) || !isFinite(y) || !isFinite(r) || r <= 0) return;
      const dark = this.theme === "dark";
      ctx.save();
      ctx.globalAlpha = 0.5 + depth * 0.5;
      // Drop-shadow plate
      hexPath(ctx, x + r * 0.12, y + r * 0.14, r, f.spin);
      ctx.fillStyle = dark ? "#050505" : "#d8cdbb";
      ctx.fill();
      // Plate body with subtle vertical gradient
      hexPath(ctx, x, y, r, f.spin);
      const g = ctx.createLinearGradient(x, y - r, x, y + r);
      if (dark) {
        const t = Math.round(20 + depth * 26);
        const b = Math.round(5 + depth * 10);
        g.addColorStop(0, `rgb(${t + 8},${t + 7},${t + 6})`);
        g.addColorStop(1, `rgb(${b},${b},${b})`);
      } else {
        const t = Math.round(155 + depth * 85);
        const b = Math.round(120 + depth * 60);
        g.addColorStop(0, `rgb(${t},${t - 10},${t - 22})`);
        g.addColorStop(1, `rgb(${b},${b - 12},${b - 22})`);
      }
      ctx.fillStyle = g;
      ctx.fill();
      // Orange seam edge
      const heat = Math.max(0, Math.min(1.2, f.heat)) * (0.45 + depth * 0.55);
      ctx.lineWidth = Math.max(1, 1.5 * sc);
      ctx.strokeStyle = `rgba(${dark ? OR : "200,120,30"},${heat})`;
      ctx.shadowColor = "#ff7a00";
      ctx.shadowBlur = dark ? (8 + heat * 16) * depth : 0;
      ctx.stroke();
      // Inner spoke (hot highlight)
      ctx.shadowBlur = 0;
      ctx.globalAlpha *= 0.8;
      const a = f.spin;
      ctx.beginPath();
      ctx.moveTo(x + Math.cos(a) * r * 0.35, y + Math.sin(a) * r * 0.35);
      ctx.lineTo(x + Math.cos(a) * r * 0.62, y + Math.sin(a) * r * 0.62);
      ctx.strokeStyle = `rgba(${HIc},${heat * 0.5})`;
      ctx.lineWidth = 1.3 * sc;
      ctx.stroke();
      ctx.restore();
    }

    _tick(now) {
      if (!this._running) return;
      this._raf = requestAnimationFrame((tt) => this._tick(tt));
      const t = now;
      this.frame++;
      this._readAudio();

      const ctx = this.ctx;
      const W = this.W, H = this.H;
      const dark = this.theme === "dark";

      // Background halo
      ctx.clearRect(0, 0, W, H);
      const bg = ctx.createRadialGradient(this.cx, this.cy, 10, this.cx, this.cy, Math.max(W, H) * 0.62);
      if (dark) {
        bg.addColorStop(0, "rgba(40,22,0,.4)");
        bg.addColorStop(0.5, "rgba(255,122,0,.05)");
        bg.addColorStop(1, "rgba(5,5,5,0)");
      } else {
        bg.addColorStop(0, "rgba(255,179,71,.18)");
        bg.addColorStop(1, "rgba(244,241,236,0)");
      }
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, W, H);

      const v = this.mode;
      const micActive = this.micActive;
      const b = this.bands;

      const radialFactor =
        (v === "thinking") ? 1.18 :
        (v === "listening") ? 0.82 :
        (v === "speaking") ? (1.05 + (micActive ? b.low : 0.12) * 0.7) :
        0.92;
      const rotSpeed =
        (v === "speaking") ? (0.0014 + (micActive ? b.mid : 0.1) * 0.004) :
        (v === "listening") ? 0.0008 :
        0.0012;
      const ay = this.frame * rotSpeed + this.dragY;
      const ax = this.frame * rotSpeed * 0.6 + this.dragX;
      let coreBright =
        (v === "listening") ? 0.85 :
        (v === "speaking") ? (0.8 + (micActive ? b.high : 0.3) * 0.5) :
        (v === "thinking") ? (0.7 + 0.3 * Math.sin(t / 120)) :
        (0.5 + 0.25 * Math.max(0, Math.sin(t / 1000 * (Math.PI / 2))));
      coreBright = Math.max(0.2, Math.min(1.2, coreBright));

      const R = this.R;
      for (let i = 0; i < this.N; i++) {
        const f = this.frags[i];
        let rad = R * radialFactor;
        if (v === "idle") { f.np += 0.01; rad += Math.sin(f.np) * R * 0.012; }
        if (v === "thinking") { f.np += 0.03; rad += Math.sin(f.np * 1.5 + t / 200) * R * 0.06; }
        if (v === "speaking") {
          rad += (micActive ? b.low : 0.12) * R * 0.5 * (0.5 + 0.5 * Math.sin(f.np + t / 200));
          f.np += 0.02;
        }
        const goal = { x: f.dir.x * rad, y: f.dir.y * rad, z: f.dir.z * rad };
        f.cur.x += (goal.x - f.cur.x) * 0.15;
        f.cur.y += (goal.y - f.cur.y) * 0.15;
        f.cur.z += (goal.z - f.cur.z) * 0.15;
        if (!isFinite(f.cur.x) || !isFinite(f.cur.y) || !isFinite(f.cur.z)) {
          f.cur.x = f.dir.x * R; f.cur.y = f.dir.y * R; f.cur.z = f.dir.z * R;
        }
        f.spin += f.spinV;
        const hT =
          (v === "speaking") ? (micActive ? 0.5 + b.high * 0.9 : 0.85) :
          (v === "listening") ? 0.75 :
          (v === "thinking") ? 0.9 : 0.55;
        f.heat += (hT - f.heat) * 0.08;
      }

      // Project + z-sort
      const proj = [];
      for (let i = 0; i < this.N; i++) {
        const f = this.frags[i];
        const p = rot3(f.cur, ay, ax);
        const fz = 520; let dn = fz - p.z; if (dn < 60) dn = 60;
        const sc = fz / dn;
        proj.push({ f, x: this.cx + p.x * sc, y: this.cy + p.y * sc, z: p.z, sc });
      }
      proj.sort((a, b2) => a.z - b2.z);
      let mid = 0;
      for (let k = 0; k < proj.length; k++) { if (proj[k].z >= 0) { mid = k; break; } mid = k + 1; }

      // Back half
      for (let k = 0; k < mid; k++) this._drawFrag(proj[k]);
      for (let sI = 0; sI < this.sats.length; sI++) {
        this.sats[sI].ang += this.sats[sI].sp * (v === "speaking" ? (1 + (micActive ? b.mid : 0.2) * 3) : 1);
        this.sats[sI].spin += 0.03;
        if (Math.sin(this.sats[sI].ang) < 0) this._drawSat(this.sats[sI], ay, ax);
      }
      // Core
      this._drawCore(0.9 + coreBright * 0.18, coreBright);
      // Front half
      for (let k = mid; k < proj.length; k++) this._drawFrag(proj[k]);
      for (let sI = 0; sI < this.sats.length; sI++) {
        if (Math.sin(this.sats[sI].ang) >= 0) this._drawSat(this.sats[sI], ay, ax);
      }
    }

    destroy() {
      this.pause();
      this.detachStream();
      window.removeEventListener("resize", this._sizeBound);
      if (this._ro) this._ro.disconnect();
    }
  }

  window.ObsidianNexus = ObsidianNexus;
})();
