#!/usr/bin/env python3
"""jarvis_mcp.py — a tiny stdio MCP server that exposes the JARVIS Command
Center to any MCP-capable coding agent (Claude Code, Codex, OpenCode, Hermes,
Cursor, …). Zero dependencies: newline-delimited JSON-RPC 2.0 over stdin/stdout,
talking to the cockpit's local HTTP API.

Tools: board_list, board_add, dispatch_agent, memory_read, memory_write,
system_status. Start the cockpit (./start.sh) first; this process is launched
by the MCP client, not by hand.

Register (examples):
  claude   :  claude mcp add jarvis -- <this-python> <this-file>
  codex    :  add to ~/.codex/config.toml  [mcp_servers.jarvis]
              command="<python>"  args=["<this-file>"]
  opencode :  opencode mcp add jarvis <python> <this-file>
  hermes   :  hermes mcp add jarvis -- <python> <this-file>
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("JARVIS_CC_URL", "http://127.0.0.1:7745")
PROTO = "2024-11-05"


def _api(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _mem_dir():
    try:
        return os.path.expanduser(_api("GET", "/api/config").get(
            "shared_memory_dir", "~/.jarvis-memory"))
    except Exception:
        return os.path.expanduser("~/.jarvis-memory")


TOOLS = [
    {"name": "board_list", "description": "List all task cards on the JARVIS board.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "board_add", "description": "Add a task card to the board (Backlog).",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"}, "desc": {"type": "string"},
         "agent": {"type": "string"}, "cwd": {"type": "string"}},
         "required": ["title"]}},
    {"name": "dispatch_agent", "description": "Spawn a coding agent in a folder "
     "with a prompt (returns the terminal id).",
     "inputSchema": {"type": "object", "properties": {
         "agent": {"type": "string"}, "cwd": {"type": "string"},
         "prompt": {"type": "string"}}, "required": ["agent", "prompt"]}},
    {"name": "memory_read", "description": "Read a file from the shared agent "
     "memory folder (relative name).",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}},
                     "required": ["name"]}},
    {"name": "memory_write", "description": "Write/append a file in the shared "
     "agent memory folder.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "content": {"type": "string"},
         "append": {"type": "boolean"}}, "required": ["name", "content"]}},
    {"name": "system_status", "description": "Current CPU/mem/GPU/power snapshot.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def call_tool(name, args):
    if name == "board_list":
        return json.dumps(_api("GET", "/api/kanban").get("cards", []), indent=2)
    if name == "board_add":
        kb = _api("GET", "/api/kanban")
        kb["cards"].append({"id": 0, "title": args["title"],
                            "desc": args.get("desc", ""),
                            "agent": args.get("agent", "claude"),
                            "cwd": args.get("cwd", "~"), "status": "backlog"})
        _api("POST", "/api/kanban", {"cards": kb["cards"]})
        return "card added to backlog"
    if name == "dispatch_agent":
        out = _api("POST", "/api/terms", {"agent": args["agent"],
                   "cwd": args.get("cwd"), "prompt": args["prompt"]})
        return json.dumps(out)
    if name == "memory_read":
        p = os.path.join(_mem_dir(), os.path.basename(args["name"]))
        return open(p).read() if os.path.exists(p) else f"(no such memory file: {args['name']})"
    if name == "memory_write":
        d = _mem_dir(); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, os.path.basename(args["name"]))
        with open(p, "a" if args.get("append") else "w") as f:
            f.write(args["content"])
        return f"wrote {p}"
    if name == "system_status":
        s = _api("GET", "/api/snapshot")
        return json.dumps({"cpu": s.get("cpu"), "mem_pct": s.get("mem_pct"),
                           "gpus": s.get("gpus"), "watts": s.get("watts")}, indent=2)
    raise ValueError(f"unknown tool {name}")


def reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        m, mid = req.get("method"), req.get("id")
        if m == "initialize":
            reply(mid, {"protocolVersion": PROTO,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "jarvis-command-center", "version": "1.0"}})
        elif m == "notifications/initialized":
            continue
        elif m == "tools/list":
            reply(mid, {"tools": TOOLS})
        elif m == "tools/call":
            p = req.get("params", {})
            try:
                text = call_tool(p.get("name"), p.get("arguments") or {})
                reply(mid, {"content": [{"type": "text", "text": text}]})
            except Exception as e:
                reply(mid, {"content": [{"type": "text", "text": f"error: {e}"}],
                            "isError": True})
        elif mid is not None:
            reply(mid, error={"code": -32601, "message": f"method not found: {m}"})


if __name__ == "__main__":
    main()
