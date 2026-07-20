"use strict";

const SVGNS = "http://www.w3.org/2000/svg";
const $ = (sel) => document.querySelector(sel);

// Optional shared access token (when the server sets STARSHIP_ACCESS_TOKEN):
// read from the page URL (?token=…) and attach to every API/WS request.
const TOKEN = new URLSearchParams(location.search).get("token");
function api(path) {
  if (!TOKEN) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN);
}

// ---- sprite id maps --------------------------------------------------------
const ACT_ICON = {
  JUMP: "act-jump", HOLD: "act-hold", CLAIM: "act-claim", FIRE: "act-fire",
  SCAN: "act-scan", DEEP_CLOAK: "act-cloak", OVERCHARGE: "act-overcharge",
  UNLOCK_PROXIMITY_ALERT: "act-unlock-proximity",
  UNLOCK_LONG_RANGE_SCANNERS: "act-unlock-scanners",
  UNLOCK_JAMMING: "act-unlock-jamming", END_TURN: "hud-timer",
};
const UNLOCK_BADGE = { proximity_alert: "PROX", long_range_scanners: "LRS", jamming: "JAM" };

// ---- what each action does (shown on hover) --------------------------------
const ACT_DESC = {
  JUMP: "Move to an adjacent system. Entering a rival-claimed or rival-occupied system exposes your position.",
  HOLD: "Stay put and slip back under cloak — the way to disappear again after being spotted.",
  CLAIM: "Take the system you're standing on for income (binaries pay more) — but claiming exposes your position.",
  FIRE: "Attack your current system. Instant win if the rival is here; otherwise a public miss (and it exposes you if they have Proximity Alert).",
  SCAN: "Spend Energy to reveal the rival's exact system — unless they are deep-cloaked.",
  DEEP_CLOAK: "Spend Energy to become undetectable for 2 turns — immune to every reveal, even sitting in enemy territory.",
  OVERCHARGE: "Spend Energy to bank +1 extra action for next turn (stacks).",
  UNLOCK_PROXIMITY_ALERT: "Permanent unlock: the rival is revealed whenever their Fire misses near you.",
  UNLOCK_LONG_RANGE_SCANNERS: "Permanent unlock: reveal the rival when you jump into the system they're in.",
  UNLOCK_JAMMING: "Permanent unlock: your Energy-spending actions show to the rival only as a generic “spent Energy”.",
  END_TURN: "End your turn now. Any actions beyond the base 2 left unspent are banked for next turn.",
};

// ---- app state -------------------------------------------------------------
const State = {
  game: null,      // latest view payload
  ws: null,        // websocket for watch mode
  playing: false,
  replay: null,    // { frames, idx, meta, playing, timer } while watching a replay
};

// ---- svg helpers -----------------------------------------------------------
function el(tag, attrs = {}, kids = []) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k === "class") n.setAttribute("class", v);
    else n.setAttribute(k, v);
  }
  for (const k of [].concat(kids)) if (k) n.appendChild(k);
  return n;
}
function use(href, x, y, w, h, cls) {
  return el("use", { href: "#" + href, x, y, width: w, height: h, class: cls });
}
function centered(href, cx, cy, size, cls) {
  return use(href, cx - size / 2, cy - size / 2, size, size, cls);
}

// ---- bootstrap -------------------------------------------------------------
async function boot() {
  // inject sprite sheet so <use href="#id"> resolves
  const svg = await (await fetch("/static/sprites.svg")).text();
  $("#sprite-host").innerHTML = svg;

  // /api/bots returns built-in bots (incl. "human") + external "arena" bots.
  const data = await (await fetch(api("/api/bots"))).json();
  const opt = (v, label) => `<option value="${v}">${label}</option>`;
  const opts = () => {
    let html = `<optgroup label="Built-in">`
      + (data.bots || []).map((b) => opt(b, b)).join("") + `</optgroup>`;
    if ((data.arena || []).length) {
      html += `<optgroup label="Arena (external)">`
        + data.arena.map((a) => opt(a, a.replace("arena:", "") + " ⚙")).join("")
        + `</optgroup>`;
    }
    return html;
  };
  $("#sel-ship0").innerHTML = opts();
  $("#sel-ship1").innerHTML = opts();
  $("#sel-ship0").value = "human";
  $("#sel-ship1").value = "heuristic";

  $("#btn-new").onclick = newGame;
  $("#btn-reset").onclick = resetGame;
  wireGames();
  wireReplay();
  $("#btn-step").onclick = () => wsSend({ cmd: "step" });
  $("#btn-play").onclick = () => { wsSend({ cmd: "play", delay: speed() }); setPlaying(true); };
  $("#btn-pause").onclick = () => { wsSend({ cmd: "pause" }); setPlaying(false); };
  // Watch through a ship's fog of war (or the full "truth" board).
  $("#persp-seg").addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    $("#persp-seg").querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("is-on", b === btn));
    wsSend({ cmd: "perspective", value: btn.dataset.persp });
  });

  await newGame();
}

