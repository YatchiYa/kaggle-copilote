"use strict";
/* Podium dashboard: hash-routed SPA, no build step. Data from the FastAPI backend; live updates over SSE. */

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const num = (v, d = 4) => v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d);
const money = v => "$" + (Number(v) || 0).toFixed(2);
const pct = (a, b) => b ? (100 * a / b).toFixed(1) + "%" : "—";
const daysLeft = d => d ? Math.ceil((new Date(d) - Date.now()) / 864e5) : null;
const ago = ts => {
  if (!ts) return "—";
  const s = Math.max(0, (Date.now() - new Date(ts)) / 1000);
  return s < 60 ? `${s | 0}s ago` : s < 3600 ? `${s / 60 | 0}m ago` : s < 86400 ? `${s / 3600 | 0}h ago` : `${s / 86400 | 0}d ago`;
};
const when = ts => ts ? new Date(ts).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}) : "—";
const bytes = n => n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : n > 1e3 ? (n / 1e3).toFixed(0) + " KB" : n + " B";
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body?.detail || body?.error || r.statusText);
  return body;
}
const post = (p, body) => api(p, {method: "POST", headers: {"content-type": "application/json"}, body: body ? JSON.stringify(body) : undefined});
const put = (p, body) => api(p, {method: "PUT", headers: {"content-type": "application/json"}, body: JSON.stringify(body)});
function toast(msg, ms = 3200) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, ms); }
const store = {get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
               set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }};

// ------------------------------------------------------------------ icons (lucide-style paths)
const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  trophy: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0zM17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>',
  upload: '<path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  activity: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
  bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  check: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
  dollar: '<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>', x: '<path d="M18 6 6 18M6 6l12 12"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  play: '<path d="M6 4l14 8-14 8z"/>', ext: '<path d="M14 3h7v7M10 14 21 3M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/>',
  up: '<path d="M7 17 17 7M8 7h9v9"/>', brain: '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/>',
  flask: '<path d="M9 3h6M10 3v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/>', search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
};
const icon = (n, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ""}</svg>`;

// ------------------------------------------------------------------ state
const S = {fleet: null, me: null, events: [], sse: false, lastEventAt: null, route: {page: "overview"}, alertSeen: store.get("alertSeen", 0), tabs: store.get("tabs", {})};
const PAGES = [
  {group: "Monitor"}, {id: "overview", label: "Overview", icon: "home"}, {id: "ongoing", label: "Ongoing", icon: "activity"}, {id: "competitions", label: "Competitions", icon: "trophy"},
  {id: "submissions", label: "Submissions", icon: "upload"}, {id: "activity", label: "Activity", icon: "activity"},
  {group: "Pilot"}, {id: "decisions", label: "Decisions", icon: "check"}, {id: "alerts", label: "Alerts", icon: "bell"},
  {id: "spend", label: "Spend", icon: "dollar"},
  {group: "System"}, {id: "platforms", label: "Platforms", icon: "globe"}, {id: "settings", label: "Settings", icon: "settings"},
];
ICONS.spark = '<path d="M12 3l1.9 5.8L20 10.7l-6.1 1.9L12 18.5l-1.9-5.9L4 10.7l6.1-1.9z"/><path d="M19 3v4M17 5h4"/>';
ICONS.plus = '<path d="M12 5v14M5 12h14"/>';
const STATE = {active: ["Working", "ok"], needs_rules: ["Join needed", "warn"], done: ["Plateau", "info"], scouted: ["Scouted", ""],
               finished: ["Ended", ""], paused: ["Paused", "bad"], project: ["Dedicated project", "accent"], stopped: ["Stopped", ""],
               archived: ["Archived", ""]};
const IN_PLAY = ["active", "done", "needs_rules", "project"];
const stateBadge = c => { const [l, k] = c.paused ? STATE.paused : (STATE[c.state] || [c.state, ""]);
  const pj = c.state === "project" && c.project?.count ? ` · ${c.project.status === "pending" ? "scoring…" : esc(c.project.status)}` : "";
  const tip = c.project ? ` data-tip="${esc(`${c.project.count || 0} Kaggle submission(s). Latest: ${c.project.latest || "none"} (${c.project.status || "none"})`)}"` : "";
  return `<span class="badge ${k}"${tip}>${c.running && !c.paused ? '<i class="dot on"></i>' : ""}${l}${pj}</span>`; };
const rankText = c => c.lb_rank ? `#${c.lb_rank}<span class="muted">/${c.lb_teams}</span>` : "—";
const topPct = c => c.lb_rank && c.lb_teams ? 100 * c.lb_rank / c.lb_teams : null;
const sevOf = t => /error|flagged|failed/.test(t) ? "bad" : /diverged|human_task|recommended/.test(t) ? "warn" :
  /rank|scored|unblocked/.test(t) ? "ok" : /strategy|review|profile/.test(t) ? "accent" : "info";
const iconOf = t => /error|flagged|diverged/.test(t) ? "alert" : /rank/.test(t) ? "up" : /scored|sent/.test(t) ? "upload" :
  /strategy|profile/.test(t) ? "brain" : /experiment/.test(t) ? "flask" : /human|recommended/.test(t) ? "check" : /scout|found/.test(t) ? "search" : "activity";

// ------------------------------------------------------------------ router
function parseHash() {
  const [page, slug, tab] = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  S.route = {page: page || "overview", slug, tab};
}
function go(path) { location.hash = "#/" + path; }
window.addEventListener("hashchange", () => { parseHash(); $("#shell").classList.remove("open"); render(true); });

function renderNav() {
  const f = S.fleet, open = f?.kpis.open_tasks || 0, unread = unreadAlerts();
  $("#nav").innerHTML = PAGES.map(p => p.group ? `<div class="nav-group">${p.group}</div>` :
    `<a class="nav-item" href="#/${p.id}" ${S.route.page === p.id || (p.id === "competitions" && S.route.page === "competition") ? 'aria-current="page"' : ""}>
      ${icon(p.icon)}<span>${p.label}</span>
      ${p.id === "decisions" && open ? `<span class="count">${open}</span>` : ""}
      ${p.id === "alerts" && unread ? `<span class="count soft">${unread}</span>` : ""}
      ${p.id === "competitions" && f ? `<span class="count soft">${f.kpis.active}</span>` : ""}</a>`).join("")
    + `<button class="nav-item" id="navCopilot">${icon("spark")}<span>Copilot</span></button>`;
  $("#navCopilot").onclick = () => { $("#shell").classList.remove("open"); openCopilot(); };
  $("#sideFoot").innerHTML = S.me ? `Kaggle <b>${esc(S.me.username || "—")}</b><br>Model <b>${esc(S.me.model)}</b>` : "";
}
function crumbs(parts) {
  $("#crumbs").innerHTML = parts.map((p, i) => i < parts.length - 1 ? `<a href="${p[1]}">${esc(p[0])}</a><span class="sep">/</span>` :
    `<span class="cur">${esc(p[0])}</span>`).join("");
  document.title = `${parts[parts.length - 1][0]} · Podium`;
}

// ------------------------------------------------------------------ data
async function loadFleet() {
  S.fleet = await api("/api/fleet");
  $("#fleetBtn").innerHTML = S.fleet.fleet_paused ? `${icon("play")}<span class="lbl">Resume fleet</span>` : `${icon("pause")}<span class="lbl">Pause fleet</span>`;
  renderNav();
  return S.fleet;
}
const unreadAlerts = () => S.events.filter(e => e.id > S.alertSeen && /error|flagged|diverged|human_task|recommended|rank\.updated|scored|strategy/.test(e.type)).length;

// ------------------------------------------------------------------ render
let rendering = false, queued = false;
async function render(scrollTop) {
  if (rendering) { queued = true; return; }
  rendering = true;
  try {
    if (!S.fleet) await loadFleet();
    renderNav();
    const v = $("#view"), r = S.route;
    const page = {ongoing: pageOngoing, overview: pageOverview, competitions: pageCompetitions, competition: pageCompetition, submissions: pageSubmissions,
                  activity: pageActivity, decisions: pageDecisions, alerts: pageAlerts, spend: pageSpend, platforms: pagePlatforms,
                  settings: pageSettings}[r.page] || pageOverview;
    await page(v, r);
    if (scrollTop) window.scrollTo(0, 0);
  } catch (e) {
    $("#view").innerHTML = `<div class="banner bad">${icon("alert")}<div><b>Could not load this page.</b><br>${esc(e.message)}</div></div>`;
  } finally {
    rendering = false;
    if (queued) { queued = false; render(); }
  }
}

