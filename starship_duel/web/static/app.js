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
  wireRules();
  $("#btn-step").onclick = () => wsSend({ cmd: "step" });
  $("#btn-play").onclick = () => { wsSend({ cmd: "play", delay: speed() }); setPlaying(true); };
  $("#btn-pause").onclick = () => { wsSend({ cmd: "pause" }); setPlaying(false); };

  await newGame();
}

function speed() { return (1280 - Number($("#inp-speed").value)) / 1000; }

// ---- rules modal -----------------------------------------------------------
function wireRules() {
  const overlay = $("#rules-overlay");
  const open = () => { overlay.hidden = false; };
  const close = () => { overlay.hidden = true; };
  $("#btn-rules").onclick = open;
  $("#btn-rules-close").onclick = close;
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
}

// ---- game lifecycle --------------------------------------------------------
async function newGame() {
  closeWs();
  const body = {
    ship0: $("#sel-ship0").value,
    ship1: $("#sel-ship1").value,
    seed: $("#inp-seed").value === "" ? null : Number($("#inp-seed").value),
  };
  const view = await postJSON("/api/game", body);
  onView(view);
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
function renderBoard(v) {
  const board = $("#board");
  board.replaceChildren();
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
    const g = el("g");
    const status = s.status.toLowerCase();
    const kind = s.binary ? "binary" : "single";
    const ssz = sysSize(s);
    const ring = s.owner === 0 ? "ring-owned-p1" : s.owner === 1 ? "ring-owned-p2" : "ring-neutral";

    if (candidate.has(s.name)) g.appendChild(centered("marker-candidate", s.x, s.y, ssz * 0.94, "candidate-halo"));
    g.appendChild(centered(ring, s.x, s.y, ssz + 12));
    g.appendChild(centered(`sys-${kind}-${status}`, s.x, s.y, ssz));
    if (jumpDests.has(s.name)) g.appendChild(el("circle", { cx: s.x, cy: s.y, r: ssz / 2 + 9, class: "jump-ring" }));

    // cache
    if (s.cache) {
      const cx = s.x + ssz * 0.38, cy = s.y - ssz * 0.38;
      g.appendChild(centered(s.cache.kind === "ENERGY" ? "cache-energy" : "cache-overcharge", cx, cy, 28));
      if (s.cache.kind === "ENERGY") g.appendChild(text(cx, cy + 22, `+${s.cache.value}`, "sys-sub"));
    }
    // ownership flag
    if (s.owner !== null && s.owner !== undefined)
      g.appendChild(centered(s.owner === 0 ? "flag-p1" : "flag-p2", s.x - ssz * 0.40, s.y - ssz * 0.36, 26));

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

  // ships — fly in orbit around the star, not on top of it
  for (const sh of v.ships) {
    if (!sh.position) continue;
    const node = byName[sh.position];
    const orbit = sysSize(node) * 0.52;
    // opposite orbit slots so co-located ships never overlap
    const ang = sh.id === 0 ? -Math.PI * 0.62 : Math.PI * 0.38;
    board.appendChild(renderShip(sh, node.x + orbit * Math.cos(ang), node.y + orbit * Math.sin(ang)));
  }
}

function sysSize(s) { return s.binary ? 112 : 92; }

function renderShip(sh, x, y) {
  const g = el("g", { class: "ship-bob" });
  const warm = sh.id === 0;
  const size = 46;
  g.appendChild(centered(warm ? "ship-warm-flame" : "ship-cool-flame", x, y + 5, size));
  const body = centered(warm ? "ship-warm" : "ship-cool", x, y, size);
  if (sh.cloaked) body.setAttribute("opacity", "0.5");
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
  for (const h of v.hud) {
    const card = document.createElement("div");
    card.className = `ship-card p${h.id}` + (v.turn_ship === h.id && !v.done ? " active" : "");
    const known = h.position !== null && h.position !== undefined;
    const posHtml = known ? `<b>${h.position}</b>` : `<span class="pos-hidden">hidden</span>`;
    const badges = Object.entries(h.unlocked).filter(([, on]) => on)
      .map(([k]) => `<span class="badge">${UNLOCK_BADGE[k]}</span>`).join("");
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

// ---- util ------------------------------------------------------------------
async function postJSON(url, body) {
  const r = await fetch(api(url), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { const t = await r.text(); alert("Error: " + t); throw new Error(t); }
  return r.json();
}

boot();
