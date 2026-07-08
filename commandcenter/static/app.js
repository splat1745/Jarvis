/* JARVIS Command Center frontend — vanilla JS, no frameworks. */
"use strict";

const $ = (s) => document.querySelector(s);
const fmt = (n, d = 1) => n == null ? "—" : Number(n).toFixed(d);
const fmtTok = (n) => {
  n = Number(n) || 0;
  const a = Math.abs(n);
  if (a >= 1e12) return (n / 1e12).toFixed(1) + "T";
  if (a >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n | 0);
};

// Auto-scaling unit formatter: rolls a value up/down its unit ladder so it never
// reads "29000MB" when it should say "28.3GB". Handles data sizes (×1024),
// rates (×1024, keeps the /s), and power (×1000). Anything else is left as-is.
const UNIT_LADDERS = [
  { step: 1024, units: ["B", "KB", "MB", "GB", "TB", "PB"] },
  { step: 1024, units: ["B/s", "KB/s", "MB/s", "GB/s", "TB/s"] },
  { step: 1000, units: ["W", "kW", "MW"] },
];
function fmtScaled(value, unit, dec = 1) {
  const v0 = Number(value) || 0;
  const ladder = UNIT_LADDERS.find((l) => l.units.includes(unit));
  if (!ladder) return v0.toFixed(dec) + unit; // %, generic — no scaling
  const start = ladder.units.indexOf(unit);
  let i = start, v = v0;
  while (Math.abs(v) >= ladder.step && i < ladder.units.length - 1) { v /= ladder.step; i++; }
  while (v !== 0 && Math.abs(v) < 1 && i > 0) { v *= ladder.step; i--; }
  // keep a fractional digit when we changed units (so 1500 W → 1.5 kW, not 2 kW)
  const scaled = i !== start;
  const d = Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : (scaled ? 1 : dec);
  return v.toFixed(d) + ladder.units[i];
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const MONO = '"JetBrains Mono", ui-monospace, monospace';
const DIM = "#8095bd";

// ---------------------------------------------------------------- navigation
document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".page").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("#page-" + b.dataset.page).classList.add("active");
    if (b.dataset.page === "terminals") fitAll();
    if (LAST) render(LAST); // canvases are 0-width while hidden; redraw on switch
    if (b.dataset.page === "system") loadProcs();
    if (b.dataset.page === "settings") loadSettings();
    if (b.dataset.page === "board") loadBoard();
    if (b.dataset.page === "files") filesInit();
    if (b.dataset.page === "jarvis") jarvisInit();
    if (b.dataset.page === "hive") squadInit();
  })
);

// ------------------------------------------------------- workspaces + launcher
const TERMS = new Map(); // id -> {id, term, fit, ws, pane, wsName}
let AGENTS = {};         // name -> {cmd, available}
let WS_LIST = JSON.parse(localStorage.getItem("cc-ws") || "[]"); // ordered names
let ACTIVE_WS = localStorage.getItem("cc-active-ws") || "";
let PENDING_RENAME = null; // ws name to auto-open in rename mode after render
let selAgent = "claude", selCount = 1;

const THEME = {
  background: "#070b16", foreground: "#dbe9ff", cursor: "#38e1ff",
  selectionBackground: "#22c5e055",
};

function saveWs() {
  localStorage.setItem("cc-ws", JSON.stringify(WS_LIST));
  localStorage.setItem("cc-active-ws", ACTIVE_WS);
}

function wsOf() {
  const m = new Map();
  TERMS.forEach((t) => {
    if (!m.has(t.wsName)) m.set(t.wsName, []);
    m.get(t.wsName).push(t);
  });
  return m;
}

function nextWsName() {
  let n = 1;
  while (WS_LIST.includes("WS " + n)) n++;
  return "WS " + n;
}

// Guarantee ACTIVE_WS is a real, listed workspace and return it. Without this,
// a fresh/empty ACTIVE_WS gets sent as "" and the server files the terminals
// under "main", which the grid then filters out — so they never appear.
function ensureActiveWs() {
  if (!WS_LIST.length) WS_LIST = ["WS 1"];
  if (!ACTIVE_WS || !WS_LIST.includes(ACTIVE_WS)) ACTIVE_WS = WS_LIST[0];
  saveWs();
  return ACTIVE_WS;
}

function renderWsBar() {
  const bar = $("#ws-bar");
  bar.innerHTML = "";
  const groups = wsOf();
  WS_LIST.forEach((name) => {
    const tab = document.createElement("button");
    const members = groups.get(name) || [];
    const needsAttn = members.some((t) => t.attn);
    tab.className = "ws-tab" + (name === ACTIVE_WS ? " active" : "") + (needsAttn ? " attn" : "");
    tab.innerHTML = `${needsAttn ? '<span class="attn-dot">●</span>' : ""}<span class="ws-name">${esc(name)}</span><span class="ws-count">${members.length}</span><span class="ws-rename-btn" title="rename group">✎</span><span class="ws-close" title="close workspace">✕</span>`;
    tab.title = "click to open · ✎ or double-click to rename";
    tab.onclick = (e) => {
      if (e.target.classList.contains("ws-close")) return closeWorkspace(name);
      if (e.target.classList.contains("ws-rename-btn")) return startRename(tab, name);
      ACTIVE_WS = name; saveWs(); refreshTermView();
    };
    tab.ondblclick = (e) => {
      if (e.target.classList.contains("ws-close")) return;
      startRename(tab, name);
    };
    bar.appendChild(tab);
    if (name === PENDING_RENAME) { PENDING_RENAME = null; startRename(tab, name); }
  });
  const add = document.createElement("button");
  add.className = "ws-tab ws-add";
  add.textContent = "+";
  add.title = "new workspace";
  add.onclick = () => {
    const name = nextWsName();
    WS_LIST.push(name); ACTIVE_WS = name;
    PENDING_RENAME = name; // drop straight into naming the new group
    saveWs(); refreshTermView();
  };
  bar.appendChild(add);
}

function startRename(tab, oldName) {
  const span = tab.querySelector(".ws-name");
  const input = document.createElement("input");
  input.className = "ws-rename";
  input.value = oldName;
  input.maxLength = 32;
  span.replaceWith(input);
  input.focus(); input.select();
  const commit = async () => {
    const newName = input.value.trim();
    if (newName && newName !== oldName && !WS_LIST.includes(newName)) {
      WS_LIST[WS_LIST.indexOf(oldName)] = newName;
      if (ACTIVE_WS === oldName) ACTIVE_WS = newName;
      TERMS.forEach((t) => { if (t.wsName === oldName) t.wsName = newName; });
      saveWs();
      fetch("api/terms/rename_ws", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old: oldName, new: newName }),
      });
    }
    refreshTermView();
  };
  input.onblur = commit;
  input.onkeydown = (e) => {
    if (e.key === "Enter") input.blur();
    if (e.key === "Escape") { input.value = oldName; input.blur(); }
  };
}

