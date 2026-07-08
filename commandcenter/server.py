#!/usr/bin/env python3
"""Jarvis Command Center — local agent cockpit.

FastAPI backend on :7744. SSE telemetry, PTY-backed web terminals for
claude/codex/opencode/hermes, GPU + power + Ontario TOU energy costing,
Claude Code token/cost tracking. Frontend served from ./static.
"""
import asyncio
import atexit
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

import sys

# POSIX ptys vs Windows ConPTY (pywinpty). The terminal backend picks the right
# one so the whole cockpit runs on Linux, macOS, and Windows.
IS_WIN = os.name == "nt"
if IS_WIN:
    try:
        from winpty import PtyProcess  # pip install pywinpty
    except Exception:
        PtyProcess = None
else:
    import fcntl
    import pty
    import termios

import psutil
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
ENERGY_PATH = BASE / "energy_state.json"


def load_config():
    return json.loads(CONFIG_PATH.read_text())


CFG = load_config()

# Unified JARVIS brain (local-only LLM, voice packs, safety, tools). Best-effort:
# if its deps/modules are missing the cockpit still runs without the voice page.
JARVIS = None
try:
    from jarvis_brain import Jarvis
    JARVIS = Jarvis(CFG)
except Exception as _e:
    print(f"[JARVIS] brain not loaded: {_e}")

# ProductivityTracker's battle-tested incremental collectors (hermes/claude/
# opencode/codex). Imported read-only; falls back to the built-in claude-only
# scanner when unavailable.
UNIFIED = None
_pt_dir = os.path.expanduser(CFG.get("pt_collectors_dir", ""))
if _pt_dir and (Path(_pt_dir) / "collectors.py").exists():
    sys.path.insert(0, _pt_dir)
    try:
        from collectors import UnifiedCollector
        UNIFIED = UnifiedCollector()
    except Exception:
        UNIFIED = None

# ---------------------------------------------------------------- terminals