function kpi(label, value, hint = "", ic = "") {
  return `<div class="card kpi"><div class="label">${ic ? icon(ic) : ""}${label}</div><div class="value">${value}</div>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
}
function feedItem(e) {
  const s = sevOf(e.type);
  return `<div class="feed-item"><div class="ic sev-${s}">${icon(iconOf(e.type))}</div><div class="txt">
    <p>${esc(e.text || e.type)}</p><small>${esc(e.agent)} · ${ago(e.ts)}${e.cost_usd ? ` · ${money(e.cost_usd)}` : ""}</small></div></div>`;
}

// ---------- Overview (360)
async function pageOverview(v) {
  crumbs([["Overview"]]);
  const f = await loadFleet(), k = f.kpis, comps = f.competitions;
  const working = comps.filter(c => ["active", "done", "project"].includes(c.state));
  const best = working.filter(c => c.lb_rank).sort((a, b) => topPct(a) - topPct(b))[0];
  const quota = working.reduce((s, c) => s + (c.quota || 0), 0);
  const tasks = await api("/api/human-tasks");
  const open = tasks.filter(t => t.status === "open");
  const alerts = S.events.filter(e => /error|flagged|diverged|human_task|recommended|rank\.updated|scored|strategy|reviewed/.test(e.type)).slice(-8).reverse();
  v.innerHTML = `
  <div class="page-head"><div><h1>Fleet overview</h1><p>Everything the fleet is doing, in one view. ${f.fleet_paused ? '<span class="badge bad">Fleet paused</span>' : '<span class="badge ok"><i class="dot on"></i>Autonomous</span>'}</p></div>
    <div class="actions"><a class="btn" href="#/decisions">${icon("check")}Decisions${open.length ? ` <span class="badge warn">${open.length}</span>` : ""}</a></div></div>
  <div class="grid g-kpi" style="margin-bottom:16px">
    ${kpi("Competitions in play", working.length, `${k.active} experimenting · ${comps.filter(c => c.state === "needs_rules").length} waiting to join`, "trophy")}
    ${kpi("Best rank", best ? `top ${topPct(best).toFixed(1)}%` : "—", best ? `#${best.lb_rank}/${best.lb_teams} · ${esc(best.slug)}` : "No scored submission yet", "target")}
    ${kpi("Submissions today", `${k.submissions_today}<span class="muted" style="font-size:15px">/${quota || "—"}</span>`, "within Kaggle daily limits", "upload")}
    ${kpi("Experiments today", k.experiments_today, "run in the sandbox", "flask")}
    ${kpi("LLM spend · 7 days", money(k.spend_week), "against weekly cap", "dollar")}
    ${kpi("Needs you", `<span class="${open.length ? "warn" : ""}">${open.length}</span>`, k.errors_24h ? `<span class="bad">${k.errors_24h} errors in 24h</span>` : "no errors in 24h", "alert")}
  </div>
  <div class="grid g-main">
    <div class="stack">
      <div class="card"><div class="card-h"><h3>Competitions in play</h3><span class="sub">ranked by live position</span><div class="right"><a class="btn sm" href="#/competitions">All competitions</a></div></div>
        <div class="card-b">${working.length ? `<div class="comp-cards">${working.sort((a, b) => (topPct(a) ?? 999) - (topPct(b) ?? 999)).map(compCard).join("")}</div>`
          : `<div class="empty">Nothing in play yet. Join a recommended competition from <a href="#/decisions">Decisions</a>.</div>`}</div></div>
      <div class="card"><div class="card-h"><h3>Agents</h3><span class="sub">what each one is doing now</span></div><div class="card-b flush">${agentRows(f)}</div></div>
    </div>
    <div class="stack">
      <div class="card"><div class="card-h"><h3>Decisions needed</h3><div class="right"><a class="btn sm" href="#/decisions">Open</a></div></div>
        <div class="feed">${open.slice(0, 5).map(t => `<div class="feed-item"><div class="ic sev-warn">${icon("check")}</div><div class="txt">
          <p><b>${esc(t.competition_slug)}</b>: ${esc(t.detail)}</p><small>${ago(t.created_at)}${t.url ? ` · <a href="${esc(t.url)}" target="_blank" rel="noopener">Open on Kaggle</a>` : ""}</small></div></div>`).join("")
          || `<div class="empty">Nothing needs you.</div>`}</div></div>
      <div class="card"><div class="card-h"><h3>Latest alerts</h3><div class="right"><a class="btn sm" href="#/alerts">All</a></div></div>
        <div class="feed">${alerts.map(feedItem).join("") || `<div class="empty">No alerts yet.</div>`}</div></div>
    </div>
  </div>`;
  bindCompCards(v);
}
function compCard(c) {
  const p = topPct(c);
  return `<div class="card comp-card" data-slug="${esc(c.slug)}" tabindex="0" role="link">
    <div class="top"><div style="min-width:0;flex:1"><h4>${esc(c.title)}</h4><small class="muted">${esc(c.metric)} · ${c.state === "finished" ? "ended" : daysLeft(c.deadline) + " days left"}</small></div>${stateBadge(c)}</div>
    <div class="stats"><div><small>Rank</small><b>${rankText(c)}</b></div><div><small>Best CV</small><b>${num(c.best_cv)}</b></div><div><small>Public LB</small><b>${num(c.lb_public)}</b></div></div>
    <div><div class="meter" data-tip="Percentile on the public leaderboard (left is better)"><i class="${p == null ? "" : p <= 10 ? "" : p <= 50 ? "warn" : "bad"}" style="width:${p == null ? 0 : Math.max(2, 100 - p)}%"></i></div>
      <small class="muted">${p == null ? "Not ranked yet" : `Top ${p.toFixed(1)}% · ${c.experiments} experiments · strategy v${c.strategy_version || "—"}`}${c.diverged ? ' · <span class="warn">CV≠LB</span>' : ""}</small></div>
  </div>`;
}
function bindCompCards(root) { $$(".comp-card", root).forEach(el => { el.onclick = () => go(`competition/${el.dataset.slug}`); el.onkeydown = e => e.key === "Enter" && el.click(); }); }
function agentRows(f) {
  const by = Object.fromEntries(f.agents.map(a => [a.agent, a]));
  const roles = {Scout: "Finds and scores competitions", Gatekeeper: "Joins, downloads, unblocks", Strategist: "Profiles data, plans, reflects",
                 Solver: "Writes and runs experiments", Critic: "Validates and reviews before submit", Submitter: "Submits within quota, tracks rank"};
  const running = f.competitions.filter(c => c.running).map(c => c.slug);
  return `<div class="table-wrap"><table><thead><tr><th>Agent</th><th>Last action</th><th class="r">When</th></tr></thead><tbody>${Object.keys(roles).map(n => {
    const a = by[n];
    const live = n === "Solver" && running.length;
    return `<tr><td><b>${n}</b><br><small class="muted">${roles[n]}</small></td>
      <td class="wrap">${live ? `<span class="badge ok"><i class="dot on"></i>running on ${running.length}</span> ` : ""}${a ? esc(a.text || a.type) : '<span class="muted">idle</span>'}</td>
      <td class="r muted">${a ? ago(a.ts) : "—"}</td></tr>`; }).join("")}</tbody></table></div>`;
}

// ---------- Competitions list (search, filters, sortable columns)
const CT = Object.assign({sort: "chance", dir: -1, q: "", kind: "", cat: "", joined: false}, store.get("ctable", {}));
const STATE_ORDER = {active: 0, project: 1, needs_rules: 2, done: 3, scouted: 4, stopped: 5, archived: 6, finished: 7};
const COLS = [
  ["name", "Competition", c => c.title.toLowerCase()], ["chance", "Rank chance", c => c.interest_score || 0],
  ["state", "State", c => (c.paused ? 0.5 : 0) + (STATE_ORDER[c.state] ?? 9)], ["rank", "Rank", c => topPct(c) ?? Infinity],
  ["cv", "Best CV", c => c.best_cv ?? -Infinity], ["lb", "Public LB", c => c.lb_public ?? -Infinity],
  ["gap", "LB − CV", c => c.cv_lb_gap == null ? Infinity : Math.abs(c.cv_lb_gap)], ["subs", "Subs today", c => c.submissions_today],
  ["spend", "Spend", c => c.spend_usd || 0], ["deadline", "Deadline", c => +new Date(c.deadline)]];
async function pageCompetitions(v, r) {
  crumbs([["Competitions"]]);
  const f = await loadFleet(), filt = r.slug || "play";
  const groups = {play: c => IN_PLAY.includes(c.state), rec: c => c.state === "scouted" && c.interest_score >= f.config.min_chance,
                  stopped: c => ["stopped", "archived"].includes(c.state), all: c => c.state !== "archived"};
  const kinds = [...new Set(f.competitions.map(c => c.kind))].sort(), cats = [...new Set(f.competitions.map(c => c.category).filter(Boolean))].sort();
  v.innerHTML = `<div class="page-head"><div><h1>Competitions</h1><p>Rank chance is how likely the fleet is to place well (hover it for the reasons). Click a column to sort.</p></div>
    <div class="actions"><form id="addForm" style="display:flex;gap:6px"><input id="addQ" type="text" placeholder="Add: Kaggle URL, slug or name" style="min-width:260px" aria-label="Add a competition">
      <button class="btn primary">Add</button></form>
      <div style="display:flex;gap:6px"><select id="scanDays" aria-label="Scan period">${[7, 14, 30, 60, 90].map(n => `<option value="${n}" ${n === 30 ? "selected" : ""}>last ${n} days</option>`).join("")}</select>
      <button class="btn" id="scanBtn" title="Fetch every competition launched in this period">${icon("search")}Scan Kaggle</button></div></div></div>
  <div id="addResult">${S.addMsg || ""}</div>
  <div class="filters">
    <div class="seg">${[["play", "In play"], ["rec", "Recommended"], ["stopped", "Stopped & archived"], ["all", "All live"]].map(([k, l]) =>
      `<button aria-pressed="${filt === k}" data-f="${k}">${l}</button>`).join("")}</div>
    <input id="ctQ" type="text" placeholder="Search title or slug…" value="${esc(CT.q)}" aria-label="Search competitions" style="min-width:220px">
    <select id="ctKind" aria-label="Kind"><option value="">All kinds</option>${kinds.map(k => `<option ${k === CT.kind ? "selected" : ""}>${esc(k)}</option>`).join("")}</select>
    <select id="ctCat" aria-label="Category"><option value="">All categories</option>${cats.map(k => `<option ${k === CT.cat ? "selected" : ""}>${esc(k)}</option>`).join("")}</select>
    <label class="muted" style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="ctJoined" ${CT.joined ? "checked" : ""}> Joined only</label>
    <button class="btn sm" id="ctClear">Clear</button><span class="muted" id="ctCount"></span></div>
  <div class="card"><div class="table-wrap"><table><thead><tr>${COLS.map(([k, l]) =>
    `<th class="${k === "name" || k === "state" ? "" : "r"} sortable" data-sort="${k}" aria-sort="${CT.sort === k ? (CT.dir > 0 ? "ascending" : "descending") : "none"}">${l}${CT.sort === k ? (CT.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}<th></th></tr></thead>
    <tbody id="ctBody"></tbody></table></div></div>`;
  const draw = () => {
    const q = CT.q.trim().toLowerCase(), col = COLS.find(x => x[0] === CT.sort) || COLS[1];
    const rows = f.competitions.filter(groups[filt] || groups.play).filter(c =>
      (!q || `${c.title} ${c.slug}`.toLowerCase().includes(q)) && (!CT.kind || c.kind === CT.kind) && (!CT.cat || c.category === CT.cat) && (!CT.joined || c.rules_accepted))
      .sort((a, b) => { const x = col[2](a), y = col[2](b); return (x > y ? 1 : x < y ? -1 : 0) * CT.dir; });
    $("#ctCount").textContent = `${rows.length} competition${rows.length === 1 ? "" : "s"}`;
    $("#ctBody").innerHTML = rows.map(c => `<tr class="click" data-slug="${esc(c.slug)}">
      <td class="comp-name"><b>${esc(c.title)}</b><small>${esc(c.slug)} · ${esc(c.kind)} · ${esc(c.category || "")}${c.rules_accepted ? ' · <span class="ok">joined</span>' : ""}</small></td>
      <td class="r" data-tip="${esc(c.why || "not scored yet")}"><div style="display:inline-flex;align-items:center;gap:8px"><div class="meter" style="width:60px"><i style="width:${c.interest_score || 0}%"></i></div><span class="num">${(c.interest_score || 0) | 0}</span></div></td>
      <td>${stateBadge(c)}</td><td class="r num">${rankText(c)}</td><td class="r num">${num(c.best_cv)}</td><td class="r num">${num(c.lb_public)}</td>
      <td class="r num">${c.cv_lb_gap == null ? "—" : `<span class="${c.diverged ? "warn" : "muted"}">${c.cv_lb_gap > 0 ? "+" : ""}${num(c.cv_lb_gap)}</span>`}</td>
      <td class="r num">${c.submissions_today}/${c.quota}</td><td class="r num">${money(c.spend_usd)}</td>
      <td class="r">${c.state === "finished" ? "ended" : daysLeft(c.deadline) + " d"}</td>
      <td class="r">${compActions(c)}</td></tr>`).join("") || `<tr><td colspan="11" class="empty">No competitions match.</td></tr>`;
    bindRows($("#ctBody").closest("table"));
  };
  const save = () => { store.set("ctable", CT); draw(); };
  $$(".seg button", v).forEach(b => b.onclick = () => go(`competitions/${b.dataset.f}`));
  const showMsg = html => { S.addMsg = html ? `<div style="position:relative">${html}<button class="cp-mini" id="addClose" style="position:absolute;top:8px;right:8px" aria-label="Dismiss">×</button></div>` : null;
    $("#addResult").innerHTML = S.addMsg || ""; $("#addClose") && ($("#addClose").onclick = () => showMsg(null)); };
  $("#addClose") && ($("#addClose").onclick = () => showMsg(null));
  $("#addForm").onsubmit = async e => {
    e.preventDefault(); const q = $("#addQ").value.trim(); if (!q) return;
    showMsg(`<div class="banner info">${icon("search")}<div>Looking up <b>${esc(q)}</b> on Kaggle…</div></div>`);
    try {
      const r = await post("/api/competitions/add", {query: q});
      showMsg(`<div class="banner ${r.ended ? "warn" : "info"}">${icon(r.ended ? "alert" : "check")}<div><b><a href="#/competition/${esc(r.slug)}">${esc(r.title)}</a></b>
        · ${esc(r.kind)} · chance ${(r.chance || 0) | 0} · deadline ${when(r.deadline)} · ${r.joined ? "joined" : "not joined yet"}<br>${esc(r.note)}</div></div>`);
      $("#addQ").value = ""; await loadFleet();
    } catch (err) { showMsg(`<div class="banner bad">${icon("alert")}<div>${esc(err.message)}</div></div>`); }
  };
  $("#scanBtn").onclick = async () => {
    const days = $("#scanDays").value; $("#scanBtn").disabled = true;
    showMsg(`<div class="banner info">${icon("search")}<div>Scanning Kaggle for competitions launched in the last ${days} days…</div></div>`);
    try {
      const r = await post(`/api/scout/scan?days=${days}`);
      showMsg(`<div class="banner info">${icon("check")}<div><b>${r.found} live competitions launched in the last ${days} days</b> (${r.new} new to Podium):
        ${r.competitions.map(c => `<a href="#/competition/${esc(c.slug)}">${esc(c.title)}</a> <span class="muted">(${esc(c.kind)}, chance ${(c.interest_score || 0) | 0}${c.rules_accepted ? ", joined" : ""})</span>`).join(" · ") || "none"}</div></div>`);
      await loadFleet();
      if (filt === "all") { render(); } else go("competitions/all");
    } catch (err) { showMsg(`<div class="banner bad">${icon("alert")}<div>${esc(err.message)}</div></div>`); }
    $("#scanBtn") && ($("#scanBtn").disabled = false);
  };
  $$("th.sortable", v).forEach(th => th.onclick = () => { CT.dir = CT.sort === th.dataset.sort ? -CT.dir : (th.dataset.sort === "name" || th.dataset.sort === "rank" || th.dataset.sort === "deadline" ? 1 : -1);
    CT.sort = th.dataset.sort; store.set("ctable", CT); render(); });
  $("#ctQ").oninput = e => { CT.q = e.target.value; save(); };
  $("#ctKind").onchange = e => { CT.kind = e.target.value; save(); };
  $("#ctCat").onchange = e => { CT.cat = e.target.value; save(); };
  $("#ctJoined").onchange = e => { CT.joined = e.target.checked; save(); };
  $("#ctClear").onclick = () => { Object.assign(CT, {q: "", kind: "", cat: "", joined: false}); store.set("ctable", CT); render(); };
  draw();
}
function compActions(c) {
  if (c.state === "active") return `<button class="btn sm" data-act="pause" data-slug="${esc(c.slug)}">${c.paused ? "Resume" : "Pause"}</button>
    <button class="btn sm danger" data-act="stop" data-slug="${esc(c.slug)}">Stop</button>`;
  if (c.state === "project") return `<a class="btn sm" href="https://www.kaggle.com/competitions/${esc(c.slug)}" target="_blank" rel="noopener">Kaggle ${icon("ext")}</a>`;
  if (["stopped", "archived"].includes(c.state)) return `<button class="btn sm" data-act="activate" data-slug="${esc(c.slug)}">Resume</button>`;
  if (c.state === "needs_rules") return `<a class="btn sm" href="https://www.kaggle.com/competitions/${esc(c.slug)}/rules" target="_blank" rel="noopener">Join ${icon("ext")}</a>`;
  if (["scouted", "done"].includes(c.state)) return `<button class="btn sm" data-act="activate" data-slug="${esc(c.slug)}">${c.state === "done" ? "New run" : "Work on it"}</button>`;
  return "";
}
function bindRows(root) {
  $$("tr.click", root).forEach(tr => tr.onclick = async e => {
    const b = e.target.closest("[data-act]");
    if (e.target.closest("a")) return;
    if (b) {
      e.stopPropagation();
      if (b.dataset.act === "stop" && !confirm("Stop this competition? Its running experiment is cancelled and it stays stopped until you resume it.")) return;
      await post(`/api/competitions/${b.dataset.slug}/${b.dataset.act}`);
      toast({activate: "Queued: the Gatekeeper starts it within a minute.", stop: "Stopped. It stays stopped until you resume it.",
             archive: "Archived: hidden from lists; files and history kept."}[b.dataset.act] || "Updated.");
      await loadFleet(); return render();
    }
    go(`competition/${tr.dataset.slug}`);
  });
}

// ---------- Competition detail
const CTABS = [["overview", "Overview"], ["leaderboard", "Leaderboard"], ["strategy", "Strategy"], ["experiments", "Experiments"],
               ["submissions", "Submissions"], ["files", "Files"]];
async function pageCompetition(v, r) {
  const slug = r.slug, tab = r.tab || "overview";
  const d = await api(`/api/competitions/${encodeURIComponent(slug)}`), c = d.competition;
  const fc = S.fleet.competitions.find(x => x.slug === slug) || c;
  crumbs([["Competitions", "#/competitions"], [c.title]]);
  v.innerHTML = `<div class="page-head"><div style="min-width:0"><h1>${esc(c.title)}</h1>
      <p>${stateBadge(fc)} <span class="badge">${esc(c.metric)} · ${c.higher_is_better ? "higher" : "lower"} is better</span>
      <span class="badge">${c.lb_teams || c.team_count || "?"} teams</span> <span class="badge">${daysLeft(c.deadline)} days left</span>
      ${c.state === "project" ? "" : `<span class="badge accent" data-tip="${esc(c.why || "")}">Rank chance ${(c.interest_score || 0) | 0}</span>`}</p></div>
    <div class="actions"><a class="btn" href="https://www.kaggle.com/competitions/${esc(slug)}" target="_blank" rel="noopener">Kaggle ${icon("ext")}</a>
      ${c.state === "active" ? `<button class="btn" id="cPause">${c.paused ? icon("play") + "Resume" : icon("pause") + "Pause"}</button>` : ""}
      ${IN_PLAY.includes(c.state) ? `<button class="btn danger" id="cStop">Stop</button>` : `<button class="btn primary" id="cStart">${["stopped", "archived"].includes(c.state) ? "Resume" : "Work on it"}</button>`}
      ${c.kind !== "other" && c.state !== "finished" ? `<button class="btn primary" id="cRun" title="Start it yourself now (no chance bar, no slot limit), optionally with an idea for the next experiment">${icon("play")}Run now</button>` : ""}
      ${c.state === "finished" ? `<button class="btn" id="cPractice" title="Late submissions: scored, never ranked">Practice (late submissions)</button>` : ""}
      ${c.state !== "archived" ? `<button class="btn" id="cArchive" title="Hide from all lists (files and history kept)">Archive</button>` : ""}</div></div>
  ${c.state === "finished" ? `<div class="banner warn">${icon("alert")}<div><b>This competition has ended</b> (${when(c.deadline)}). Late submissions may still be scored, but there is no ranking, medal or prize. Use <b>Practice</b> only to learn or to test a pipeline.</div></div>` : ""}
  ${c.practice && c.state !== "finished" ? `<div class="banner info">${icon("flask")}<div><b>Practice mode</b>: late submissions, scored but not ranked.</div></div>` : ""}
  ${c.state === "project" ? `<div class="banner info">${icon("brain")}<div><b>Dedicated project.</b> You joined this competition; it is not a file-submission competition, so it is run as a dedicated project: Podium writes its plan, can launch a public baseline on Kaggle, and tracks every submission, score and rank.</div></div>` : ""}
  ${c.state === "stopped" ? `<div class="banner warn">${icon("pause")}<div><b>Stopped.</b> The fleet will not touch it until you click Resume.</div></div>` : ""}
  ${d.gap.diverged ? `<div class="banner warn">${icon("alert")}<div><b>CV and the public leaderboard disagree</b> (gap ${num(d.gap.cv_lb_gap)}). The strategist is told to fix validation before chasing CV gains.</div></div>` : ""}
  ${c.paused && c.note ? `<div class="banner bad">${icon("alert")}<div><b>Paused:</b> ${esc(c.note)}. Raise the cap in the Overview tab to resume.</div></div>` : ""}
  <div class="tabs" role="tablist">${CTABS.map(([k, l]) => `<button class="tab" role="tab" aria-selected="${tab === k}" data-t="${k}">${l}</button>`).join("")}</div>
  <div id="ctab"></div>`;
  $$(".tab", v).forEach(b => b.onclick = () => go(`competition/${slug}/${b.dataset.t}`));
  $("#cPause") && ($("#cPause").onclick = () => post(`/api/competitions/${slug}/pause`).then(() => { toast("Updated."); render(); }));
  $("#cStop") && ($("#cStop").onclick = () => confirm("Stop working on this competition? It stays stopped until you resume it. Results are kept.") && post(`/api/competitions/${slug}/stop`).then(() => { toast("Stopped. It stays stopped until you resume it."); loadFleet(); render(); }));
  $("#cPractice") && ($("#cPractice").onclick = () => confirm("Work on this ended competition through late submissions? They are scored, but never ranked or awarded.") && post(`/api/competitions/${slug}/practice`).then(() => { toast("Practice mode on."); loadFleet(); render(); }));
  $("#cArchive") && ($("#cArchive").onclick = () => confirm("Archive this competition? It disappears from lists and recommendations; files and history are kept, and you can resume it later.") && post(`/api/competitions/${slug}/archive`).then(() => { toast("Archived."); loadFleet(); go("competitions"); }));
  $("#cRun") && ($("#cRun").onclick = async () => {
    const idea = prompt("Optional: what should the next experiment try? (leave empty to just run it now)", "");
    if (idea === null) return;
    try { const r = await post(`/api/competitions/${slug}/run`, {idea}); toast(r.note, 6000); loadFleet(); render(); } catch (x) { toast(String(x.message || x), 6000); }
  });
  $("#cStart") && ($("#cStart").onclick = () => post(`/api/competitions/${slug}/activate`).then(() => { toast("Queued for the Gatekeeper."); render(); }));
  const t = $("#ctab");
  await ({overview: c.state === "project" ? tabProject : tabOverview, leaderboard: tabLeaderboard, strategy: tabStrategy, experiments: tabExperiments,
          submissions: tabSubmissions, files: tabFiles}[tab] || tabOverview)(t, d, slug);
}

async function tabProject(t, d, slug) {
  const c = d.competition, p = await api(`/api/competitions/${slug}/project`), k = p.kaggle || {};
  const st = r => r.status === "complete" ? "ok" : ["error", "cancelacknowledged"].includes(r.status) ? "bad" : "warn";
  t.innerHTML = `<div class="grid g-kpi" style="margin-bottom:16px">
    ${kpi("Public rank", c.lb_rank ? `#${c.lb_rank}` : "—", c.lb_rank ? `of ${c.lb_teams} · top ${pct(c.lb_rank, c.lb_teams)}` : "no scored submission yet", "target")}
    ${kpi("Best public score", num(k.best, 5), `${k.count || 0} Kaggle submission(s)`, "trophy")}
    ${kpi("Latest submission", esc(k.status || "none"), esc(k.latest || ""), "upload")}
    ${kpi("Deadline", `${daysLeft(c.deadline)} days`, when(c.deadline), "activity")}</div>
  <div class="card" style="margin-bottom:16px"><div class="card-h"><h3>Project actions</h3><span class="sub mono">${esc(p.dir)}</span></div><div class="card-b">
    <p class="muted">Public baseline: forks the best-scoring public notebook as a <b>private</b> notebook under your account, runs it on Kaggle (your GPU quota) and submits it when it finishes (1 submission). It puts you on the leaderboard; the plan says how to improve from there.</p>
    <div class="actions"><button class="btn primary" id="pBase">${icon("play")}Run public baseline</button>
    <button class="btn" id="pPlan">${icon("brain")}${p.plan ? "Rewrite plan" : "Write plan now"}</button></div></div></div>
  <div class="grid g-2" style="margin-bottom:16px">
    <div class="card"><div class="card-h"><h3>Kaggle runs</h3></div><div class="card-b">${p.runs.length ? `<div class="table-wrap"><table><thead><tr><th>Run</th><th>From</th><th>Status</th></tr></thead><tbody>${p.runs.map(r => `<tr><td class="mono">${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.tag)}</a>` : esc(r.tag)}</td><td>${esc(r.agent)}</td><td><span class="badge ${st(r)}">${esc(r.status || "?")}</span> ${esc(r.failure || "")}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">No run yet.</p>`}</div></div>
    <div class="card"><div class="card-h"><h3>Top public notebooks</h3></div><div class="card-b">${p.notebooks.length ? `<ul>${p.notebooks.map(n => `<li><a href="https://www.kaggle.com/code/${esc(n.ref)}" target="_blank" rel="noopener">${esc(n.title)}</a> <span class="muted">${n.votes ?? "?"} votes · ${n.by === "voteCount" ? "most voted" : "best score"}</span></li>`).join("")}</ul>` : `<p class="muted">Fetched with the plan.</p>`}</div></div></div>
  <div class="card"><div class="card-h"><h3>Plan</h3></div><div class="card-b md">${p.plan ? md(p.plan) : `<p class="muted">The Strategist writes the plan automatically within a few minutes of joining (official pages, top notebooks, web research).</p>`}</div></div>`;
  $("#pBase").onclick = async e => { if (!confirm("Fork the best public notebook privately, run it on Kaggle and submit it when it finishes (uses GPU quota and 1 submission)?")) return;
    e.target.disabled = true; try { const r = await post(`/api/competitions/${slug}/project/baseline`); toast(r.error ? `Kaggle: ${r.error}` : `Launched ${r.ref}.`); } catch (x) { toast(String(x.message || x)); } render(); };
  $("#pPlan").onclick = async e => { e.target.disabled = true; e.target.textContent = "Writing the plan (a few minutes)…";
    try { await post(`/api/competitions/${slug}/project/bootstrap`); toast("Plan written."); } catch (x) { toast(String(x.message || x)); } render(); };
}

async function tabOverview(t, d, slug) {
  const c = d.competition, b = d.budget;
  const rankEvents = S.events.filter(e => e.type === "rank.updated" && e.competition === slug);
  const scored = d.submissions.filter(s => s.lb_public != null);
  const bestLb = scored.length ? scored.map(s => s.lb_public).reduce((a, x) => c.higher_is_better ? Math.max(a, x) : Math.min(a, x)) : null;
  t.innerHTML = `<div class="grid g-kpi" style="margin-bottom:16px">
    ${kpi("Public rank", c.lb_rank ? `#${c.lb_rank}` : "—", c.lb_rank ? `of ${c.lb_teams} · top ${pct(c.lb_rank, c.lb_teams)}` : "not ranked yet", "target")}
    ${kpi("Best public LB", num(bestLb, 5), `top-10% cutoff ${num(c.lb_p10, 5)}`, "trophy")}
    ${(() => { const ok = d.experiments.filter(e => e.cv_mean != null && e.critic_flags === "[]").map(e => e.cv_mean);
      const bestCv = ok.length ? (c.higher_is_better ? Math.max(...ok) : Math.min(...ok)) : null;
      return kpi("Best CV", num(bestCv, 5), `${d.experiments.length} experiments`, "flask"); })()}
    ${kpi("Submissions today", `${b.subs_today}/${b.subs_quota}`, `${d.submissions.length} in total`, "upload")}
    ${kpi("LLM spend", money(b.usd), `cap ${money(b.cap_usd)}`, "dollar")}
    ${kpi("Compute", `${num(b.hours, 2)} h`, `cap ${num(b.cap_hours, 0)} h`, "activity")}</div>
  <div class="grid g-2" style="margin-bottom:16px">
    <div class="card"><div class="card-h"><h3>CV per experiment</h3><span class="sub">best so far as a step line</span></div><div class="card-b">${progressChart(d.experiments, c.higher_is_better)}</div></div>
    <div class="card"><div class="card-h"><h3>CV vs public leaderboard</h3><span class="sub">off-diagonal = validation is lying</span></div><div class="card-b">${scatter(scored.map(s => ({x: s.cv_mean, y: s.lb_public, label: s.experiment_id})))}</div></div>
  </div>
  <div class="grid g-2">
    <div class="card"><div class="card-h"><h3>Rank history</h3></div><div class="card-b">${rankChart(rankEvents)}</div></div>
    <div class="card"><div class="card-h"><h3>Budget & controls</h3></div><div class="card-b">
      ${meterRow("LLM spend", b.usd, b.cap_usd, money)}${meterRow("Compute hours", b.hours, b.cap_hours, x => num(x, 2) + " h")}${meterRow("Submissions today", b.subs_today, b.subs_quota, x => x ?? 0)}
      <form class="filters" id="capForm" style="margin:12px 0 0"><input name="cap_usd" type="number" min="0" step="1" placeholder="LLM cap $" aria-label="LLM cap in dollars" style="width:120px">
      <input name="cap_hours" type="number" min="0" step="1" placeholder="Compute cap h" aria-label="Compute cap in hours" style="width:130px"><button class="btn">Set caps</button></form></div></div>
  </div>`;
  $("#capForm").onsubmit = async e => {
    e.preventDefault(); const fd = new FormData(e.target), body = {competition: slug};
    for (const k of ["cap_usd", "cap_hours"]) if (fd.get(k)) body[k] = Number(fd.get(k));
    await put("/api/budgets", body); toast("Caps updated."); render();
  };
}
function meterRow(label, used, cap, fmt) {
  const p = cap ? Math.min(100, 100 * used / cap) : 0;
  return `<div style="margin-bottom:12px"><div style="display:flex;justify-content:space-between"><span>${label}</span><span class="num muted">${fmt(used)} / ${fmt(cap)}</span></div>
    <div class="meter" role="progressbar" aria-valuenow="${p | 0}" aria-valuemin="0" aria-valuemax="100"><i class="${p >= 100 ? "bad" : p >= 80 ? "warn" : ""}" style="width:${p}%"></i></div></div>`;
}

async function tabLeaderboard(t, d, slug) {
  t.innerHTML = `<div class="empty">Loading leaderboard…</div>`;
  const lb = await api(`/api/competitions/${encodeURIComponent(slug)}/leaderboard`);
  if (!lb.teams) { t.innerHTML = `<div class="empty">Leaderboard not available yet.</div>`; return; }
  const me = lb.me, rowsHtml = rows => rows.map(r => `<tr class="${me && r.Rank === me.Rank ? "me" : ""}"><td class="num">${r.Rank}</td>
    <td>${esc(r.TeamName)}</td><td class="r num">${num(r.Score, 5)}</td><td class="r num">${r.SubmissionCount ?? ""}</td><td class="r muted">${esc(String(r.LastSubmissionDate || "").slice(0, 16))}</td></tr>`).join("");
  const head = `<thead><tr><th>Rank</th><th>Team</th><th class="r">Score</th><th class="r">Entries</th><th class="r">Last</th></tr></thead>`;
  t.innerHTML = `<div class="grid g-kpi" style="margin-bottom:16px">
    ${kpi("Your position", me ? `#${me.Rank}` : "not on board", me ? `top ${pct(me.Rank, lb.teams)} of ${lb.teams}` : "no scored submission under your account yet", "target")}
    ${kpi("Your score", me ? num(me.Score, 5) : "—", me ? `${me.SubmissionCount} entries` : "", "trophy")}
    ${kpi("Top-10% cutoff", num(lb.p10, 5), me ? `gap ${num(Math.abs(me.Score - lb.p10), 5)}` : "", "up")}
    ${kpi("Leader", num(lb.top[0]?.Score, 5), esc(lb.top[0]?.TeamName || ""), "trophy")}</div>
  <div class="card" style="margin-bottom:16px"><div class="card-h"><h3>Score distribution</h3><span class="sub">${lb.teams} teams · updated ${ago(lb.updated)}</span></div>
    <div class="card-b">${histogram(lb.histogram, me?.Score, lb.p10)}</div></div>
  <div class="grid g-2"><div class="card"><div class="card-h"><h3>Around you</h3></div><div class="card-b flush table-wrap"><table>${head}<tbody>${rowsHtml(lb.around) || `<tr><td colspan="5" class="empty">You are not on the board yet.</td></tr>`}</tbody></table></div></div>
    <div class="card"><div class="card-h"><h3>Top 20</h3></div><div class="card-b flush table-wrap"><table>${head}<tbody>${rowsHtml(lb.top)}</tbody></table></div></div></div>`;
}

async function tabStrategy(t, d, slug) {
  const s = await api(`/api/competitions/${encodeURIComponent(slug)}/strategy`);
  if (!s.versions.length) { t.innerHTML = `<div class="empty">No strategy yet: the Strategist profiles the data and writes one before the next experiment.</div>`; return; }
  const cur = s.versions[0], conv = s.conversation || {};
  t.innerHTML = `<div class="grid g-kpi" style="margin-bottom:16px">
    ${kpi("Strategy", `v${cur.version}`, `${esc(s.models.strategy)} · ${ago(cur.at)}`, "brain")}
    ${kpi("Solver conversation", conv.turns ? `turn ${conv.turns}` : "new", conv.turns ? `${esc(s.models.experiment)} · resets after ${s.conversation_max_turns}` : "starts with the next experiment", "flask")}
    ${kpi("Fleet memory", `${(s.memory || "").split("\n").filter(Boolean).length} lessons`, "carried across competitions", "target")}</div>
  <div class="grid g-main"><div class="card"><div class="card-h"><h3>Strategy v${cur.version}</h3><span class="sub">${ago(cur.at)} · followed by every experiment</span>
      <div class="right"><select id="ver" aria-label="Version">${s.versions.map(v => `<option value="${v.version}">v${v.version} · ${when(v.at)}</option>`).join("")}</select></div></div>
      <div class="card-b md" id="planMd">${md(cur.markdown)}</div></div>
    <div class="stack"><div class="card"><div class="card-h"><h3>Fleet memory</h3><span class="sub">transferable lessons, fed to every new plan</span></div>
      <div class="card-b md">${s.memory ? md(s.memory) : '<p class="muted">Empty: lessons are added after each reflection.</p>'}</div></div>
    <div class="card"><div class="card-h"><h3>Data profile</h3><span class="sub">computed in the sandbox</span></div><div class="card-b md">${md(s.profile)}</div></div></div></div>`;
  $("#ver").onchange = e => { const v = s.versions.find(x => x.version == e.target.value); $("#planMd").innerHTML = md(v.markdown); };
}

async function tabExperiments(t, d) {
  const c = d.competition;
  t.innerHTML = `<div class="card"><div class="card-h"><h3>Experiments</h3><span class="sub">${d.experiments.length} runs · newest first</span></div>
  <div class="card-b flush table-wrap"><table><thead><tr><th>#</th><th>Idea</th><th class="r">CV</th><th class="r">± std</th><th>Verdict</th><th class="r">Time</th><th class="r">LLM</th><th></th></tr></thead><tbody>
  ${d.experiments.map((e, i) => { const fl = JSON.parse(e.critic_flags || "[]"), rv = e.review ? JSON.parse(e.review) : null;
    const verdict = e.cv_mean == null ? `<span class="badge bad">failed</span>` : fl.length ? `<span class="badge bad" data-tip="${esc(fl.join(" · "))}">rejected</span>`
      : rv ? `<span class="badge ok" data-tip="${esc((rv.issues || []).join(" · "))}">reviewed ✓</span>` : `<span class="badge">valid</span>`;
    return `<tr><td class="num muted">${i + 1}</td><td class="wrap">${esc(e.summary)}${e.parent_id ? `<br><small class="muted">from ${esc(e.parent_id)}</small>` : ""}</td>
    <td class="r num">${num(e.cv_mean, 5)}</td><td class="r num muted">${num(e.cv_std, 5)}</td><td>${verdict}</td><td class="r num">${num(e.seconds / 60, 1)}m</td>
    <td class="r num">${money(e.cost_usd)}</td><td class="r"><button class="btn sm" data-code="${e.id}">Code</button> <button class="btn sm" data-log="${e.id}">Log</button></td></tr>`; }).reverse().join("")
    || `<tr><td colspan="8" class="empty">No experiments yet.</td></tr>`}</tbody></table></div></div>`;
  $$("[data-code]", t).forEach(b => b.onclick = () => viewFile(c.slug, `experiments/${b.dataset.code}/main.py`));
  $$("[data-log]", t).forEach(b => b.onclick = () => viewFile(c.slug, `experiments/${b.dataset.log}/log.txt`));
}

async function tabSubmissions(t, d, slug) {
  t.innerHTML = `<div class="empty">Loading your Kaggle submission history…</div>`;
  let remote = [];
  try { remote = await api(`/api/competitions/${encodeURIComponent(slug)}/kaggle-submissions`); } catch (e) { toast("Kaggle history unavailable: " + e.message); }
  const local = Object.fromEntries(d.submissions.map(s => [s.experiment_id, s]));
  t.innerHTML = `${d.final_manual ? `<div class="banner warn">${icon("alert")}<div style="flex:1"><b>Manual final picks.</b> You overrode the fleet's choice; it will not change them.</div><button class="btn sm" id="finAuto">Reset to automatic</button></div>`
    : `<div class="banner info">${icon("check")}<div>Final picks: the best CV plus the best public score as a hedge (★). Kaggle has no API for selecting finals; if you never select, Kaggle uses your best public scores. Click ★ to override.</div></div>`}
  <div class="card"><div class="card-h"><h3>Submission history on Kaggle</h3><span class="sub">${remote.length} shown, including manual ones</span></div>
  <div class="card-b flush table-wrap"><table><thead><tr><th>When</th><th>Description</th><th>Status</th><th class="r">Public</th><th class="r">Private</th><th class="r">CV</th><th>Why submitted</th><th>Final</th><th></th></tr></thead><tbody>
  ${remote.map(s => { const l = local[s.experiment_id];
    return `<tr><td>${when(s.date)}</td><td class="wrap mono">${esc(s.description || s.fileName)}</td>
    <td><span class="badge ${/COMPLETE/i.test(s.status) ? "ok" : /ERROR/i.test(s.status) ? "bad" : "info"}">${esc(String(s.status || "").toLowerCase())}</span>${s.errorDescription ? `<br><small class="bad">${esc(s.errorDescription)}</small>` : ""}</td>
    <td class="r num">${s.publicScore ?? "—"}</td><td class="r num">${s.privateScore ?? "—"}</td><td class="r num">${l ? num(l.cv_mean, 5) : "—"}</td>
    <td class="wrap">${l ? esc(l.reason) : '<span class="muted">manual / other</span>'}</td>
    <td>${l ? `<button class="btn sm ${l.is_final_pick ? "primary" : ""}" data-final="${l.id}">${l.is_final_pick ? "★" : "☆"}</button>` : ""}</td>
    <td>${s.experiment_id ? `<button class="btn sm" data-code="${esc(s.experiment_id)}">Code</button>` : ""}</td></tr>`; }).join("")
    || `<tr><td colspan="9" class="empty">No submissions on Kaggle yet.</td></tr>`}</tbody></table></div></div>`;
  $$("[data-final]", t).forEach(b => b.onclick = () => post(`/api/submissions/${b.dataset.final}/final`).then(() => render()));
  $("#finAuto") && ($("#finAuto").onclick = () => post(`/api/competitions/${encodeURIComponent(slug)}/finals/auto`).then(() => { toast("Final picks are automatic again."); render(); }));
  $$("[data-code]", t).forEach(b => b.onclick = () => viewFile(slug, `experiments/${b.dataset.code}/main.py`));
}

async function tabFiles(t, d, slug) {
  const f = await api(`/api/competitions/${encodeURIComponent(slug)}/files`);
  const list = (items, empty) => items.length ? `<table><tbody>${items.map(x => `<tr><td>${icon("file")} <span class="mono">${esc(x.name)}</span></td>
    <td class="r muted num">${bytes(x.size)}</td><td class="r"><button class="btn sm" data-view="${esc(x.path)}">View</button>
    <a class="btn sm" href="/api/files/${encodeURIComponent(slug)}/${x.path.split("/").map(encodeURIComponent).join("/")}" download>Download</a></td></tr>`).join("")}</tbody></table>` : `<div class="empty">${empty}</div>`;
  t.innerHTML = `<p class="muted mono" style="margin-top:0">${esc(f.root)}</p>
  <div class="grid g-2" style="margin-bottom:16px"><div class="card"><div class="card-h">${icon("folder")}<h3>Submitted files</h3><span class="sub">exact files sent to Kaggle</span></div><div class="card-b flush table-wrap">${list(f.submissions, "Nothing submitted yet.")}</div></div>
    <div class="card"><div class="card-h">${icon("folder")}<h3>Competition data</h3></div><div class="card-b flush table-wrap">${list(f.data, "Not downloaded.")}</div></div></div>
  <div class="card"><div class="card-h">${icon("folder")}<h3>Experiments</h3><span class="sub">code, logs, predictions, metadata</span></div><div class="card-b flush">
    ${f.experiments.map(e => `<details style="border-bottom:1px solid var(--line-2)"><summary style="padding:10px 16px;cursor:pointer" class="mono">${esc(e.id)} <span class="muted">· ${e.files.length} files</span></summary>
      <div class="table-wrap">${list(e.files, "")}</div></details>`).join("") || `<div class="empty">No experiments yet.</div>`}</div></div>`;
  $$("[data-view]", t).forEach(b => b.onclick = () => viewFile(slug, b.dataset.view));
}
async function viewFile(slug, path) {
  $("#modalTitle").textContent = path; $("#modalBody").innerHTML = `<div class="empty">Loading…</div>`; $("#modal").hidden = false;
  try {
    const text = await api(`/api/files/${encodeURIComponent(slug)}/${path.split("/").map(encodeURIComponent).join("/")}?head=400`);
    $("#modalBody").innerHTML = /\.md$/.test(path) ? `<div class="md" style="padding:16px">${md(text)}</div>` : `<pre class="code">${esc(text)}</pre>`;
  } catch (e) { $("#modalBody").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

// ---------- Submissions (all)
async function pageSubmissions(v) {
  crumbs([["Submissions"]]);
  const subs = await api("/api/submissions");
  v.innerHTML = `<div class="page-head"><div><h1>Submissions</h1><p>Every file the fleet sent to Kaggle, why, and how it scored. Open a competition for its full Kaggle history.</p></div></div>
  <div class="card"><div class="table-wrap"><table><thead><tr><th>When</th><th>Competition</th><th>Experiment</th><th class="r">CV</th><th class="r">Public LB</th><th class="r">LB − CV</th><th>Reason</th><th>Final</th></tr></thead><tbody>
  ${subs.map(s => `<tr class="click" data-slug="${esc(s.competition_slug)}"><td>${when(s.created_at)}</td><td>${esc(s.competition_slug)}</td>
    <td class="wrap">${esc(s.summary).slice(0, 120)}</td><td class="r num">${num(s.cv_mean, 5)}</td>
    <td class="r num">${s.lb_public == null ? '<span class="badge info">scoring</span>' : num(s.lb_public, 5)}</td>
    <td class="r num">${s.lb_public == null ? "—" : num(s.lb_public - s.cv_mean, 4)}</td><td class="wrap">${esc(s.reason)}</td><td>${s.is_final_pick ? "★" : ""}</td></tr>`).join("")
    || `<tr><td colspan="8" class="empty">No submissions yet.</td></tr>`}</tbody></table></div></div>`;
  $$("tr.click", v).forEach(tr => tr.onclick = () => go(`competition/${tr.dataset.slug}/submissions`));
}

// ---------- Activity (live trace)
const actFilter = {agent: "", q: "", problems: false};
function pageActivity(v) {
  crumbs([["Activity"]]);
  const agents = [...new Set(S.events.map(e => e.agent))].sort();
  v.innerHTML = `<div class="page-head"><div><h1>Activity</h1><p>Every agent step, live. ${S.events.length} events loaded.</p></div></div>
  <div class="filters"><select id="fa" aria-label="Agent"><option value="">All agents</option>${agents.map(a => `<option ${a === actFilter.agent ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>
    <input id="fq" type="text" placeholder="Filter text or competition" value="${esc(actFilter.q)}" style="min-width:220px" aria-label="Filter">
    <label class="muted" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="fp" ${actFilter.problems ? "checked" : ""}> Problems only</label></div>
  <div class="card"><div class="feed" id="actFeed"></div></div>`;
  const draw = () => $("#actFeed").innerHTML = S.events.filter(actMatch).slice(-300).reverse().map(feedItem).join("") || `<div class="empty">No matching events.</div>`;
  $("#fa").onchange = e => { actFilter.agent = e.target.value; draw(); };
  $("#fq").oninput = e => { actFilter.q = e.target.value.toLowerCase(); draw(); };
  $("#fp").onchange = e => { actFilter.problems = e.target.checked; draw(); };
  draw();
}
const actMatch = e => (!actFilter.agent || e.agent === actFilter.agent) && (!actFilter.problems || /error|flagged|diverged|fail/.test(e.type + (e.text || ""))) &&
  (!actFilter.q || `${e.text} ${e.competition} ${e.type}`.toLowerCase().includes(actFilter.q));

// ---------- Decisions
async function pageDecisions(v) {
  crumbs([["Decisions"]]);
  const [tasks, f] = await Promise.all([api("/api/human-tasks"), loadFleet()]);
  const open = tasks.filter(t => t.status === "open"), done = tasks.filter(t => t.status !== "open").slice(0, 10);
  const recommended = f.competitions.filter(c => c.state === "scouted" && c.interest_score >= f.config.min_chance);
  const plateau = f.competitions.filter(c => c.state === "done");
  v.innerHTML = `<div class="page-head"><div><h1>Decisions</h1><p>The calls only you can make. Everything else runs on its own.</p></div></div>
  <div class="grid g-main"><div class="stack">
    <div class="card"><div class="card-h"><h3>Waiting on you</h3><span class="badge ${open.length ? "warn" : "ok"}">${open.length}</span></div>
      <div class="feed">${open.map(t => `<div class="feed-item"><div class="ic sev-warn">${icon(t.kind === "accept_rules" ? "trophy" : "alert")}</div><div class="txt">
        <p><b>${t.kind === "accept_rules" ? "Join competition" : "Approve"}</b> · ${esc(t.competition_slug)}</p><p class="muted">${esc(t.detail)}</p>
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">${t.url ? `<a class="btn sm" href="${esc(t.url)}" target="_blank" rel="noopener">Open on Kaggle ${icon("ext")}</a>` : ""}
        <button class="btn sm primary" data-done="${t.id}">${t.kind === "accept_rules" ? "I joined, start it" : "Done"}</button>
        ${t.kind === "accept_rules" ? `<button class="btn sm" data-skip="${esc(t.competition_slug)}" data-id="${t.id}">Not interested</button>` : ""}</div>
        <small>${ago(t.created_at)}</small></div></div>`).join("") || `<div class="empty">Nothing waiting. The fleet is autonomous.</div>`}</div></div>
    <div class="card"><div class="card-h"><h3>Plateaued competitions</h3><span class="sub">experiment budget used; start a new run to keep climbing</span></div>
      <div class="card-b flush table-wrap"><table><tbody>${plateau.map(c => `<tr class="click" data-slug="${esc(c.slug)}"><td class="comp-name"><b>${esc(c.title)}</b><small>rank ${c.lb_rank ? `#${c.lb_rank}/${c.lb_teams}` : "—"} · ${c.experiments} experiments</small></td>
        <td class="r">${compActions(c)}</td></tr>`).join("") || `<tr><td class="empty">None.</td></tr>`}</tbody></table></div></div>
  </div><div class="stack">
    <div class="card"><div class="card-h"><h3>Recommended to join</h3></div><div class="card-b flush table-wrap"><table><tbody>
      ${recommended.map(c => `<tr class="click" data-slug="${esc(c.slug)}"><td class="comp-name"><b>${esc(c.title)}</b><small>chance ${(c.interest_score | 0)} · ${daysLeft(c.deadline)} days</small></td>
        <td class="r"><a class="btn sm" href="https://www.kaggle.com/competitions/${esc(c.slug)}/rules" target="_blank" rel="noopener">Join ${icon("ext")}</a></td></tr>`).join("") || `<tr><td class="empty">No new recommendations.</td></tr>`}</tbody></table></div></div>
    <div class="card"><div class="card-h"><h3>Recently resolved</h3></div><div class="feed">${done.map(t => `<div class="feed-item"><div class="ic sev-ok">${icon("check")}</div><div class="txt"><p>${esc(t.competition_slug)}: ${esc(t.kind.replace("_", " "))}</p><small>${ago(t.created_at)}</small></div></div>`).join("") || `<div class="empty">—</div>`}</div></div>
  </div></div>`;
  $$("[data-done]", v).forEach(b => b.onclick = async () => { await post(`/api/human-tasks/${b.dataset.done}/done`); toast("Thanks. The fleet picks it up within a minute."); await loadFleet(); render(); });
  $$("[data-skip]", v).forEach(b => b.onclick = async () => { await post(`/api/competitions/${b.dataset.skip}/stop`); await post(`/api/human-tasks/${b.dataset.id}/done`); toast("Dismissed."); await loadFleet(); render(); });
  bindRows(v);
}

// ---------- Alerts
async function pageAlerts(v) {
  crumbs([["Alerts"]]);
  const alerts = await api("/api/alerts?limit=200");
  v.innerHTML = `<div class="page-head"><div><h1>Alerts</h1><p>Scores, rank moves, rejections, divergences, errors and requests. Also sent by email when SMTP is set.</p></div>
    <div class="actions"><button class="btn" id="markRead">Mark all read</button></div></div>
  <div class="card"><div class="feed">${alerts.map(e => feedItem(e).replace('class="feed-item"', `class="feed-item" style="${e.id > S.alertSeen ? "background:var(--surface-2)" : ""}"`)).join("") || `<div class="empty">No alerts.</div>`}</div></div>`;
  $("#markRead").onclick = () => { markAlertsRead(); render(); };
}
function markAlertsRead() { S.alertSeen = Math.max(S.alertSeen, ...S.events.map(e => e.id), 0); store.set("alertSeen", S.alertSeen); updateBell(); renderNav(); }

// ---------- Claude plan usage (real utilization reported by Claude Code)
const until = ts => { if (!ts) return "—"; const s = ts - Date.now() / 1000; if (s <= 0) return "now";
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : h ? `${h}h ${m}m` : `${m}m`; };
function planCard(pl, compact) {
  const w = pl.windows || {}, rows = [["five_hour", "Current 5-hour session"], ["seven_day", "This week (7 days)"]];
  if (!Object.keys(w).length) return `<div class="empty">No plan data yet: it appears after the first Claude Code call.</div>`;
  return rows.filter(([k]) => w[k]).map(([k, label]) => { const u = w[k].utilization ?? 0, pctUsed = Math.round(u * 100);
    const reserveMark = k === "seven_day" ? (1 - (pl.reserve ?? 0.15)) * 100 : null;
    return `<div style="margin-bottom:14px"><div style="display:flex;justify-content:space-between;gap:8px"><b>${label}</b>
      <span class="num">${pctUsed}% used · <b>${100 - pctUsed}% left</b></span></div>
      <div class="meter" style="height:10px;position:relative"><i class="${u >= 0.9 ? "bad" : u >= 0.7 ? "warn" : ""}" style="width:${pctUsed}%"></i>
      ${reserveMark != null ? `<span title="fleet stops here (your reserve)" style="position:absolute;top:-3px;bottom:-3px;left:${reserveMark}%;width:2px;background:var(--ink)"></span>` : ""}</div>
      <small class="muted">resets in ${until(w[k].resets_at)}${k === "seven_day" ? ` · fleet pauses at ${Math.round(reserveMark)}% (keeps ${Math.round((pl.reserve ?? 0.15) * 100)}% for you)` : ""}</small></div>`; }).join("")
    + (compact ? "" : `<small class="muted">Status: <b>${esc(pl.status || "?")}</b>${pl.holding ? ' · <span class="warn">fleet is holding AI work</span>' : ""} · updated ${pl.at ? ago(new Date(pl.at * 1000)) : "—"}</small>`);
}

// ---------- Ongoing: everything in progress and what it is waiting for
async function pageOngoing(v) {
  crumbs([["Ongoing"]]);
  const d = await api("/api/ongoing");
  const since = ts => ts ? `<span class="muted" data-since="${esc(ts)}">${ago(ts)}</span>` : "";
  const item = (ic, sev, title, what, extra = "") => `<div class="feed-item"><div class="ic sev-${sev}">${icon(ic)}</div><div class="txt">
    <p>${title}</p><small>${what}</small>${extra}</div></div>`;
  const compLink = (slug, title) => slug ? `<a href="#/competition/${esc(slug)}">${esc(title || slug)}</a>` : "Fleet";
  const ms = m => ({done: ["ok", "✓ done"], current: ["accent", "● now"], overdue: ["bad", "! overdue"], upcoming: ["", "upcoming"]}[m.state]);
  v.innerHTML = `<div class="page-head"><div><h1>Ongoing</h1><p>What is running right now, what it is waiting for, and what comes next. Refreshes live.</p></div>
    <div class="actions"><button class="btn" id="ogRefresh">Refresh</button></div></div>
  ${d.fleet.paused ? `<div class="banner bad">${icon("pause")}<div><b>The fleet is paused.</b> Nothing new starts until you resume it (header button).</div></div>` : ""}
  ${d.fleet.plan_holding ? `<div class="banner warn">${icon("alert")}<div><b>Holding AI work to protect your Claude plan.</b> It resumes automatically when the window resets.</div></div>` : ""}
  <div class="grid g-main" style="margin-bottom:16px"><div class="stack">
    <div class="card"><div class="card-h"><h3>Running now</h3><span class="badge ${d.running.length ? "ok" : ""}">${d.running.length}</span></div><div class="feed">
      ${d.running.map(r => item(r.kind === "ai" ? "brain" : "flask", "ok", `<b>${compLink(r.competition, r.title)}</b>`,
        `${esc(r.what)} · ${esc(r.where || "")} · started ${since(r.since)}`)).join("") || `<div class="empty">Nothing is running right now.</div>`}</div></div>
    <div class="card"><div class="card-h"><h3>Waiting on Kaggle</h3><span class="badge ${d.kaggle.length ? "info" : ""}">${d.kaggle.length}</span></div><div class="feed">
      ${d.kaggle.map(r => item(r.kind === "notebook" ? "activity" : "upload", "info", `<b>${r.kind === "notebook" ? esc(r.title) : compLink(r.competition, r.title)}</b>`,
        `${esc(r.what)}${r.since ? ` · ${since(r.since)}` : ""}${r.url ? ` · <a href="${esc(r.url)}" target="_blank" rel="noopener">open on Kaggle</a>` : ""}`)).join("") || `<div class="empty">Nothing waiting on Kaggle.</div>`}</div></div>
    <div class="card"><div class="card-h"><h3>Waiting on you</h3><span class="badge ${d.you.length ? "warn" : "ok"}">${d.you.length}</span></div><div class="feed">
      ${d.you.map(r => item(r.kind === "budget" ? "dollar" : r.kind === "paused" ? "pause" : "check", "warn", `<b>${compLink(r.competition, r.title)}</b>`,
        esc(r.what), r.url ? `<div><a class="btn sm" href="${esc(r.url)}" target="_blank" rel="noopener">Open on Kaggle ${icon("ext")}</a></div>` : "")).join("") || `<div class="empty">Nothing needs you.</div>`}</div></div>
  </div><div class="stack">
    <div class="card"><div class="card-h"><h3>Claude plan</h3><div class="right"><a class="btn sm" href="#/spend">Details</a></div></div><div class="card-b">${planCard({...d.fleet.plan, reserve: d.fleet.reserve, holding: d.fleet.plan_holding}, true)}</div></div>
    <div class="card"><div class="card-h"><h3>Coming up</h3></div><div class="feed">
      ${d.next.map(n => item("target", n.state === "overdue" ? "bad" : "accent", esc(n.what), n.at ? `${new Date(n.at) > new Date() ? "in " + until(+new Date(n.at) / 1000) : "due " + ago(n.at)} · ${when(n.at)}` : "")).join("")}</div></div>
  </div></div>
  ${d.projects.map(pj => `<div class="card" style="margin-bottom:16px"><div class="card-h">${icon("brain")}<h3>Project: ${esc(pj.title)}</h3><span class="sub mono">${esc(pj.path)}</span></div>
    <div class="card-b"><div class="grid g-2">
      <div><h4 style="margin:0 0 8px">Timeline</h4><div class="table-wrap"><table><tbody>${pj.milestones.map(m => { const [k, l] = ms(m);
        return `<tr><td class="num" style="white-space:nowrap">${esc(m.dates)}</td><td class="wrap">${esc(m.text)}</td><td><span class="badge ${k}">${l}</span></td></tr>`; }).join("")}</tbody></table></div></div>
      <div class="stack"><div><h4 style="margin:0 0 8px">Kaggle</h4>${pj.competitions.map(c => `<div style="margin-bottom:8px"><a href="#/competition/${esc(c.slug)}"><b>${esc(c.title)}</b></a><br>
        <small class="muted">${c.kaggle?.count ? `${c.kaggle.count} submission(s) · latest <b>${esc(c.kaggle.status)}</b>${c.kaggle.best != null ? ` · best public ${c.kaggle.best}` : ""}` : "no submission yet"} · deadline ${when(c.deadline)}</small></div>`).join("") || `<span class="muted">—</span>`}</div>
      <div><h4 style="margin:0 0 8px">Development runs (official harness on Kaggle GPUs)</h4>${pj.runs.map(r => `<div style="margin-bottom:8px"><b>${esc(r.tag)}</b> · ${esc(r.agent)} on ${r.tasks} tasks ·
        <span class="badge ${r.status === "complete" ? "ok" : r.status === "error" ? "bad" : "info"}">${esc(r.status || "?")}</span>
        ${r.summary ? ` · <b>${r.summary.resolved}/${r.summary.tasks} resolved (${(100 * r.summary.rate).toFixed(0)}%)</b>` : ""}
        ${r.url ? ` · <a href="${esc(r.url)}" target="_blank" rel="noopener">notebook</a>` : ""}${r.failure ? `<br><small class="bad">${esc(r.failure)}</small>` : ""}</div>`).join("") || `<span class="muted">none yet</span>`}</div></div>
    </div></div></div>`).join("")}`;
  $("#ogRefresh").onclick = () => render();
}

// ---------- Spend
const BILLING = {plan: ["Covered by plan", "ok", "Runs on your Claude Code subscription: no per-call bill, but it uses your plan's usage limits. Amounts are the equivalent API price."],
                 api: ["Billed per call", "warn", "Charged to the provider account behind the API key."],
                 local: ["Local, free", "info", "Runs on your own machine (Ollama)."]};
async function pageSpend(v, r) {
  crumbs([["Spend"]]);
  const days = Number(r.slug) || 30;
  const [d, ai, plan] = await Promise.all([api(`/api/spend/detail?days=${days}`), api("/api/ai"), api("/api/ai/plan")]);
  const t = d.totals, cc = ai.claude_code || {};
  const maxDay = Math.max(...d.daily.map(x => x.usd), 0.01);
  const aggTable = (rows, label) => `<table><thead><tr><th>${label}</th><th class="r">Calls</th><th class="r">Tokens in / out / cache</th><th class="r">Time</th><th class="r">Cost</th></tr></thead><tbody>
    ${rows.map(x => `<tr><td class="mono">${esc(x.k || "—")}${x.errors ? ` <span class="badge bad">${x.errors} failed</span>` : ""}</td><td class="r num">${x.calls}</td>
      <td class="r num muted">${(x.inp || 0).toLocaleString()} / ${(x.outp || 0).toLocaleString()} / ${(x.cache || 0).toLocaleString()}</td>
      <td class="r num">${num((x.secs || 0) / 60, 1)}m</td><td class="r num"><b>${money(x.usd)}</b></td></tr>`).join("") || `<tr><td colspan="5" class="empty">No calls yet.</td></tr>`}</tbody></table>`;
  v.innerHTML = `<div class="page-head"><div><h1>Spend</h1><p>Every AI call the fleet makes: which model, which provider, how it is paid, for what, and for which competition.</p></div>
    <div class="actions"><div class="seg">${[7, 30, 90].map(n => `<button aria-pressed="${days === n}" data-days="${n}">${n} days</button>`).join("")}</div></div></div>
  <div class="banner ${ai.backend === "claude-code" && cc.authMethod === "claude.ai" ? "info" : "warn"}">${icon("dollar")}<div>
    ${ai.backend === "claude-code" ? `<b>Running on your Claude Code login</b> (${esc(cc.email || "?")}, ${esc(cc.authMethod || "?")}${cc.subscriptionType ? `, ${esc(cc.subscriptionType)} plan` : ""}).
      ${cc.authMethod === "claude.ai" ? "Calls are covered by your subscription, not billed per call; they count against your plan's usage limits. Dollar amounts below are the equivalent API price, useful for comparing and for the caps." : "This login is billed per call."}`
      : `<b>Running on ${esc(ai.backend)}</b>. Calls are billed by that provider unless it is a local model.`}
    Work model <b class="mono">${esc(ai.work_model)}</b> · strategy model <b class="mono">${esc(ai.strategy_model)}</b> · <a href="#/settings">change</a></div></div>
  ${ai.backend === "claude-code" || Object.values(ai.roles || {}).some(m => String(m).startsWith("claude-code")) ? `<div class="card" style="margin-bottom:16px"><div class="card-h"><h3>Your Claude plan: real usage</h3>
    <span class="sub">${esc(cc.subscriptionType || "")} plan · shared with your own Claude use</span><div class="right"><button class="btn sm" id="planRefresh">Refresh now</button></div></div>
    <div class="card-b" id="planBody">${planCard(plan)}</div></div>` : ""}
  <div class="grid g-kpi" style="margin-bottom:16px">
    ${kpi(`AI cost · ${days} days`, money(t.usd), `${t.calls} calls · ${(t.tokens || 0).toLocaleString()} tokens`, "dollar")}
    ${kpi("Covered by plan", money(t.plan_usd), "Claude subscription usage", "check")}
    ${kpi("Billed per call", money(t.api_usd), "API keys", "alert")}
    ${kpi("Average per call", money(t.calls ? t.usd / t.calls : 0), "", "activity")}</div>
  <div class="card" style="margin-bottom:16px"><div class="card-h"><h3>Daily cost</h3><span class="sub">equivalent USD</span></div><div class="card-b">
    ${d.daily.length ? `<svg class="chart" viewBox="0 0 760 170" width="100%" role="img" aria-label="Daily AI cost">${d.daily.map((x, i) => { const w = Math.min(64, 720 / Math.max(d.daily.length, 1)), h = 130 * x.usd / maxDay;
      return `<rect x="${(30 + i * w + 2).toFixed(1)}" y="${(140 - h).toFixed(1)}" width="${Math.max(2, w - 4).toFixed(1)}" height="${h.toFixed(1)}" rx="3" fill="var(--accent)" data-tip="${x.d} · ${money(x.usd)} · ${x.calls} calls"/>`; }).join("")}
      <line class="axis" x1="30" x2="750" y1="140" y2="140"/><text x="30" y="160">${esc(d.daily[0].d)}</text><text x="750" y="160" text-anchor="end">${esc(d.daily.at(-1).d)}</text></svg>` : `<div class="empty">Detailed tracking started with this version.</div>`}</div></div>
  <div class="grid g-2" style="margin-bottom:16px">
    <div class="card"><div class="card-h"><h3>By model</h3></div><div class="card-b flush table-wrap">${aggTable(d.by_model, "Model")}</div></div>
    <div class="card"><div class="card-h"><h3>By provider & billing</h3></div><div class="card-b flush table-wrap">${aggTable(d.by_backend, "Provider · billing")}</div></div>
    <div class="card"><div class="card-h"><h3>By purpose</h3><span class="sub">what the AI was doing</span></div><div class="card-b flush table-wrap">${aggTable(d.by_purpose, "Purpose")}</div></div>
    <div class="card"><div class="card-h"><h3>By competition</h3></div><div class="card-b flush table-wrap">${aggTable(d.by_competition, "Competition")}</div></div>
  </div>
  <div class="card"><div class="card-h"><h3>Every call</h3><span class="sub">latest 200</span></div><div class="card-b flush table-wrap"><table>
    <thead><tr><th>When</th><th>Agent · purpose</th><th>Competition</th><th>Model</th><th>Provider</th><th class="r">In / out / cache</th><th class="r">Time</th><th class="r">Cost</th></tr></thead><tbody>
    ${d.calls.map(c => { const b = BILLING[c.billing] || [c.billing || "—", "", ""];
      return `<tr><td>${when(c.ts)}</td><td>${esc(c.agent || "—")} · <span class="muted">${esc(c.purpose || "")}</span>${c.ok ? "" : ` <span class="badge bad" data-tip="${esc(c.error)}">failed</span>`}</td>
      <td>${esc(c.competition || "—")}</td><td class="mono">${esc(c.model)}</td><td>${esc(c.backend)} <span class="badge ${b[1]}" data-tip="${esc(b[2])}">${b[0]}</span></td>
      <td class="r num muted">${c.input_tokens.toLocaleString()} / ${c.output_tokens.toLocaleString()} / ${c.cache_tokens.toLocaleString()}</td><td class="r num">${num(c.seconds, 1)}s</td><td class="r num">${money(c.cost_usd)}</td></tr>`; }).join("")
      || `<tr><td colspan="8" class="empty">No calls yet.</td></tr>`}</tbody></table></div></div>
  <p class="muted" style="margin-top:12px">Before per-call tracking started, the fleet spent ${money(d.legacy_usd)} in total (shown on each competition); those older calls have no model breakdown.</p>`;
  $$("[data-days]", v).forEach(b => b.onclick = () => go(`spend/${b.dataset.days}`));
  $("#planRefresh") && ($("#planRefresh").onclick = async () => { $("#planBody").innerHTML = `<div class="muted">Reading your plan usage…</div>`;
    $("#planBody").innerHTML = planCard(await api("/api/ai/plan?refresh=1")); });
}

// ---------- Platforms
async function pagePlatforms(v) {
  crumbs([["Platforms"]]);
  const p = await api("/api/platforms");
  v.innerHTML = `<div class="page-head"><div><h1>Platforms</h1><p>Where else an autonomous fleet can compete. Fit is how well each suits automated pipelines (researched October 2026).</p></div></div>
  <div class="card"><div class="table-wrap"><table><thead><tr><th>Platform</th><th>Focus</th><th>API / client</th><th>Fit</th><th>Status</th></tr></thead><tbody>
  ${p.map(x => `<tr><td><a href="${esc(x.url)}" target="_blank" rel="noopener"><b>${esc(x.name)}</b></a></td><td class="wrap">${esc(x.focus)}</td><td class="mono">${esc(x.api)}</td>
    <td><div style="display:flex;align-items:center;gap:8px"><div class="meter" style="width:70px"><i style="width:${x.fit * 10}%"></i></div><span class="num">${x.fit}/10</span></div></td>
    <td><span class="badge ${x.status === "integrated" ? "ok" : x.status.startsWith("recommended") ? "accent" : x.status === "candidate" ? "info" : ""}">${esc(x.status)}</span></td></tr>`).join("")}</tbody></table></div></div>`;
}

// ---------- Settings
async function pageSettings(v) {
  crumbs([["Settings"]]);
  const s = await api("/api/settings"), e = s.editable;
  const field = (k, label, help, type = "number", extra = "") => `<div class="field"><label for="${k}">${label}</label>
    <input id="${k}" name="${k}" type="${type}" value="${esc(e[k] ?? "")}" ${extra}><span class="help">${help}</span></div>`;
  const tog = (k, label, help) => `<div class="switch"><div>${label}<small>${help}</small></div><label class="toggle"><input type="checkbox" name="${k}" ${e[k] ? "checked" : ""}><span></span></label></div>`;
  v.innerHTML = `<div class="page-head"><div><h1>Settings</h1><p>Changes apply immediately and are saved to <span class="mono">.env</span>.</p></div></div>
  <div class="grid g-2">
    <div class="stack">
      <div class="card"><div class="card-h"><h3>Kaggle account</h3><span class="badge ${String(s.kaggle.username).startsWith("(") ? "bad" : "ok"}">${esc(s.kaggle.username)}</span></div><div class="card-b">
        <dl class="kv"><dt>Auth method</dt><dd>${esc(s.kaggle.method)}</dd><dt>Token</dt><dd class="mono">${esc(s.kaggle.token || "—")}</dd></dl>
        <form class="form" id="kgForm" style="margin-top:14px"><div class="field"><label for="kgTok">Switch account: new API token</label>
          <input id="kgTok" type="password" autocomplete="off" placeholder="KGAT_…" required><span class="help">Create one at kaggle.com → Settings → API. It is checked against Kaggle before it is saved.</span></div>
          <div><button class="btn primary">Verify & save</button></div></form></div></div>
      <div id="aiCard"></div>
      <div class="card"><div class="card-h"><h3>Notifications</h3></div><div class="card-b form" data-group>
        <dl class="kv"><dt>SMTP</dt><dd>${s.smtp.host ? `${esc(s.smtp.host)}:${esc(s.smtp.port)}` : '<span class="bad">not set</span>'}</dd><dt>From</dt><dd>${esc(s.smtp.from || "—")}</dd><dt>Password</dt><dd>${s.smtp.password ? "set" : '<span class="bad">missing</span>'}</dd></dl>
        ${field("PODIUM_NOTIFY_EMAIL", "Send alerts to", "Joins needed, new competitions, scores, rank changes, strategy updates, errors.", "email")}
        ${field("PODIUM_NOTIFY_URL", "Webhook (optional)", "ntfy.sh topic URL for phone push, or a Slack / Discord webhook.", "url")}
        ${field("PODIUM_PUBLIC_URL", "Dashboard link in alerts", "e.g. http://192.168.1.20:8000", "url")}
        <div style="display:flex;gap:8px"><button class="btn primary" data-save>Save</button><button class="btn" type="button" id="testNotif">Send test</button></div></div></div>
    </div>
    <div class="stack">
      <div class="card"><div class="card-h"><h3>Autonomy</h3></div><div class="card-b form" data-group>
        ${tog("PODIUM_AUTO_SUBMIT", "Auto-submit", "Submit to Kaggle on real CV gains and use spare quota for leaderboard probes.")}
        ${tog("PODIUM_REVIEW", "Expert code review before submit", "An LLM reviewer must pass every LLM-written submission (leakage, validation, format).")}
        ${field("PODIUM_MAX_ACTIVE", "Competitions in parallel", "Each runs its own experiment stream.", "number", 'min="1" max="10"')}
        ${field("PODIUM_KINDS", "Competition kinds the fleet works on", "Comma list: tabular, code (notebook-only), cv, nlp, audio, other. Code/cv/nlp run as Kaggle notebooks (free GPU).", "text")}
        <div class="field"><label for="PODIUM_EXECUTOR">Where experiments run</label><select id="PODIUM_EXECUTOR" name="PODIUM_EXECUTOR">
          ${[["auto", "Auto: local Docker for tabular, Kaggle notebooks for code / vision / NLP"], ["docker", "Always local Docker sandbox"], ["kaggle", "Always Kaggle notebooks"]].map(([k, l]) => `<option value="${k}" ${e.PODIUM_EXECUTOR === k ? "selected" : ""}>${l}</option>`).join("")}</select>
          <span class="help">Kaggle notebooks use your free weekly GPU quota (phone-verified account needed for GPU).</span></div>
        ${field("PODIUM_MIN_CHANCE", "Minimum rank chance (0-100)", "Competitions below this are not recommended.", "number", 'min="0" max="100"')}
        ${field("PODIUM_MAX_EXPERIMENTS", "Experiments per run", "After this, the competition plateaus until you start a new run.", "number", 'min="1"')}
        ${field("PODIUM_REFLECT_EVERY", "Re-plan every N experiments", "The strategist rewrites the plan from evidence (also after each new leaderboard score).", "number", 'min="1"')}
        ${field("PODIUM_DAILY_SUBMISSIONS", "Daily submission cap", "0 = each competition's own Kaggle limit.", "number", 'min="0"')}
        ${field("PODIUM_SCOUT_SECONDS", "Scout interval (seconds)", "How often to look for new competitions. 3600 = hourly.", "number", 'min="300"')}
        ${field("PODIUM_EXPERIMENT_TIMEOUT_S", "Experiment timeout (seconds)", "Hard stop for one experiment in the sandbox.", "number", 'min="60"')}
        <div><button class="btn primary" data-save>Save</button></div></div></div>
      <div class="card"><div class="card-h"><h3>Budgets</h3></div><div class="card-b form" data-group>
        ${field("PODIUM_WEEKLY_CAP_USD", "Weekly cap on BILLED AI spend ($)", "Counts only money actually billed (API-key providers). Calls on your Claude subscription are not billed: they are governed by the Claude plan reserve below.", "number", 'min="0" step="1"')}
        ${field("PODIUM_PLAN_RESERVE", "Claude plan reserve (0-1)", "Share of your weekly Claude plan the fleet leaves for you. 0.15 = the fleet pauses its AI work at 85% weekly usage and resumes after the reset.", "number", 'min="0" max="0.9" step="0.05"')}
        ${field("PODIUM_COMP_CAP_USD", "Default per-competition billed AI cap ($)", "Billed spend only (API keys). Applies to new runs.", "number", 'min="0" step="1"')}
        ${field("PODIUM_COMP_CAP_HOURS", "Default per-competition compute cap (h)", "Applies to new runs.", "number", 'min="0" step="1"')}
        <div><button class="btn primary" data-save>Save</button></div></div></div>
      <div class="card"><div class="card-h"><h3>System</h3></div><div class="card-b"><dl class="kv">
        ${Object.entries(s.readonly).map(([k, val]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd class="mono">${esc(Array.isArray(val) ? val.join(", ") : val)}</dd>`).join("")}</dl></div></div>
    </div></div>`;
  await renderAI($("#aiCard"));
  $$("[data-group]", v).forEach(g => $("[data-save]", g).onclick = async () => {
    const body = {};
    $$("input[name], select[name]", g).forEach(i => body[i.name] = i.type === "checkbox" ? i.checked : i.value);
    try { await put("/api/settings", body); toast("Saved."); S.me = await api("/api/me"); renderNav(); } catch (err) { toast("Not saved: " + err.message); }
  });
  $("#kgForm").onsubmit = async ev => {
    ev.preventDefault();
    try { const r = await post("/api/settings/kaggle", {token: $("#kgTok").value}); toast(`Kaggle account switched to ${r.username}.`); S.me = await api("/api/me"); render(); }
    catch (err) { toast(err.message, 6000); }
  };
  $("#testNotif").onclick = async () => { const r = await post("/api/settings/test-notification"); toast(r.ok ? "Test sent." : "Failed: " + r.errors.join("; "), 8000); };
}

// ---------- AI engine (connection mode, credentials, models, guides)
const PROVIDERS = {
  "claude-code": {label: "Claude Code (your login)", billing: "Your Claude subscription, no API key",
    work: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-haiku-4-5"], strategy: ["claude-fable-5-1", "claude-opus-5-5"],
    fields: [["PODIUM_CLAUDE_BIN", "Claude Code binary", "text", "Default ~/.local/bin/claude (native build, needed for the newest models)."]],
    guide: ["Install the native Claude Code build: <code>curl -fsSL https://claude.ai/install.sh | bash</code> (or run <code>claude install</code> from an older copy).",
      "Log in once: <code>claude auth login</code>, then choose your Claude.ai account (Pro or Max). You can check it with <code>claude auth status</code>.",
      "No API key is needed. Calls use your plan's usage limits; the dollar figures in Spend are the equivalent API price.",
      "Recommended: <b>claude-opus-5-5</b> for work (experiments, review, Copilot) and <b>claude-fable-5-1</b>, the most capable model, for strategy."]},
  anthropic: {label: "Anthropic API (API key)", billing: "Billed per token to your Anthropic Console account", prefix: "anthropic/",
    work: ["anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5", "anthropic/claude-haiku-4-5"], strategy: ["anthropic/claude-fable-5-1", "anthropic/claude-opus-5-5"],
    fields: [["ANTHROPIC_API_KEY", "Anthropic API key", "password", "Starts with sk-ant-. Stored in .env (mode 600), never shown again."]],
    guide: ["Open <a href='https://console.anthropic.com/settings/keys' target='_blank' rel='noopener'>console.anthropic.com → API keys</a> and create a key.",
      "Paste it here and save. Make sure the workspace has credits or billing set up.",
      "Billed per token: keep the weekly cap in Budgets."]},
  bedrock: {label: "AWS Bedrock (AWS credentials)", billing: "Billed by AWS", prefix: "bedrock/",
    work: ["bedrock/anthropic.claude-opus-5-5", "bedrock/anthropic.claude-sonnet-5-5"], strategy: ["bedrock/anthropic.claude-opus-5-5"],
    fields: [["AWS_REGION_NAME", "AWS region", "text", "e.g. us-east-1, where the model is enabled."], ["AWS_PROFILE", "AWS profile (optional)", "text", "Use a named profile from ~/.aws/credentials instead of keys."],
             ["AWS_ACCESS_KEY_ID", "Access key ID", "password", "Leave empty when using a profile or instance role."], ["AWS_SECRET_ACCESS_KEY", "Secret access key", "password", ""]],
    guide: ["In the AWS console, open <b>Bedrock → Model access</b> and enable the Claude models in your region.",
      "Create an IAM user or role allowed <code>bedrock:InvokeModel</code>, then give it keys here, or use a profile.",
      "Model IDs vary by region and inference profile: copy the exact ID from the Bedrock console and prefix it with <code>bedrock/</code>, then use Test."]},
  gemini: {label: "Google Gemini (API key)", billing: "Billed by Google AI Studio (free tier available)", prefix: "gemini/",
    work: ["gemini/gemini-2.5-pro", "gemini/gemini-2.5-flash"], strategy: ["gemini/gemini-2.5-pro"],
    fields: [["GEMINI_API_KEY", "Gemini API key", "password", "From Google AI Studio."]],
    guide: ["Get a key at <a href='https://aistudio.google.com/apikey' target='_blank' rel='noopener'>aistudio.google.com/apikey</a>.", "Paste it, pick a model and Test."]},
  openai: {label: "OpenAI (API key)", billing: "Billed per token by OpenAI", prefix: "openai/",
    work: ["openai/gpt-5", "openai/gpt-5-mini"], strategy: ["openai/gpt-5"],
    fields: [["OPENAI_API_KEY", "OpenAI API key", "password", "Starts with sk-."]],
    guide: ["Create a key at <a href='https://platform.openai.com/api-keys' target='_blank' rel='noopener'>platform.openai.com/api-keys</a>.", "Paste it, pick a model and Test."]},
  ollama: {label: "Ollama (local, free)", billing: "Free: runs on your machine", prefix: "ollama_chat/",
    work: ["ollama_chat/qwen2.5-coder:32b", "ollama_chat/qwen2.5-coder:14b", "ollama_chat/llama3.3:70b"], strategy: ["ollama_chat/qwen2.5-coder:32b"],
    fields: [["OLLAMA_API_BASE", "Ollama URL", "text", "Default http://localhost:11434; use the other machine's IP for a remote GPU box."]],
    guide: ["Install from <a href='https://ollama.com/download' target='_blank' rel='noopener'>ollama.com</a> and pull a coding model: <code>ollama pull qwen2.5-coder:32b</code> (needs about 24 GB VRAM; 14b needs about 12 GB).",
      "Set the URL if Ollama runs on another computer, then Test.",
      "Free, but expect weaker strategies and code than the frontier models: keep the code review on."]},
  none: {label: "No AI (baseline engine)", billing: "Free", work: ["none"], strategy: [""], fields: [],
    guide: ["Runs a fixed gradient-boosting plan on tabular competitions. No strategy, no review, no Copilot."]},
};
const providerOf = m => !m || m === "none" ? "none" : m.startsWith("claude-code") ? "claude-code" : m.startsWith("anthropic/") ? "anthropic" :
  m.startsWith("bedrock/") ? "bedrock" : m.startsWith("gemini/") ? "gemini" : m.startsWith("openai/") ? "openai" : m.startsWith("ollama") ? "ollama" : "anthropic";
const ROLE_INFO = {strategy: ["Strategy & reflection", "Plans each competition, researches winning approaches on the web, rewrites the plan from evidence. Few calls, biggest impact: use the most capable model."],
  experiment: ["Experiments", "Writes every experiment and blend, inside one ongoing conversation per competition. Most of the calls."],
  review: ["Code review", "Must pass every AI-written submission (leakage, validation, format) and suggests improvements."],
  copilot: ["Copilot", "Your chat assistant in the dashboard."]};
const roleOptions = prov => { const P = PROVIDERS[prov]; if (!P || prov === "none") return ["none"];
  const list = [...new Set([...P.strategy, ...P.work])]; return prov === "claude-code" ? list.map(m => "claude-code:" + m) : list; };
async function renderAI(el, pick) {
  const ai = await api("/api/ai"), cc = ai.claude_code || {};
  const cur = providerOf(ai.roles.experiment), sel = pick || cur, P = PROVIDERS[sel];
  const pref = (role) => { const v = ai.roles[role]; if (providerOf(v) === sel) return v;
    const o = roleOptions(sel); return role === "strategy" ? o[0] : (o.find(m => /opus-5-5|gemini-2.5-pro|gpt-5$|32b/.test(m)) || o[0]); };
  el.innerHTML = `<div class="card"><div class="card-h"><h3>AI engine</h3><span class="badge ok">${esc(PROVIDERS[cur].label)}</span></div><div class="card-b form">
    <div class="field"><label for="aiProv">Connection mode</label><select id="aiProv">${Object.entries(PROVIDERS).map(([k, x]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(x.label)}${k === cur ? " (active)" : ""}</option>`).join("")}</select>
      <span class="help">${esc(P.billing)}. Each role below can also point at a different provider (custom value).</span></div>
    ${sel === "claude-code" ? `<div class="banner ${cc.loggedIn ? "info" : "bad"}" style="margin:0">${icon(cc.loggedIn ? "check" : "alert")}<div>${cc.loggedIn
      ? `Logged in as <b>${esc(cc.email)}</b> via <b>${esc(cc.authMethod)}</b>${cc.subscriptionType ? ` · <b>${esc(cc.subscriptionType)}</b> plan` : ""}. Binary <span class="mono">${esc(cc.bin)}</span>.`
      : `Claude Code is not logged in or not found at <span class="mono">${esc(cc.bin || "?")}</span>. Follow the guide below.`}</div></div>` : ""}
    ${P.fields.map(([k, label, type, help]) => { const isSecret = type === "password"; const val = isSecret ? "" : (ai.plain[k] || "");
      return `<div class="field"><label for="f_${k}">${label}${isSecret && ai.credentials[k] ? ` <span class="badge ok">set: ${esc(ai.credentials[k])}</span>` : ""}</label>
      <input id="f_${k}" data-cred="${k}" type="${type}" autocomplete="off" value="${esc(val)}" placeholder="${isSecret ? (ai.credentials[k] ? "leave empty to keep the current key" : "paste here") : k === "PODIUM_CLAUDE_BIN" ? esc(cc.bin || "") : ""}"><span class="help">${help}</span></div>`; }).join("")}
    ${sel !== "none" ? `<div class="field"><label>Model per role</label><div class="table-wrap"><table><tbody>${Object.entries(ROLE_INFO).map(([r, [label, help]]) => {
      const v = pref(r), opts = [...new Set([v, ...roleOptions(sel)])];
      return `<tr><td style="white-space:normal;min-width:180px"><b>${label}</b><br><small class="muted">${help}</small><br><small class="mono muted">PODIUM_MODEL_${r.toUpperCase()}</small></td>
        <td style="min-width:240px"><select data-role="${r}">${opts.map(m => `<option ${m === v ? "selected" : ""}>${esc(m)}</option>`).join("")}<option value="__custom">custom…</option></select>
        <input data-custom="${r}" type="text" placeholder="e.g. bedrock/… or claude-code:claude-sonnet-5-5" style="margin-top:6px" hidden>
        <button class="btn sm" type="button" data-test="${r}" style="margin-top:6px">Test</button></td></tr>`; }).join("")}</tbody></table></div></div>
    <div class="switch"><div>Competition conversations<small>The Solver keeps ONE ongoing conversation per competition: every round it gets the last result, diagnostics, review notes and leaderboard feedback, and builds on everything it learned. ${Object.keys(ai.conversations_active || {}).length} active · restarts fresh after ${ai.conversation_max_turns} turns.</small></div><label class="toggle"><input type="checkbox" id="aiConv" ${ai.conversations ? "checked" : ""}><span></span></label></div>
    <div class="switch"><div>Web research<small>The Strategist searches public write-ups of winning solutions for this and similar competitions (Claude Code roles). Public information only.</small></div><label class="toggle"><input type="checkbox" id="aiRes" ${ai.research ? "checked" : ""}><span></span></label></div>` : ""}
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="aiSave" type="button">${sel === cur ? "Save" : "Switch to this mode"}</button></div>
    <div id="aiResult"></div>
    <details><summary style="cursor:pointer;font-weight:600">How to set up: ${esc(P.label)}</summary><ol class="md" style="margin-top:8px">${P.guide.map(g => `<li>${g}</li>`).join("")}</ol></details>
  </div></div>`;
  $("#aiProv").onchange = ev => renderAI(el, ev.target.value);
  $$("[data-role]", el).forEach(x => x.onchange = () => { $(`[data-custom="${x.dataset.role}"]`, el).hidden = x.value !== "__custom"; });
  const roleValue = r => { const s2 = $(`[data-role="${r}"]`, el); return s2.value === "__custom" ? $(`[data-custom="${r}"]`, el).value.trim() : s2.value; };
  $("#aiSave").onclick = async () => {
    try {
      const creds = {}; $$("[data-cred]", el).forEach(i => { if (i.value.trim() || i.type !== "password") creds[i.dataset.cred] = i.value.trim(); });
      if (Object.keys(creds).length) await put("/api/ai/credentials", creds);
      const body = {};
      if (sel === "none") Object.keys(ROLE_INFO).forEach(r => body[`PODIUM_MODEL_${r.toUpperCase()}`] = "none");
      else Object.keys(ROLE_INFO).forEach(r => { const v = roleValue(r); if (v) body[`PODIUM_MODEL_${r.toUpperCase()}`] = v; });
      body.PODIUM_MODEL = sel === "none" ? "none" : sel === "claude-code" ? "claude-code" : body.PODIUM_MODEL_EXPERIMENT;
      if ($("#aiConv")) { body.PODIUM_CONVERSATIONS = $("#aiConv").checked; body.PODIUM_RESEARCH = $("#aiRes").checked; }
      await put("/api/settings", body); toast("AI engine saved. New calls use it right away."); S.me = await api("/api/me"); renderNav(); renderAI(el);
    } catch (err) { toast("Not saved: " + err.message, 6000); }
  };
  $$("[data-test]", el).forEach(b => b.onclick = async () => {
    const role = b.dataset.test; $("#aiResult").innerHTML = `<div class="muted">Testing ${role} (saved configuration)…</div>`;
    const r = await post(`/api/ai/test?role=${role}`);
    $("#aiResult").innerHTML = r.ok ? `<div class="banner info" style="margin:0">${icon("check")}<div><b>${esc(role)} connected.</b> ${esc(r.model)} via ${esc(r.backend)} (${esc(r.billing)}) answered "${esc(r.reply)}" in ${r.seconds}s · ${money(r.cost_usd)}</div></div>`
      : `<div class="banner bad" style="margin:0">${icon("alert")}<div><b>${esc(role)} failed.</b> ${esc(r.error)}</div></div>`; });
}

// ------------------------------------------------------------------ charts (SVG, one accent hue + neutrals, one axis each)
function scaleOf(vals, pad = 0.1) { let lo = Math.min(...vals), hi = Math.max(...vals); const p = (hi - lo) * pad || Math.abs(hi) * 0.01 || 0.01; return [lo - p, hi + p]; }
function progressChart(exps, hib) {
  const ok = exps.map((e, i) => ({...e, i})).filter(e => e.cv_mean != null);
  if (!ok.length) return `<div class="empty">No successful experiment yet.</div>`;
  const W = 560, H = 230, L = 56, R = 12, T = 12, B = 28, n = exps.length, [lo, hi] = scaleOf(ok.map(e => e.cv_mean));
  const sx = i => L + (n === 1 ? (W - L - R) / 2 : i / (n - 1) * (W - L - R)), sy = y => T + (1 - (y - lo) / (hi - lo)) * (H - T - B);
  let best = null; const pts = [];
  exps.forEach((e, i) => { if (e.cv_mean != null && e.critic_flags === "[]" && (best == null || (hib ? e.cv_mean > best : e.cv_mean < best))) best = e.cv_mean; if (best != null) pts.push([sx(i), sy(best)]); });
  const path = pts.map((p, j) => j ? `H${p[0].toFixed(1)}V${p[1].toFixed(1)}` : `M${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const ticks = [lo, (lo + hi) / 2, hi];
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="CV per experiment">
    ${ticks.map(t => `<line class="grid-l" x1="${L}" x2="${W - R}" y1="${sy(t)}" y2="${sy(t)}"/><text x="${L - 8}" y="${sy(t) + 4}" text-anchor="end">${t.toFixed(4)}</text>`).join("")}
    <line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/><text x="${(L + W) / 2}" y="${H - 6}" text-anchor="middle">experiment</text>
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2"/>
    ${exps.map((e, i) => { const bad = e.cv_mean == null || e.critic_flags !== "[]"; const tip = `#${i + 1} ${e.summary} · ${e.cv_mean == null ? "failed" : "CV " + num(e.cv_mean, 5)}${bad && e.cv_mean != null ? " · rejected" : ""}`;
      return bad ? `<text x="${sx(i)}" y="${H - B - 6}" text-anchor="middle" style="fill:var(--bad);font-size:12px" data-tip="${esc(tip)}">✕</text>`
        : `<circle cx="${sx(i)}" cy="${sy(e.cv_mean)}" r="5" fill="var(--surface)" stroke="var(--ink)" stroke-width="2" data-tip="${esc(tip)}"/>`; }).join("")}
  </svg><div class="legend"><span><i style="background:var(--accent)"></i>best so far</span><span><i class="d" style="border:2px solid var(--ink)"></i>experiment</span><span><span class="bad">✕</span> failed / rejected</span></div>`;
}
function scatter(points) {
  if (!points.length) return `<div class="empty">No scored submissions yet.</div>`;
  const W = 560, H = 230, L = 56, R = 12, T = 12, B = 28, [lo, hi] = scaleOf(points.flatMap(p => [p.x, p.y]));
  const sx = x => L + (x - lo) / (hi - lo) * (W - L - R), sy = y => T + (1 - (y - lo) / (hi - lo)) * (H - T - B);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="CV versus public leaderboard">
    <line x1="${sx(lo)}" y1="${sy(lo)}" x2="${sx(hi)}" y2="${sy(hi)}" stroke="var(--line)" stroke-dasharray="4"/>
    <line class="axis" x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}"/><line class="axis" x1="${L}" y1="${T}" x2="${L}" y2="${H - B}"/>
    <text x="${(L + W) / 2}" y="${H - 6}" text-anchor="middle">CV</text><text x="14" y="${(H - B) / 2}" transform="rotate(-90 14 ${(H - B) / 2})" text-anchor="middle">Public LB</text>
    <text x="${L}" y="${H - B + 14}">${lo.toFixed(3)}</text><text x="${W - R}" y="${H - B + 14}" text-anchor="end">${hi.toFixed(3)}</text>
    ${points.map(p => `<circle cx="${sx(p.x)}" cy="${sy(p.y)}" r="6" fill="var(--accent)" stroke="var(--surface)" stroke-width="2" data-tip="${esc(p.label)} · CV ${p.x.toFixed(5)} · LB ${p.y.toFixed(5)}"/>`).join("")}
  </svg><div class="legend"><span><i style="background:var(--line)"></i>CV = LB</span></div>`;
}
function rankChart(evts) {
  const pts = evts.map(e => ({t: new Date(e.ts), p: e.payload.top_pct, r: e.payload.rank, n: e.payload.teams}));
  if (!pts.length) return `<div class="empty">Rank appears after the first scored submission.</div>`;
  const W = 560, H = 200, L = 48, R = 12, T = 12, B = 26, t0 = +pts[0].t, t1 = Math.max(+pts[pts.length - 1].t, t0 + 1);
  const sx = t => L + (t - t0) / (t1 - t0) * (W - L - R), sy = p => T + p / 100 * (H - T - B);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Leaderboard percentile over time (top is better)">
    ${[0, 25, 50, 75, 100].map(p => `<line class="grid-l" x1="${L}" x2="${W - R}" y1="${sy(p)}" y2="${sy(p)}"/><text x="${L - 8}" y="${sy(p) + 4}" text-anchor="end">${p}%</text>`).join("")}
    <line x1="${L}" x2="${W - R}" y1="${sy(10)}" y2="${sy(10)}" stroke="var(--ok)" stroke-dasharray="4"/><text x="${W - R}" y="${sy(10) - 4}" text-anchor="end" style="fill:var(--ok)">top 10%</text>
    <polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${pts.map(p => `${sx(+p.t).toFixed(1)},${sy(p.p).toFixed(1)}`).join(" ")}"/>
    ${pts.map(p => `<circle cx="${sx(+p.t)}" cy="${sy(p.p)}" r="4.5" fill="var(--accent)" stroke="var(--surface)" stroke-width="2" data-tip="${when(p.t)} · #${p.r}/${p.n} · top ${p.p}%"/>`).join("")}
  </svg>`;
}
function histogram(h, mine, p10) {
  const W = 760, H = 200, L = 12, R = 12, T = 10, B = 26, c = h.counts, e = h.edges, mx = Math.max(...c, 1), lo = e[0], hi = e[e.length - 1];
  const sx = x => L + (x - lo) / (hi - lo || 1) * (W - L - R), bw = (W - L - R) / c.length;
  const marker = (x, color, label, row) => x == null || x < lo || x > hi ? "" : `<line x1="${sx(x)}" x2="${sx(x)}" y1="${T}" y2="${H - B}" stroke="${color}" stroke-width="2"/>
    <text x="${sx(x) + (sx(x) > W - 90 ? -4 : 4)}" y="${T + 12 + row * 14}" text-anchor="${sx(x) > W - 90 ? "end" : "start"}" style="fill:${color};font-weight:600">${label}</text>`;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Score distribution">
    ${c.map((n, i) => `<rect x="${(L + i * bw + 1).toFixed(1)}" y="${(H - B - n / mx * (H - T - B)).toFixed(1)}" width="${Math.max(1, bw - 2).toFixed(1)}" height="${(n / mx * (H - T - B)).toFixed(1)}" rx="2" fill="var(--line)" data-tip="${n} teams · ${e[i].toFixed(4)} – ${e[i + 1].toFixed(4)}"/>`).join("")}
    <line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>
    <text x="${L}" y="${H - 8}">${lo.toFixed(4)}</text><text x="${W - R}" y="${H - 8}" text-anchor="end">${hi.toFixed(4)}</text>
    ${marker(p10, "var(--ok)", "top 10%", 0)}${marker(mine, "var(--accent)", "you", 1)}
  </svg><div class="legend"><span><i style="background:var(--accent)"></i>your score</span><span><i style="background:var(--ok)"></i>top-10% cutoff</span><span><i style="background:var(--line)"></i>teams per score band (competitive 90% of the board)</span></div>`;
}

// ------------------------------------------------------------------ tiny, safe markdown (escape first)
function md(src) {
  const inline = s => esc(s).replace(/\[([^\]]+)\]\((#\/[^)\s]+|https?:\/\/[^)\s]+)\)/g,
      (_, t, u) => `<a href="${u}"${u.startsWith("http") ? ' target="_blank" rel="noopener"' : ""}>${t}</a>`).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|\W)\*([^*]+)\*(?=\W|$)/g, "$1<i>$2</i>");
  const lines = String(src || "").split("\n"), out = []; let list = null, table = [];
  const flushList = () => { if (list) { out.push(`<${list.t}>${list.items.map(i => `<li>${i}</li>`).join("")}</${list.t}>`); list = null; } };
  const flushTable = () => { if (table.length) { const rows = table.filter(r => !/^\|?\s*:?-{2,}/.test(r)).map(r => r.replace(/^\||\|$/g, "").split("|").map(x => inline(x.trim())));
    out.push(`<div class="table-wrap"><table><thead><tr>${rows[0].map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.slice(1).map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`); table = []; } };
  for (const raw of lines) {
    const l = raw.trimEnd();
    if (/^\s*\|/.test(l)) { flushList(); table.push(l.trim()); continue; } else flushTable();
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { flushList(); out.push(`<h${Math.min(4, m[1].length + 1)}>${inline(m[2])}</h${Math.min(4, m[1].length + 1)}>`); }
    else if ((m = l.match(/^\s*[-*]\s+(.*)/))) { if (!list || list.t !== "ul") { flushList(); list = {t: "ul", items: []}; } list.items.push(inline(m[1])); }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (!list || list.t !== "ol") { flushList(); list = {t: "ol", items: []}; } list.items.push(inline(m[1])); }
    else if (!l.trim()) flushList();
    else { flushList(); out.push(`<p>${inline(l)}</p>`); }
  }
  flushList(); flushTable();
  return out.join("");
}