function speed() { return (1280 - Number($("#inp-speed").value)) / 1000; }

// ---- game lifecycle --------------------------------------------------------
function syncPerspSeg(view) {
  // Reflect the perspective the server is actually serving: truth for spectating
  // two bots, or the human's own (fogged) seat when you're playing.
  const seg = $("#persp-seg");
  if (!seg || !view) return;
  const want = view.perspective === "truth" || view.perspective == null
    ? "truth" : String(view.perspective);
  seg.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("is-on", b.dataset.persp === want));
}

async function newGame() {
  exitReplay();
  closeWs();
  const body = {
    ship0: $("#sel-ship0").value,
    ship1: $("#sel-ship1").value,
    seed: $("#inp-seed").value === "" ? null : Number($("#inp-seed").value),
  };
  const view = await postJSON("/api/game", body);
  onView(view);
  syncPerspSeg(view);     // human game -> your (fogged) seat; bot-vs-bot -> truth
  openWs(view.game_id);   // WS drives Step/Auto in every mode
}

async function resetGame() {
  if (!State.game) return;
  if (State.ws) { wsSend({ cmd: "reset" }); setPlaying(false); return; }
  onView(await postJSON(`/api/game/${State.game.game_id}/reset`, {}));
}

async function humanAction(type, dest) {
  const view = await postJSON(`/api/game/${State.game.game_id}/action`, { type, dest });
  onView(view);
  // If Auto is engaged, let the bot continue after our move.
  if (State.playing && view.can_step) wsSend({ cmd: "play", delay: speed() });
}