class Terminal:
    _next_id = 1

    def __init__(self, agent: str, cmd: str, cwd: str, ws: str = "main",
                 env_extra: dict | None = None):
        self.id = Terminal._next_id
        Terminal._next_id += 1
        self.agent = agent
        self.cwd = cwd
        self.ws = ws
        self.created = time.time()
        self.alive = True
        self.scrollback = bytearray()  # capped replay buffer
        self.queues: set[asyncio.Queue] = set()
        self.loop = asyncio.get_running_loop()
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor",
               **(env_extra or {})}
        if not IS_WIN:
            env.setdefault("DISPLAY", ":0")  # desktop apps (jarvis-gui) need X
        if IS_WIN:
            self._start_win(cmd, cwd, env)
        else:
            self._start_posix(cmd, cwd, env)

    # ---- POSIX (pty) backend ----
    def _start_posix(self, cmd, cwd, env):
        self.winproc = None
        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen(
            ["bash", "-lc", cmd], stdin=slave, stdout=slave, stderr=slave,
            preexec_fn=os.setsid, cwd=cwd, env=env, close_fds=True)
        os.close(slave)
        self.loop.add_reader(self.master, self._on_data)

    def _on_data(self):
        try:
            data = os.read(self.master, 65536)
        except OSError:
            data = b""
        if not data:
            self._close()
            return
        self._feed(data)

    # ---- Windows (ConPTY / pywinpty) backend ----
    def _start_win(self, cmd, cwd, env):
        if PtyProcess is None:
            raise RuntimeError("pywinpty not installed — run: pip install pywinpty")
        self.proc = None
        shell = os.environ.get("COMSPEC", "cmd.exe")
        self.winproc = PtyProcess.spawn(f'{shell} /c {cmd}', cwd=cwd, env=env,
                                        dimensions=(24, 80))
        threading.Thread(target=self._win_reader, daemon=True).start()

    def _win_reader(self):
        while True:
            try:
                s = self.winproc.read(65536)
            except EOFError:
                s = ""
            except Exception:
                s = ""
            if not s:
                self.loop.call_soon_threadsafe(self._close)
                return
            self.loop.call_soon_threadsafe(self._feed, s.encode("utf-8", "replace"))

    # ---- shared ----
    def _feed(self, data: bytes):
        self.scrollback += data
        if len(self.scrollback) > 200_000:
            del self.scrollback[:len(self.scrollback) - 200_000]
        for q in list(self.queues):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    def _close(self):
        if not self.alive:
            return
        self.alive = False
        if not IS_WIN:
            try:
                self.loop.remove_reader(self.master)
                os.close(self.master)
            except OSError:
                pass
        for q in list(self.queues):
            q.put_nowait(None)  # sentinel: terminal ended
        kanban_on_term_exit(self.id)

    def write(self, data: str):
        if not self.alive:
            return
        if IS_WIN:
            try:
                self.winproc.write(data)
            except Exception:
                pass
        else:
            os.write(self.master, data.encode())

    def resize(self, rows: int, cols: int):
        if not self.alive:
            return
        if IS_WIN:
            try:
                self.winproc.setwinsize(rows, cols)
            except Exception:
                pass
        else:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))

    def kill(self):
        if IS_WIN:
            try:
                self.winproc.terminate(force=True)
            except Exception:
                pass
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGHUP)
                os.killpg(self.proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        self._close()

    def _running(self):
        if IS_WIN:
            try:
                return self.winproc.isalive()
            except Exception:
                return False
        return self.proc.poll() is None

    def info(self):
        return {"id": self.id, "agent": self.agent, "cwd": self.cwd,
                "ws": self.ws,
                "alive": self.alive and self._running(),
                "created": self.created}


TERMINALS: dict[int, Terminal] = {}


def spawn_terminal(agent: str, cwd: str, ws: str, prompt: str = "") -> "Terminal":
    """Spawn one agent terminal: pretrust + optional permission bypass +
    optional prompt auto-typed once the agent has started."""
    cmd = CFG["agents"][agent]
    env_extra = {}
    if CFG.get("bypass_permissions"):
        flag = CFG.get("bypass_flags", {}).get(agent, "")
        if flag:
            cmd += " " + flag
        if agent == "opencode":  # opencode TUI has no bypass flag, only env
            env_extra["OPENCODE_PERMISSION"] = json.dumps(
                {"edit": "allow", "bash": "allow", "webfetch": "allow"})
    pretrust(agent, cwd)
    t = Terminal(agent, cmd, cwd, ws, env_extra)
    TERMINALS[t.id] = t
    if prompt:
        # ponytail: fixed startup delay; per-agent readiness detection if it misfires
        delay = float(CFG.get("dispatch_prompt_delay_s", 3.0))
        t.loop.call_later(delay, lambda: t.alive and t.write(prompt + "\r"))
    return t


def pretrust(agent: str, cwd: str):
    """Pre-accept the 'do you trust this folder' dialog for claude/codex so
    spawned terminals start clean."""
    try:
        if agent == "claude":
            cj = Path.home() / ".claude.json"
            d = json.loads(cj.read_text())
            proj = d.setdefault("projects", {}).setdefault(cwd, {})
            if not proj.get("hasTrustDialogAccepted"):
                proj["hasTrustDialogAccepted"] = True
                cj.write_text(json.dumps(d, indent=2))
        elif agent == "codex":
            ct = Path.home() / ".codex" / "config.toml"
            text = ct.read_text() if ct.exists() else ""
            if f'[projects."{cwd}"]' not in text:
                with open(ct, "a") as f:
                    f.write(f'\n[projects."{cwd}"]\ntrust_level = "trusted"\n')
    except (OSError, json.JSONDecodeError):
        pass  # trust is best-effort; the agent will just prompt


# ------------------------------------------------------------ kanban (Board)

KANBAN_PATH = BASE / "kanban.json"
KANBAN_COLS = ("backlog", "todo", "in_progress", "review", "done")
KANBAN = {"cards": [], "next_id": 1}
if KANBAN_PATH.exists():
    try:
        KANBAN.update(json.loads(KANBAN_PATH.read_text()))
    except json.JSONDecodeError:
        pass


def kanban_save():
    KANBAN_PATH.write_text(json.dumps(KANBAN, indent=1))


def kanban_on_term_exit(term_id: int):
    """Agent finished → its card moves to review."""
    moved = False
    for c in KANBAN["cards"]:
        if c.get("term_id") == term_id and c["status"] == "in_progress":
            c["status"] = "review"
            c["term_id"] = None
            moved = True
    if moved:
        kanban_save()
        broadcast("kanban", KANBAN)

# ---------------------------------------------------------- Ontario TOU energy


def easter(year):
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(d):
    # fixed-date holiday on a weekend rolls to next weekday
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7 + (n - 1) * 7)
    return d


def ontario_holidays(year):
    e = easter(year)
    vic = date(year, 5, 24)
    while vic.weekday() != 0:  # Monday strictly before May 25
        vic -= timedelta(days=1)
    return {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 2, 0, 3),          # Family Day
        e - timedelta(days=2),                # Good Friday
        vic,                                  # Victoria Day
        _observed(date(year, 7, 1)),          # Canada Day
        _nth_weekday(year, 8, 0, 1),          # Civic Holiday
        _nth_weekday(year, 9, 0, 1),          # Labour Day
        _nth_weekday(year, 10, 0, 2),         # Thanksgiving
        _observed(date(year, 12, 25)),
        _observed(date(year, 12, 26)),
    }


def tou_period(dt: datetime) -> str:
    d = dt.date()
    if d.weekday() >= 5 or d in ontario_holidays(d.year):
        return "off"
    h = dt.hour
    summer = date(d.year, 5, 1) <= d <= date(d.year, 10, 31)
    if summer:
        if 11 <= h < 17:
            return "on"
        if 7 <= h < 11 or 17 <= h < 19:
            return "mid"
    else:
        if 7 <= h < 11 or 17 <= h < 19:
            return "on"
        if 11 <= h < 17:
            return "mid"
    return "off"


