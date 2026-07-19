"use strict";
// Local-app extras on top of the shared app.js: the My Bots manager and the
// batch Test Run modal. app.js is loaded first, so its globals ($, api,
// postJSON, startReplay, onView, openWs, closeWs, syncPerspSeg) are available.

function wireModal(overlayId, openBtnId, closeBtnId, onOpen) {
  const overlay = $(overlayId);
  const close = () => { overlay.hidden = true; };
  $(openBtnId).onclick = () => { overlay.hidden = false; onOpen && onOpen(); };
  $(closeBtnId).onclick = close;
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  return close;
}

// ---- My Bots ---------------------------------------------------------------
const closeMyBots = wireModal("#mybots-overlay", "#btn-mybots", "#btn-mybots-close", refreshMyBots);

async function refreshMyBots() {
  const host = $("#mybots-list");
  host.innerHTML = `<div class="games-empty">Loading…</div>`;
  let bots = [];
  try {
    bots = (await (await fetch(api("/api/mybots"))).json()).bots || [];
  } catch (e) {
    host.innerHTML = `<div class="games-empty">Could not load bots.</div>`;
    return;
  }
  host.replaceChildren();
  if (!bots.length) host.innerHTML = `<div class="games-empty">No bots yet — add your solution file below.</div>`;
  for (const b of bots) {
    const row = document.createElement("div");
    row.className = "game-row";
    row.innerHTML = `
      <div class="game-main">
        <div class="game-players">${b.name}${b.builtin ? " <span class='game-sub'>(bundled example)</span>" : ""}</div>
        <div class="bot-entry" title="${b.command.join(" ")}">${b.entry}</div>
        <div class="bot-msg" hidden></div>
      </div>
      <span class="game-sub">${b.timeout}s</span>
      <div class="game-actions">
        <button class="btn act-check" title="Play one quick game vs random to verify the bot works">Check ✓</button>
        ${b.builtin ? "" : `<button class="btn btn-danger act-del" title="Remove">🗑</button>`}
      </div>`;
    const msg = row.querySelector(".bot-msg");
    row.querySelector(".act-check").onclick = async (e) => {
      const btn = e.target;
      btn.disabled = true; btn.textContent = "Checking…";
      msg.hidden = false; msg.className = "bot-msg"; msg.textContent = "running one game vs random…";
      try {
        const r = await (await fetch(api(`/api/mybots/${encodeURIComponent(b.name)}/check`), { method: "POST" })).json();
        msg.className = "bot-msg " + (r.ok ? "ok" : "bad");
        msg.textContent = r.message;
      } catch (err) {
        msg.className = "bot-msg bad"; msg.textContent = "check failed: " + err;
      }
      btn.disabled = false; btn.textContent = "Check ✓";
    };
    const del = row.querySelector(".act-del");
    if (del) del.onclick = async () => {
      await fetch(api(`/api/mybots/${encodeURIComponent(b.name)}`), { method: "DELETE" });
      refreshMyBots(); refreshBotSelects();
    };
    host.appendChild(row);
  }
  try {
    const info = await (await fetch(api("/api/info"))).json();
    $("#mybots-foot").textContent =
      `v${info.version} · ${info.platform} · py ${info.python} · data: ${info.data_dir}`;
  } catch (e) { /* cosmetic only */ }
}