// ------------------------------------------------------------------ chrome: alerts popover, live status, tooltips, menu
function updateBell() {
  const n = unreadAlerts(), b = $("#bellCount");
  b.hidden = !n; b.textContent = n > 99 ? "99+" : n;
}
function renderAlertPop() {
  const items = S.events.filter(e => /error|flagged|diverged|human_task|recommended|rank\.updated|scored|strategy|reviewed/.test(e.type)).slice(-12).reverse();
  $("#alertPop").innerHTML = `<div class="card-h"><h3>Alerts</h3><div class="right"><a class="btn sm" href="#/alerts">All</a><button class="btn sm" id="popRead">Mark read</button></div></div>
    <div class="feed">${items.map(feedItem).join("") || `<div class="empty">No alerts.</div>`}</div>`;
  $("#popRead").onclick = () => { markAlertsRead(); renderAlertPop(); };
}
function heartbeat() {
  const l = $("#live"); const fresh = S.lastEventAt && Date.now() - new Date(S.lastEventAt) < 180000;
  l.innerHTML = `<i class="dot ${S.sse ? (fresh ? "on" : "warn") : "off"}"></i><span>${S.sse ? `live · ${ago(S.lastEventAt)}` : "reconnecting"}</span>`;
}
function initChrome() {
  $("#menuBtn").innerHTML = icon("menu"); $("#bellBtn").insertAdjacentHTML("afterbegin", icon("bell")); $("#modalClose").innerHTML = icon("x");
  $("#menuBtn").onclick = () => $("#shell").classList.toggle("open");
  $("#scrim").onclick = () => $("#shell").classList.remove("open");
  $("#bellBtn").onclick = e => { e.stopPropagation(); const p = $("#alertPop"); p.hidden = !p.hidden; if (!p.hidden) renderAlertPop(); };
  document.addEventListener("click", e => { if (!e.target.closest("#alertPop, #bellBtn")) $("#alertPop").hidden = true; });
  $("#modalClose").onclick = () => $("#modal").hidden = true;
  $("#modal").onclick = e => { if (e.target.id === "modal") $("#modal").hidden = true; };
  document.addEventListener("keydown", e => { if (e.key === "Escape") { $("#modal").hidden = true; $("#copilot").hidden = true; $("#alertPop").hidden = true; $("#shell").classList.remove("open"); } });
  $("#fleetBtn").onclick = async () => { const r = await post("/api/fleet/pause"); toast(r.paused ? "Fleet paused." : "Fleet resumed."); await loadFleet(); render(); };
  const tip = $("#tip");
  document.addEventListener("mouseover", e => { const t = e.target.closest("[data-tip]"); if (!t || !t.dataset.tip) { tip.style.display = "none"; return; } tip.textContent = t.dataset.tip; tip.style.display = "block"; });
  document.addEventListener("mousemove", e => { tip.style.left = Math.min(e.clientX + 12, innerWidth - 350) + "px"; tip.style.top = (e.clientY + 14) + "px"; });
}