class EnergyStore:
    def __init__(self):
        self.state = {"days": {}, "kwh_total": 0.0, "cost_total": 0.0}
        if ENERGY_PATH.exists():
            try:
                self.state.update(json.loads(ENERGY_PATH.read_text()))
            except json.JSONDecodeError:
                pass
        self.last_tick = time.time()
        self.ticks = 0

    def tick(self, watts: float):
        now = time.time()
        dt_h = (now - self.last_tick) / 3600
        self.last_tick = now
        if dt_h <= 0 or dt_h > 1:  # skip corrupted intervals (sleep/suspend)
            return
        kwh = watts * dt_h / 1000
        rate = CFG["tou_rates_cents"][tou_period(datetime.now())] / 100
        cost = kwh * rate
        day = self.state["days"].setdefault(
            date.today().isoformat(), {"kwh": 0.0, "cost": 0.0})
        day["kwh"] += kwh
        day["cost"] += cost
        self.state["kwh_total"] += kwh
        self.state["cost_total"] += cost
        # keep last 90 days
        for k in sorted(self.state["days"])[:-90]:
            del self.state["days"][k]
        self.ticks += 1
        if self.ticks % 4 == 0:
            self.flush()

    def flush(self):
        ENERGY_PATH.write_text(json.dumps(self.state, indent=1))

    def summary(self, watts: float):
        today = self.state["days"].get(date.today().isoformat(),
                                       {"kwh": 0.0, "cost": 0.0})
        days = self.state["days"]
        peak = max(days.values(), key=lambda d: d["kwh"], default=today)
        rate = CFG["tou_rates_cents"][tou_period(datetime.now())] / 100
        return {
            "period": tou_period(datetime.now()),
            "rate_cents": CFG["tou_rates_cents"][tou_period(datetime.now())],
            "kwh_today": today["kwh"], "cost_today": today["cost"],
            "kwh_total": self.state["kwh_total"],
            "cost_total": self.state["cost_total"],
            "peak_day_kwh": peak["kwh"],
            "avg_day_kwh": sum(d["kwh"] for d in days.values()) / max(len(days), 1),
            "proj_hour": watts / 1000 * rate,
            "proj_day": watts / 1000 * 24 * rate,
            "proj_month": watts / 1000 * 24 * 30 * rate,
            "days": days,
        }


ENERGY = EnergyStore()
atexit.register(ENERGY.flush)

# ------------------------------------------------------------------ telemetry

STATE = {
    "cpu": 0.0, "ping_ms": None, "gpus": [], "watts": {}, "net": {},
    "usage": {}, "history": deque(maxlen=120),
}
_gpu_cache = {"t": 0.0, "data": []}
_powercap_last = None  # (t, microjoules)


def read_gpus():
    if time.time() - _gpu_cache["t"] < 2:
        return _gpu_cache["data"]
    gpus = []
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,"
                 "utilization.gpu,power.draw,power.limit,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout
            for line in out.strip().splitlines():
                f = [x.strip() for x in line.split(",")]
                gpus.append({"index": int(f[0]), "name": f[1],
                             "mem_total": float(f[2]), "mem_used": float(f[3]),
                             "util": float(f[4]),
                             "power": float(f[5]) if f[5] not in ("[N/A]", "N/A") else 0,
                             "power_limit": float(f[6]) if f[6] not in ("[N/A]", "N/A") else 0,
                             "temp": float(f[7])})
        except (subprocess.SubprocessError, ValueError, IndexError):
            pass
    _gpu_cache.update(t=time.time(), data=gpus)
    return gpus


def cpu_watts(cpu_pct: float) -> float:
    global _powercap_last
    # primary: RAPL powercap energy_uj delta
    try:
        uj = int(Path("/sys/class/powercap/intel-rapl:0/energy_uj").read_text())
        now = time.time()
        if _powercap_last:
            t0, uj0 = _powercap_last
            if uj > uj0 and now > t0:
                _powercap_last = (now, uj)
                return (uj - uj0) / (now - t0) / 1e6
        _powercap_last = (now, uj)
    except (OSError, ValueError):
        pass
    # fallback: linear idle→max estimate scaled by utilization
    p = CFG["power"]
    return p["cpu_idle_w"] + (p["cpu_max_w"] - p["cpu_idle_w"]) * cpu_pct / 100


def power_snapshot(cpu_pct: float, gpus):
    p = CFG["power"]
    cpu_w = cpu_watts(cpu_pct)
    gpu_w = sum(g["power"] for g in gpus)
    internal = cpu_w + gpu_w + p["baseline_w"] + p["display_w"]
    psu = internal * p["psu_loss_pct"] / 100
    return {"cpu": round(cpu_w, 1), "gpu": round(gpu_w, 1),
            "baseline": p["baseline_w"], "display": p["display_w"],
            "psu_loss": round(psu, 1), "total": round(internal + psu, 1)}


def tcp_ping(host="1.1.1.1", port=53, timeout=3):
    try:
        t0 = time.time()
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.time() - t0) * 1000, 1)
    except OSError:
        return None


# ------------------------------------------------- Claude Code usage collector

_jsonl_offsets: dict[str, int] = {}
_usage = {"models": {}, "days": {}, "sessions": 0, "tools": 0}