// ---- websocket (watch mode) ------------------------------------------------
function openWs(id) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}${api(`/ws/watch/${id}`)}`);
  ws.onmessage = (e) => onView(JSON.parse(e.data));
  ws.onclose = () => { if (State.ws === ws) State.ws = null; setPlaying(false); };
  State.ws = ws;
}
function closeWs() { if (State.ws) { State.ws.close(); State.ws = null; } setPlaying(false); }
function wsSend(msg) { if (State.ws && State.ws.readyState === 1) State.ws.send(JSON.stringify(msg)); }
function setPlaying(p) {
  State.playing = p;
  $("#btn-play").hidden = p; $("#btn-pause").hidden = !p;
}

// ---- top-level view update -------------------------------------------------
function onView(view) {
  if (State.replay) return;  // ignore live updates while a replay is on screen
  State.game = view;
  renderChips(view);
  renderBoard(view);
  renderHud(view);
  renderActions(view);
  renderLog(view);
  renderBanner(view);
  // Step/Auto are available in every mode so you can watch bot actions unfold.
  $("#watch-controls").hidden = false;
  $("#btn-step").disabled = !view.can_step;
  $("#btn-play").disabled = view.done;
  // The action panel is only relevant when a human is involved.
  $("#action-panel").style.display = view.mode === "bot_vs_bot" ? "none" : "";
  // Stop auto-play indicator once the game can no longer advance on its own.
  if (view.done) setPlaying(false);
}

function renderChips(v) {
  const modeName = { human_vs_bot: "Human vs Bot", bot_vs_bot: "Bot vs Bot", human_vs_human: "Hotseat" }[v.mode];
  const cname = (c) => c.replace("arena:", "");
  $("#mode-chip").textContent = `${modeName} · ${cname(v.controllers["0"])} / ${cname(v.controllers["1"])}`;
  const turnChip = $("#turn-chip");
  if (v.done) { turnChip.textContent = "Skirmish over"; turnChip.style.background = "#3a2140"; turnChip.style.color = "#fff"; }
  else {
    turnChip.textContent = `P${v.turn_ship + 1} to move · turn ${v.turn_number}`;
    turnChip.style.background = v.turn_ship === 0 ? "var(--p1)" : "var(--p2-bright)";
    turnChip.style.color = "var(--ink)";
  }
}

// ---- board -----------------------------------------------------------------
// Global size multiplier for the current board: shrink sprites just enough
// that no two systems' stars (or the ships orbiting them) can overlap, however
// densely the planar layout packs them.
let boardScale = 1;
function computeBoardScale(systems) {
  const REACH = 0.55;                      // a node's sprite+orbit reach, as a fraction of its size
  const baseSize = (s) => (s.binary ? 112 : 92);
  let s = 1;
  for (let i = 0; i < systems.length; i++) {
    for (let j = i + 1; j < systems.length; j++) {
      const a = systems[i], b = systems[j];
      const gap = Math.hypot(a.x - b.x, a.y - b.y);
      const need = REACH * (baseSize(a) + baseSize(b));
      if (need > 0) s = Math.min(s, 0.95 * gap / need);
    }
  }
  return Math.max(0.5, Math.min(1, s));     // never upscale; keep a sane floor
}

function renderBoard(v) {
  const board = $("#board");
  board.replaceChildren();
  boardScale = computeBoardScale(v.systems);
  const byName = Object.fromEntries(v.systems.map((s) => [s.name, s]));

  // edges
  const eg = el("g");
  for (const [a, b] of v.edges) {
    const A = byName[a], B = byName[b];
    eg.appendChild(el("line", { x1: A.x, y1: A.y, x2: B.x, y2: B.y, class: "edge" }));
  }
  board.appendChild(eg);

  // which systems are jump targets (human turn)
  const jumpDests = new Set((v.legal_actions || []).filter((a) => a.type === "JUMP").map((a) => a.dest));

  // candidate overlay (rival belief) — union of all candidate sets provided
  const candidate = new Set();
  for (const key of Object.keys(v.candidates || {})) for (const s of v.candidates[key]) candidate.add(s);

  // systems
  for (const s of v.systems) {
    const g = el("g", { class: s.fogged ? "sys-node fogged" : "sys-node" });
    const status = s.status.toLowerCase();
    const kind = s.binary ? "binary" : "single";
    const ssz = sysSize(s);
    const ring = s.fogged ? "ring-fogged"
      : s.owner === 0 ? "ring-owned-p1" : s.owner === 1 ? "ring-owned-p2" : "ring-neutral";

    if (candidate.has(s.name)) g.appendChild(centered("marker-candidate", s.x, s.y, ssz * 0.94, "candidate-halo"));
    g.appendChild(centered(ring, s.x, s.y, ssz + 12, s.fogged ? "fogged-node" : null));
    g.appendChild(centered(`sys-${kind}-${status}`, s.x, s.y, ssz));
    if (jumpDests.has(s.name)) g.appendChild(el("circle", { cx: s.x, cy: s.y, r: ssz / 2 + 9, class: "jump-ring" }));

    // cache
    if (s.cache) {
      const cx = s.x + ssz * 0.38, cy = s.y - ssz * 0.38;
      g.appendChild(centered(s.cache.kind === "ENERGY" ? "cache-energy" : "cache-overcharge", cx, cy, 28 * boardScale));
      if (s.cache.kind === "ENERGY") g.appendChild(text(cx, cy + 22 * boardScale, `+${s.cache.value}`, "sys-sub"));
    }
    // ownership flag
    if (s.owner !== null && s.owner !== undefined)
      g.appendChild(centered(s.owner === 0 ? "flag-p1" : "flag-p2", s.x - ssz * 0.40, s.y - ssz * 0.36, 26 * boardScale));

    // collapse early-warning: a countdown on a system about to go supernova
    if (s.status === "DESTABILIZING" && s.collapse_in != null) {
      g.appendChild(text(s.x, s.y - ssz / 2 - 6, `⚠ ${s.collapse_in}`, "sys-warn"));
    }

    // label
    g.appendChild(text(s.x, s.y + ssz / 2 + 15, s.name, "sys-label"));

    // click-to-jump hit area
    const hit = el("circle", { cx: s.x, cy: s.y, r: ssz / 2, fill: "transparent", class: "sys-hit" });
    if (jumpDests.has(s.name)) hit.onclick = () => humanAction("JUMP", s.name);
    g.appendChild(hit);
    board.appendChild(g);
  }

  // ships — fly in a tight orbit around the star, not on top of it
  for (const sh of v.ships) {
    if (!sh.position) continue;
    const node = byName[sh.position];
    const ssz = sysSize(node);
    const orbit = ssz * 0.34;
    // opposite orbit slots so co-located ships never overlap
    const ang = sh.id === 0 ? -Math.PI * 0.62 : Math.PI * 0.38;
    board.appendChild(renderShip(sh, node.x + orbit * Math.cos(ang), node.y + orbit * Math.sin(ang), ssz * 0.40));
  }
}

function sysSize(s) { return (s.binary ? 112 : 92) * boardScale; }

function renderShip(sh, x, y, size) {
  const g = el("g", { class: "ship-bob" });
  const warm = sh.id === 0;
  size = size || 46;
  g.appendChild(centered(warm ? "ship-warm-flame" : "ship-cool-flame", x, y + 5, size));
  const body = centered(warm ? "ship-warm" : "ship-cool", x, y, size);
  // Keep a cloaked ship clearly visible — the cloak aura/sparkles already
  // signal the state, so only hint at transparency rather than fading it out.
  if (sh.cloaked) body.setAttribute("opacity", "0.82");
  g.appendChild(body);
  if (sh.cloaked) g.appendChild(centered("fx-cloak", x, y, size * 1.1));
  else g.appendChild(centered("fx-exposed", x + size * 0.34, y - size * 0.44, 22));
  return g;
}

function text(x, y, str, cls) {
  const t = el("text", { x, y, class: cls });
  t.textContent = str;
  return t;
}

// ---- HUD -------------------------------------------------------------------
function renderHud(v) {
  const host = $("#hud");
  host.replaceChildren();
  const domTarget = v.domination_target || 0;
  for (const h of v.hud) {
    const card = document.createElement("div");
    card.className = `ship-card p${h.id}` + (v.turn_ship === h.id && !v.done ? " active" : "");
    const known = h.position !== null && h.position !== undefined;
    const posHtml = known ? `<b>${h.position}</b>` : `<span class="pos-hidden">hidden</span>`;
    const badges = Object.entries(h.unlocked).filter(([, on]) => on)
      .map(([k]) => `<span class="badge">${UNLOCK_BADGE[k]}</span>`).join("");
    // Domination race toward the map-control win.
    const dom = (v.domination && v.domination[h.id]) || 0;
    const pct = domTarget ? Math.min(100, Math.round((100 * dom) / domTarget)) : 0;
    const domCol = h.id === 0 ? "var(--p1)" : "var(--p2-bright)";
    const domHtml = domTarget ? `
      <div class="dom" title="Map control — first to ${domTarget} wins">
        <span class="dom-label"><svg class="ic-dom" viewBox="0 0 64 64"><use href="#res-domination"/></svg>CONTROL</span>
        <div class="dom-bar"><div class="dom-fill" style="width:${pct}%;background:${domCol}"></div></div>
        <span class="dom-num">${dom}/${domTarget}</span>
      </div>` : "";
    // Lives (the hunt): filled/empty pips; run to 0 and you're eliminated.
    const livesMax = v.lives_max || 0;
    const livesNow = (v.lives && v.lives[h.id] != null) ? v.lives[h.id] : livesMax;
    let livesHtml = "";
    if (livesMax) {
      let pips = "";
      for (let i = 0; i < livesMax; i++)
        pips += `<svg class="ic-life" viewBox="0 0 24 24"><use href="#${i < livesNow ? "hud-life-filled" : "hud-life-empty"}"/></svg>`;
      livesHtml = `<div class="lives" title="Lives — lose all and you're eliminated">${pips}</div>`;
    }
    card.innerHTML = `
      <svg class="avatar" viewBox="0 0 140 140"><use href="#${h.id === 0 ? "ship-warm" : "ship-cool"}"/></svg>
      <div class="meta">
        <div class="name" style="color:${h.id === 0 ? "var(--p1)" : "var(--p2-bright)"}">Player ${h.id + 1}
          <span style="color:var(--muted);font-weight:700;font-size:11px">${h.cloaked ? "· cloaked" : "· EXPOSED"}</span></div>
        <div class="stats">
          <span class="stat">at ${posHtml}</span>
          <span class="stat"><svg class="ic" viewBox="0 0 64 64"><use href="#res-energy"/></svg><b>${h.energy}</b></span>
          <span class="stat"><svg class="ic" viewBox="0 0 24 24"><use href="#pip-action"/></svg><b>${h.actions_remaining}</b> act</span>
          <span class="stat"><svg class="ic" viewBox="0 0 64 64"><use href="#res-overcharge"/></svg><b>${h.banked_overcharge}</b> banked</span>
        </div>
        ${domHtml}
        ${livesHtml}
        <div class="badges">${badges}</div>
      </div>`;
    host.appendChild(card);
  }
}

// ---- actions ---------------------------------------------------------------
function renderActions(v) {
  const host = $("#actions");
  const pips = $("#actions-pips");
  hideTip();  // avoid a stale tooltip lingering across re-renders
  host.replaceChildren();
  pips.replaceChildren();
  const hint = $("#action-hint");

  if (v.mode === "bot_vs_bot") return;
  if (v.done) { hint.textContent = "Skirmish over — start a new game."; return; }
  if (!v.awaiting_human) {
    const who = v.controllers[String(v.turn_ship)];
    hint.textContent = `${who}'s turn — press Step to watch one action, or Auto to run it.`;
    return;
  }
  hint.textContent = "Tip: glowing systems are jump targets — click them on the map too.";

  const me = v.hud[0];
  for (let i = 0; i < (me ? me.actions_remaining : 0); i++) {
    const s = document.createElementNS(SVGNS, "svg");
    s.setAttribute("class", "ic"); s.setAttribute("viewBox", "0 0 24 24");
    s.appendChild(use("pip-action", 0, 0, 24, 24));
    pips.appendChild(s);
  }

  // Show the whole catalogue; dim what's currently unaffordable/unavailable.
  for (const a of v.action_menu) {
    const btn = document.createElement("button");
    // NB: no `disabled` attribute on unavailable buttons -- disabled controls
    // don't fire hover events, and we want their tooltip to work too. We just
    // style them and withhold the click handler.
    btn.className = `act-btn act-${a.type}`
      + (a.type === "JUMP" && a.enabled ? " jump-highlight" : "")
      + (a.enabled ? "" : " disabled");
    const cost = a.enabled && a.cost ? `<span class="cost">${a.cost}⚡</span>` : "";
    const reason = a.enabled ? "" : `<span class="reason">${a.reason || ""}</span>`;
    btn.innerHTML = `<svg class="ic" viewBox="0 0 64 64"><use href="#${ACT_ICON[a.type]}"/></svg>
                     <span class="body"><span class="label">${a.label}</span>${reason}</span>${cost}`;
    if (a.enabled) btn.onclick = () => { hideTip(); humanAction(a.type, a.dest); };
    btn.addEventListener("mouseenter", () => showTip(btn, a));
    btn.addEventListener("mouseleave", hideTip);
    host.appendChild(btn);
  }
}