$("#mb-add").onclick = async () => {
  const msg = $("#mb-msg");
  msg.className = "bot-msg"; msg.textContent = "adding…";
  const body = {
    name: $("#mb-name").value.trim(),
    entry: $("#mb-entry").value.trim(),
    timeout: Number($("#mb-timeout").value) || 2,
  };
  const r = await fetch(api("/api/mybots"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = ""; try { detail = (await r.json()).detail; } catch (e) { detail = await r.text(); }
    msg.className = "bot-msg bad"; msg.textContent = detail || "failed";
    return;
  }
  msg.className = "bot-msg ok"; msg.textContent = `added ${body.name} — hit Check ✓ to verify it`;
  $("#mb-name").value = ""; $("#mb-entry").value = "";
  refreshMyBots(); refreshBotSelects();
};

// Refresh the top-bar (and batch) selects after the bot list changes, keeping
// the current picks where possible.
async function refreshBotSelects() {
  const data = await (await fetch(api("/api/bots"))).json();
  const opt = (v, label) => `<option value="${v}">${label}</option>`;
  let html = `<optgroup label="Built-in">` + (data.bots || []).map((b) => opt(b, b)).join("") + `</optgroup>`;
  if ((data.arena || []).length) {
    html += `<optgroup label="${data.arena_label || "My bots"}">`
      + data.arena.map((a) => opt(a, a.replace("arena:", "") + " ⚙")).join("") + `</optgroup>`;
  }
  for (const id of ["#sel-ship0", "#sel-ship1"]) {
    const sel = $(id); const cur = sel.value;
    sel.innerHTML = html;
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }
  fillBatchSelects(data);
}

// ---- Test Run (batch) ------------------------------------------------------
const closeBatch = wireModal("#batch-overlay", "#btn-batch", "#btn-batch-close", initBatchModal);
let batchPoll = null;   // { id, timer }

async function initBatchModal() {
  if (!$("#bt-ship0").options.length) {
    const data = await (await fetch(api("/api/bots"))).json();
    fillBatchSelects(data);
  }
  if (!$("#bt-map").options.length) {
    const md = await (await fetch(api("/api/maps"))).json();
    $("#bt-map").innerHTML = `<option value="">random</option>`
      + (md.maps || []).map((m) => `<option value="${m}">${m}</option>`).join("");
  }
}

function fillBatchSelects(data) {
  const opt = (v, label) => `<option value="${v}">${label}</option>`;
  // no "human" in headless batches
  let html = `<optgroup label="Built-in">`
    + (data.bots || []).filter((b) => b !== "human").map((b) => opt(b, b)).join("") + `</optgroup>`;
  if ((data.arena || []).length) {
    html += `<optgroup label="${data.arena_label || "My bots"}">`
      + data.arena.map((a) => opt(a, a.replace("arena:", "") + " ⚙")).join("") + `</optgroup>`;
  }
  for (const id of ["#bt-ship0", "#bt-ship1"]) {
    const sel = $(id); const cur = sel.value;
    sel.innerHTML = html;
    if (cur && [...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }
  if (!$("#bt-ship0").value) $("#bt-ship0").value = (data.arena || [])[0] || "heuristic";
  if (!$("#bt-ship1").value) $("#bt-ship1").value = "heuristic";
}

$("#bt-run").onclick = async () => {
  const msg = $("#bt-msg");
  msg.className = "bot-msg"; msg.textContent = "";
  const body = {
    ship0: $("#bt-ship0").value,
    ship1: $("#bt-ship1").value,
    games: Number($("#bt-games").value) || 20,
    seed: $("#bt-seed").value === "" ? null : Number($("#bt-seed").value),
    map_id: $("#bt-map").value || null,
    alternate_first: $("#bt-alt").checked,
    record: $("#bt-record").checked,
  };
  let run;
  try {
    run = await postJSON("/api/batch", body);
  } catch (e) { return; }
  $("#bt-run").hidden = true; $("#bt-stop").hidden = false;
  $("#bt-bar").hidden = false;
  $("#bt-rows").replaceChildren();
  $("#bt-table-wrap").hidden = true;
  pollBatch(run.id);
};

$("#bt-stop").onclick = async () => {
  if (batchPoll) await fetch(api(`/api/batch/${batchPoll.id}/stop`), { method: "POST" });
};

function pollBatch(id) {
  if (batchPoll && batchPoll.timer) clearTimeout(batchPoll.timer);
  batchPoll = { id, timer: null };
  const tick = async () => {
    let st;
    try {
      st = await (await fetch(api(`/api/batch/${id}`))).json();
    } catch (e) { batchPoll.timer = setTimeout(tick, 800); return; }
    renderBatch(st);
    if (st.status === "running") batchPoll.timer = setTimeout(tick, 400);
    else { $("#bt-run").hidden = false; $("#bt-stop").hidden = true; }
  };
  tick();
}

function renderBatch(st) {
  const pct = st.games ? Math.round((100 * st.done) / st.games) : 0;
  $("#bt-fill").style.width = pct + "%";
  const n0 = cname(st.controllers["0"]), n1 = cname(st.controllers["1"]);
  const reasons = Object.entries(st.end_reasons || {})
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ");
  const wr = st.done ? Math.round((100 * st.wins[0]) / st.done) : 0;
  const label = { running: `running… ${st.done}/${st.games}`, done: "finished",
                  stopped: "stopped", error: `error — ${st.error || "?"}` }[st.status] || st.status;
  $("#bt-summary").innerHTML = `
    <div>${label}</div>
    <div><b class="p0">${n0}</b> ${st.wins[0]} — ${st.wins[1]} <b class="p1">${n1}</b>
      (draws ${st.draws})${st.done ? ` · <b class="p0">${n0}</b> win rate ${wr}%` : ""}
      ${st.avg_turns ? ` · avg ${st.avg_turns} turns` : ""}</div>
    ${reasons ? `<div class="game-sub">${reasons}</div>` : ""}`;
  const rows = st.rows || [];
  if (rows.length) {
    $("#bt-table-wrap").hidden = false;
    const tbody = $("#bt-rows");
    // append only the new rows (poll updates arrive incrementally)
    for (let i = tbody.children.length; i < rows.length; i++) {
      const r = rows[i];
      const tr = document.createElement("tr");
      const wcls = r.winner === 0 ? "w0" : r.winner === 1 ? "w1" : "";
      const wname = r.winner === 0 ? n0 : r.winner === 1 ? n1 : "draw";
      tr.innerHTML = `
        <td>${r.game}</td><td>${r.map_id}</td><td>${r.first === 0 ? n0 : n1}</td>
        <td class="${wcls}">${wname}</td><td>${(r.end_reason || "").replace(/_/g, " ")}</td>
        <td>${r.turns}</td>
        <td>${r.rid
          ? `<button class="btn act-replay" data-rid="${r.rid}">Watch ▶</button>`
          : `<button class="btn act-rerun" data-i="${i}">Re-run ▶</button>`}</td>`;
      const rp = tr.querySelector(".act-replay");
      if (rp) rp.onclick = () => { closeBatch(); startReplay(r.rid); };
      const rr = tr.querySelector(".act-rerun");
      if (rr) rr.onclick = () => rerunGame(st, r);
      tbody.appendChild(tr);
    }
  }
}

// Re-create a batch game as a live watchable session (same seed / map / first
// mover; exact for deterministic bots).
async function rerunGame(st, row) {
  const view = await postJSON("/api/game", {
    ship0: st.controllers["0"], ship1: st.controllers["1"],
    seed: row.seed, map_id: row.map_id, first_ship: row.first,
  });
  closeBatch();
  exitReplay();
  closeWs();
  onView(view);
  syncPerspSeg(view);
  openWs(view.game_id);
}