// ------------------------------------------------------------------ Copilot (server-side conversations, resumable streaming)
const CP = {convId: store.get("cpConv", null), title: "", messages: [], busy: false, runId: null, ctrl: null, pending: [], view: "chat", q: ""};
ICONS.clip = '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>';
ICONS.history = '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 3"/>';
ICONS.expand = '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>';
const SUGGEST = ["Brief me on the whole fleet: where do we stand and what matters most?",
  "What exactly would move Predicting Airline Satisfaction into the top 10%?",
  "Search Kaggle for big new competitions we can submit to and assess the best 3.",
  "What is ongoing right now and what is it waiting for?"];
const TOOL_LABEL = {fleet: "Read fleet status", competition: "Read competition", leaderboard: "Read leaderboard", strategy: "Read strategy",
  events: "Read activity", search_kaggle: "Search Kaggle", assess: "Assess competition", recommend: "Recommend & email", start: "Start competition",
  pause: "Pause / resume", stop: "Stop competition", archive: "Archive competition", set_setting: "Change setting", scout_now: "Run scout",
  read_file: "Read file", resolve_decision: "Clear decision", navigate: "Open page", kaggle_submissions: "Read Kaggle submissions",
  add_competition: "Add competition", scan_recent: "Scan recent launches", practice: "Start practice (late)"};
