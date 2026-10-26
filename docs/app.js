/* MatchLens front end: render the live stream and personalise it for the viewer. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const RTL = new Set(["ar", "he", "fa", "ur"]);
  const PITCH = { L: 105, W: 68 };

  const store = {
    get(k, d) { try { const v = localStorage.getItem("ml:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("ml:" + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };

  const S = {
    config: null, session: null, es: null, players: {}, speed: 60, paused: false,
    from: {}, to: {}, ballFrom: null, ballTo: null, t0: 0, dur: 1000,
    moments: new Map(), states: [], trace: [], recaps: [], lastState: null,
    viewer: store.get("viewer", { mode: "casual", language: "en", favourite_club: "", favourite_player: "", focus_metric: "" }),
    personal: new Map(), overlayTimers: {}, playerTimer: null,
  };

  // ------------------------------------------------------------ boot
  async function boot() {
    S.config = await (await fetch("/api/config")).json();
    const status = $("ai-status");
    status.textContent = S.config.status || (S.config.ai_available ? `AI: ${S.config.ai}` : "AI offline: showing engine facts");
    status.classList.toggle("live", S.config.ai_available || !!S.config.status);
    if (window.ML_STATIC) $("new-match").hidden = true;
    const lang = $("language");
    lang.innerHTML = `<option value="en">English</option>` + Object.entries(S.config.languages)
      .filter(([c]) => c !== "en").map(([c, n]) => `<option value="${c}">${n}</option>`).join("");
    await loadMatches();
    bindControls();
    applyViewerToForm();
    resizeCanvases();
    requestAnimationFrame(draw);
  }

  async function loadMatches(selectId) {
    const list = await (await fetch("/api/matches")).json();
    $("match-select").onchange = renderDownloads;
    $("match-select").innerHTML = list.map((m) =>
      `<option value="${m.id}">${esc(m.home)} v ${esc(m.away)}${m.ai_explained ? " (AI explained)" : ""}</option>`).join("");
    if (selectId) $("match-select").value = selectId;
    renderDownloads();
  }

  // ------------------------------------------------------------ downloads
  const DOWNLOADS = [
    ["players.csv", "Player stats", "Every player, every stat. Opens in Excel or Google Sheets."],
    ["teams.csv", "Team stats", "Both teams side by side."],
    ["moments.csv", "Key moments", "Each moment with the AI explanation and fact-check result."],
    ["timeline.csv", "Minute by minute", "Score, possession, xG and momentum for every minute."],
    ["events.jsonl", "Event feed (JSON Lines)", "Every raw event, for data pipelines."],
  ];

  function renderDownloads() {
    const box = $("downloads");
    if (!box) return;
    const match = $("match-select").value;
    const live = !window.ML_STATIC && S.session && S.session.match_id && !$("pause").disabled;
    const base = window.ML_STATIC ? "data/export" : live ? `/api/session/${S.session.session}/export` : `/api/matches/${encodeURIComponent(match)}/export`;
    $("dl-note").textContent = live
      ? `Stats so far, up to ${$("clock").textContent}. Download again later for more.`
      : "Full-match data for the selected match.";
    box.innerHTML = DOWNLOADS.map(([file, label, help]) =>
      `<a class="dl" href="${base}/${file}" download title="${esc(help)}"><b>${esc(label)}</b><span>${esc(help)}</span></a>`).join("") +
      `<a class="dl all" href="${window.ML_STATIC ? "data/export" : base}.zip" download><b>Everything (ZIP)</b><span>All tables as CSV, the event feed, and a data dictionary.</span></a>` +
      (live ? `<a class="dl" href="/api/matches/${encodeURIComponent(match)}/export.zip" download><b>Full match instead (ZIP)</b><span>All 90+ minutes, without waiting.</span></a>` : "");
  }

  // ------------------------------------------------------------ controls
  function bindControls() {
    $("start").onclick = start;
    $("pause").onclick = () => control({ paused: !S.paused });
    $("new-match").onclick = async () => {
      $("new-match").disabled = true;
      $("new-match").textContent = "Generating…";
      const m = await (await fetch("/api/matches", { method: "POST" })).json();
      await loadMatches(m.id);
      $("new-match").disabled = false;
      $("new-match").textContent = "New match";
    };
    document.querySelectorAll(".speed button").forEach((b) => b.onclick = () => {
      S.speed = +b.dataset.speed;
      document.querySelectorAll(".speed button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      if (S.session) control({ speed: S.speed });
    });
    document.querySelectorAll(".seg button").forEach((b) => b.onclick = () => setViewer({ mode: b.dataset.mode }));
    $("language").onchange = (e) => setViewer({ language: e.target.value });
    $("fav-club").onchange = (e) => setViewer({ favourite_club: e.target.value });
    $("fav-player").onchange = (e) => { setViewer({ favourite_player: e.target.value }); if (e.target.value) showTab("player"); };
    $("focus-metric").onchange = (e) => setViewer({ focus_metric: e.target.value });
    document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
    window.addEventListener("resize", resizeCanvases);
  }

  function showTab(name) {
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
    document.querySelectorAll(".tab").forEach((t) => t.hidden = t.id !== "tab-" + name);
    if (name === "match") { drawMomentum(); renderDownloads(); }
    if (name === "player") refreshPlayer();
  }

  function setViewer(patch) {
    Object.assign(S.viewer, patch);
    store.set("viewer", S.viewer);
    S.personal.clear();
    applyViewerToForm();
    renderFeed();
    renderTeamStats();
    renderRecaps();
    renderFocus();
    if ("favourite_player" in patch) refreshPlayer();
  }

  function applyViewerToForm() {
    const v = S.viewer;
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.mode === v.mode));
    $("language").value = v.language || "en";
    $("fav-club").value = v.favourite_club || "";
    $("fav-player").value = v.favourite_player || "";
    $("focus-metric").value = v.focus_metric || "";
    const fav = v.favourite_club && S.session ? (S.session.home.name === v.favourite_club ? "home" : "away") : "";
    document.querySelector(".team.home").classList.toggle("fav", fav === "home");
    document.querySelector(".team.away").classList.toggle("fav", fav === "away");
  }

  async function control(body) {
    if (!S.session) return;
    const r = await (await fetch(`/api/session/${S.session.session}/control`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    S.paused = r.paused;
    S.speed = r.speed;
    $("pause").textContent = S.paused ? "Resume" : "Pause";
  }

  // ------------------------------------------------------------ stream
  function start() {
    if (S.es) S.es.close();
    Object.assign(S, { moments: new Map(), states: [], trace: [], recaps: [], lastState: null, from: {}, to: {},
      ballFrom: null, ballTo: null, paused: false });
    S.personal.clear();
    $("feed").innerHTML = `<li class="hint">Kick-off. Moments appear here as they happen.</li>`;
    $("trace").innerHTML = "";
    $("recap").innerHTML = "";
    $("agent-summary").textContent = "";
    $("empty").hidden = true;
    $("pause").disabled = false;
    $("pause").textContent = "Pause";
    ["full_banner", "lower_third", "corner_badge"].forEach((s) => $("ov-" + s).hidden = true);
    const es = new EventSource(`/api/stream?match=${encodeURIComponent($("match-select").value)}&speed=${S.speed}`);
    S.es = es;
    es.addEventListener("session", (e) => onSession(JSON.parse(e.data)));
    es.addEventListener("tick", (e) => onTick(JSON.parse(e.data)));
    es.addEventListener("state", (e) => onState(JSON.parse(e.data)));
    es.addEventListener("moment", (e) => onMoment(JSON.parse(e.data)));
    es.addEventListener("recap", (e) => onRecap(JSON.parse(e.data)));
    es.addEventListener("trace", (e) => onTrace(JSON.parse(e.data)));
    es.addEventListener("end", (e) => { onEnd(JSON.parse(e.data)); es.close(); });
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) $("pause").disabled = true; };
  }

  function onSession(s) {
    S.session = s;
    S.players = {};
    for (const side of ["home", "away"]) {
      for (const p of s[side].players) S.players[p.id] = { ...p, team: side };
      document.documentElement.style.setProperty(`--${side}`, s[side].colours[0]);
      document.documentElement.style.setProperty(`--${side}-2`, s[side].colours[1]);
      $(`name-${side}`).textContent = s[side].name;
    }
    $("fav-club").innerHTML = `<option value="">None</option>` +
      ["home", "away"].map((x) => `<option value="${esc(s[x].name)}">${esc(s[x].name)}</option>`).join("");
    const opts = (side) => s[side].players.map((p) => `<option value="${p.id}">${esc(p.name)} (${p.position})</option>`).join("");
    $("fav-player").innerHTML = `<option value="">None</option><optgroup label="${esc(s.home.name)}">${opts("home")}</optgroup>` +
      `<optgroup label="${esc(s.away.name)}">${opts("away")}</optgroup>`;
    if (S.viewer.favourite_player && !S.players[S.viewer.favourite_player]) S.viewer.favourite_player = "";
    if (S.viewer.favourite_club && ![s.home.name, s.away.name].includes(S.viewer.favourite_club)) S.viewer.favourite_club = "";
    applyViewerToForm();
    renderDownloads();
    const how = { precomputed: "AI-explained replay", live: "AI agents working live", engine: "Engine facts only" }[s.mode];
    $("agent-summary").textContent = `${how}. ${s.ai}.`;
  }

  function onTick(f) {
    const now = performance.now();
    for (const id in S.to) S.from[id] = lerpPos(id, now);
    S.ballFrom = S.ballTo ? lerpBall(now) : f.ball;
    S.to = {};
    for (const [id, x, y] of f.players) { S.to[id] = { x, y }; if (!S.from[id]) S.from[id] = { x, y }; }
    S.ballTo = f.ball;
    S.t0 = now;
    S.dur = Math.max(120, 5000 / S.speed);
    $("clock").textContent = f.clock;
    $("score-home").textContent = f.score.home;
    $("score-away").textContent = f.score.away;
  }

  function onState(st) {
    S.lastState = st;
    S.states.push(st);
    const share = st.momentum_home_share;
    $("mom-home").style.width = `${share * 100}%`;
    $("mom-away").style.width = `${(1 - share) * 100}%`;
    const phase = $("phase");
    phase.hidden = st.phase === "balanced";
    phase.className = "phase " + (st.phase === "chaos" ? "chaos" : "");
    phase.textContent = st.phase === "chaos" ? "Chaotic" : st.phase.startsWith("control_") ?
      `${S.session[st.phase.slice(8)].short} in control` : "";
    renderFocus();
    if (!$("tab-match").hidden) { renderTeamStats(); drawMomentum(); renderDownloads(); }
    if (!$("tab-player").hidden && S.viewer.favourite_player) refreshPlayer();
  }

  function onMoment(m) {
    const existed = S.moments.has(m.insight_id);
    S.moments.set(m.insight_id, m);
    if (!existed || !m.pending) showOverlay(m);
    renderFeed();
    if (needsPersonal(m)) personalize(m);
  }

  function onRecap(r) {
    S.recaps.push(r);
    renderRecaps();
    const text = localised(r.recap.headline, r.translations, "caption");
    showOverlay({ insight_id: "recap-" + r.stage, overlay: { slot: "full_banner", duration_s: 12 }, team: null,
      narration: { caption: text, fan_take: r.recap.summary, analyst_take: r.recap.tactical_story }, clock: r.clock });
  }

  function onTrace(t) {
    S.trace.push(t);
    const li = document.createElement("li");
    li.innerHTML = `<span class="a">${esc(agentLabel(t.agent))}</span><span class="s ${t.status}">${t.status}${t.ms ? ` · ${t.ms} ms` : ""}</span>` +
      (t.note ? `<span class="n">${esc(t.note)}</span>` : "");
    $("trace").prepend(li);
    while ($("trace").children.length > 120) $("trace").lastChild.remove();
    document.querySelectorAll(`[data-agent="${t.agent}"]`).forEach((el) => {
      el.classList.add("active");
      setTimeout(() => el.classList.remove("active"), 900);
    });
  }

  function onEnd(e) {
    $("pause").disabled = true;
    renderDownloads();
    const s = e.summary || {};
    if (s.agent_calls) $("agent-summary").textContent += ` Full time: ${s.agent_calls} agent calls, ${s.narrator_retries} fact-check retries, ${s.fallbacks} fallbacks.`;
  }

  // ------------------------------------------------------------ personalisation
  function involves(m) {
    const v = S.viewer;
    return (v.favourite_club && m.team_name === v.favourite_club) || (v.favourite_player && m.player_id === v.favourite_player);
  }

  function needsPersonal(m) {
    if (!S.config.ai_available || m.pending || m.priority > 2 || !S.session) return false;
    const lang = S.viewer.language || "en";
    const missingLang = lang !== "en" && !(m.translations && m.translations[lang]);
    return missingLang || involves(m) || !!S.viewer.focus_metric && m.priority === 1;
  }

  async function personalize(m) {
    const key = m.insight_id;
    if (S.personal.has(key)) return;
    S.personal.set(key, null);
    try {
      const r = await fetch(`/api/session/${S.session.session}/personalize`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ insight_id: m.insight_id, viewer: viewerPayload() }) });
      if (!r.ok) { S.personal.delete(key); return; }
      S.personal.set(key, await r.json());
      renderFeed();
      if (S.overlayShowing === key) showOverlay(m);
    } catch { S.personal.delete(key); }
  }

  function viewerPayload() {
    const v = S.viewer;
    const fp = S.players[v.favourite_player];
    return { mode: v.mode, language: v.language || "en", favourite_club: v.favourite_club || null,
      favourite_player: fp ? fp.name : null, focus_metric: v.focus_metric || null };
  }

  function localised(text, translations, field) {
    const lang = S.viewer.language || "en";
    return (lang !== "en" && translations && translations[lang] && translations[lang][field]) || text;
  }

  function textsFor(m) {
    const lang = S.viewer.language || "en";
    const p = S.personal.get(m.insight_id);
    const n = m.narration;
    const tr = (m.translations || {})[lang];
    if (p) return { cap: p.caption, sub: p.text, why: p.why_you_care, dir: lang };
    const caption = lang !== "en" && tr ? tr.caption : n.caption;
    const sub = S.viewer.mode === "casual"
      ? (lang !== "en" && tr ? tr.fan_take : n.fan_take)
      : (lang !== "en" && tr ? tr.commentary : n.analyst_take);
    const why = S.viewer.mode === "analyst" && lang === "en" ? (m.explanation || {}).why_it_matters : "";
    return { cap: caption, sub, why, dir: tr || lang === "en" ? lang : "en" };
  }

  // ------------------------------------------------------------ overlays
  function showOverlay(m) {
    const slot = (m.overlay && m.overlay.slot) || "lower_third";
    const el = $("ov-" + slot);
    const t = textsFor(m);
    const edge = m.team ? `var(--${m.team})` : "var(--flood)";
    el.style.setProperty("--edge", edge);
    el.setAttribute("dir", RTL.has(t.dir) ? "rtl" : "ltr");
    el.innerHTML = `<div class="meta">${esc(m.clock || "")}${involves(m) ? " · for you" : ""}</div>` +
      `<div class="cap">${esc(t.cap)}</div>` +
      (slot !== "corner_badge" && t.sub && t.sub !== t.cap ? `<div class="sub">${esc(t.sub)}</div>` : "") +
      (slot !== "corner_badge" && t.why ? `<div class="why">${esc(t.why)}</div>` : "");
    el.hidden = false;
    el.classList.remove("show");
    void el.offsetWidth;
    el.classList.add("show");
    S.overlayShowing = m.insight_id;
    clearTimeout(S.overlayTimers[slot]);
    const secs = Math.max(3.5, ((m.overlay && m.overlay.duration_s) || 8) * 0.6);
    S.overlayTimers[slot] = setTimeout(() => { el.hidden = true; }, secs * 1000);
  }

  // ------------------------------------------------------------ feed
  function renderFeed() {
    const items = [...S.moments.values()].sort((a, b) => b.match_seconds - a.match_seconds);
    if (!items.length) return;
    const analyst = S.viewer.mode === "analyst";
    $("feed").innerHTML = items.map((m) => {
      const t = textsFor(m);
      const badges = [];
      if (involves(m)) badges.push(`<span class="badge fav">For you</span>`);
      if (m.pending) badges.push(`<span class="badge pending">Agents explaining…</span>`);
      else if (m.source === "ai" || m.source === "ai_light") badges.push(`<span class="badge ok">AI explained · fact-checked</span>`);
      else if (m.source === "fallback") badges.push(`<span class="badge">Facts only (AI check failed)</span>`);
      if (analyst && m.explanation && m.explanation.significance) badges.push(`<span class="badge">Significance ${m.explanation.significance}/5</span>`);
      const who = m.player_id && S.players[m.player_id]
        ? `<button class="link" data-player="${m.player_id}">${esc(S.players[m.player_id].name)}</button>` : "";
      const why = analyst && m.explanation && m.explanation.why_it_matters
        ? `<div class="why">${esc(m.explanation.why_it_matters)} ${esc(m.explanation.tactical_context || "")}</div>` : "";
      return `<li class="moment ${m.priority === 1 ? "big" : ""}" style="--edge: var(--${m.team || "stand-3"})">
        <div class="row"><span class="t">${esc(m.clock)}</span><span class="cap" dir="${RTL.has(t.dir) ? "rtl" : "ltr"}">${esc(t.cap)}</span></div>
        ${t.sub && t.sub !== t.cap ? `<div class="txt" dir="${RTL.has(t.dir) ? "rtl" : "ltr"}">${esc(t.sub)}</div>` : ""}
        ${why}
        <div class="tags">${badges.join("")}${who}</div></li>`;
    }).join("");
    $("feed").querySelectorAll("[data-player]").forEach((b) => b.onclick = () => {
      setViewer({ favourite_player: b.dataset.player });
      showTab("player");
    });
  }

  // ------------------------------------------------------------ match tab
  function renderRecaps() {
    $("recap").innerHTML = S.recaps.slice().reverse().map((r) => {
      const c = r.recap;
      const tr = (r.translations || {})[S.viewer.language];
      return `<div class="recap"><h4>${esc(tr ? tr.caption : c.headline)}</h4><p>${esc(tr ? tr.commentary : c.summary)}</p>
        ${S.viewer.mode === "analyst" && c.tactical_story ? `<p>${esc(c.tactical_story)}</p>` : ""}
        ${c.key_moments && c.key_moments.length ? `<ul>${c.key_moments.map((k) => `<li>${esc(k)}</li>`).join("")}</ul>` : ""}
        ${c.player_of_the_match ? `<p><b>Player of the match:</b> ${esc(c.player_of_the_match)}</p>` : ""}</div>`;
    }).join("");
  }

  const STAT_ROWS = {
    casual: [["possession", "Possession", pct], ["shots", "Shots", int], ["shots_on_target", "On target", int],
             ["corners", "Corners", int]],
    analyst: [["possession", "Possession", pct], ["xg", "Expected goals (xG)", dec], ["xgot", "xG on target", dec],
              ["shots", "Shots", int], ["shots_on_target", "On target", int], ["big_chances", "Big chances", int],
              ["pass_accuracy", "Pass accuracy", pct], ["line_breaking_passes", "Line-breaking passes", int],
              ["final_third_entries", "Final-third entries", int], ["pressures", "Pressures", int],
              ["recoveries", "Recoveries", int], ["aerials_won", "Aerial duels won", int],
              ["distance_km", "Distance (km)", dec1], ["sprints", "Sprints", int]],
  };

  function renderTeamStats() {
    const st = S.lastState;
    if (!st) return;
    const rows = STAT_ROWS[S.viewer.mode] || STAT_ROWS.casual;
    $("team-stats").innerHTML = `<h3>${S.viewer.mode === "analyst" ? "Team numbers" : "The basics"}</h3>` + rows.map(([k, label, fmt]) => {
      const h = +st.teams.home[k] || 0, a = +st.teams.away[k] || 0;
      const share = h + a ? (h / (h + a)) * 100 : 50;
      return `<div class="stat"><span class="lbl">${label}</span><span class="v">${fmt(h)}</span>
        <span class="split"><i style="width:${share}%;background:var(--home)"></i><i style="width:${100 - share}%;background:var(--away)"></i></span>
        <span class="v r">${fmt(a)}</span></div>`;
    }).join("");
  }

  function renderFocus() {
    const k = S.viewer.focus_metric, st = S.lastState, tile = $("focus-tile");
    if (!k || !st || !S.session) { tile.hidden = true; return; }
    const labels = { xg: "xG", possession: "Possession", shots: "Shots", pressures: "Pressing, last 5", pass_accuracy: "Pass accuracy", distance_km: "Distance (km)" };
    let h, a;
    if (k === "pressures") { h = st.last_5_min.home.pressures; a = st.last_5_min.away.pressures; }
    else { h = st.teams.home[k]; a = st.teams.away[k]; }
    const f = k === "possession" || k === "pass_accuracy" ? pct : k === "xg" || k === "distance_km" ? dec : int;
    $("focus-label").textContent = labels[k];
    $("focus-value").textContent = `${S.session.home.short} ${f(h)} · ${f(a)} ${S.session.away.short}`;
    tile.hidden = false;
  }

  // ------------------------------------------------------------ player tab
  async function refreshPlayer() {
    const pid = S.viewer.favourite_player;
    const box = $("player-card");
    if (!pid || !S.session) { box.innerHTML = `<p class="hint">Choose a player to follow above, or click one in the feed.</p>`; return; }
    const r = await fetch(`/api/session/${S.session.session}/player/${pid}`);
    if (!r.ok) return;
    renderPlayer(await r.json(), box.querySelector(".spot") ? box.querySelector(".spot").outerHTML : "");
  }

  const CASUAL_STATS = new Set(["Goals", "Assists", "Shots", "Shots on target", "Successful passes %", "Chances created",
    "Distance covered (km)", "Top speed (km/h)", "Number of sprints", "Tackles", "Saves"]);

  function renderPlayer(p, spotHtml) {
    const analyst = S.viewer.mode === "analyst";
    const sections = Object.entries(p.sections).map(([name, items]) => {
      const rows = items.filter((it) => analyst || CASUAL_STATS.has(it.stat)).map((it) =>
        `<tr><td>${esc(it.stat)}</td><td class="v">${it.value === null ? "–" : esc(String(it.value))}</td>
         <td class="rk ${it.rank && it.rank <= 3 ? "top" : ""}">${it.rank ? `${ordinal(it.rank)} of ${it.of}` : ""}</td></tr>`).join("");
      return rows ? `<h3>${esc(name)}</h3><table>${rows}</table>` : "";
    }).join("");
    $("player-card").innerHTML = `<div class="pcard"><span class="rating" title="Match rating">${p.rating}</span>
      <h4>${esc(p.name)}</h4><div class="sub">#${p.number} ${esc(p.position)} · ${esc(p.team_name)} · ${p.minutes_played} min</div>
      ${window.ML_STATIC ? `<p class="hint">Live AI spotlights run in the full app (see the demo video).</p>` : `<button type="button" id="spotlight-btn" ${S.session ? "" : "disabled"}>Ask the AI about ${esc(p.name.split(" ")[0])}'s match</button>`}
      ${spotHtml || ""}${sections}</div>`;
    if ($("spotlight-btn")) $("spotlight-btn").onclick = () => spotlight(p.player_id);
  }

  async function spotlight(pid) {
    const btn = $("spotlight-btn");
    btn.disabled = true;
    btn.textContent = "Player Spotlight agent is writing…";
    const r = await fetch(`/api/session/${S.session.session}/spotlight`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player_id: pid, viewer: viewerPayload() }) });
    if (!r.ok) { btn.textContent = "Couldn't reach the AI. Try again."; btn.disabled = false; return; }
    const { profile, spotlight: sp } = await r.json();
    const html = `<div class="spot"><b>${esc(sp.headline)}</b>${sp.text ? `<p>${esc(sp.text)}</p>` : ""}
      ${sp.standout_stats && sp.standout_stats.length ? `<ul>${sp.standout_stats.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}</div>`;
    renderPlayer(profile, html);
  }

  // ------------------------------------------------------------ drawing
  const cv = $("pitch"), ctx = cv.getContext("2d");
  const mc = $("momentum-chart"), mctx = mc.getContext("2d");

  function resizeCanvases() {
    const dpr = window.devicePixelRatio || 1;
    for (const c of [cv, mc]) {
      const r = c.getBoundingClientRect();
      c.width = Math.max(1, Math.round(r.width * dpr));
      c.height = Math.max(1, Math.round(r.height * dpr));
    }
    drawMomentum();
  }

  function lerp(a, b, k) { return a + (b - a) * k; }
  function progress(now) { return Math.min(1, Math.max(0, (now - S.t0) / S.dur)); }
  function lerpPos(id, now) {
    const a = S.from[id], b = S.to[id];
    if (!a || !b) return b || a;
    const k = progress(now);
    return { x: lerp(a.x, b.x, k), y: lerp(a.y, b.y, k) };
  }
  function lerpBall(now) {
    if (!S.ballFrom) return S.ballTo;
    const k = progress(now);
    return { x: lerp(S.ballFrom.x, S.ballTo.x, k), y: lerp(S.ballFrom.y, S.ballTo.y, k) };
  }

  function draw(now) {
    const w = cv.width, h = cv.height, sx = w / PITCH.L, sy = h / PITCH.W;
    for (let i = 0; i < 12; i++) {
      ctx.fillStyle = i % 2 ? "#1e5b3b" : "#22663f";
      ctx.fillRect((i * w) / 12, 0, w / 12 + 1, h);
    }
    ctx.strokeStyle = "rgba(242,244,238,.85)";
    ctx.lineWidth = Math.max(1.5, w / 600);
    const m = 1.2;
    ctx.strokeRect(m * sx, m * sy, (PITCH.L - 2 * m) * sx, (PITCH.W - 2 * m) * sy);
    ctx.beginPath(); ctx.moveTo((PITCH.L / 2) * sx, m * sy); ctx.lineTo((PITCH.L / 2) * sx, (PITCH.W - m) * sy); ctx.stroke();
    ctx.beginPath(); ctx.arc((PITCH.L / 2) * sx, (PITCH.W / 2) * sy, 9.15 * sx, 0, Math.PI * 2); ctx.stroke();
    for (const left of [true, false]) {
      const x0 = left ? m : PITCH.L - m, dir = left ? 1 : -1;
      ctx.strokeRect(Math.min(x0, x0 + dir * 16.5) * sx, (PITCH.W / 2 - 20.16) * sy, 16.5 * sx, 40.32 * sy);
      ctx.strokeRect(Math.min(x0, x0 + dir * 5.5) * sx, (PITCH.W / 2 - 9.16) * sy, 5.5 * sx, 18.32 * sy);
      ctx.beginPath(); ctx.arc((x0 + dir * 11) * sx, (PITCH.W / 2) * sy, 9.15 * sx, left ? -0.93 : Math.PI - 0.93, left ? 0.93 : Math.PI + 0.93); ctx.stroke();
    }

    const r = Math.max(5, 1.25 * sx);
    const css = getComputedStyle(document.documentElement);
    const col = kitColours(css);
    let favPos = null;
    for (const id in S.to) {
      const p = lerpPos(id, now), info = S.players[id];
      if (!p || !info) continue;
      ctx.beginPath();
      ctx.arc(p.x * sx, p.y * sy, r, 0, Math.PI * 2);
      ctx.fillStyle = info.team === "home" ? col.home : col.away;
      ctx.fill();
      ctx.lineWidth = Math.max(1.5, r / 4);
      ctx.strokeStyle = info.team === "home" ? col.home2 : col.away2;
      ctx.stroke();
      if (r >= 9) {
        ctx.fillStyle = info.team === "home" ? col.home2 : col.away2;
        ctx.font = `700 ${Math.round(r * 0.95)}px Archivo, sans-serif`;
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(info.number, p.x * sx, p.y * sy + 1);
      }
      if (id === S.viewer.favourite_player) favPos = { p, info };
    }
    if (favPos) {
      const { p, info } = favPos;
      ctx.beginPath(); ctx.arc(p.x * sx, p.y * sy, r * 1.7, 0, Math.PI * 2);
      ctx.strokeStyle = "#f2c14e"; ctx.lineWidth = Math.max(2, r / 3); ctx.stroke();
      ctx.font = `700 ${Math.round(Math.max(12, r * 1.1))}px Archivo, sans-serif`;
      ctx.textAlign = "center"; ctx.textBaseline = "bottom";
      const label = info.name.split(" ").slice(-1)[0];
      const tw = ctx.measureText(label).width + 10;
      ctx.fillStyle = "rgba(16,24,32,.85)";
      ctx.fillRect(p.x * sx - tw / 2, p.y * sy - r * 2 - Math.max(16, r * 1.4), tw, Math.max(16, r * 1.4));
      ctx.fillStyle = "#f2c14e";
      ctx.fillText(label, p.x * sx, p.y * sy - r * 2 - 2);
    }
    const b = lerpBall(now);
    if (b) {
      ctx.beginPath(); ctx.arc(b.x * sx, b.y * sy, Math.max(3.5, r * 0.5), 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff"; ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = "#1b2530"; ctx.stroke();
    }
    requestAnimationFrame(draw);
  }

  // Kits too close to the grass colour swap to their second colour so players stay visible.
  function hexRgb(h) { const m = /^#?([0-9a-f]{6})$/i.exec(h || ""); if (!m) return [255, 255, 255]; const n = parseInt(m[1], 16); return [n >> 16, (n >> 8) & 255, n & 255]; }
  function nearTurf(h) { const [r, g, b] = hexRgb(h); return Math.hypot(r - 0x20, g - 0x60, b - 0x3d) < 110 || (g > r + 30 && g > b + 20); }
  function kitColours(css) {
    const out = {};
    for (const side of ["home", "away"]) {
      let a = css.getPropertyValue(`--${side}`).trim(), b = css.getPropertyValue(`--${side}-2`).trim();
      if (nearTurf(a) && !nearTurf(b)) [a, b] = [b, a];
      out[side] = a; out[side + "2"] = b;
    }
    return out;
  }

  function drawMomentum() {
    const rect = mc.getBoundingClientRect();
    if (!rect.width) return;  // tab hidden: draw when it is shown
    const dpr = window.devicePixelRatio || 1;
    if (Math.abs(mc.width - rect.width * dpr) > 1) { mc.width = Math.round(rect.width * dpr); mc.height = Math.round(rect.height * dpr); }
    const w = mc.width, h = mc.height;
    mctx.clearRect(0, 0, w, h);
    if (!w || S.states.length < 2) return;
    const css = getComputedStyle(document.documentElement);
    const n = Math.max(95, S.states[S.states.length - 1].minute + 1);
    const x = (i) => (S.states[i].minute / n) * w;
    const y = (v) => h - v * h;
    mctx.strokeStyle = "rgba(142,155,167,.4)";
    mctx.beginPath(); mctx.moveTo(0, h / 2); mctx.lineTo(w, h / 2); mctx.stroke();
    for (const [side, above] of [["home", true], ["away", false]]) {
      mctx.beginPath();
      mctx.moveTo(x(0), h / 2);
      S.states.forEach((s, i) => {
        const v = s.momentum_home_share;
        mctx.lineTo(x(i), above ? y(Math.max(v, 0.5)) : y(Math.min(v, 0.5)));
      });
      mctx.lineTo(x(S.states.length - 1), h / 2);
      mctx.closePath();
      mctx.fillStyle = css.getPropertyValue(`--${side}`).trim();
      mctx.globalAlpha = 0.75; mctx.fill(); mctx.globalAlpha = 1;
    }
    mctx.fillStyle = "rgba(242,193,78,.95)";
    for (const m of S.moments.values()) {
      if (m.type !== "goal") continue;
      const gx = (m.match_seconds / 60 / n) * w;
      mctx.fillRect(gx - 1, 0, Math.max(2, w / 300), h);
    }
  }

  // ------------------------------------------------------------ utils
  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function pct(v) { return v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`; }
  function int(v) { return v === null || v === undefined ? "–" : String(Math.round(v)); }
  function dec(v) { return v === null || v === undefined ? "–" : (+v).toFixed(2); }
  function dec1(v) { return v === null || v === undefined ? "–" : (+v).toFixed(1); }
  function ordinal(n) { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }
  function agentLabel(a) {
    return { TacticalAnalyst: "Tactical Analyst", BroadcastNarrator: "Narrator", FactChecker: "Fact Checker",
      Localizer: "Localizer", RecapWriter: "Recap Writer", Personalizer: "Personalizer",
      PlayerSpotlight: "Player Spotlight", Orchestrator: "Orchestrator" }[a] || a;
  }

  boot();
})();