def price_for(model: str, agent: str = ""):
    pricing = CFG["pricing_per_mtok"]
    for key in (f"{agent}:{model}", model):
        if key in pricing:
            return pricing[key]
    for key, p in pricing.items():  # substring fallback
        if key.split(":")[-1] in model:
            return p
    return None


def _cost(p, itok, otok, crtok, cwtok=0):
    # cache writes default to 1.25x input (Anthropic's rate) when unpriced
    return (itok * p.get("input", p.get("in", 0))
            + otok * p.get("output", p.get("out", 0))
            + crtok * p.get("cache_read", 0)
            + cwtok * p.get("cache_write", p.get("input", 0) * 1.25)) / 1e6


def scan_claude_usage():
    root = Path(os.path.expanduser(CFG["claude_projects_dir"]))
    if not root.exists():
        return
    for f in root.glob("*/*.jsonl"):
        key = str(f)
        try:
            size = f.stat().st_size
        except OSError:
            continue
        off = _jsonl_offsets.get(key, 0)
        if size <= off:
            continue
        if off == 0:
            _usage["sessions"] += 1
        try:
            with open(f, "rb") as fh:
                fh.seek(off)
                chunk = fh.read()
                # only consume complete lines; leave partial tail for next scan
                last_nl = chunk.rfind(b"\n")
                if last_nl < 0:
                    continue
                _jsonl_offsets[key] = off + last_nl + 1
                for line in chunk[:last_nl].splitlines():
                    _ingest_line(line, f)
        except OSError:
            continue


def _ingest_line(line: bytes, path: Path):
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return
    msg = rec.get("message") or {}
    u = msg.get("usage")
    if not u:
        return
    model = msg.get("model", "unknown")
    m = _usage["models"].setdefault(model, {"in": 0, "out": 0, "cache": 0,
                                            "cost": 0.0, "turns": 0})
    tin = u.get("input_tokens", 0)
    tout = u.get("output_tokens", 0)
    tcache = u.get("cache_read_input_tokens", 0)
    tcw = u.get("cache_creation_input_tokens", 0)
    m["in"] += tin
    m["out"] += tout
    m["cache"] += tcache
    m["cache_w"] = m.get("cache_w", 0) + tcw
    m["turns"] += 1
    p = price_for(model)
    if p:
        m["cost"] += _cost(p, tin, tout, tcache, tcw)
    if isinstance(msg.get("content"), list):
        _usage["tools"] += sum(1 for c in msg["content"]
                               if isinstance(c, dict) and c.get("type") == "tool_use")
    day = (rec.get("timestamp") or "")[:10] or date.today().isoformat()
    dd = _usage["days"].setdefault(day, {"tokens": 0, "in": 0, "out": 0,
                                         "cache": 0, "cost": 0.0})
    dd["tokens"] += tin + tout
    dd["in"] = dd.get("in", 0) + tin
    dd["out"] = dd.get("out", 0) + tout
    dd["cache"] = dd.get("cache", 0) + tcache
    if p:
        dd["cost"] += _cost(p, tin, tout, tcache, tcw)


_pt_snap = None  # latest UnifiedCollector.snapshot(), refreshed by loop_usage


def _pt_usage_summary():
    s = _pt_snap
    models, unpriced = {}, []
    for m in s["by_model"]:
        key = f'{m["agent"]}:{m["model"]}'
        p = price_for(m["model"], m["agent"])
        cwtok = m.get("cache_write_tokens", 0)
        if p:
            cost = _cost(p, m["input_tokens"], m["output_tokens"],
                         m["cache_read_tokens"], cwtok)
        elif m.get("reported_cost"):  # opencode reports its own spend
            cost = m["reported_cost"]
        else:
            cost = 0.0
            unpriced.append(key)
        models[key] = {"agent": m["agent"], "in": m["input_tokens"],
                       "out": m["output_tokens"], "cache": m["cache_read_tokens"],
                       "cache_w": cwtok, "cost": cost, "turns": m["count"]}
    days = {}
    for d in s["daily"][-30:]:
        cost = sum(_cost(p, v["input"], v["output"], v["cache_read"])
                   for v in d["by_model"].values()
                   if (p := price_for(v["model"], v["agent"])))
        days[d["day"]] = {"tokens": d["input_tokens"] + d["output_tokens"],
                          "in": d["input_tokens"], "out": d["output_tokens"],
                          "cache": d["cache_read_tokens"], "cost": cost}
    today = days.get(date.today().isoformat(), {"tokens": 0, "cost": 0.0})
    st = s.get("stats", {})
    # today's cost from the exact per-model breakdown (more precise than days)
    tbm = st.get("today_by_model") or {}
    if tbm:
        today = dict(today)
        today["cost"] = sum(
            _cost(p, v.get("input", 0), v.get("output", 0), v.get("cache_read", 0))
            for v in tbm.values()
            if (p := price_for(v.get("model", ""), v.get("agent", ""))))
    tools = sum(a.get("tool_calls", 0) for a in s["by_agent"].values())
    return {"models": models, "sessions": s["totals"]["sessions"],
            "tools": tools, "today": today, "days": days,
            "total_cost": sum(m["cost"] for m in models.values()),
            "total_tokens": s["totals"]["input_tokens"] + s["totals"]["output_tokens"],
            "cache_read": s["totals"]["cache_read_tokens"],
            "cache_write": s["totals"].get("cache_write_tokens", 0),
            "unpriced": unpriced,
            "agents": s["by_agent"], "recent": s["recent_sessions"][:15],
            "heatmap": s.get("heatmap"), "by_project": s.get("by_project", [])[:10],
            "stats": st}


