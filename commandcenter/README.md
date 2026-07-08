# JARVIS Command Center

Local agent cockpit: spawn parallel AI agent terminals (Claude Code,
Codex, OpenCode, Hermes, plain shell) in tabbed workspaces, with live system
telemetry, power/energy costing (Ontario TOU rates), and unified token/cost
tracking across all four agents via ProductivityTracker's collectors.
Entirely local, no frameworks, no build step.

## Run

Cross-platform (Linux, macOS, Windows) with auto-bootstrap on first run:

```bash
./start.sh            # Linux/macOS  → python3 start.py
start.bat             # Windows
python start.py       # any OS, directly
python start.py --voice   # also bootstrap the voice/torch stack (hardware-aware)
# open http://127.0.0.1:7745
```

`start.py` creates the local `.venv`, installs deps (adds `pywinpty` on Windows
for ConPTY terminals), and launches the server — re-runs are instant. `--voice`
also sets up the voice/LLM stack in the root venv, picking the CUDA or CPU
PyTorch wheel from detected hardware. Terminals use POSIX `pty` on Unix and
ConPTY (`pywinpty`) on Windows. Port/LAN binding in `config.json` (7745 because
ProductivityTracker owns 7744 here).

## Pages

- **Terminals** — welcome screen with an animated JARVIS core when a workspace
  is empty: pick an agent, a preset count (up to 16 panes), a directory via
  the folder **browser** (◆ marks git repos), and an optional **mission**
  prompt that is auto-typed into every deployed agent a few seconds after
  spawn. Workspaces are tabs across the top (＋ to add, ✕ to close-and-kill,
  double-click to rename); a terminal removes itself when its process exits.
  On spawn the server pre-accepts the "do you trust this folder" dialog for
  Claude/Codex **and appends per-agent permission-bypass flags**
  (`bypass_permissions` in Settings; claude `--dangerously-skip-permissions`,
  codex `--dangerously-bypass-approvals-and-sandbox`, hermes `--yolo`,
  opencode via `OPENCODE_PERMISSION` env). A pane **glows amber** when its
  agent prints a permission/confirmation prompt; typing in it clears the glow,
  and hidden workspaces show a blinking dot. `jarvis-voice` / `jarvis-gui`
  agents launch the local voice assistant (root `.venv`).
- **JARVIS** — holographic voice page. A live core (idle cyan / listening green
  / thinking violet) with waveform, the current transcript, and JARVIS's reply.
  Type to it or run the `jarvis-voice` agent for speech in/out. The brain
  (`jarvis_brain.py`) is **local-model-only** (Hermes pinned to an Ollama model,
  default `qwen3.5:4b`, with a direct-Ollama fallback — no cloud, no per-token
  cost), reuses the root-Jarvis `profile_db`/`jarvis_tools`, and does: keyword +
  prompt-injection **safety guard**, **voice packs** (professional / Gen-Z) with
  **mood** detection, **wake-word** welcome (pending work + repos), on-device
  **note-taking** ("make a note …"), system status, **RL preference** capture
  (▲/▼), and a gated **cart/order** flow (off by default; asks which cart,
  never buys without an explicit confirm). Web page and the desktop GUI mirror
  the same state. A **Code Mode** toggle turns a spoken/typed request into a
  dispatched coding agent in a chosen repo instead of a chat reply.
- **Hive** — one mission → a whole team of agents, or a single recursive
  **Autopilot**. Swarm mode spawns a coordinator + N workers; Autopilot spawns
  one agent that loops plan→build→test→fix on its own until done. All panes land
  in a shared workspace with a shared memory folder; works with any agent.
- **Board** — kanban task queue (`kanban.json`): Backlog / To Do /
  In Progress / Review / Done. Drag a card into **In Progress** (or hit ▶)
  and the card's agent is spawned in the card's directory with the card text
  as its prompt (+ pointer to the shared memory dir, `shared_memory_dir`).
  When the agent's terminal exits the card auto-moves to Review.
- **Files** — directory tree browser, text file viewer/editor with save, and
  an embedded localhost preview iframe for reviewing what agents shipped.
- **Overview** — unified metrics from ProductivityTracker's collectors:
  per-agent bars with live-session dots, agent-tagged per-model cost table
  (input / output / cache-read / cache-write columns, unpriced models flagged),
  cache hit rate, record days (tokens, cost, cache reads), busiest / peak-token
  hour with rate-limit hits, stacked 30-day in/out/cache bars + daily cost
  bars, activity heatmap, recent sessions, projects.
- **System** — power breakdown (CPU via RAPL powercap with linear-estimate
  fallback, GPUs via nvidia-smi), GPU table, top processes.
- **Settings** — editable model pricing (`input`/`output`/`cache_read`/
  `cache_write` per MTok; cache_write defaults to 1.25× input when unset),
  permission-bypass toggle + per-agent flags, power model watts, TOU rates,
  agent launch commands. Persists to `config.json`.

## MCP server (share the cockpit with external editors)

`jarvis_mcp.py` is a zero-dependency stdio MCP server exposing the board,
shared memory, agent dispatch, and system status to any MCP client (Claude
Code, Codex, OpenCode, Hermes, Cursor). Start the cockpit first, then register
it — for example:

```bash
claude   mcp add jarvis -- .venv/bin/python "$PWD/jarvis_mcp.py"
opencode mcp add jarvis .venv/bin/python "$PWD/jarvis_mcp.py"
hermes   mcp add jarvis -- .venv/bin/python "$PWD/jarvis_mcp.py"
# codex: add [mcp_servers.jarvis] command/args to ~/.codex/config.toml
```

Tools: `board_list`, `board_add`, `dispatch_agent`, `memory_read`,
`memory_write`, `system_status`. Point it at a non-default cockpit with
`JARVIS_CC_URL`.

## Architecture

- FastAPI + uvicorn on `127.0.0.1:7745` (set `"network_mode": "lan"` to bind
  0.0.0.0).
- SSE at `/api/stream` (per-client asyncio queues, max 128, 30s heartbeat,
  circuit breaker: 3 consecutive snapshot failures → 30s back-off).
- Usage metrics: imports `collectors.py` from ProductivityTracker
  (`pt_collectors_dir` in config) — incremental, read-only collectors for
  Hermes (`~/.hermes/state.db`), Claude Code (JSONL transcripts with
  message-id dedup), OpenCode (sqlite), and Codex (sqlite + rollout-file
  token split so cached input isn't double-billed). Falls back to a built-in
  Claude-only JSONL scanner if PT is missing.
- Ontario TOU pricing: summer/winter weekday matrices, weekends and statutory
  holidays off-peak, computus Easter, observed-holiday rollover
  (`test_tou.py` covers it).
- Terminals: `pty` + `subprocess` in their own process groups, bridged to
  xterm.js over WebSocket; `\x00`-prefixed JSON frames carry resize/exit
  control messages. Each terminal carries a workspace tag; the grid filters
  by active workspace tab.

## Deliberately not included

3D universe graph, briefing narratives, kanban/mail widgets, skills-dir
watchdog, sandboxed file editor. Add them if they earn their keep; the
terminal orchestration + unified telemetry core is the productivity payload.