// ---- action tooltip --------------------------------------------------------
let _tipEl = null;
function _tip() {
  if (!_tipEl) { _tipEl = document.createElement("div"); _tipEl.id = "act-tooltip"; document.body.appendChild(_tipEl); }
  return _tipEl;
}
function showTip(btn, a) {
  const tip = _tip();
  const cost = a.cost ? `<div class="tip-cost">Cost: ${a.cost}⚡</div>` : "";
  const warn = (!a.enabled && a.reason) ? `<div class="tip-warn">Unavailable — ${a.reason}</div>` : "";
  tip.innerHTML = `<div class="tip-title">${a.label}</div>
                   <div class="tip-desc">${ACT_DESC[a.type] || ""}</div>${cost}${warn}`;
  tip.classList.add("show");
  // Anchor the tooltip's right edge just left of the button (sidebar is on the
  // right, so there's room), clamped into the viewport.
  const r = btn.getBoundingClientRect();
  tip.style.left = "0px"; tip.style.top = "0px"; // reset before measuring
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = r.left - tw - 12;
  if (left < 8) left = Math.min(r.right + 12, window.innerWidth - tw - 8); // flip right if no room
  let top = Math.max(8, Math.min(r.top, window.innerHeight - th - 8));
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
function hideTip() { if (_tipEl) _tipEl.classList.remove("show"); }

// ---- log & banner ----------------------------------------------------------
function renderLog(v) {
  const host = $("#log");
  host.replaceChildren();
  for (const ev of v.events) {
    const d = document.createElement("div");
    d.className = "ev" + (/wins skirmish/.test(ev) ? " win" : /exposed|FIRES|force-fires|detects/.test(ev) ? " hot" : "");
    d.textContent = ev;
    host.appendChild(d);
  }
  host.scrollTop = host.scrollHeight;
}

function renderBanner(v) {
  const b = $("#banner");
  if (!v.done) { b.hidden = true; return; }
  b.hidden = false;
  if (v.winner === null || v.winner === undefined) {
    b.className = "banner draw"; b.textContent = `Draw — ${v.end_reason}`;
  } else {
    b.className = `banner win-${v.winner}`;
    b.textContent = `Player ${v.winner + 1} wins — ${v.end_reason.replace("_", " ")}`;
  }
}

// ---- games browser ---------------------------------------------------------
function wireGames() {
  const overlay = $("#games-overlay");
  const close = () => { overlay.hidden = true; };
  $("#btn-games").onclick = openGames;
  $("#btn-games-close").onclick = close;
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
}

const MODE_LABEL = { human_vs_bot: "Human vs Bot", bot_vs_bot: "Bot vs Bot", human_vs_human: "Hotseat" };
function cname(c) { return String(c).replace("arena:", ""); }
function fmtWhen(sec) {
  if (!sec) return "—";
  const d = new Date(sec * 1000);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
function outcomeText(g) {
  if (g.winner === 0 || g.winner === 1) return `P${g.winner + 1} won`;
  return "Draw";
}

async function openGames() {
  const overlay = $("#games-overlay");
  overlay.hidden = false;
  const host = $("#games-list");
  host.innerHTML = `<div class="games-empty">Loading…</div>`;
  let games = [];
  try {
    games = (await (await fetch(api("/api/games"))).json()).games || [];
  } catch (e) {
    host.innerHTML = `<div class="games-empty">Could not load games.</div>`;
    return;
  }
  if (!games.length) {
    host.innerHTML = `<div class="games-empty">No games recorded yet — finish a skirmish and it'll show up here.</div>`;
    return;
  }
  host.replaceChildren();
  for (const g of games) {
    const row = document.createElement("div");
    row.className = "game-row";
    const players = `${cname(g.controllers["0"])} vs ${cname(g.controllers["1"])}`;
    const winCls = g.winner === 0 ? "win-0" : g.winner === 1 ? "win-1" : "draw";
    row.innerHTML = `
      <span class="game-when">${fmtWhen(g.created)}</span>
      <div class="game-main">
        <div class="game-players">${players}</div>
        <div class="game-sub">${MODE_LABEL[g.mode] || g.mode} · ${g.map_id} · ${g.plies} plies · ${(g.end_reason || "").replace(/_/g, " ")}</div>
      </div>
      <span class="game-outcome ${winCls}">${outcomeText(g)}</span>
      <div class="game-actions">
        <button class="btn btn-primary act-watch">Watch ▶</button>
        <button class="btn btn-danger act-del" title="Delete">🗑</button>
      </div>`;
    row.querySelector(".act-watch").onclick = () => startReplay(g.rid);
    row.querySelector(".act-del").onclick = async () => {
      await fetch(api(`/api/games/${g.rid}`), { method: "DELETE" });
      openGames();
    };
    host.appendChild(row);
  }
}

// ---- replay ----------------------------------------------------------------
function wireReplay() {
  $("#rp-prev").onclick = () => { replayPause(); replaySeek(State.replay.idx - 1); };
  $("#rp-next").onclick = () => { replayPause(); replaySeek(State.replay.idx + 1); };
  $("#rp-play").onclick = replayPlay;
  $("#rp-pause").onclick = replayPause;
  $("#rp-exit").onclick = () => {
    exitReplay();
    // Return to the live game you were in (reconnect its socket); if there is
    // none, just start a fresh one.
    if (State.game && State.game.game_id) { onView(State.game); openWs(State.game.game_id); }
    else newGame();
  };
  $("#rp-slider").oninput = (e) => { replayPause(); replaySeek(Number(e.target.value)); };
}
function rpSpeed() { return (1280 - Number($("#rp-speed").value)) / 1000; }

async function startReplay(rid) {
  let data;
  try {
    data = await (await fetch(api(`/api/games/${rid}/replay`))).json();
  } catch (e) { alert("Could not load replay."); return; }
  if (!data.frames || !data.frames.length) { alert("Empty replay."); return; }

  closeWs();                       // detach from any live game
  $("#games-overlay").hidden = true;
  State.replay = { frames: data.frames, idx: 0, meta: data.meta, playing: false, timer: null };

  // Swap the header controls: hide live watch controls, show the replay bar.
  // (.watch-controls sets display:flex, which beats the [hidden] attribute, so
  // hide it with an inline style instead.)
  $("#watch-controls").style.display = "none";
  $("#btn-reset").style.display = "none";
  $("#action-panel").style.display = "none";
  const bar = $("#replay-bar");
  bar.hidden = false;
  const slider = $("#rp-slider");
  slider.max = String(data.frames.length - 1);
  slider.value = "0";
  replaySeek(0);
}

function renderReplayFrame() {
  const rp = State.replay;
  const v = rp.frames[rp.idx];
  renderChips(v);
  renderBoard(v);
  renderHud(v);
  renderLog(v);
  renderBanner(v);
  $("#rp-counter").textContent = `${rp.idx + 1} / ${rp.frames.length}`;
  $("#rp-slider").value = String(rp.idx);
  $("#rp-prev").disabled = rp.idx <= 0;
  $("#rp-next").disabled = rp.idx >= rp.frames.length - 1;
}

function replaySeek(idx) {
  const rp = State.replay;
  if (!rp) return;
  rp.idx = Math.max(0, Math.min(idx, rp.frames.length - 1));
  renderReplayFrame();
}

function replayPlay() {
  const rp = State.replay;
  if (!rp) return;
  if (rp.idx >= rp.frames.length - 1) rp.idx = 0;  // restart from the top
  rp.playing = true;
  $("#rp-play").hidden = true; $("#rp-pause").hidden = false;
  const tick = () => {
    if (!State.replay || !State.replay.playing) return;
    if (State.replay.idx >= State.replay.frames.length - 1) { replayPause(); return; }
    replaySeek(State.replay.idx + 1);
    State.replay.timer = setTimeout(tick, Math.max(rpSpeed(), 0.02) * 1000);
  };
  rp.timer = setTimeout(tick, Math.max(rpSpeed(), 0.02) * 1000);
}

function replayPause() {
  const rp = State.replay;
  if (!rp) return;
  rp.playing = false;
  if (rp.timer) { clearTimeout(rp.timer); rp.timer = null; }
  $("#rp-play").hidden = false; $("#rp-pause").hidden = true;
}

function exitReplay() {
  if (!State.replay) return;
  replayPause();
  State.replay = null;
  $("#replay-bar").hidden = true;
  $("#watch-controls").style.display = "";
  $("#btn-reset").style.display = "";
  $("#action-panel").style.display = "";
  // Caller (New Game / exit button) decides what to render next.
}

// ---- util ------------------------------------------------------------------
async function postJSON(url, body) {
  const r = await fetch(api(url), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { const t = await r.text(); alert("Error: " + t); throw new Error(t); }
  return r.json();
}

boot();