async function closeWorkspace(name) {
  const victims = [...TERMS.values()].filter((t) => t.wsName === name);
  for (const t of victims) await destroyTerm(t);
  WS_LIST = WS_LIST.filter((w) => w !== name);
  if (ACTIVE_WS === name) ACTIVE_WS = WS_LIST[WS_LIST.length - 1] || "";
  saveWs(); refreshTermView();
}

function refreshTermView() {
  if (!WS_LIST.length) { WS_LIST = ["WS 1"]; ACTIVE_WS = "WS 1"; saveWs(); }
  if (!WS_LIST.includes(ACTIVE_WS)) ACTIVE_WS = WS_LIST[0];
  renderWsBar();
  const groups = wsOf();
  const active = groups.get(ACTIVE_WS) || [];
  TERMS.forEach((t) => { t.pane.style.display = t.wsName === ACTIVE_WS ? "" : "none"; });
  const empty = active.length === 0;
  $("#launcher").style.display = empty ? "" : "none";
  $("#term-grid").style.display = empty ? "none" : "";
  $("#term-toolbar").classList.toggle("hidden", empty);
  if (!empty) {
    renderToolbar();
    const n = active.length;
    $("#term-grid").style.setProperty("--cols", n <= 1 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : 4);
    fitAll();
  }
  updateLaunchStatus();
}

function renderToolbar() {
  const bar = $("#term-toolbar");
  bar.innerHTML = "";
  for (const [name, info] of Object.entries(AGENTS)) {
    const b = document.createElement("button");
    b.className = "btn slim";
    b.textContent = "+ " + name;
    b.disabled = !info.available;
    b.title = info.available ? info.cmd : info.cmd + " not found on PATH";
    b.onclick = () => spawn(name, 1, ensureActiveWs());
    bar.appendChild(b);
  }
}

// ---------------------------------------------------------------- launcher UI
function renderLauncher() {
  const chips = $("#agent-chips");
  chips.innerHTML = "";
  for (const [name, info] of Object.entries(AGENTS)) {
    const c = document.createElement("button");
    c.className = "chip" + (name === selAgent ? " sel" : "");
    c.textContent = name;
    c.disabled = !info.available;
    c.title = info.available ? info.cmd : info.cmd + " not found on PATH";
    c.onclick = () => { selAgent = name; renderLauncher(); };
    chips.appendChild(c);
  }
  const counts = $("#count-btns");
  counts.innerHTML = "";
  [1, 2, 3, 4, 6, 9, 12, 16].forEach((n) => {
    const b = document.createElement("button");
    b.className = "chip num" + (n === selCount ? " sel" : "");
    b.textContent = n;
    b.onclick = () => { selCount = n; renderLauncher(); };
    counts.appendChild(b);
  });
}

function updateLaunchStatus() {
  const el = $("#launch-status");
  if (!el) return;
  const avail = Object.values(AGENTS).filter((a) => a.available).length;
  const s = LAST;
  el.textContent = `${avail} AGENTS ARMED` +
    (s ? ` · CPU ${fmt(s.cpu, 0)}% · ${fmtScaled(s.watts.total, "W", 0)} · PING ${s.ping_ms ?? "—"}ms` : "");
}

$("#deploy-btn").onclick = () => spawn(selAgent, selCount, ensureActiveWs());
$("#mix-btn").onclick = async () => {
  const ws = ensureActiveWs();
  for (const name of ["claude", "codex", "opencode", "hermes"])
    if (AGENTS[name]?.available) await spawn(name, 1, ws, true);
  refreshTermView();
};

async function spawn(agent, count, ws, quiet) {
  const r = await fetch("api/terms", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent, count, ws, cwd: $("#cwd-input").value || null,
                           prompt: $("#mission-input")?.value?.trim() || "" }),
  });
  const infos = await r.json();
  if (infos.error) return alert(infos.error);
  infos.forEach(attach);
  if (!quiet) refreshTermView();
}

// ---------------------------------------------------------------- dir picker
let pickerPath = null;
async function pickerLoad(path) {
  const r = await fetch("api/browse?path=" + encodeURIComponent(path || "~"));
  const d = await r.json();
  if (d.error) return;
  pickerPath = d.path;
  const el = $("#dir-picker");
  el.innerHTML =
    `<div class="dp-head">
       <button class="dp-btn" data-go="${esc(d.parent || d.path)}">↑</button>
       <button class="dp-btn" data-go="${esc(d.home)}">⌂</button>
       <span class="dp-path" title="${esc(d.path)}">${esc(d.path)}</span>
       <button class="dp-use btn slim deploy">USE THIS</button>
     </div>` +
    (d.dirs.length
      ? d.dirs.map((x) => `<button class="dp-dir" data-go="${esc(x.path)}">${x.git ? "◆" : "▸"} ${esc(x.name)}</button>`).join("")
      : '<span class="dimtext dp-empty">no subfolders</span>');
  el.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => pickerLoad(b.dataset.go)));
  el.querySelector(".dp-use").onclick = () => {
    $("#cwd-input").value = pickerPath;
    localStorage.setItem("cc-cwd", pickerPath);
    el.classList.add("hidden");
  };
}
document.addEventListener("click", (e) => {
  const p = $("#dir-picker");
  if (p && !p.classList.contains("hidden") &&
      !p.contains(e.target) && e.target.id !== "cwd-browse" && e.target.id !== "cwd-input")
    p.classList.add("hidden");
});

// ------------------------------------------------------------ kanban board
const COLS = [["backlog", "BACKLOG"], ["todo", "TO DO"], ["in_progress", "IN PROGRESS"],
              ["review", "REVIEW"], ["done", "DONE"]];
let BOARD = { cards: [] };
let dragId = null;

async function loadBoard() {
  BOARD = await (await fetch("api/kanban")).json();
  renderBoard();
}

async function saveBoard() {
  BOARD = await (await fetch("api/kanban", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(BOARD),
  })).json();
  renderBoard();
}

function cardHtml(c) {
  const live = c.status === "in_progress" && c.term_id;
  return `<div class="kcard" draggable="true" data-id="${c.id}">
    <div class="kcard-top">
      ${live ? '<span class="live">●</span>' : ""}
      <span class="kcard-title">${esc(c.title)}</span>
      <button class="kill kcard-del" title="delete">✕</button>
    </div>
    ${c.desc ? `<div class="kcard-desc">${esc(c.desc)}</div>` : ""}
    <div class="kcard-meta">
      <span class="agent-tag">${esc(c.agent || "claude")}</span>
      <span class="kcard-cwd" title="${esc(c.cwd || "~")}">${esc((c.cwd || "~").split("/").pop() || "~")}</span>
      ${c.status !== "in_progress" && c.status !== "done"
        ? '<button class="btn slim kcard-go" title="dispatch agent now">▶</button>' : ""}
    </div></div>`;
}

