/* MatchLens static demo: replays a precomputed, AI-explained match entirely in the browser.
 * Loaded before app.js on the GitHub Pages build (build_static.py). It answers app.js's
 * /api/... calls and its live event stream from files in data/, so no server is needed. */
(() => {
  "use strict";
  window.ML_STATIC = true;
  const DATA = "data/";
  const realFetch = window.fetch.bind(window);
  let bundle = null, profiles = null;
  const sess = { vt: 0, speed: 60, paused: false };

  const load = () => bundle || (bundle = realFetch(DATA + "match.json").then((r) => r.json()));
  const loadProfiles = () => profiles || (profiles = realFetch(DATA + "profiles.json").then((r) => r.json()));
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  window.fetch = async (url, opts = {}) => {
    if (typeof url !== "string" || !url.startsWith("/api/")) return realFetch(url, opts);
    const b = await load();
    if (url === "/api/config") return json(b.config);
    if (url === "/api/matches") return opts.method === "POST" ? json({ error: "not in the static demo" }, 400) : json(b.matches);
    let m;
    if ((m = url.match(/^\/api\/session\/[^/]+\/control$/))) {
      const body = JSON.parse(opts.body || "{}");
      if (body.speed) sess.speed = Math.max(1, Math.min(600, body.speed));
      if (body.paused !== undefined && body.paused !== null) sess.paused = body.paused;
      return json({ speed: sess.speed, paused: sess.paused });
    }
    if ((m = url.match(/^\/api\/session\/[^/]+\/player\/([^/]+)$/))) {
      const p = await loadProfiles();
      let snap = null;
      for (const s of p) { if (s.t <= sess.vt) snap = s; else break; }
      const prof = snap && snap.players[m[1]];
      return prof ? json(prof) : json({ detail: "not on the pitch yet" }, 404);
    }
    return json({ detail: "live AI features need the full app" }, 404);
  };

  class StaticEventSource {
    constructor(url) {
      this.readyState = 0;
      this.handlers = {};
      this.onerror = null;
      const q = new URLSearchParams(url.split("?")[1] || "");
      Object.assign(sess, { vt: 0, paused: false, speed: Math.max(1, +q.get("speed") || 60) });
      load().then((b) => this.run(b));
    }
    addEventListener(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); }
    emit(name, data) { (this.handlers[name] || []).forEach((fn) => fn({ data: JSON.stringify(data) })); }
    close() { this.readyState = 2; clearInterval(this.timer); }
    run(b) {
      this.readyState = 1;
      this.emit("session", { ...b.session, speed: sess.speed });
      const tl = b.timeline;
      let i = 0, last = performance.now();
      this.timer = setInterval(() => {
        if (this.readyState === 2) return;
        const now = performance.now();
        if (!sess.paused) sess.vt += ((now - last) / 1000) * sess.speed;
        last = now;
        let latestTick = null;
        while (i < tl.length && tl[i][0] <= sess.vt) {
          const [, name, data] = tl[i++];
          if (name === "tick") { latestTick = data; continue; }  // only draw the newest frame
          if (latestTick) { this.emit("tick", latestTick); latestTick = null; }
          this.emit(name, data);
          if (name === "end") { this.close(); return; }
        }
        if (latestTick) this.emit("tick", latestTick);
      }, 40);
    }
  }
  StaticEventSource.CLOSED = 2;
  window.EventSource = StaticEventSource;
})();