def usage_summary():
    if _pt_snap:
        return _pt_usage_summary()
    today = _usage["days"].get(date.today().isoformat(), {"tokens": 0, "cost": 0.0})
    total_cost = sum(m["cost"] for m in _usage["models"].values())
    total_tokens = sum(m["in"] + m["out"] for m in _usage["models"].values())
    unpriced = [m for m in _usage["models"] if not price_for(m)]
    days = {k: _usage["days"][k] for k in sorted(_usage["days"])[-30:]}
    return {"models": _usage["models"], "sessions": _usage["sessions"],
            "tools": _usage["tools"], "today": today, "days": days,
            "total_cost": total_cost, "total_tokens": total_tokens,
            "cache_read": sum(m["cache"] for m in _usage["models"].values()),
            "cache_write": 0,
            "unpriced": unpriced, "agents": {}, "recent": [],
            "heatmap": None, "by_project": [], "stats": {}}


# ----------------------------------------------------------------- SSE + loops

SSE_CLIENTS: set[asyncio.Queue] = set()
_snap_failures = 0
_snap_backoff_until = 0.0


def broadcast(event: str, data):
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for q in list(SSE_CLIENTS):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


_net_last = None


def snapshot():
    global _net_last
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    nio = psutil.net_io_counters()
    now = time.time()
    rx_bps = tx_bps = 0.0
    if _net_last:
        t0, r0, s0 = _net_last
        dt = max(now - t0, 1e-3)
        rx_bps = (nio.bytes_recv - r0) / dt
        tx_bps = (nio.bytes_sent - s0) / dt
    _net_last = (now, nio.bytes_recv, nio.bytes_sent)
    gpus = read_gpus()
    watts = power_snapshot(STATE["cpu"], gpus)

    disks = []
    seen_dev = set()
    for part in psutil.disk_partitions():
        if (part.device in seen_dev or not part.device.startswith("/dev")
                or part.device.startswith("/dev/loop")):
            continue
        seen_dev.add(part.device)
        try:
            du = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append({"dev": part.device, "mount": part.mountpoint,
                      "total_gb": round(du.total / 2**30, 1),
                      "used_gb": round(du.used / 2**30, 1), "pct": du.percent})

    temps, fans = [], []
    try:
        for chip, entries in psutil.sensors_temperatures().items():
            for e in entries:
                if e.current:
                    temps.append({"chip": chip, "label": e.label or chip,
                                  "c": round(e.current, 1)})
        for chip, entries in psutil.sensors_fans().items():
            for e in entries:
                fans.append({"chip": chip, "label": e.label or chip,
                             "rpm": e.current})
    except (AttributeError, OSError):
        pass

    snap = {
        "t": now, "cpu": STATE["cpu"], "cpu_cores": psutil.cpu_count(),
        "cpu_per_core": psutil.cpu_percent(percpu=True),
        "loadavg": list(getattr(os, "getloadavg", lambda: (0, 0, 0))()),
        "mem_pct": vm.percent, "mem_used_gb": round(vm.used / 2**30, 1),
        "mem_total_gb": round(vm.total / 2**30, 1),
        "disk_pct": disk.percent, "disks": disks,
        "temps": temps[:14], "fans": fans[:8],
        "nproc": len(psutil.pids()),
        "uptime_h": round((now - psutil.boot_time()) / 3600, 1),
        "ping_ms": STATE["ping_ms"], "rx_bps": rx_bps, "tx_bps": tx_bps,
        "gpus": gpus, "watts": watts,
        "energy": ENERGY.summary(watts["total"]),
        "usage": usage_summary(),
        "terminals": [t.info() for t in TERMINALS.values()],
    }
    STATE["watts"] = watts
    STATE["history"].append({"t": now, "cpu": STATE["cpu"],
                             "mem": vm.percent, "w": watts["total"],
                             "rx": rx_bps, "tx": tx_bps,
                             "gpu_util": sum(g["util"] for g in gpus) / max(len(gpus), 1),
                             "gpu_w": sum(g["power"] for g in gpus),
                             "gpu_mem": sum(g["mem_used"] for g in gpus)})
    snap["history"] = list(STATE["history"])
    return snap


async def loop_ping():
    while True:
        STATE["ping_ms"] = await asyncio.to_thread(tcp_ping)
        await asyncio.sleep(60)


async def loop_cpu():
    while True:
        STATE["cpu"] = await asyncio.to_thread(psutil.cpu_percent, 1)
        await asyncio.sleep(1)


