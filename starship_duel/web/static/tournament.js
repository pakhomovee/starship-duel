"use strict";
/* Tournament page: auth, bot submission, leaderboard, and admin controls.
   Vanilla JS against the FastAPI endpoints; the session lives in an httpOnly
   cookie so we just include credentials on every request. */

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};
const SVGNS = "http://www.w3.org/2000/svg";
// An <svg><use href="#id"> element; the symbol carries its own viewBox, so we
// only set width/height (mixed-size sprites all render correctly this way).
function icon(id, cls, size = 18) {
  const s = document.createElementNS(SVGNS, "svg");
  s.setAttribute("width", size); s.setAttribute("height", size);
  if (cls) s.setAttribute("class", cls);
  const u = document.createElementNS(SVGNS, "use");
  u.setAttribute("href", "#" + id);
  s.appendChild(u);
  return s;
}
async function loadSprites() {
  try { $("#sprite-host").innerHTML = await (await fetch("/static/sprites.svg")).text(); }
  catch (_) { /* icons degrade to empty; page still works */ }
}
const RANK_ICON = { 1: "tour-medal-gold", 2: "tour-medal-silver", 3: "tour-medal-bronze" };
const LANG_ICON = (name) => /\.(cpp|cc|cxx|hpp)$/i.test(name || "") ? "lang-cpp" : "lang-python";

// Preserve a ?token= host-gate (STARSHIP_ACCESS_TOKEN) across API calls.
const ACCESS = new URLSearchParams(location.search).get("token");
function apiUrl(path) {
  if (!ACCESS) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(ACCESS);
}
async function api(path, opts = {}) {
  const res = await fetch(apiUrl(path), { credentials: "same-origin", ...opts });
  let body = null;
  try { body = await res.json(); } catch (_) {}
  if (!res.ok) throw Object.assign(new Error((body && body.detail) || res.statusText), { status: res.status, body });
  return body;
}

const T = window.I18N;
const t = (k, v) => T.t(k, v);

const State = { me: null, scope: "quick" };

// -------------------------------------------------------------- auth ---------
function renderAuth() {
  const box = $("#auth-area");
  box.innerHTML = "";
  if (State.me && State.me.authenticated) {
    const chip = el("span", "user-chip");
    chip.append(icon("tour-user", "chip-user", 18));
    chip.append(el("b", null, State.me.username));
    if (State.me.is_admin) chip.append(el("span", "badge badge-admin", t("t.admin")));
    box.append(chip);
    const out = el("button", "btn btn-ghost", t("t.logout"));
    out.onclick = async () => { await api("/api/logout", { method: "POST" }); await refreshMe(); };
    box.append(out);
  } else {
    const u = el("input"); u.placeholder = t("t.username"); u.id = "li-user";
    const p = el("input"); p.type = "password"; p.placeholder = t("t.password"); p.id = "li-pass";
    const b = el("button", "btn btn-primary", t("t.login"));
    const err = el("span", "hint auth-err");
    const submit = async () => {
      err.textContent = "";
      try {
        await api("/api/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: u.value, password: p.value }),
        });
        await refreshMe();
      } catch (e) { err.textContent = e.message || t("t.login_failed"); }
    };
    b.onclick = submit;
    p.onkeydown = (e) => { if (e.key === "Enter") submit(); };
    box.append(u, p, b, err);
  }
}

async function refreshMe() {
  try { State.me = await api("/api/me"); } catch (_) { State.me = { authenticated: false }; }
  renderAuth();
  const authed = State.me && State.me.authenticated;
  $("#submit-panel").hidden = !authed;
  $("#admin-panel").hidden = !(authed && State.me.is_admin);
  if (authed) loadMySubmissions();
  if (authed && State.me.is_admin) { loadUsers(); loadAllSubs(); loadQueue(); }
}

// --------------------------------------------------------- leaderboard -------
async function loadStandings() {
  const body = $("#board-body");
  try {
    const data = await api("/api/tournament/standings?scope=" + State.scope);
    renderStandings(body, data);
  } catch (e) {
    body.innerHTML = "";
    body.append(el("p", "hint", t("t.standings_fail", { err: e.message })));
  }
}

function locale() { return T.lang() === "ru" ? "ru-RU" : undefined; }
function fmt(x) { return (x >= 0 ? "+" : "") + x.toFixed(2); }

