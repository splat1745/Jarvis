#!/usr/bin/env python3
"""hive_dispatch.py — the coordinator's tool for spawning a subagent.

The Hive coordinator calls this once per subtask, after it has written a
self-contained brief for that subtask, so each subagent starts with full
context. Spawns the subagent in the same workspace via the cockpit API.

    python hive_dispatch.py --agent claude --cwd /path --ws hive-1234 "<prompt>"
"""
import argparse
import json
import os
import sys
import urllib.request

BASE = os.environ.get("JARVIS_CC_URL", "http://127.0.0.1:7745")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="claude")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--ws", default="hive")
    ap.add_argument("prompt", help="self-contained brief for the subagent")
    a = ap.parse_args()
    body = json.dumps({"agent": a.agent, "cwd": a.cwd, "ws": a.ws,
                       "prompt": a.prompt}).encode()
    req = urllib.request.Request(BASE + "/api/terms", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read())
    except Exception as e:
        print(f"dispatch failed: {e}", file=sys.stderr)
        sys.exit(1)
    if isinstance(out, dict) and out.get("error"):
        print(f"dispatch error: {out['error']}", file=sys.stderr)
        sys.exit(1)
    tid = out[0]["id"] if isinstance(out, list) and out else "?"
    print(f"dispatched subagent terminal {tid} in workspace {a.ws}")


if __name__ == "__main__":
    main()