const pageContext = () => { const r = S.route; return r.page === "competition" ? `competition ${r.slug} (${r.tab || "overview"} tab)` : `${r.page} page`; };
const attUrl = a => `/api/copilot/upload/${String(a.id).split("/").map(encodeURIComponent).join("/")}`;
const attChip = (a, removable) => `<span class="cp-att ${a.uploading ? "up" : ""}" title="${esc(a.name)}">${a.kind === "image" && !a.uploading
  ? `<img src="${a.preview || attUrl(a)}" alt="">` : `<span class="cp-att-ic">${a.uploading ? '<span class="spin"></span>' : icon("file")}</span>`}
  <span class="cp-att-name">${esc(a.name)}</span>${removable ? `<button type="button" data-rm="${esc(a.key)}" aria-label="Remove">×</button>` : ""}</span>`;
function drawPending() {
  $("#cpAtts").innerHTML = CP.pending.map(a => attChip(a, true)).join("");
  $$("[data-rm]", $("#cpAtts")).forEach(b => b.onclick = () => { CP.pending = CP.pending.filter(a => a.key !== b.dataset.rm); drawPending(); });
  $("#cpSend").disabled = !CP.busy && (CP.pending.some(a => a.uploading) || (!$("#cpText").value.trim() && !CP.pending.length));
}
async function addFiles(files) {
  for (const f of files) {
    if (f.size > 15 * 1024 * 1024) { toast(`${f.name} is larger than 15 MB.`); continue; }
    const kind = /^image\//.test(f.type) ? "image" : /pdf$/.test(f.type) ? "pdf" : "file";
    const a = {key: Math.random().toString(36).slice(2), name: f.name || "pasted-image.png", kind, uploading: true, preview: kind === "image" ? URL.createObjectURL(f) : null};
    CP.pending.push(a); drawPending();
    try {
      const data = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
      Object.assign(a, await post("/api/copilot/upload", {name: a.name, data}), {uploading: false});
    } catch (e) { toast(`Upload failed: ${e.message}`); CP.pending = CP.pending.filter(x => x !== a); }
    drawPending();
  }
}
function stepHtml(st) {
  const args = st.args && Object.keys(st.args).length ? Object.values(st.args).map(v => String(v)).join(", ") : "";
  return `${st.note ? `<div class="cp-note">${md(st.note)}</div>` : ""}<div class="cp-step ${st.done ? (st.ok ? "ok" : "err") : "run"}">
    <span class="cp-step-ic">${st.done ? (st.ok ? "✓" : "✕") : '<span class="spin"></span>'}</span>
    <span>${st.action ? "⚡ " : ""}<b>${esc(TOOL_LABEL[st.tool] || st.tool)}</b>${args ? ` <span class="muted mono">${esc(args.slice(0, 70))}</span>` : ""}</span>
    ${st.summary ? `<span class="muted cp-sum">${esc(st.summary)}</span>` : ""}</div>`;
}
function msgHtml(m, i) {
  if (m.role === "user") return `<div class="msg user">${(m.attachments || []).length ? `<div class="cp-msg-atts">${m.attachments.map(a => attChip(a, false)).join("")}</div>` : ""}${esc(m.content)}</div>`;
  const live = m.status === "streaming";
  const banner = {error: ["bad", `Failed: ${m.error || "error"}`], stopped: ["warn", "Stopped."], interrupted: ["warn", "Interrupted by a server restart. Ask again to continue."]}[m.status];
  return `<div class="msg bot">${(m.steps || []).length ? `<div class="cp-steps">${m.steps.map(stepHtml).join("")}</div>` : ""}
    ${m.content || !live ? `<div class="md">${md(m.content || "")}${live ? '<span class="cursor"></span>' : ""}</div>`
      : `<div><span class="typing"><i></i><i></i><i></i></span> <span class="muted">${(m.steps || []).length ? "working…" : "thinking…"}</span></div>`}
    ${banner ? `<div class="banner ${banner[0]}" style="margin:8px 0 0">${icon("alert")}<div>${esc(banner[1])}</div></div>` : ""}
    ${!live && m.content ? `<div class="cp-tools"><button class="cp-mini" data-copy="${i}" title="Copy answer">Copy</button></div>` : ""}
    ${!live && i === CP.messages.length - 1 && (m.suggestions || []).length ? `<div class="cp-chips">${m.suggestions.map(x => `<button class="cp-chip" type="button">${esc(x)}</button>`).join("")}</div>` : ""}</div>`;
}
async function drawHistory() {
  const b = $("#cpBody");
  b.innerHTML = `<div class="cp-hist-head"><input id="cpQ" type="text" placeholder="Search conversations…" value="${esc(CP.q)}" aria-label="Search conversations"></div><div id="cpList"><div class="muted">Loading…</div></div>`;
  const list = async () => {
    const rows = await api(`/api/copilot/convs?q=${encodeURIComponent(CP.q)}`);
    $("#cpList").innerHTML = rows.map(r => `<div class="cp-conv ${r.id === CP.convId ? "on" : ""}" data-id="${r.id}" tabindex="0">
      <div class="cp-conv-main"><b>${esc(r.title)}</b>${r.running ? ' <span class="badge ok"><i class="dot on"></i>answering</span>' : ""}
        <small class="muted">${esc(r.last || "")}</small><small class="muted">${r.n} messages · ${ago(r.updated_at)}</small></div>
      <div class="cp-conv-act"><button class="cp-mini" data-ren="${r.id}" title="Rename">Rename</button><button class="cp-mini" data-del="${r.id}" title="Delete">Delete</button></div></div>`).join("")
      || `<div class="empty">No conversations${CP.q ? " match" : " yet"}.</div>`;
    $$(".cp-conv", b).forEach(el => el.onclick = e => { if (e.target.closest("[data-ren],[data-del]")) return; loadConv(el.dataset.id); });
    $$("[data-del]", b).forEach(x => x.onclick = async () => { if (!confirm("Delete this conversation?")) return; await api(`/api/copilot/convs/${x.dataset.del}`, {method: "DELETE"});
      if (x.dataset.del === CP.convId) { CP.convId = null; CP.messages = []; store.set("cpConv", null); } list(); });
    $$("[data-ren]", b).forEach(x => x.onclick = async () => { const t = prompt("Rename conversation", x.closest(".cp-conv").querySelector("b").textContent); if (!t) return;
      await api(`/api/copilot/convs/${x.dataset.ren}`, {method: "PATCH", headers: {"content-type": "application/json"}, body: JSON.stringify({title: t})}); list(); });
  };
  let t; $("#cpQ").oninput = e => { CP.q = e.target.value; clearTimeout(t); t = setTimeout(list, 250); };
  list();
}
function drawCopilot() {
  $("#cpSub").textContent = CP.view === "history" ? "Conversation history" : (CP.title || "New conversation");
  $("#cpSend").textContent = CP.busy ? "Stop" : "Send"; $("#cpSend").classList.toggle("danger", CP.busy); drawPending();
  if (CP.view === "history") return drawHistory();
  const b = $("#cpBody"), atBottom = b.scrollHeight - b.scrollTop - b.clientHeight < 80;
  if (!CP.messages.length) {
    b.innerHTML = `<div class="msg bot"><div class="md"><p><b>Hi, I'm your Podium Copilot.</b> I see every competition, experiment, strategy, leaderboard, alert and dollar spent, and I can act: search and assess Kaggle competitions, recommend joins, start, pause or stop work, clear decisions, change settings and open any page. Conversations are saved; find them in History.</p></div></div>
      <div class="cp-chips">${SUGGEST.map(x => `<button class="cp-chip" type="button">${esc(x)}</button>`).join("")}</div>`;
  } else b.innerHTML = CP.messages.map(msgHtml).join("");
  $$(".cp-chip", b).forEach(x => x.onclick = () => sendCopilot(x.textContent));
  $$("[data-copy]", b).forEach(x => x.onclick = () => { navigator.clipboard?.writeText(CP.messages[x.dataset.copy].content); toast("Copied."); });
  $$(".md a[href^='#/']", b).forEach(a => a.onclick = () => { if (innerWidth < 900) $("#copilot").hidden = true; });
  if (atBottom || CP.busy) b.scrollTop = b.scrollHeight;
}
let drawQueued = false;
const drawSoon = () => { if (!drawQueued) { drawQueued = true; requestAnimationFrame(() => { drawQueued = false; drawCopilot(); }); } };
async function loadConv(id) {
  CP.ctrl?.abort(); CP.busy = false; CP.view = "chat";
  try {
    const c = await api(`/api/copilot/convs/${id}`);
    CP.convId = id; CP.title = c.title; CP.messages = c.messages; store.set("cpConv", id);
    drawCopilot();
    const live = c.messages.find(m => m.status === "streaming" && m.live);
    if (live) { live.navigated = true; follow(live.run_id, live); }  // reconnect after a refresh (no re-navigation)
  } catch { CP.convId = null; CP.messages = []; store.set("cpConv", null); drawCopilot(); }
}
async function follow(runId, bot) {
  CP.busy = true; CP.runId = runId; CP.ctrl = new AbortController();
  Object.assign(bot, {content: "", steps: [], suggestions: [], status: "streaming"}); drawCopilot();
  let acted = false;
  try {
    const r = await fetch(`/api/copilot/runs/${runId}/events?after=0`, {signal: CP.ctrl.signal});
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const {value, done} = await reader.read(); if (done) break;
      buf += dec.decode(value, {stream: true});
      let i; while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).replace(/^data: /, ""); buf = buf.slice(i + 2);
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === "delta") bot.content += ev.text;
        else if (ev.type === "step") { bot.steps.push({tool: ev.tool, args: ev.args, action: ev.action, note: ev.note, done: false}); bot.content = ""; acted ||= ev.action; }
        else if (ev.type === "step_done") Object.assign(bot.steps.at(-1) || {}, {done: true, ok: ev.ok, summary: ev.summary});
        else if (ev.type === "navigate" && ev.i >= 0 && !bot.navigated) { bot.navigated = true; go(ev.path); if (innerWidth < 900) $("#copilot").hidden = true; }
        else if (ev.type === "done") { bot.content = ev.reply; bot.suggestions = ev.suggestions || []; }
        else if (ev.type === "error") { bot.status = "error"; bot.error = ev.error; }
        else if (ev.type === "end") bot.status = ev.status === "gone" ? "interrupted" : (bot.status === "error" ? "error" : ev.status);
        drawSoon();
      }
    }
  } catch (e) { if (e.name !== "AbortError") { bot.status = "error"; bot.error = e.message; } }
  if (bot.status === "streaming") bot.status = "done";
  CP.busy = false; CP.ctrl = null; CP.runId = null; drawCopilot();
  if (acted) { await loadFleet(); render(); }
}
async function sendCopilot(text) {
  if (CP.busy) { if (CP.runId) post(`/api/copilot/runs/${CP.runId}/stop`); return; }
  text = (text || "").trim();
  if (CP.pending.some(a => a.uploading)) { toast("Wait for the upload to finish."); return; }
  const atts = CP.pending.map(({id, name, kind, size}) => ({id, name, kind, size}));
  if (!text && !atts.length) return;
  if (!text) text = "Here are the attached files.";
  try {
    if (!CP.convId) { CP.convId = (await post("/api/copilot/convs", {})).id; store.set("cpConv", CP.convId); CP.title = text.slice(0, 70); }
    CP.view = "chat"; CP.pending = []; $("#cpText").value = ""; $("#cpText").style.height = "auto";
    CP.messages.push({role: "user", content: text, attachments: atts});
    CP.busy = true;  // the button is "Stop" from the very first moment (no double send)
    const bot = {role: "assistant", content: "", steps: [], suggestions: [], status: "streaming"}; CP.messages.push(bot); drawCopilot();
    const r = await post(`/api/copilot/convs/${CP.convId}/send`, {content: text, attachments: atts, context: pageContext()});
    await follow(r.run_id, bot);
  } catch (e) { toast("Copilot: " + e.message, 6000); CP.busy = false; drawCopilot(); }
}
async function openCopilot(prefill) {
  $("#copilot").hidden = false;
  const legacy = store.get("copilot2", []);
  if (legacy.length) { try { CP.convId = (await post("/api/copilot/convs/import", {messages: legacy})).id; store.set("cpConv", CP.convId); } catch {} store.set("copilot2", []); }
  if (CP.convId && !CP.messages.length) await loadConv(CP.convId); else drawCopilot();
  if (prefill) $("#cpText").value = prefill; $("#cpText").focus();
}
function initCopilot() {
  $("#copilotBtn").innerHTML = `${icon("spark")}<span class="lbl">Copilot</span>`;
  $("#cpClose").innerHTML = icon("x"); $("#cpNew").innerHTML = icon("plus");
  $("#cpNew").insertAdjacentHTML("beforebegin", `<button class="icon-btn" id="cpHist" title="Conversation history" aria-label="Conversation history">${icon("history")}</button>
    <button class="icon-btn" id="cpWide" title="Expand" aria-label="Expand Copilot">${icon("expand")}</button>`);
  $("#cpHist").onclick = () => { CP.view = CP.view === "history" ? "chat" : "history"; drawCopilot(); };
  $("#cpWide").onclick = () => $("#copilot").classList.toggle("wide");
  $("#copilotBtn").onclick = () => $("#copilot").hidden ? openCopilot() : ($("#copilot").hidden = true);
  $("#cpClose").onclick = () => $("#copilot").hidden = true;
  $("#cpNew").onclick = () => { CP.ctrl?.abort(); CP.busy = false; CP.convId = null; CP.title = ""; CP.messages = []; CP.view = "chat"; store.set("cpConv", null); drawCopilot(); };
  $("#cpForm").onsubmit = e => { e.preventDefault(); sendCopilot($("#cpText").value); };
  $("#cpText").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!CP.busy) sendCopilot($("#cpText").value); } };
  $("#cpText").oninput = e => { e.target.style.height = "auto"; e.target.style.height = Math.min(200, e.target.scrollHeight) + "px"; drawPending(); };
  $("#cpAttach").innerHTML = icon("clip"); $("#cpAttach").onclick = () => $("#cpFile").click();
  $("#cpFile").onchange = e => { addFiles([...e.target.files]); e.target.value = ""; };
  $("#cpText").addEventListener("paste", e => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); addFiles(files); } });
  const panel = $("#copilot"); let depth = 0;
  panel.addEventListener("dragenter", e => { if ([...e.dataTransfer.types].includes("Files")) { depth++; $("#cpDrop").hidden = false; } });
  panel.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; $("#cpDrop").hidden = true; } });
  panel.addEventListener("dragover", e => e.preventDefault());
  panel.addEventListener("drop", e => { e.preventDefault(); depth = 0; $("#cpDrop").hidden = true; addFiles([...e.dataTransfer.files]); });
  if (store.get("cpOpen", false)) openCopilot();  // reopen after a refresh, and resume a live answer
  new MutationObserver(() => store.set("cpOpen", !$("#copilot").hidden)).observe($("#copilot"), {attributes: true, attributeFilter: ["hidden"]});
}