function renderStandings(body, data) {
  body.innerHTML = "";
  const stamp = $("#board-stamp");
  stamp.textContent = data.computed
    ? t("t.updated", { when: new Date(data.computed * 1000).toLocaleString(locale()) })
    : t("t.not_computed");
  if (!data.rows || !data.rows.length) {
    body.append(el("p", "hint", t("t.no_matches")));
    return;
  }
  const BASES = ["random", "heuristic", "hunter", "uppo", "uppo-easy", "ppo-easy", "ppo-medium"];
  // Older snapshots have no `ranked` flag; treat a real rank as ranked.
  const isRanked = (r) => (r.ranked !== undefined ? r.ranked : r.rank != null);
  const ranked = data.rows.filter(isRanked);
  const unranked = data.rows.filter((r) => !isRanked(r));

  if (ranked.length) {
    const table = el("table", "board-table");
    const head = el("tr");
    ["t.col_rank", "t.col_competitor", "t.col_score", "t.col_ci", "t.col_wl", "t.col_games"]
      .forEach((k) => head.append(el("th", null, t(k))));
    table.append(head);
    for (const r of ranked) {
      const tr = el("tr");
      const isBase = BASES.includes(r.id);
      const rank = el("td", "rank");
      if (RANK_ICON[r.rank]) rank.append(icon(RANK_ICON[r.rank], "rank-medal", 24));
      rank.append(el("span", null, "#" + r.rank));
      tr.append(rank);
      const name = el("td");
      name.append(icon("tour-bot", "row-avatar", 22));
      name.append(el("b", null, r.id));
      if (isBase) name.append(el("span", "badge", t("t.baseline")));
      tr.append(name);
      tr.append(el("td", "num", fmt(r.score)));
      tr.append(el("td", "num muted", `[${fmt(r.ci_low)}, ${fmt(r.ci_high)}]`));
      tr.append(el("td", "num", `${r.wins}–${r.losses}`));
      tr.append(el("td", "num muted", String(r.n_games)));
      table.append(tr);
    }
    body.append(table);
  }

  // Competitors with no decisive game yet — shown so an entry never silently
  // disappears, with the reason (all draws, still pending, or a launch error).
  if (unranked.length) {
    const wrap = el("div", "unranked");
    wrap.append(el("h3", "unranked-head", t("t.unranked")));
    for (const r of unranked) {
      const row = el("div", "sub-row");
      row.append(icon("tour-bot", "row-avatar", 20));
      row.append(el("b", null, r.id));
      let why;
      if (r.errored) why = t("t.why_errored", { n: r.errored });
      else if (r.n_games) why = t("t.why_drawn", { n: r.draws || r.n_games });
      else if (r.pending) why = t("t.why_pending", { n: r.pending });
      else why = t("t.why_none");
      row.append(el("span", "badge badge-bad", why));
      const games = (r.wins || 0) + (r.losses || 0) + (r.draws || 0);
      if (games) row.append(el("span", "sub-when", t("t.wld", { w: r.wins, l: r.losses, d: r.draws })));
      if (r.last_error) {
        const msg = el("span", "sub-msg", t("t.error_prefix", { msg: r.last_error }));
        msg.title = r.last_error;  // full text on hover
        row.append(msg);
      }
      wrap.append(row);
    }
    body.append(wrap);
  }
}

// ------------------------------------------------------- submissions ---------
function statusBadge(s) {
  const cls = { validated: "badge-ok", rejected: "badge-bad", pending: "badge" }[s.status] || "badge";
  const label = T.has("t.status_" + s.status) ? t("t.status_" + s.status) : s.status;
  return el("span", "badge " + cls, label + (s.active ? t("t.active_suffix") : ""));
}

function renderSubs(container, subs, showUser) {
  container.innerHTML = "";
  if (!subs.length) { container.append(el("p", "hint", t("t.no_subs"))); return; }
  for (const s of subs) {
    const row = el("div", "sub-row");
    row.append(icon(LANG_ICON(s.filename || s.kind), "sub-lang", 20));
    if (showUser) row.append(el("b", "sub-user", s.username));
    row.append(el("span", "sub-when", new Date(s.created * 1000).toLocaleString(locale())));
    if (s.status === "validated") row.append(icon("tour-verified", "sub-ok", 18));
    row.append(statusBadge(s));
    if (s.message) row.append(el("span", "sub-msg", s.message));
    container.append(row);
  }
}