async def loop_snapshot():
    global _snap_failures, _snap_backoff_until
    while True:
        if time.time() >= _snap_backoff_until:
            try:
                snap = await asyncio.to_thread(snapshot)
                broadcast("telemetry", snap)
                _snap_failures = 0
            except Exception:
                _snap_failures += 1
                if _snap_failures >= 3:  # circuit breaker: 30s back-off
                    _snap_backoff_until = time.time() + 30
                    _snap_failures = 0
        await asyncio.sleep(5)


async def loop_usage():
    global _pt_snap
    while True:
        if UNIFIED:
            try:
                _pt_snap = await asyncio.to_thread(UNIFIED.snapshot)
            except Exception:
                pass
            await asyncio.sleep(5)
        else:
            await asyncio.to_thread(scan_claude_usage)
            await asyncio.sleep(30)


async def loop_energy():
    while True:
        await asyncio.sleep(15)
        ENERGY.tick(STATE["watts"].get("total", 0))


# ------------------------------------------------------------------------- app

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    tasks = [asyncio.create_task(c) for c in
             (loop_ping(), loop_cpu(), loop_snapshot(),
              loop_usage(), loop_energy())]
    yield
    for t in tasks:
        t.cancel()
    ENERGY.flush()
    for term in TERMINALS.values():
        term.kill()


app = FastAPI(title="Jarvis Command Center", lifespan=lifespan)


@app.get("/api/stream")
async def sse_stream():
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    SSE_CLIENTS.add(q)

    async def gen():
        try:
            yield f"event: telemetry\ndata: {json.dumps(await asyncio.to_thread(snapshot))}\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            SSE_CLIENTS.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/api/snapshot")
async def api_snapshot():
    return await asyncio.to_thread(snapshot)


@app.get("/api/agents")
def api_agents():
    def avail(cmd):
        tok = os.path.expanduser(cmd.split()[0])
        return shutil.which(tok) is not None or os.access(tok, os.X_OK)
    return {name: {"cmd": cmd, "available": avail(cmd)}
            for name, cmd in CFG["agents"].items()}


@app.get("/api/terms")
def list_terms():
    return [t.info() for t in TERMINALS.values()]


@app.post("/api/terms")
async def create_term(body: dict):
    agent = body.get("agent", "shell")
    if agent not in CFG["agents"]:
        return JSONResponse({"error": f"unknown agent {agent}"}, status_code=400)
    cwd = os.path.expanduser(body.get("cwd") or CFG["default_cwd"])
    if not os.path.isdir(cwd):
        return JSONResponse({"error": f"bad cwd {cwd}"}, status_code=400)
    ws = str(body.get("ws") or "main")[:32]
    count = min(max(int(body.get("count", 1)), 1), 16)
    prompt = str(body.get("prompt") or "").strip()
    made = [spawn_terminal(agent, cwd, ws, prompt).info() for _ in range(count)]
    broadcast("terms", [x.info() for x in TERMINALS.values()])
    return made


@app.post("/api/terms/rename_ws")
async def rename_ws(body: dict):
    old, new = str(body.get("old", "")), str(body.get("new", ""))[:32]
    if not new:
        return JSONResponse({"error": "empty name"}, status_code=400)
    for t in TERMINALS.values():
        if t.ws == old:
            t.ws = new
    return {"ok": True}


@app.delete("/api/terms/{tid}")
async def kill_term(tid: int):
    t = TERMINALS.pop(tid, None)
    if t:
        t.kill()
    broadcast("terms", [x.info() for x in TERMINALS.values()])
    return {"ok": True}