// ------------------------------------------------------------------ live wiring
let refreshTimer = null;
function scheduleRefresh() {
  if (refreshTimer || document.activeElement?.matches("input, select, textarea") || S.route.page === "settings") return;
  refreshTimer = setTimeout(async () => { refreshTimer = null; await loadFleet(); render(); }, 2500);
}
async function startLive() {
  S.events = await api("/api/events?limit=600");
  S.lastEventAt = S.events.at(-1)?.ts;
  if (!S.alertSeen) { S.alertSeen = S.events.at(-1)?.id || 0; store.set("alertSeen", S.alertSeen); }
  updateBell(); heartbeat();
  const src = new EventSource(`/api/stream?after=${S.events.at(-1)?.id || 0}`);
  src.onopen = () => { S.sse = true; heartbeat(); };
  src.onerror = () => { S.sse = false; heartbeat(); };
  src.onmessage = m => {
    const e = JSON.parse(m.data);
    if (S.events.some(x => x.id === e.id)) return;
    S.events.push(e); if (S.events.length > 3000) S.events.shift();
    S.lastEventAt = e.ts; heartbeat(); updateBell();
    if (S.route.page === "activity" && $("#actFeed") && actMatch(e)) { $("#actFeed .empty")?.remove(); $("#actFeed").insertAdjacentHTML("afterbegin", feedItem(e)); }
    else if (!/^(scout\.done)$/.test(e.type)) scheduleRefresh();
  };
}

(async function main() {
  initChrome(); initCopilot(); parseHash();
  try { S.me = await api("/api/me"); } catch { S.me = null; }
  await loadFleet();
  await startLive();
  render();
  setInterval(heartbeat, 5000);
  setInterval(() => { if (!["settings", "activity"].includes(S.route.page) && !document.activeElement?.matches("input, select")) loadFleet().then(() => render()); }, 60000);
})();
