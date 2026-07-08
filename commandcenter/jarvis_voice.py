#!/usr/bin/env python3
"""jarvis_voice.py — the one JARVIS loop: mic → transcript → brain → speech,
mirrored live into the web cockpit's holographic page.

    STT (faster-whisper)  →  POST /api/jarvis/say  →  Piper TTS

Runs headless (no tkinter). If the mic/STT stack isn't available it degrades to
typed input so it still works as a text chat that drives the hologram. The
LLM/tools/safety all live in jarvis_brain (local model only) behind the cockpit,
so this process stays thin.
"""
import json
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:7745"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _post(path, payload):
    try:
        req = urllib.request.Request(
            BASE + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def main():
    # optional voice I/O — fall back to typed input if unavailable
    try:
        from Listen import listen, model as stt_model
    except Exception as e:
        listen = None
        print(f"[jarvis-voice] STT unavailable ({e}); using typed input.")
    try:
        from Piper_tts import speak
    except Exception:
        def speak(_):  # no-op if Piper isn't installed
            pass

    print("JARVIS online (local model). Ctrl-C to exit.\n")
    while True:
        try:
            if listen:
                _post("/api/jarvis/listening", {"on": True})
                text = listen(stt_model)
                _post("/api/jarvis/listening", {"on": False})
            else:
                text = input("You: ")
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye, sir.")
            break
        if not text or not text.strip():
            continue
        if text.strip().lower() in ("exit", "quit", "goodbye jarvis"):
            break
        print(f"You: {text}")
        out = _post("/api/jarvis/say", {"text": text})
        reply = out.get("reply") or out.get("error") or ""
        print(f"JARVIS: {reply}\n")
        speak(reply)


if __name__ == "__main__":
    main()