@app.websocket("/ws/term/{tid}")
async def ws_term(ws: WebSocket, tid: int):
    await ws.accept()
    t = TERMINALS.get(tid)
    if not t:
        await ws.close()
        return
    q: asyncio.Queue = asyncio.Queue(maxsize=512)
    t.queues.add(q)
    if t.scrollback:
        await ws.send_bytes(bytes(t.scrollback))

    async def pump_out():
        while True:
            data = await q.get()
            if data is None:
                await ws.send_text('\x00{"type":"exit"}')
                break
            await ws.send_bytes(data)

    out_task = asyncio.create_task(pump_out())
    try:
        while True:
            msg = await ws.receive_text()
            if msg.startswith("\x00"):  # control frame
                ctl = json.loads(msg[1:])
                if ctl.get("type") == "resize":
                    t.resize(ctl["rows"], ctl["cols"])
            else:
                t.write(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        t.queues.discard(q)
        out_task.cancel()


@app.get("/api/procs")
def api_procs():
    # first cpu_percent call per process always reads 0 — prime, wait, re-read
    plist = list(psutil.process_iter(["pid", "name"]))
    for p in plist:
        try:
            p.cpu_percent()
        except psutil.Error:
            pass
    time.sleep(0.3)
    procs = []
    for p in plist:
        try:
            procs.append({"pid": p.pid, "name": p.info["name"],
                          "cpu_percent": p.cpu_percent(),
                          "memory_percent": p.memory_percent()})
        except psutil.Error:
            continue
    procs.sort(key=lambda x: (x["cpu_percent"] or 0), reverse=True)
    return procs[:20]


# ----------------------------------------------------------------- file browser


@app.get("/api/browse")
def api_browse(path: str = "~", hidden: bool = False):
    p = Path(os.path.expanduser(path)).resolve()
    if not p.is_dir():
        return JSONResponse({"error": f"not a directory: {p}"}, status_code=400)
    dirs, files = [], []
    try:
        for e in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if not hidden and e.name.startswith("."):
                continue
            try:
                if e.is_dir():
                    dirs.append({"name": e.name, "path": str(e),
                                 "git": (e / ".git").exists()})
                else:
                    files.append({"name": e.name, "path": str(e),
                                  "size": e.stat().st_size})
            except OSError:
                continue
    except PermissionError:
        return JSONResponse({"error": f"permission denied: {p}"}, status_code=403)
    return {"path": str(p), "parent": str(p.parent) if p != p.parent else None,
            "home": str(Path.home()), "dirs": dirs, "files": files[:500]}


@app.get("/api/file")
def api_file_read(path: str):
    p = Path(os.path.expanduser(path)).resolve()
    if not p.is_file():
        return JSONResponse({"error": "not a file"}, status_code=400)
    if p.stat().st_size > 512_000:
        return JSONResponse({"error": "file too large to preview (>500 KB)"},
                            status_code=413)
    data = p.read_bytes()
    if b"\x00" in data[:4096]:
        return JSONResponse({"error": "binary file"}, status_code=415)
    return {"path": str(p), "content": data.decode("utf-8", "replace")}


@app.post("/api/file")
def api_file_write(body: dict):
    p = Path(os.path.expanduser(body.get("path", ""))).resolve()
    if not p.parent.is_dir():
        return JSONResponse({"error": "parent dir missing"}, status_code=400)
    p.write_text(str(body.get("content", "")))
    return {"ok": True, "path": str(p)}


# ------------------------------------------------------------------ kanban API


@app.get("/api/kanban")
def api_kanban():
    return KANBAN


@app.post("/api/kanban")
def api_kanban_save(body: dict):
    cards = body.get("cards")
    if not isinstance(cards, list):
        return JSONResponse({"error": "cards must be a list"}, status_code=400)
    for c in cards:
        if c.get("status") not in KANBAN_COLS:
            c["status"] = "backlog"
        if not c.get("id"):
            c["id"] = KANBAN["next_id"]
            KANBAN["next_id"] += 1
    KANBAN["next_id"] = max([KANBAN["next_id"]] + [c["id"] + 1 for c in cards])
    KANBAN["cards"] = cards
    kanban_save()
    broadcast("kanban", KANBAN)
    return KANBAN


@app.post("/api/kanban/dispatch")
async def api_kanban_dispatch(body: dict):
    card = next((c for c in KANBAN["cards"] if c["id"] == body.get("id")), None)
    if not card:
        return JSONResponse({"error": "card not found"}, status_code=404)
    agent = card.get("agent") or "claude"
    if agent not in CFG["agents"]:
        return JSONResponse({"error": f"unknown agent {agent}"}, status_code=400)
    cwd = os.path.expanduser(card.get("cwd") or CFG["default_cwd"])
    if not os.path.isdir(cwd):
        return JSONResponse({"error": f"bad cwd {cwd}"}, status_code=400)
    prompt = card["title"] + ("\n" + card["desc"] if card.get("desc") else "")
    mem = os.path.expanduser(CFG.get("shared_memory_dir", ""))
    if mem:
        os.makedirs(mem, exist_ok=True)
        prompt += (f"\nShared agent memory dir: {mem} — read relevant notes "
                   "before starting, write down anything the next agent should know.")
    t = spawn_terminal(agent, cwd, str(body.get("ws") or "main")[:32], prompt)
    card["status"] = "in_progress"
    card["term_id"] = t.id
    kanban_save()
    broadcast("kanban", KANBAN)
    broadcast("terms", [x.info() for x in TERMINALS.values()])
    return {"card": card, "term": t.info()}


# --------------------------------------------------------------- JARVIS voice


@app.get("/api/jarvis/state")
def jarvis_state():
    return JARVIS.state if JARVIS else {"listening": False, "reply": "",
                                        "transcript": "", "disabled": True}


@app.post("/api/jarvis/say")
async def jarvis_say(body: dict):
    if not JARVIS:
        return JSONResponse({"error": "JARVIS brain not available"}, status_code=503)
    text = str(body.get("text", ""))
    fb = body.get("feedback")
    out = await asyncio.to_thread(JARVIS.respond, text, fb)
    broadcast("jarvis", JARVIS.state)
    return out


@app.post("/api/jarvis/listening")
async def jarvis_listening(body: dict):
    """The voice loop reports mic state + a small waveform for the hologram."""
    if not JARVIS:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    JARVIS.state["listening"] = bool(body.get("on"))
    if isinstance(body.get("wave"), list):
        JARVIS.state["wave"] = [float(x) for x in body["wave"][:64]]
    JARVIS.state["ts"] = time.time()
    broadcast("jarvis", JARVIS.state)
    return {"ok": True}


# ----------------------------------------------------------------- Hive / Autopilot


@app.post("/api/swarm")
async def api_swarm(body: dict):
    """Hive: one mission → a coordinator + N workers, OR (workers=0) a single
    recursive 'autopilot' agent. Agent-agnostic — works with any
    configured coding agent. All panes land in one workspace sharing the memory
    dir; you steer any of them."""
    mission = str(body.get("mission", "")).strip()
    if not mission:
        return JSONResponse({"error": "mission required"}, status_code=400)
    agent = body.get("agent") or "claude"
    if agent not in CFG["agents"]:
        return JSONResponse({"error": f"unknown agent {agent}"}, status_code=400)
    cwd = os.path.expanduser(body.get("cwd") or CFG["default_cwd"])
    if not os.path.isdir(cwd):
        return JSONResponse({"error": f"bad cwd {cwd}"}, status_code=400)
    workers = min(max(int(body.get("workers", 3)), 0), 12)
    mem = os.path.expanduser(CFG.get("shared_memory_dir", "~/.jarvis-memory"))
    os.makedirs(mem, exist_ok=True)
    Path(mem, "MISSION.md").write_text(f"# Mission\n\n{mission}\n")

    made = []
    if workers == 0:  # ── recursive autopilot ──
        ws = str(body.get("ws") or f"auto-{int(time.time()) % 10000}")[:32]
        auto = (f"AUTOPILOT MODE. Mission: {mission}\n"
                f"Work autonomously in a loop until the mission is truly done: "
                "(1) write a short plan to {m}/PLAN.md, (2) implement the next "
                "step, (3) test/verify it, (4) if it fails, fix and repeat, "
                "(5) append what you did and what's left to {m}/PROGRESS.md after "
                "each pass. Keep going without waiting for approval; stop only "
                "when everything works and the mission is complete, then print "
                "DONE.").format(m=mem)
        made.append(spawn_terminal(agent, cwd, ws, auto).info())
        broadcast("terms", [x.info() for x in TERMINALS.values()])
        return {"ws": ws, "terminals": made, "mem": mem, "mode": "autopilot"}

    # Swarm: spawn ONLY the coordinator. It plans a todo list, writes a
    # self-contained brief per subtask, then dispatches a subagent for each
    # (giving it full context every time) via hive_dispatch.py.
    ws = str(body.get("ws") or f"hive-{int(time.time()) % 10000}")[:32]
    dispatch = f"{sys.executable} {BASE / 'hive_dispatch.py'}"
    coord = (
        f"You are the COORDINATOR of a Hive mission. Mission: {mission}\n"
        f"You DELEGATE — you do not do the subtasks yourself. Work in {mem}.\n\n"
        f"1. PLAN: break the mission into independent subtasks (at most {workers}). "
        f"Write the todo list as a checklist to {mem}/TASKS.md.\n"
        f"2. BRIEF: for each subtask <n>, write a COMPLETE self-contained brief to "
        f"{mem}/subtask-<n>.md — assume the subagent sees ONLY this file. Include: "
        f"the overall goal, this subtask's exact scope, the specific files/paths, "
        f"any conventions/interfaces to follow, and what 'done' looks like plus "
        f"write results to {mem}/worker-<n>.md.\n"
        f"3. DISPATCH one subagent per subtask by running:\n"
        f'   {dispatch} --agent {agent} --cwd "{cwd}" --ws {ws} "You are worker <n>. '
        f'Read your full brief at {mem}/subtask-<n>.md, complete it, and write status '
        f'and results to {mem}/worker-<n>.md."\n'
        f"   Keep at most {workers} subagents running at once.\n"
        f"4. REVIEW: as {mem}/worker-<n>.md files appear, check the work, integrate "
        f"it, and update {mem}/TASKS.md. If a subtask was done poorly, rewrite its "
        f"brief with more context and re-dispatch.\n"
        f"5. When every subtask is complete and integrated, print DONE.")
    made.append(spawn_terminal(agent, cwd, ws, coord).info())
    broadcast("terms", [x.info() for x in TERMINALS.values()])
    return {"ws": ws, "terminals": made, "mem": mem, "mode": "swarm"}


@app.get("/api/config")
def get_config():
    return CFG


@app.post("/api/config")
def set_config(body: dict):
    global CFG
    allowed = {"pricing_per_mtok", "power", "tou_rates_cents", "agents",
               "default_cwd", "network_mode", "bypass_permissions",
               "bypass_flags", "dispatch_prompt_delay_s", "shared_memory_dir",
               "jarvis"}
    for k in body:
        if k in allowed:
            CFG[k] = body[k]
    CONFIG_PATH.write_text(json.dumps(CFG, indent=2))
    if JARVIS:  # apply voice/model/safety changes live
        JARVIS.cfg = CFG.get("jarvis", {})
    return CFG


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")


if __name__ == "__main__":
    host = "0.0.0.0" if CFG.get("network_mode") == "lan" else "127.0.0.1"
    uvicorn.run(app, host=host, port=CFG.get("port", 7744), log_level="warning")