function renderBoard() {
  const board = $("#board");
  board.innerHTML = COLS.map(([key, label]) => {
    const cards = BOARD.cards.filter((c) => c.status === key);
    const body = cards.length ? cards.map(cardHtml).join("")
      : `<div class="kcol-empty">${key === "in_progress" ? "drop a card here\nto launch its agent" : "—"}</div>`;
    return `<div class="kcol" data-col="${key}">
      <div class="kcol-head">${label}<span class="ws-count">${cards.length}</span></div>
      <div class="kcol-body">${body}</div>
    </div>`;
  }).join("");

  board.querySelectorAll(".kcard").forEach((el) => {
    el.addEventListener("dragstart", () => (dragId = +el.dataset.id));
    el.querySelector(".kcard-del").onclick = async () => {
      BOARD.cards = BOARD.cards.filter((c) => c.id !== +el.dataset.id);
      await saveBoard();
    };
    const go = el.querySelector(".kcard-go");
    if (go) go.onclick = () => dispatchCard(+el.dataset.id);
  });
  board.querySelectorAll(".kcol").forEach((col) => {
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("over"); });
    col.addEventListener("dragleave", () => col.classList.remove("over"));
    col.addEventListener("drop", async (e) => {
      e.preventDefault(); col.classList.remove("over");
      const card = BOARD.cards.find((c) => c.id === dragId);
      if (!card || card.status === col.dataset.col) return;
      const wasIdle = card.status !== "in_progress";
      card.status = col.dataset.col;
      await saveBoard();
      // Board behaviour: dropping into In Progress dispatches the agent
      if (col.dataset.col === "in_progress" && wasIdle) dispatchCard(card.id);
    });
  });
}

async function dispatchCard(id) {
  const r = await fetch("api/kanban/dispatch", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, ws: ACTIVE_WS }),
  });
  const d = await r.json();
  if (d.error) return alert(d.error);
  BOARD.cards = BOARD.cards.map((c) => (c.id === id ? d.card : c));
  attach(d.term);
  renderBoard();
}

$("#card-new").onclick = async () => {
  const title = prompt("Task title (this becomes the agent's prompt):");
  if (!title) return;
  const desc = prompt("Details (optional):") || "";
  const agent = prompt("Agent (claude / codex / opencode / hermes):", "claude") || "claude";
  const cwd = prompt("Working directory:", $("#cwd-input").value || "~") || "~";
  BOARD.cards.push({ id: 0, title, desc, agent, cwd, status: "backlog", term_id: null });
  await saveBoard();
};

// --------------------------------------------------------------- files page
let filesCwd = null, openFile = null;
async function filesLoad(path) {
  const r = await fetch("api/browse?path=" + encodeURIComponent(path || "~") +
                        ($("#files-hidden").checked ? "&hidden=1" : ""));
  const d = await r.json();
  if (d.error) return alert(d.error);
  filesCwd = d;
  $("#files-path").value = d.path;
  $("#files-list").innerHTML =
    d.dirs.map((x) => `<button class="frow fdir" data-p="${esc(x.path)}">${x.git ? "◆" : "▸"} ${esc(x.name)}</button>`).join("") +
    d.files.map((x) => `<button class="frow ffile" data-p="${esc(x.path)}">· ${esc(x.name)} <span class="fsize">${x.size > 1024 ? (x.size / 1024).toFixed(0) + "k" : x.size}</span></button>`).join("");
  $("#files-list").querySelectorAll(".fdir").forEach((b) => (b.onclick = () => filesLoad(b.dataset.p)));
  $("#files-list").querySelectorAll(".ffile").forEach((b) => (b.onclick = () => fileOpen(b.dataset.p)));
}

async function fileOpen(path) {
  const r = await fetch("api/file?path=" + encodeURIComponent(path));
  const d = await r.json();
  if (d.error) return alert(d.error);
  openFile = d.path;
  $("#file-name").textContent = d.path.split("/").pop();
  $("#file-name").title = d.path;
  $("#file-content").value = d.content;
  $("#file-save").disabled = false;
}

let filesBooted = false;
function filesInit() {
  if (filesBooted) return;
  filesBooted = true;
  filesLoad($("#cwd-input").value || "~");
  $("#files-up").onclick = () => filesCwd?.parent && filesLoad(filesCwd.parent);
  $("#files-home").onclick = () => filesLoad(filesCwd?.home || "~");
  $("#files-path").onchange = () => filesLoad($("#files-path").value);
  $("#files-hidden").onchange = () => filesLoad(filesCwd?.path || "~");
  $("#file-save").onclick = async () => {
    if (!openFile) return;
    await fetch("api/file", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: openFile, content: $("#file-content").value }),
    });
    $("#file-save").textContent = "SAVED ✓";
    setTimeout(() => ($("#file-save").textContent = "SAVE"), 1500);
  };
  $("#preview-go").onclick = () => ($("#preview-frame").src = $("#preview-url").value);
}

// ------------------------------------------------- permission-needed detection
// Agent is genuinely blocking on a user choice. Deliberately narrow: only the
// interactive prompt *widgets* agents draw — NOT prose. Broad words like
// "permission" or "bypass" match the startup banner (esp. with
// --dangerously-skip-permissions) and lit every pane yellow on launch.
const ATTN_RE = /❯\s*1\.\s*yes|\b1\.\s*yes\b[\s\S]{0,40}\b2\.\s*no\b|do you want to (?:proceed|continue|create|make|run|allow|apply|delete|overwrite)|\((?:y\/n|yes\/no)\)|\[y\/n\]|press\s+(?:enter|y)\s+to\s+(?:confirm|continue|proceed)|waiting for (?:your |user )?(?:input|approval|confirmation)/i;
const ANSI_RE = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|[\x00-\x08\x0b\x0c\x0e-\x1f]/g;
const ATTN_GRACE_MS = 3000; // ignore the startup banner burst

function feedAttn(rec, text) {
  rec.tail = ((rec.tail || "") + text).slice(-800);
  if (Date.now() - rec.spawnedAt < ATTN_GRACE_MS) return;
  if (ATTN_RE.test(rec.tail.replace(ANSI_RE, " "))) setAttn(rec, true);
}

function setAttn(rec, on) {
  if (rec.attn === on) return;
  rec.attn = on;
  rec.pane.classList.toggle("attn", on);
  if (rec.wsName !== ACTIVE_WS) renderWsBar(); // dot only matters for hidden ws
}