async function loadMySubmissions() {
  try {
    const data = await api("/api/submissions");
    renderSubs($("#my-subs"), data.submissions, false);
  } catch (_) {}
}

async function uploadBot() {
  const f = $("#sub-file").files[0];
  const out = $("#sub-result");
  if (!f) { out.textContent = t("t.choose_file"); out.className = "sub-result bad"; return; }
  out.textContent = t("t.validating"); out.className = "sub-result";
  const fd = new FormData();
  fd.append("file", f, f.name);
  try {
    const r = await api("/api/submissions", { method: "POST", body: fd });
    out.textContent = (r.status === "validated" ? "✓ " : "✗ ") + (T.has("t.status_" + r.status) ? t("t.status_" + r.status) : r.status) +
      (r.message ? " — " + r.message : "");
    out.className = "sub-result " + (r.status === "validated" ? "ok" : "bad");
    loadMySubmissions();
    loadStandings();
  } catch (e) {
    out.textContent = e.message || t("t.upload_failed");
    out.className = "sub-result bad";
  }
}

// -------------------------------------------------------------- admin --------
async function loadUsers() {
  try {
    const data = await api("/api/admin/users");
    const box = $("#users-list"); box.innerHTML = "";
    for (const u of data.users) {
      const row = el("div", "sub-row");
      row.append(el("b", null, u.username));
      if (u.is_admin) row.append(el("span", "badge badge-admin", t("t.admin")));
      box.append(row);
    }
  } catch (_) {}
}

async function loadAllSubs() {
  try {
    const data = await api("/api/admin/submissions");
    renderSubs($("#all-subs"), data.submissions, true);
  } catch (_) {}
}

async function loadQueue() {
  try {
    const data = await api("/api/tournament/matches?limit=1");
    const c = data.counts || {};
    $("#queue-status").textContent = t("t.queue", {
      pending: c.pending || 0, running: c.running || 0, done: c.done || 0, error: c.error || 0,
    });
  } catch (_) {}
}

async function createUser() {
  const out = $("#nu-result");
  try {
    await api("/api/admin/users", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("#nu-name").value, password: $("#nu-pass").value,
        is_admin: $("#nu-admin").checked,
      }),
    });
    out.textContent = t("t.created"); $("#nu-name").value = ""; $("#nu-pass").value = "";
    loadUsers();
  } catch (e) { out.textContent = e.message || t("t.failed"); }
}

async function adminPost(path, msg) {
  const out = $("#admin-msg");
  out.textContent = t("t.working");
  try {
    const r = await api(path, { method: "POST" });
    out.textContent = msg(r);
    loadQueue(); loadStandings();
  } catch (e) { out.textContent = e.message || t("t.failed"); }
}

// -------------------------------------------------------------- wire up ------
function init() {
  $("#scope-seg").addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    State.scope = btn.dataset.scope;
    $("#scope-seg").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("is-on", b === btn));
    loadStandings();
  });
  $("#sub-upload").onclick = uploadBot;
  $("#nu-create").onclick = createUser;
  $("#sched-base").onclick = () =>
    adminPost("/api/tournament/schedule/baselines?n_each=" + ($("#sched-n").value || 10),
      (r) => t("t.sched_base_ok", { n: r.added }));
  $("#sched-full").onclick = () =>
    adminPost("/api/tournament/schedule/full?n_each=" + ($("#sched-n").value || 10),
      (r) => t("t.sched_full_ok", { n: r.added }));
  $("#recompute-quick").onclick = () =>
    adminPost("/api/tournament/recompute?scope=quick", (r) => t("t.recomputed_quick", { n: r.rows.length }));
  $("#recompute-full").onclick = () =>
    adminPost("/api/tournament/recompute?scope=full", (r) => t("t.recomputed_full", { n: r.rows.length }));

  // Re-render the dynamic panels when the language changes.
  T.onChange(() => { renderAuth(); loadStandings(); if (State.me && State.me.authenticated) {
    loadMySubmissions();
    if (State.me.is_admin) { loadUsers(); loadAllSubs(); loadQueue(); }
  } });

  loadSprites();
  refreshMe();
  loadStandings();
  setInterval(loadStandings, 30000);  // gentle live refresh
}

document.addEventListener("DOMContentLoaded", init);