// --------------------------------------------------------- JARVIS hologram
let jarvisBooted = false, holoLastText = "";
function jarvisInit() {
  drawHolo();
  if (jarvisBooted) return;
  jarvisBooted = true;
  fetch("api/config").then((r) => r.json()).then((c) => {
    const m = c.jarvis?.local_model; if (m) $("#holo-model").textContent = m;
    if (c.jarvis?.voice_pack) $("#holo-pack").value = c.jarvis.voice_pack;
  });
  // code-mode agent list (any real coding agent)
  $("#holo-codeagent").innerHTML = Object.keys(AGENTS)
    .filter((n) => !n.startsWith("jarvis") && n !== "shell")
    .map((n) => `<option>${esc(n)}</option>`).join("");
  $("#holo-codemode").onchange = () => {
    const on = $("#holo-codemode").checked;
    $("#holo-codeagent").classList.toggle("hidden", !on);
    $("#holo-coderepo").classList.toggle("hidden", !on);
    $("#holo-input").placeholder = on
      ? "describe a coding task to dispatch…"
      : "type to JARVIS, or use the voice loop…";
  };

  const send = async () => {
    const text = $("#holo-input").value.trim();
    if (!text) return;
    holoLastText = text;
    $("#holo-you").textContent = text; $("#holo-input").value = "";
    // Code Mode: dispatch the request to a coding agent instead of chatting
    if ($("#holo-codemode").checked) {
      $("#holo-state").textContent = "DISPATCHING…";
      const r = await fetch("api/terms", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent: $("#holo-codeagent").value,
          cwd: $("#holo-coderepo").value || null, prompt: text,
          ws: "voice-" + (Date.now() % 10000) }),
      });
      const d = await r.json();
      if (d.error) { $("#holo-reply").textContent = d.error; $("#holo-state").textContent = "IDLE"; return; }
      d.forEach(attach);
      const ws = d[0]?.ws; if (ws && !WS_LIST.includes(ws)) { WS_LIST.push(ws); ACTIVE_WS = ws; saveWs(); }
      $("#holo-reply").textContent = `Dispatched to ${$("#holo-codeagent").value} in ${d[0]?.cwd || "~"}. Opening Terminals…`;
      $("#holo-state").textContent = "IDLE";
      setTimeout(() => document.querySelector('.tab[data-page="terminals"]').click(), 700);
      return;
    }
    $("#holo-state").textContent = "THINKING…";
    const r = await fetch("api/jarvis/say", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const d = await r.json();
    if (d.error) { $("#holo-reply").textContent = d.error; return; }
    $("#holo-reply").textContent = d.reply;
    $("#holo-state").textContent = "IDLE";
    $("#holo-mood").textContent = d.mood ? "· " + d.mood : "";
  };
  $("#holo-send").onclick = send;
  $("#holo-input").onkeydown = (e) => e.key === "Enter" && send();
  $("#holo-pack").onchange = async () => {
    const cfg = await (await fetch("api/config")).json();
    cfg.jarvis = { ...(cfg.jarvis || {}), voice_pack: $("#holo-pack").value };
    fetch("api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) });
  };
  const rl = (v) => holoLastText && fetch("api/jarvis/say", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: holoLastText, feedback: v }),
  });
  $("#holo-up").onclick = () => { rl("up"); $("#holo-up").textContent = "✓"; };
  $("#holo-down").onclick = () => { rl("down"); $("#holo-down").textContent = "✓"; };
}

// --------------------------------------------------------------- Squad page
let squadBooted = false;
function squadInit() {
  // agent list from the real, non-voice agents
  $("#swarm-agent").innerHTML = Object.keys(AGENTS)
    .filter((n) => !n.startsWith("jarvis") && n !== "shell")
    .map((n) => `<option>${esc(n)}</option>`).join("");
  if (squadBooted) return;
  squadBooted = true;
  const mode = () => document.querySelector('input[name="hive-mode"]:checked').value;
  const syncMode = () => {
    const auto = mode() === "auto";
    $("#workers-wrap").classList.toggle("hidden", auto);
    $("#hive-help-swarm").classList.toggle("hidden", auto);
    $("#hive-help-auto").classList.toggle("hidden", !auto);
    $("#swarm-go").textContent = auto ? "LAUNCH AUTOPILOT" : "LAUNCH HIVE";
    // Autopilot defaults to Hermes (native browsing + screenshots + tools)
    const sel = $("#swarm-agent");
    if (auto && AGENTS.hermes?.available && [...sel.options].some((o) => o.value === "hermes"))
      sel.value = "hermes";
  };
  document.querySelectorAll('input[name="hive-mode"]').forEach((r) => (r.onchange = syncMode));
  syncMode();
  $("#swarm-go").onclick = async () => {
    const mission = $("#swarm-mission").value.trim();
    if (!mission) return alert("describe the mission first");
    const workers = mode() === "auto" ? 0 : +$("#swarm-workers").value;
    const r = await fetch("api/swarm", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mission, agent: $("#swarm-agent").value,
        workers, cwd: $("#swarm-cwd").value || null }),
    });
    const d = await r.json();
    if (d.error) return alert(d.error);
    d.terminals.forEach(attach);
    WS_LIST.includes(d.ws) || WS_LIST.push(d.ws);
    ACTIVE_WS = d.ws; saveWs();
    document.querySelector('.tab[data-page="terminals"]').click();
  };
}

let holoState = { listening: false, thinking: false, wave: [] };
function drawHolo() {
  const c = $("#holo-core"); if (!c) return;
  const [ctx, W, H] = prepCanvas(c);
  const cx = W / 2, cy = H / 2, t = Date.now() / 1000;
  const active = holoState.listening || holoState.thinking;
  const base = Math.min(W, H) * 0.16;
  for (let ring = 0; ring < 3; ring++) {
    const rad = base * (1 + ring * 0.55) + (active ? Math.sin(t * 3 + ring) * 6 * devicePixelRatio : 0);
    ctx.beginPath();
    ctx.arc(cx, cy, rad, 0, Math.PI * 2);
    ctx.strokeStyle = (holoState.thinking ? "#a78bfa" : holoState.listening ? "#34e39b" : "#38e1ff")
      + ["cc", "77", "33"][ring];
    ctx.lineWidth = (2 - ring * 0.4) * devicePixelRatio;
    ctx.stroke();
  }
  // waveform ring when listening
  const wave = holoState.wave || [];
  if (wave.length) {
    ctx.beginPath();
    wave.forEach((v, i) => {
      const a = (i / wave.length) * Math.PI * 2;
      const rad = base * 2.4 + v * base * 1.4;
      const x = cx + Math.cos(a) * rad, y = cy + Math.sin(a) * rad;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.closePath();
    ctx.strokeStyle = "#34e39baa";
    ctx.lineWidth = 1.5 * devicePixelRatio;
    ctx.stroke();
  }
  // core
  ctx.beginPath();
  ctx.arc(cx, cy, base * 0.5 * (active ? 1 + Math.sin(t * 4) * 0.08 : 1), 0, Math.PI * 2);
  ctx.fillStyle = holoState.thinking ? "#a78bfa" : "#38e1ff";
  ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 30 * devicePixelRatio;
  ctx.fill(); ctx.shadowBlur = 0;
}
setInterval(() => { if ($("#page-jarvis")?.classList.contains("active")) drawHolo(); }, 60);

// ---------------------------------------------------------------- terminals
function attach(info) {
  if (TERMS.has(info.id)) return;
  const wsName = info.ws || "main";
  if (!WS_LIST.includes(wsName)) WS_LIST.push(wsName);

  const pane = document.createElement("div");
  pane.className = "term-pane";
  pane.innerHTML = `<div class="term-head">
      <span class="agent">${esc(info.agent)}</span>
      <span class="cwd">${esc(info.cwd)}</span>
      <span class="state"></span>
      <button class="kill" title="kill">✕</button>
    </div><div class="term-body"></div>`;
  $("#term-grid").appendChild(pane);

  const term = new Terminal({
    theme: THEME, fontFamily: '"JetBrains Mono", monospace',
    fontSize: 13, cursorBlink: true, scrollback: 5000, allowProposedApi: true,
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(pane.querySelector(".term-body"));

  const rec = { id: info.id, term, fit, ws: null, pane, wsName,
                spawnedAt: Date.now(), tail: "" };
  const ws = new WebSocket(`ws://${location.host}/ws/term/${info.id}`);
  rec.ws = ws;
  ws.binaryType = "arraybuffer";
  const dec = new TextDecoder();
  ws.onmessage = async (e) => {
    if (typeof e.data === "string" && e.data.startsWith("\x00")) {
      // process exited — remove its pane automatically
      await destroyTerm(rec);
      refreshTermView();
      return;
    }
    term.write(new Uint8Array(e.data));
    feedAttn(rec, dec.decode(e.data, { stream: true }));
  };
  ws.onopen = () => { fit.fit(); sendResize(ws, term); };
  term.onData((d) => {
    setAttn(rec, false); rec.tail = ""; // user responded — clear the alert
    if (ws.readyState === 1) ws.send(d);
  });
  term.onResize(() => sendResize(ws, term));
  pane.addEventListener("mousedown", () => {
    document.querySelectorAll(".term-pane").forEach((p) => p.classList.remove("focused"));
    pane.classList.add("focused");
  });
  pane.querySelector(".kill").onclick = async () => { await destroyTerm(rec); refreshTermView(); };
  TERMS.set(info.id, rec);
}

async function destroyTerm(t) {
  if (!TERMS.has(t.id)) return;
  TERMS.delete(t.id);
  await fetch("api/terms/" + t.id, { method: "DELETE" });
  try { t.ws.close(); } catch (e) {}
  t.term.dispose();
  t.pane.remove();
}

function sendResize(ws, term) {
  if (ws.readyState === 1)
    ws.send("\x00" + JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
}

function fitAll() {
  requestAnimationFrame(() =>
    TERMS.forEach((t) => {
      if (t.pane.style.display === "none") return;
      try { t.fit.fit(); sendResize(t.ws, t.term); } catch (e) {}
    })
  );
}
window.addEventListener("resize", fitAll);

async function boot() {
  AGENTS = await (await fetch("api/agents")).json();
  const list = await (await fetch("api/terms")).json();
  list.forEach(attach);
  const cwd = $("#cwd-input");
  cwd.value = localStorage.getItem("cc-cwd") || "";
  cwd.onchange = () => localStorage.setItem("cc-cwd", cwd.value);
  $("#cwd-browse").onclick = () => {
    const p = $("#dir-picker");
    p.classList.toggle("hidden");
    if (!p.classList.contains("hidden")) pickerLoad(cwd.value || "~");
  };
  renderLauncher();
  refreshTermView();
  jarvisInit(); // JARVIS is the default landing page
}

// ---------------------------------------------------------------- charts
function prepCanvas(canvas) {
  const ctx = canvas.getContext("2d");
  canvas.width = canvas.clientWidth * devicePixelRatio;
  canvas.height = canvas.clientHeight * devicePixelRatio;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  return [ctx, canvas.width, canvas.height];
}

// opts: { unit, dec } → draws min/max/current value markers on series[0].
function sparkline(canvas, series, colors, maxOverride, opts) {
  const [ctx, W, H] = prepCanvas(canvas);
  const dpr = devicePixelRatio;
  const pad = 3 * dpr;
  const flat = series.flat().filter((v) => Number.isFinite(v));
  const dataMax = Math.max(0, ...flat), dataMin = Math.min(0, ...flat);
  const max = maxOverride || Math.max(1, dataMax);
  const yOf = (v) => H - (v / max) * (H - 2 * pad) - pad;

  series.forEach((data, si) => {
    if (!data.length) return;
    const line = new Path2D();
    data.forEach((v, i) => {
      const x = (i / Math.max(data.length - 1, 1)) * W;
      const y = yOf(v);
      i ? line.lineTo(x, y) : line.moveTo(x, y);
    });
    if (si === 0) { // subtle area fill under the primary series
      const area = new Path2D(line);
      area.lineTo(W, H); area.lineTo(0, H); area.closePath();
      ctx.fillStyle = colors[0] + "1f";
      ctx.fill(area);
    }
    ctx.strokeStyle = colors[si];
    ctx.lineWidth = 1.5 * dpr;
    ctx.stroke(line);
  });

  if (!opts || !flat.length) return;
  const s0 = series[0] || [];
  const cur = s0.length ? s0[s0.length - 1] : 0;
  const u = opts.unit || "", d = opts.dec ?? 0;
  const lab = (v) => opts.k ? fmtTok(v) + u : fmtScaled(v, u, d);
  ctx.font = `${9 * dpr}px ${MONO}`;
  ctx.textBaseline = "top";
  // max marker (top-right) and min marker (bottom-right)
  ctx.fillStyle = colors[0] + "cc";
  ctx.textAlign = "right";
  ctx.fillText("▲ " + lab(dataMax), W - 2 * dpr, 2 * dpr);
  ctx.fillStyle = DIM;
  ctx.textBaseline = "bottom";
  ctx.fillText("▼ " + lab(Math.max(0, dataMin)), W - 2 * dpr, H - 2 * dpr);
  // current value dot + label (top-left)
  ctx.fillStyle = colors[0];
  ctx.beginPath();
  ctx.arc(W - 2 * dpr, yOf(cur), 2.2 * dpr, 0, 7);
  ctx.fill();
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(lab(cur), 2 * dpr, 2 * dpr);
}

function barsChart(canvas, values, color) {
  const [ctx, W, H] = prepCanvas(canvas);
  const max = Math.max(1, ...values);
  const bw = W / Math.max(values.length, 1);
  values.forEach((v, i) => {
    const h = (v / max) * (H - 8);
    ctx.fillStyle = color;
    ctx.fillRect(i * bw + 1, H - h, Math.max(bw - 2, 1), h);
  });
}

// series = [[...],[...],...] stacked bottom-up, one color per series
function stackedBars(canvas, series, colors) {
  const [ctx, W, H] = prepCanvas(canvas);
  const n = Math.max(...series.map((s) => s.length), 1);
  const totals = Array.from({ length: n }, (_, i) =>
    series.reduce((a, s) => a + (s[i] || 0), 0));
  const max = Math.max(1, ...totals);
  const bw = W / n;
  for (let i = 0; i < n; i++) {
    let y = H;
    series.forEach((s, si) => {
      const h = ((s[i] || 0) / max) * (H - 8);
      ctx.fillStyle = colors[si];
      ctx.fillRect(i * bw + 1, y - h, Math.max(bw - 2, 1), h);
      y -= h;
    });
  }
}

const DOW = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
function heatmapChart(canvas, grid) { // grid[7][24]
  const [ctx, W, H] = prepCanvas(canvas);
  const dpr = devicePixelRatio;
  const lx = 18 * dpr, ty = 10 * dpr;
  const cw = (W - lx) / 24, ch = (H - ty) / 7;
  const max = Math.max(1, ...grid.flat());
  ctx.font = `${9 * dpr}px JetBrains Mono, monospace`;
  ctx.fillStyle = DIM;
  for (let h = 0; h < 24; h += 3) ctx.fillText(h, lx + h * cw + 1, 8 * dpr);
  for (let d = 0; d < 7; d++) {
    ctx.fillStyle = DIM;
    ctx.fillText(DOW[d], 0, ty + d * ch + ch * 0.75);
    for (let h = 0; h < 24; h++) {
      const v = grid[d][h];
      ctx.fillStyle = v ? `rgba(56,225,255,${0.12 + 0.88 * (v / max)})` : "#0b1122";
      ctx.fillRect(lx + h * cw + dpr, ty + d * ch + dpr, cw - 2 * dpr, ch - 2 * dpr);
    }
  }
}

// ---------------------------------------------------------------- telemetry render
const card = (title, val, sub) =>
  `<div class="card metric"><h3>${title}</h3><div class="val">${val}</div><div class="sub">${sub || ""}</div></div>`;
const bar = (pct, cls = "") =>
  `<div class="bar"><i class="${cls}" style="width:${Math.min(pct, 100)}%"></i></div>`;

let LAST = null;
function render(s) {
  LAST = s;
  $("#mini-cpu").textContent = `CPU ${fmt(s.cpu, 0)}%`;
  $("#mini-w").textContent = fmtScaled(s.watts.total, "W", 0);
  $("#mini-ping").textContent = s.ping_ms == null ? "ping —" : `ping ${s.ping_ms}ms`;
  updateLaunchStatus();

  const u = s.usage, e = s.energy, st = u.stats || {};

  const todayCR = Object.values(u.agents || {}).reduce((a, x) => a + (x.today_cache_read || 0), 0);
  $("#ov-cards").innerHTML =
    card("Tokens today", fmtTok(u.today.tokens), `${st.sessions_today ?? "—"} sessions · ${fmtTok(todayCR)} cache`) +
    card("Cost today", "$" + fmt(u.today.cost, 2), `record $${fmt(st.atd_cost, 0)} (${st.atd_cost_date || "—"})`) +
    card("All-time cost", "$" + fmt(u.total_cost, 2), u.sessions + " sessions") +
    card("All-time tokens", fmtTok(u.total_tokens), "in + out") +
    card("Cache reads", fmtTok(u.cache_read), fmtTok(u.cache_write || 0) + " cache writes") +
    card("Cache hit rate", (st.cache_hit_rate ?? "—") + "%", `record ${fmtTok(st.atd_crtok || 0)} reads (${st.atd_crtok_date || "—"})`) +
    card("Tool calls", fmtTok(u.tools), `avg session ${fmt(st.avg_session_minutes, 0)} min`) +
    card("Record day", fmtTok(st.atd_tok || 0), st.atd_tok_date || "—") +
    card("Busiest hour", st.busiest_hour != null ? st.busiest_hour + ":00" : "—", `${st.busiest_count ?? "—"} active sessions`) +
    card("Peak token hour", st.peak_tok_hour != null ? st.peak_tok_hour + ":00" : "—", `${fmtTok(st.peak_tok_total || 0)} tok · ${st.peak_tok_rl ?? 0} rate-limits`) +
    card("Active agents", s.terminals.filter((t) => t.alive).length, s.terminals.length + " terminals total");

  const h = s.history || [];
  sparkline($("#chart-cpu"), [h.map((x) => x.cpu), h.map((x) => x.mem)], ["#38e1ff", "#fbbf24"], 100, { unit: "%", dec: 0 });
  sparkline($("#chart-w"), [h.map((x) => x.w), h.map((x) => x.gpu_w || 0)], ["#34e39b", "#38e1ff"], 0, { unit: "W", dec: 0 });
  sparkline($("#chart-net"), [h.map((x) => x.rx / 1024), h.map((x) => x.tx / 1024)], ["#38e1ff", "#f87171"], 0, { unit: "KB/s", dec: 0 });

  // per-agent breakdown
  const ag = u.agents || {};
  const agNames = Object.keys(ag);
  if (agNames.length) {
    const maxTok = Math.max(1, ...agNames.map((a) => ag[a].today_input + ag[a].today_output));
    $("#ov-agents").innerHTML = agNames.map((name) => {
      const a = ag[name];
      const tok = a.today_input + a.today_output;
      return `<div class="agent-row">
        <span class="agent-name">${a.active_sessions ? '<span class="live">●</span> ' : ""}${esc(name)}</span>
        <span class="agent-nums">${fmtTok(tok)} today · ${a.today_sessions} sess · ${fmtTok(a.input_tokens + a.output_tokens)} all-time</span>
        ${bar((tok / maxTok) * 100)}
      </div>`;
    }).join("");
  } else {
    $("#ov-agents").innerHTML = '<span class="dimtext">ProductivityTracker collectors not found</span>';
  }

  // model table
  let rows = "";
  const models = Object.entries(u.models).sort((a, b) => (b[1].in + b[1].out) - (a[1].in + a[1].out));
  for (const [m, v] of models.slice(0, 16)) {
    const bad = u.unpriced.includes(m);
    const label = v.agent ? `<span class="agent-tag">${esc(v.agent)}</span>${esc(m.split(":").slice(1).join(":") || m)}` : esc(m);
    rows += `<tr class="${bad ? "unpriced" : ""}"><td>${label}${bad ? " ⚠" : ""}</td>
      <td class="num">${fmtTok(v.in)}</td><td class="num">${fmtTok(v.out)}</td>
      <td class="num">${fmtTok(v.cache)}</td><td class="num">${fmtTok(v.cache_w || 0)}</td>
      <td class="num">$${fmt(v.cost, 2)}</td></tr>`;
  }
  $("#ov-models").innerHTML = `<table><tr><th>model</th><th>in</th><th>out</th><th>cache r</th><th>cache w</th><th>cost</th></tr>${rows || "<tr><td colspan=6>no usage found</td></tr>"}</table>`;

  const recent = u.recent || [];
  $("#ov-recent").innerHTML = recent.length
    ? `<table><tr><th></th><th>agent</th><th>project</th><th>model</th><th>tokens</th></tr>` +
      recent.map((r) => `<tr>
        <td>${r.active ? '<span class="live">●</span>' : ""}</td>
        <td>${esc(r.agent)}</td><td>${esc((r.project || r.title || "—").slice(0, 22))}</td>
        <td>${esc(String(r.model).slice(0, 24))}</td>
        <td class="num">${fmtTok(r.input_tokens + r.output_tokens)}</td></tr>`).join("") + "</table>"
    : '<span class="dimtext">no sessions</span>';

  const days = Object.entries(u.days);
  stackedBars($("#chart-days"),
    [days.map((d) => d[1].in ?? d[1].tokens), days.map((d) => d[1].out ?? 0),
     days.map((d) => d[1].cache ?? 0)],
    ["#38e1ff", "#34e39b", "#a78bfa"]);
  barsChart($("#chart-cost"), days.map((d) => d[1].cost || 0), "#fbbf24");
  $("#days-cost").textContent = "· $" + fmt(days.reduce((a, d) => a + (d[1].cost || 0), 0), 0) + " over " + days.length + "d";

  if (u.heatmap) heatmapChart($("#chart-heat"), u.heatmap);

  $("#ov-projects").innerHTML = (u.by_project || []).length
    ? `<table><tr><th>project</th><th>agents</th><th>sess</th><th>tokens</th></tr>` +
      u.by_project.map((p) => `<tr><td>${esc(p.project.slice(0, 24))}</td>
        <td>${p.agents.map((a) => `<span class="agent-tag">${esc(a)}</span>`).join("")}</td>
        <td class="num">${p.sessions}</td>
        <td class="num">${fmtTok(p.input_tokens + p.output_tokens)}</td></tr>`).join("") + "</table>"
    : '<span class="dimtext">no project data</span>';

  // ---- system page ----
  $("#sys-cards").innerHTML =
    card("CPU", fmt(s.cpu, 0) + "%", s.cpu_cores + " cores") +
    card("Load avg", (s.loadavg || []).map((x) => fmt(x, 1)).join(" "), "1 / 5 / 15 min") +
    card("Memory", fmt(s.mem_pct, 0) + "%", `${s.mem_used_gb} / ${s.mem_total_gb} GB`) +
    card("Processes", s.nproc ?? "—", "running") +
    card("Uptime", fmt(s.uptime_h, 1) + "h", `ping ${s.ping_ms ?? "—"}ms`) +
    card("Power", fmtScaled(s.watts.total, "W", 0), `$${fmt(e.proj_hour, 3)}/h · $${fmt(e.proj_month, 0)}/mo`) +
    card("Energy today", fmt(e.kwh_today, 2) + " kWh", `$${fmt(e.cost_today, 2)} @ ${e.rate_cents}¢ ${e.period}`) +
    card("Energy all-time", fmt(e.kwh_total, 1) + " kWh", `$${fmt(e.cost_total, 2)} · peak ${fmt(e.peak_day_kwh, 1)} kWh/d`);

  const w = s.watts;
  $("#sys-power").innerHTML = ["cpu", "gpu", "display", "baseline", "psu_loss"]
    .map((k) => `<div class="set-row"><label>${k}</label><span class="wval">${fmtScaled(w[k], "W", 1)}</span>
        <div class="bar grow"><i style="width:${(w[k] / w.total) * 100}%"></i></div></div>`).join("")
    + `<div class="set-row"><label><b>total</b></label><b class="wval">${fmtScaled(w.total, "W", 1)}</b></div>`;

  $("#sys-cores").innerHTML = `<div class="cores">` +
    (s.cpu_per_core || []).map((c, i) =>
      `<div class="core-cell" title="core ${i}: ${fmt(c, 0)}%">
        <div class="core-bar"><i class="${c > 85 ? "crit" : c > 60 ? "warn" : ""}" style="height:${c}%"></i></div>
        <span>${i}</span></div>`).join("") + "</div>";

  $("#sys-gpus").innerHTML = s.gpus.length
    ? `<table><tr><th>#</th><th>name</th><th>util</th><th>vram</th><th>power</th><th>temp</th></tr>` +
      s.gpus.map((g) =>
        `<tr><td>${g.index}</td><td>${esc(g.name)}</td><td class="num">${g.util}%</td>
         <td class="num">${fmt(g.mem_used / 1024, 1)}/${fmt(g.mem_total / 1024, 1)}G</td>
         <td class="num">${g.power}/${g.power_limit}W</td><td class="num">${g.temp}°C</td></tr>`).join("") + "</table>"
    : '<span class="dimtext">no NVIDIA GPU detected</span>';
  sparkline($("#chart-gpu-util"), [h.map((x) => x.gpu_util || 0)], ["#38e1ff"], 100, { unit: "%", dec: 0 });
  sparkline($("#chart-gpu-w"), [h.map((x) => x.gpu_w || 0)], ["#34e39b"], 0, { unit: "W", dec: 0 });
  sparkline($("#chart-gpu-mem"), [h.map((x) => x.gpu_mem || 0)], ["#fbbf24"], 0, { unit: "MB", dec: 0 });

  const temps = s.temps || [], fans = s.fans || [];
  $("#sys-sensors").innerHTML = (temps.length || fans.length)
    ? `<table>` +
      temps.map((t) => `<tr><td>${esc(t.label)}</td><td class="dimtext">${esc(t.chip)}</td>
        <td class="num ${t.c > 85 ? "hot" : ""}">${t.c}°C</td></tr>`).join("") +
      fans.map((f) => `<tr><td>${esc(f.label)}</td><td class="dimtext">${esc(f.chip)}</td>
        <td class="num">${f.rpm} rpm</td></tr>`).join("") + "</table>"
    : '<span class="dimtext">no sensor data</span>';

  $("#sys-disks").innerHTML = (s.disks || []).length
    ? `<table><tr><th>device</th><th>mount</th><th>used</th><th></th></tr>` +
      s.disks.map((d) => `<tr><td>${esc(d.dev.replace("/dev/", ""))}</td>
        <td>${esc(d.mount.length > 26 ? "…" + d.mount.slice(-25) : d.mount)}</td>
        <td class="num">${fmt(d.used_gb, 0)}/${fmt(d.total_gb, 0)}G</td>
        <td style="width:30%">${bar(d.pct, d.pct > 90 ? "crit" : d.pct > 75 ? "warn" : "")}</td></tr>`).join("") + "</table>"
    : "";
}

async function loadProcs() {
  const procs = await (await fetch("api/procs")).json();
  $("#sys-procs").innerHTML = `<table><tr><th>pid</th><th>name</th><th>cpu%</th><th>mem%</th></tr>` +
    procs.map((p) => `<tr><td>${p.pid}</td><td>${esc(p.name)}</td>
      <td class="num">${fmt(p.cpu_percent, 1)}</td><td class="num">${fmt(p.memory_percent, 1)}</td></tr>`).join("") + "</table>";
}

// ---------------------------------------------------------------- settings
let CFG = null;
async function loadSettings() {
  buildSettings(await (await fetch("api/config")).json());
}

function buildSettings(cfg) {
  CFG = cfg;

  $("#set-pricing").innerHTML = `<table class="set-table">
    <tr><th>model</th><th>input</th><th>output</th><th>cache read</th></tr>` +
    Object.entries(CFG.pricing_per_mtok).map(([m, p]) => `<tr>
      <td class="model-name" title="${esc(m)}">${esc(m)}</td>
      <td><input type="number" step="0.01" min="0" data-key="pricing|${esc(m)}|input" value="${p.input}"></td>
      <td><input type="number" step="0.01" min="0" data-key="pricing|${esc(m)}|output" value="${p.output}"></td>
      <td><input type="number" step="0.01" min="0" data-key="pricing|${esc(m)}|cache_read" value="${p.cache_read}"></td>
    </tr>`).join("") + "</table>";

  $("#set-agents").innerHTML = `<table class="set-table">` +
    Object.entries(CFG.agents).map(([k, v]) => `<tr>
      <td class="model-name">${esc(k)}</td>
      <td><input class="wide" data-key="agents|${esc(k)}" value="${esc(v)}" spellcheck="false"></td>
    </tr>`).join("") + "</table>";

  const grid = (obj, scope, unit) => Object.entries(obj).map(([k, v]) =>
    `<label class="set-cell"><span>${esc(k).replace(/_/g, " ")}</span>
     <input type="number" step="0.1" data-key="${scope}|${esc(k)}" value="${v}"></label>`).join("");
  $("#set-power").innerHTML = grid(CFG.power, "power");
  $("#set-tou").innerHTML = grid(CFG.tou_rates_cents, "tou");

  const memEl = $("#mem-path");
  if (memEl && CFG.shared_memory_dir) memEl.textContent = CFG.shared_memory_dir;

  const jv = CFG.jarvis || {};
  document.querySelectorAll("[data-jkey]").forEach((inp) => {
    const k = inp.dataset.jkey, v = jv[k];
    if (inp.type === "checkbox") inp.checked = !!v;
    else if (k === "blocked_words") inp.value = (v || []).join(", ");
    else inp.value = v ?? "";
  });

  $("#set-bypass").checked = !!CFG.bypass_permissions;
  $("#set-bypass-flags").innerHTML = `<table class="set-table">` +
    Object.entries(CFG.bypass_flags || {}).map(([k, v]) => `<tr>
      <td class="model-name">${esc(k)}</td>
      <td><input class="wide" data-key="bypass|${esc(k)}" value="${esc(v)}"
           spellcheck="false" placeholder="no flag"></td>
    </tr>`).join("") + "</table>";
}

$("#save-cfg").onclick = async () => {
  document.querySelectorAll("#page-settings input").forEach((inp) => {
    const parts = inp.dataset.key.split("|");
    if (parts[0] === "pricing") CFG.pricing_per_mtok[parts[1]][parts[2]] = parseFloat(inp.value) || 0;
    if (parts[0] === "power") CFG.power[parts[1]] = parseFloat(inp.value) || 0;
    if (parts[0] === "tou") CFG.tou_rates_cents[parts[1]] = parseFloat(inp.value) || 0;
    if (parts[0] === "agents") CFG.agents[parts[1]] = inp.value;
    if (parts[0] === "bypass") (CFG.bypass_flags ||= {})[parts[1]] = inp.value.trim();
  });
  CFG.bypass_permissions = $("#set-bypass").checked;
  CFG.jarvis = CFG.jarvis || {};
  document.querySelectorAll("[data-jkey]").forEach((inp) => {
    const k = inp.dataset.jkey;
    if (inp.type === "checkbox") CFG.jarvis[k] = inp.checked;
    else if (k === "blocked_words") CFG.jarvis[k] = inp.value.split(",").map((s) => s.trim()).filter(Boolean);
    else if (k === "timeout_s") CFG.jarvis[k] = parseFloat(inp.value) || 90;
    else CFG.jarvis[k] = inp.value;
  });
  await fetch("api/config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(CFG),
  });
  $("#save-msg").textContent = "saved ✓";
  setTimeout(() => ($("#save-msg").textContent = ""), 2000);
  AGENTS = await (await fetch("api/agents")).json();
  renderLauncher();
};

// ---------------------------------------------------------------- SSE
function connectSSE() {
  const es = new EventSource("api/stream");
  es.addEventListener("telemetry", (e) => {
    $("#dot-sse").classList.add("ok");
    render(JSON.parse(e.data));
  });
  es.addEventListener("kanban", (e) => {
    BOARD = JSON.parse(e.data);
    if ($("#page-board").classList.contains("active")) renderBoard();
  });
  es.addEventListener("terms", (e) => {
    // attach panes for terminals spawned server-side (e.g. board dispatch)
    JSON.parse(e.data).forEach((t) => { if (t.alive) attach(t); });
    refreshTermView();
  });
  es.addEventListener("jarvis", (e) => {
    const s = JSON.parse(e.data);
    holoState = { listening: s.listening, thinking: s.thinking, wave: s.wave || [] };
    if (!$("#page-jarvis")) return;
    $("#holo-state").textContent = s.thinking ? "THINKING…" : s.listening ? "LISTENING…" : "IDLE";
    if (s.transcript) $("#holo-you").textContent = s.transcript;
    if (s.reply) $("#holo-reply").textContent = s.reply;
    $("#holo-mood").textContent = s.mood && s.mood !== "neutral" ? "· " + s.mood : "";
  });
  es.onerror = () => {
    $("#dot-sse").classList.remove("ok");
    es.close();
    setTimeout(connectSSE, 3000);
  };
}

boot();
connectSSE();
