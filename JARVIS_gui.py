"""
JARVIS GUI — CustomTkinter implementation
Matches the Figma design: dark theme, cyan accents, three-panel layout
Wired to: Listen.py (STT), mainchat.py (LLM), Piper_tts.py (TTS)

Install deps:
    pip install customtkinter psutil

Run:
    python jarvis_gui.py
"""

import customtkinter as ctk
import threading
import time
import datetime
import psutil
import queue
import random

# ── Try importing your modules (graceful fallback if not present) ──────────────
try:
    from Listen import listen, model as whisper_model
    HAS_STT = True
except Exception as e:
    HAS_STT = False
    whisper_model = None
    listen = None
    print(f"[JARVIS] Listen.py not found — mic input disabled ({e})")

try:
    from Piper_tts import speak
    HAS_TTS = True
except Exception:
    HAS_TTS = False
    speak = lambda text: print(f"[TTS] {text}")
    print("[JARVIS] Piper_tts.py not found — TTS disabled")

import os
USE_AGENT = os.environ.get("USE_QWEN_AGENT", "0") == "1"

import os
USE_AGENT = os.environ.get("USE_QWEN_AGENT", "0") == "1"
HAS_LLM = False
QwenChatbot = None

try:
    if USE_AGENT:
        from qwen_agent.agents import Assistant
    else:
        from mainchat import QwenChatbot
    HAS_LLM = True
except Exception as e:
    print(f"[JARVIS] LLM not loaded — using echo mode ({e})")

# ── Color palette (from Figma) ─────────────────────────────────────────────────
BG_MAIN      = "#050d14"
BG_PANEL     = "#000a19"
BG_CARD      = "#001428"
CYAN         = "#00ffff"
CYAN_DIM     = "#00ccff"
CYAN_BORDER  = "#001e2e"
GREEN        = "#00ffcc"
AMBER        = "#ffbf1a"
TEXT_PRI     = "#a1d9f0"
TEXT_MUTED   = "#3d7a99"
TEXT_FAINT   = "#1a4d66"
BORDER       = "#0d2233"


# ── App setup ──────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class JarvisApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window config
        self.title("J.A.R.V.I.S")
        self.geometry("1440x900")
        self.minsize(1100, 700)
        self.configure(fg_color=BG_MAIN)

        # State
        self.mic_active   = False
        self.is_thinking  = False
        self.mic_animating = False
        self.uptime_start = time.time()
        self.voice_mode   = "Professional"
        self.msg_queue    = queue.Queue()  # thread-safe message passing to UI

        # LLM instance (loaded once, reused)
        self.chatbot = None
        self._init_llm_async()

        # Build layout
        self._build_topbar()
        self._build_main()

        # Start background loops
        self._start_stat_loop()
        self._start_uptime_loop()
        self._start_queue_drain()

    # ── LLM init (off main thread so UI doesn't freeze) ───────────────────────
    def _init_llm_async(self):
        def _load():
            global _chatbot_instance
            if HAS_LLM and not USE_AGENT and QwenChatbot is not None:
                try:
                    _chatbot_instance = QwenChatbot()
                    self.chatbot = _chatbot_instance
                    self.msg_queue.put(("status", "SYSTEM ONLINE"))
                except Exception as e:
                    self.msg_queue.put(("status", f"LLM ERROR: {e}"))
            else:
                self.msg_queue.put(("status", "SYSTEM ONLINE"))
        threading.Thread(target=_load, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════════
    # TOP BAR
    # ══════════════════════════════════════════════════════════════════════════
    def _build_topbar(self):
        bar = ctk.CTkFrame(self, height=56, fg_color=BG_PANEL, corner_radius=0)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # Logo
        logo_frame = ctk.CTkFrame(bar, fg_color="transparent")
        logo_frame.pack(side="left", padx=24, pady=0)
        ctk.CTkLabel(logo_frame, text="J.A.R.V.I.S",
                     font=ctk.CTkFont("Courier New", 20, "bold"),
                     text_color=CYAN).pack(anchor="w")
        ctk.CTkLabel(logo_frame, text="JUST A RATHER VERY INTELLIGENT SYSTEM",
                     font=ctk.CTkFont("Courier New", 8),
                     text_color=TEXT_FAINT).pack(anchor="w")

        # Status pill (centre-ish)
        status_frame = ctk.CTkFrame(bar, fg_color="transparent")
        status_frame.pack(side="left", padx=60)
        # Dot canvas
        dot_canvas = ctk.CTkCanvas(status_frame, width=10, height=10,
                                   bg=BG_PANEL, highlightthickness=0)
        dot_canvas.pack(side="left", padx=(0, 6))
        dot_canvas.create_oval(1, 1, 9, 9, fill=GREEN, outline="")
        self.status_label = ctk.CTkLabel(status_frame, text="LOADING...",
                                          font=ctk.CTkFont("Courier New", 10),
                                          text_color=GREEN)
        self.status_label.pack(side="left")

        # Stats (right)
        stats_frame = ctk.CTkFrame(bar, fg_color="transparent")
        stats_frame.pack(side="right", padx=20)

        self.stat_labels = {}
        for key in ["UPTIME", "PING", "RAM", "GPU", "CPU"]:
            col = ctk.CTkFrame(stats_frame, fg_color="transparent")
            col.pack(side="right", padx=14)
            val_lbl = ctk.CTkLabel(col, text="--",
                                   font=ctk.CTkFont("Courier New", 15, "bold"),
                                   text_color=CYAN_DIM)
            val_lbl.pack()
            ctk.CTkLabel(col, text=key,
                         font=ctk.CTkFont("Courier New", 8),
                         text_color=TEXT_FAINT).pack()
            self.stat_labels[key] = val_lbl

        # Bottom border line
        border = ctk.CTkFrame(self, height=1, fg_color=BORDER, corner_radius=0)
        border.pack(fill="x", side="top")

    # ══════════════════════════════════════════════════════════════════════════
    # MAIN 3-PANEL LAYOUT
    # ══════════════════════════════════════════════════════════════════════════
    def _build_main(self):
        main = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        main.pack(fill="both", expand=True)

        self._build_left_sidebar(main)
        self._build_right_panel(main)
        self._build_center(main)  # center last so it fills remaining space

    # ══════════════════════════════════════════════════════════════════════════
    # LEFT SIDEBAR
    # ══════════════════════════════════════════════════════════════════════════
    def _build_left_sidebar(self, parent):
        sb = ctk.CTkFrame(parent, width=240, fg_color=BG_PANEL, corner_radius=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # Right border
        border = ctk.CTkFrame(sb, width=1, fg_color=BORDER, corner_radius=0)
        border.pack(side="right", fill="y")

        inner = ctk.CTkScrollableFrame(sb, fg_color="transparent",
                                        scrollbar_button_color=BORDER,
                                        scrollbar_button_hover_color=CYAN_BORDER)
        inner.pack(fill="both", expand=True, padx=0, pady=0)

        # ── System Resources ─────────────────────────────────────────────────
        self._section_label(inner, "SYSTEM RESOURCES")

        self.bar_widgets = {}
        bar_defs = [
            ("CPU",  "#00ffff", 0.43),
            ("GPU",  "#8033ff", 0.61),
            ("RAM",  "#00ffff", 0.58),
            ("VRAM", "#00ffcc", 0.34),
            ("DISK", "#00ccff", 0.72),
        ]
        for name, color, pct in bar_defs:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(3, 0))
            ctk.CTkLabel(row, text=name, font=ctk.CTkFont("Courier New", 10),
                         text_color=TEXT_MUTED, width=40, anchor="w").pack(side="left")
            val = ctk.CTkLabel(row, text=f"{int(pct*100)}%",
                               font=ctk.CTkFont("Courier New", 10),
                               text_color=CYAN_DIM, width=40, anchor="e")
            val.pack(side="right")

            bar_bg = ctk.CTkFrame(inner, height=3, fg_color="#0d1f2d", corner_radius=2)
            bar_bg.pack(fill="x", padx=16, pady=(2, 6))
            fill = ctk.CTkFrame(bar_bg, height=3, fg_color=color, corner_radius=2)
            fill.place(relx=0, rely=0, relwidth=pct, relheight=1)
            self.bar_widgets[name] = (val, fill)

        self._divider(inner)

        # ── Agent Stats ───────────────────────────────────────────────────────
        self._section_label(inner, "AGENT STATS")
        self.agent_vals = {}
        agent_stats = [
            ("Latency", "38 ms"), ("TTFT", "0.4 s"), ("Tokens/s", "47"),
            ("Model", "Qwen 2B"), ("STT", "Whisper"), ("TTS", "Piper"),
        ]
        for k, v in agent_stats:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=1)
            ctk.CTkLabel(row, text=k, font=ctk.CTkFont("Courier New", 10),
                         text_color=TEXT_MUTED, anchor="w").pack(side="left")
            lbl = ctk.CTkLabel(row, text=v, font=ctk.CTkFont("Courier New", 10),
                               text_color=CYAN_DIM, anchor="e")
            lbl.pack(side="right")
            self.agent_vals[k] = lbl

        self._divider(inner)

        # ── Wake Words ────────────────────────────────────────────────────────
        self._section_label(inner, "WAKE WORDS")
        for ww in ["Jarvis daddy's home", "Let's get cookin", "Hey Jarvis"]:
            pill = ctk.CTkFrame(inner, height=26, fg_color="#001520",
                                border_color=BORDER, border_width=1, corner_radius=2)
            pill.pack(fill="x", padx=16, pady=3)
            pill.pack_propagate(False)
            ctk.CTkLabel(pill, text=f"● {ww}",
                         font=ctk.CTkFont("Courier New", 10),
                         text_color=TEXT_MUTED).pack(side="left", padx=8)

    # ══════════════════════════════════════════════════════════════════════════
    # RIGHT PANEL
    # ══════════════════════════════════════════════════════════════════════════
    def _build_right_panel(self, parent):
        rp = ctk.CTkFrame(parent, width=200, fg_color=BG_PANEL, corner_radius=0)
        rp.pack(side="right", fill="y")
        rp.pack_propagate(False)

        border = ctk.CTkFrame(rp, width=1, fg_color=BORDER, corner_radius=0)
        border.pack(side="left", fill="y")

        inner = ctk.CTkScrollableFrame(rp, fg_color="transparent",
                                        scrollbar_button_color=BORDER,
                                        scrollbar_button_hover_color=CYAN_BORDER)
        inner.pack(fill="both", expand=True)

        # ── Voice Pack ────────────────────────────────────────────────────────
        self._section_label(inner, "VOICE PACK")

        mode_row = ctk.CTkFrame(inner, fg_color="transparent")
        mode_row.pack(fill="x", padx=12, pady=(0, 6))
        self.mode_btns = {}
        for mode in ["PRO", "GEN Z", "CALM"]:
            btn = ctk.CTkButton(mode_row, text=mode, width=48, height=24,
                                font=ctk.CTkFont("Courier New", 9),
                                fg_color="#002d3d" if mode == "PRO" else "#001520",
                                border_color=CYAN_DIM if mode == "PRO" else BORDER,
                                border_width=1, corner_radius=2,
                                text_color=GREEN if mode == "PRO" else TEXT_MUTED,
                                hover_color="#002d3d",
                                command=lambda m=mode: self._set_mode(m))
            btn.pack(side="left", padx=1)
            self.mode_btns[mode] = btn

        self.voice_pack_btns = {}
        for pack in ["Professional", "Gen Z Slang", "Gym Hype"]:
            active = pack == "Professional"
            btn = ctk.CTkButton(inner, text=pack, height=28,
                                font=ctk.CTkFont("Courier New", 10),
                                fg_color="#001e2e" if active else "#000f1a",
                                border_color=CYAN_DIM if active else BORDER,
                                border_width=1, corner_radius=2,
                                text_color=GREEN if active else TEXT_MUTED,
                                hover_color="#002535",
                                anchor="w",
                                command=lambda p=pack: self._set_voice_pack(p))
            btn.pack(fill="x", padx=12, pady=2)
            self.voice_pack_btns[pack] = btn

        self._divider(inner)

        # ── Uptime ────────────────────────────────────────────────────────────
        self._section_label(inner, "UPTIME")
        uptime_box = ctk.CTkFrame(inner, height=52, fg_color="#001520",
                                   border_color=BORDER, border_width=1, corner_radius=2)
        uptime_box.pack(fill="x", padx=12, pady=(0, 8))
        uptime_box.pack_propagate(False)
        self.uptime_label = ctk.CTkLabel(uptime_box, text="00:00:00",
                                          font=ctk.CTkFont("Courier New", 20, "bold"),
                                          text_color=CYAN_DIM)
        self.uptime_label.pack(pady=(6, 0))
        ctk.CTkLabel(uptime_box, text="HH:MM:SS",
                     font=ctk.CTkFont("Courier New", 8),
                     text_color=TEXT_FAINT).pack()

        self._divider(inner)

        # ── Quick Commands ────────────────────────────────────────────────────
        self._section_label(inner, "QUICK COMMANDS")
        cmds = [
            ("+ New Repo",        "make new repo"),
            ("Open VSCode",       "open vs code"),
            ("Note Mode",         "start note mode"),
            ("System Status",     "system status"),
            ("Check Assignments", "check assignments"),
            ("Screenshot",        "take a screenshot"),
        ]
        for label, cmd in cmds:
            btn = ctk.CTkButton(inner, text=label, height=26,
                                font=ctk.CTkFont("Courier New", 10),
                                fg_color="#000f1a",
                                border_color=BORDER, border_width=1,
                                corner_radius=2, text_color=TEXT_MUTED,
                                hover_color="#001a2a", anchor="w",
                                command=lambda c=cmd: self._quick_cmd(c))
            btn.pack(fill="x", padx=12, pady=2)

    # ══════════════════════════════════════════════════════════════════════════
    # CENTER — CHAT + INPUT
    # ══════════════════════════════════════════════════════════════════════════
    def _build_center(self, parent):
        center = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        center.pack(side="left", fill="both", expand=True)

        # Chat header
        header = ctk.CTkFrame(center, height=36, fg_color=BG_PANEL, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="CONVERSATION LOG",
                     font=ctk.CTkFont("Courier New", 9),
                     text_color=TEXT_FAINT).pack(side="left", padx=20, pady=10)
        self.exchange_label = ctk.CTkLabel(header, text="0 EXCHANGES",
                                            font=ctk.CTkFont("Courier New", 9),
                                            text_color=TEXT_FAINT)
        self.exchange_label.pack(side="right", padx=20)
        ctk.CTkFrame(center, height=1, fg_color=BORDER, corner_radius=0).pack(fill="x")

        # Chat messages area (scrollable)
        self.chat_scroll = ctk.CTkScrollableFrame(center, fg_color="transparent",
                                                   scrollbar_button_color=BORDER,
                                                   scrollbar_button_hover_color=CYAN_BORDER)
        self.chat_scroll.pack(fill="both", expand=True, padx=0, pady=0)
        self.exchange_count = 0

        # Add welcome message
        self._add_jarvis_message(
            "Good morning, Sachin. All systems initialising. "
            "Say 'Hey Jarvis' or type a command below."
        )

        # Input bar
        self._build_input_bar(center)

    def _build_input_bar(self, parent):
        # Top border
        ctk.CTkFrame(parent, height=1, fg_color=BORDER, corner_radius=0).pack(fill="x")

        bar = ctk.CTkFrame(parent, height=56, fg_color=BG_PANEL, corner_radius=0)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        # Prompt prefix
        ctk.CTkLabel(bar, text=">",
                     font=ctk.CTkFont("Courier New", 16, "bold"),
                     text_color=TEXT_MUTED).pack(side="left", padx=(16, 6), pady=12)

        # Input field
        self.input_entry = ctk.CTkEntry(bar, placeholder_text="Enter command or speak...",
                                 font=ctk.CTkFont("Courier New", 13),
                                 fg_color=BG_PANEL,
                                 border_color=BG_PANEL,
                                 border_width=0,
                                 text_color=TEXT_PRI,
                                 placeholder_text_color=TEXT_FAINT)
        self.input_entry.pack(side="left", fill="x", expand=True, pady=12)
        self.input_entry.bind("<Return>", lambda e: self._on_send())

        # Buttons
        btn_frame = ctk.CTkFrame(bar, fg_color="transparent")
        btn_frame.pack(side="right", padx=12)

        self.mic_btn = ctk.CTkButton(btn_frame, text="MIC", width=34, height=34,
                                      font=ctk.CTkFont("Courier New", 9),
                                      fg_color="#002233",
                                      border_color=CYAN_DIM, border_width=1,
                                      corner_radius=2, text_color=CYAN,
                                      hover_color="#002d3d",
                                      command=self._toggle_mic)
        self.mic_btn.pack(side="left", padx=2)

        ctk.CTkButton(btn_frame, text="CAP", width=34, height=34,
                      font=ctk.CTkFont("Courier New", 9),
                      fg_color="#001520", border_color=BORDER, border_width=1,
                      corner_radius=2, text_color=TEXT_MUTED,
                      hover_color="#001a2a",
                      command=self._on_screenshot).pack(side="left", padx=2)

        ctk.CTkButton(btn_frame, text="NOTE", width=34, height=34,
                      font=ctk.CTkFont("Courier New", 9),
                      fg_color="#001520", border_color=BORDER, border_width=1,
                      corner_radius=2, text_color=TEXT_MUTED,
                      hover_color="#001a2a",
                      command=self._on_note).pack(side="left", padx=2)

        ctk.CTkButton(btn_frame, text="SEND", width=60, height=34,
                      font=ctk.CTkFont("Courier New", 10, "bold"),
                      fg_color="#002233",
                      border_color=CYAN_DIM, border_width=1,
                      corner_radius=2, text_color=CYAN,
                      hover_color="#002d3d",
                      command=self._on_send).pack(side="left", padx=(4, 0))

    # ══════════════════════════════════════════════════════════════════════════
    # CHAT MESSAGE HELPERS
    # ══════════════════════════════════════════════════════════════════════════
    def _add_jarvis_message(self, text):
        """Add a Jarvis (cyan) message bubble to the chat."""
        self._add_message(text, is_user=False)

    def _add_user_message(self, text):
        """Add a user (amber) message bubble to the chat."""
        self._add_message(text, is_user=True)

    def _add_message(self, text, is_user=False):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        sender = "SACHIN" if is_user else "JARVIS"
        label_color = AMBER if is_user else CYAN_DIM
        bubble_bg   = "#1e0e00" if is_user else "#002438"
        border_col  = AMBER if is_user else CYAN
        text_color  = AMBER if is_user else TEXT_PRI

        # Outer container
        outer = ctk.CTkFrame(self.chat_scroll, fg_color="transparent")
        outer.pack(fill="x", padx=20, pady=6)

        # Label
        lbl = ctk.CTkLabel(outer, text=f"{sender} — {now}",
                           font=ctk.CTkFont("Courier New", 9),
                           text_color=TEXT_FAINT,
                           anchor="e" if is_user else "w")
        lbl.pack(fill="x", padx=4, pady=(0, 2))

        # Bubble row (accent bar + text)
        row = ctk.CTkFrame(outer, fg_color="transparent")
        row.pack(fill="x")

        if not is_user:
            ctk.CTkFrame(row, width=2, fg_color=CYAN_DIM,
                         corner_radius=0).pack(side="left", fill="y")

        bubble = ctk.CTkFrame(row, fg_color=bubble_bg,
                               border_color=BORDER,
                               border_width=1, corner_radius=2)
        bubble.pack(side="left" if not is_user else "right",
                    fill="x", expand=True, padx=(4 if not is_user else 0, 0))

        ctk.CTkLabel(bubble, text=text,
                     font=ctk.CTkFont("Courier New", 11),
                     text_color=text_color,
                     wraplength=700, justify="left" if not is_user else "right",
                     anchor="w" if not is_user else "e").pack(padx=12, pady=10)

        if is_user:
            ctk.CTkFrame(row, width=2, fg_color=AMBER,
                         corner_radius=0).pack(side="right", fill="y")

        # Update exchange count
        if is_user:
            self.exchange_count += 1
            self.exchange_label.configure(text=f"{self.exchange_count} EXCHANGES")

        # Auto-scroll to bottom
        self.after(50, lambda: self.chat_scroll._parent_canvas.yview_moveto(1.0))

    def _show_thinking(self):
        self._thinking_frame = ctk.CTkFrame(
            self.chat_scroll,
            fg_color="#002438",
            border_color=BORDER,
            border_width=1,
            corner_radius=2
        )
        self._thinking_frame.pack(anchor="w", padx=24, pady=6)

        self.thinking_label = ctk.CTkLabel(
            self._thinking_frame,
            text=".",
            font=ctk.CTkFont("Courier New", 14),
            text_color=GREEN
        )
        self.thinking_label.pack(padx=12, pady=10)

        self.thinking_animating = True
        self._animate_thinking()

    def _animate_thinking(self):
        if not hasattr(self, "thinking_animating") or not self.thinking_animating:
            return

        current = self.thinking_label.cget("text")

        if current == ".":
            new = ".."
        elif current == "..":
            new = "..."
        else:
            new = "."

        self.thinking_label.configure(text=new)

        self.after(300, self._animate_thinking)

    def _hide_thinking(self):
        if hasattr(self, "thinking_animating"):
            self.thinking_animating = False

        if hasattr(self, "_thinking_frame"):
            self._thinking_frame.destroy()
            del self._thinking_frame

    # ══════════════════════════════════════════════════════════════════════════
    # USER ACTIONS
    # ══════════════════════════════════════════════════════════════════════════
    def _on_send(self):
        text = self.input_entry.get().strip()
        if not text or self.is_thinking:
            return
        self.input_entry.delete(0, "end")
        self._process_input(text)

    def _process_input(self, text):
        """Add user message then run LLM on a background thread."""
        self._add_user_message(text)
        self.is_thinking = True
        self._update_status("THINKING...")
        self._show_thinking()
        threading.Thread(target=self._run_llm, args=(text,), daemon=True).start()

    def _run_llm(self, text):
        """Run LLM inference on a background thread. Never touch UI directly."""
        try:
            if self.chatbot:
                response = self.chatbot.generate_response(text)
            elif not HAS_LLM:
                # Echo mode fallback
                time.sleep(0.5)
                response = f"[Echo mode] You said: {text}"
            else:
                time.sleep(0.5)
                response = "LLM not yet loaded. Please wait."

            self.msg_queue.put(("response", response))
        except Exception as e:
            self.msg_queue.put(("response", f"Error: {e}"))

    def _toggle_mic(self):
        if self.is_thinking:
            return

        self.mic_active = not self.mic_active

        if self.mic_active:
            self.mic_btn.configure(
                fg_color="#003d30",
                border_color=GREEN,
                text_color=GREEN,
                text="■ MIC"
            )
            self._update_status("LISTENING...")
            self.mic_animating = True
            self._animate_mic()

            threading.Thread(target=self._run_mic, daemon=True).start()
        else:
            self.mic_animating = False
            self.mic_btn.configure(
                fg_color="#002233",
                border_color=CYAN_DIM,
                text_color=CYAN,
                text="MIC"
            )

            self._update_status("SYSTEM ONLINE")

    def _animate_mic(self):
        if not self.mic_animating:
            return

        # Pulse between colors
        current = self.mic_btn.cget("fg_color")

        new_color = "#004d3a" if current == "#003d30" else "#003d30"

        self.mic_btn.configure(fg_color=new_color)

        self.after(300, self._animate_mic)

    def _run_mic(self):
        """Record audio and transcribe on a background thread."""
        try:
            if HAS_STT and whisper_model and listen:
                text = listen(whisper_model)
                if text.strip():
                    self.msg_queue.put(("mic_input", text))
            else:
                time.sleep(2)
                self.msg_queue.put(("status", "SYSTEM ONLINE"))
        except Exception as e:
            self.msg_queue.put(("status", f"MIC ERROR: {e}"))
        finally:
            self.mic_active = False
            self.msg_queue.put(("mic_done", None))

    def _on_screenshot(self):
        self._add_jarvis_message("Screenshot capture not yet implemented. Coming soon.")

    def _on_note(self):
        self._add_jarvis_message("Note mode not yet implemented. Coming soon.")

    def _quick_cmd(self, cmd):
        if not self.is_thinking:
            self.input_entry.delete(0, "end")
            self.input_entry.insert(0, cmd)
            self._on_send()

    def _set_mode(self, mode):
        for m, btn in self.mode_btns.items():
            active = m == mode
            btn.configure(
                fg_color="#002d3d" if active else "#001520",
                border_color=CYAN_DIM if active else BORDER,
                text_color=GREEN if active else TEXT_MUTED,
            )

    def _set_voice_pack(self, pack):
        self.voice_mode = pack
        for p, btn in self.voice_pack_btns.items():
            active = p == pack
            btn.configure(
                fg_color="#001e2e" if active else "#000f1a",
                border_color=CYAN_DIM if active else BORDER,
                text_color=GREEN if active else TEXT_MUTED,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # BACKGROUND LOOPS
    # ══════════════════════════════════════════════════════════════════════════
    def _start_stat_loop(self):
        """Update CPU/GPU/RAM bars every 2 seconds."""
        def loop():
            while True:
                try:
                    cpu  = psutil.cpu_percent(interval=1)
                    ram  = psutil.virtual_memory().percent
                    self.msg_queue.put(("stats", {"CPU": cpu, "RAM": ram}))
                except Exception:
                    pass
                time.sleep(2)
        threading.Thread(target=loop, daemon=True).start()

    def _start_uptime_loop(self):
        """Tick uptime every second."""
        def loop():
            while True:
                elapsed = int(time.time() - self.uptime_start)
                h = elapsed // 3600
                m = (elapsed % 3600) // 60
                s = elapsed % 60
                self.msg_queue.put(("uptime", f"{h:02d}:{m:02d}:{s:02d}"))
                time.sleep(1)
        threading.Thread(target=loop, daemon=True).start()

    def _start_queue_drain(self):
        """Drain the msg_queue on the main thread every 50ms."""
        def drain():
            while not self.msg_queue.empty():
                try:
                    kind, data = self.msg_queue.get_nowait()
                    self._handle_queue_msg(kind, data)
                except queue.Empty:
                    break
            self.after(50, drain)
        self.after(50, drain)

    def _handle_queue_msg(self, kind, data):
        """Process messages from background threads — runs on main thread."""
        if kind == "response":
            self._hide_thinking()
            self._add_jarvis_message(data)
            self._update_status("SYSTEM ONLINE")
            self.is_thinking = False
            if HAS_TTS:
                threading.Thread(target=speak, args=(data,), daemon=True).start()

        elif kind == "mic_input":
            self.mic_btn.configure(fg_color="#002233", border_color=CYAN_DIM,
                                   text_color=CYAN, text="MIC")
            self._process_input(data)

        elif kind == "mic_done":
            self.mic_btn.configure(fg_color="#002233", border_color=CYAN_DIM,
                                   text_color=CYAN, text="MIC")
            if not self.is_thinking:
                self._update_status("SYSTEM ONLINE")

        elif kind == "status":
            self._update_status(data)

        elif kind == "uptime":
            self.uptime_label.configure(text=data)
            self.stat_labels["UPTIME"].configure(text=data)

        elif kind == "stats":
            cpu = data.get("CPU", 0)
            ram = data.get("RAM", 0)
            ping = random.randint(8, 22)  # simulated until network tool added

            self.stat_labels["CPU"].configure(text=f"{cpu:.0f}%")
            self.stat_labels["RAM"].configure(text=f"{ram:.0f}%")
            self.stat_labels["PING"].configure(text=f"{ping}ms")
            self.stat_labels["GPU"].configure(text="--")  # needs GPUtil

            # Update sidebar bars
            if "CPU" in self.bar_widgets:
                self.bar_widgets["CPU"][0].configure(text=f"{cpu:.0f}%")
                self.bar_widgets["CPU"][1].place(relwidth=min(cpu/100, 1.0))
            if "RAM" in self.bar_widgets:
                self.bar_widgets["RAM"][0].configure(text=f"{ram:.0f}%")
                self.bar_widgets["RAM"][1].place(relwidth=min(ram/100, 1.0))

    def _update_status(self, text):
        self.status_label.configure(text=text)

        if text == "SYSTEM ONLINE":
            self._pulse_status()

    def _pulse_status(self):
        def pulse(on=True):
            color = GREEN if on else "#00aa88"
            self.status_label.configure(text_color=color)

            if not self.is_thinking and not self.mic_active:
                self.after(800, lambda: pulse(not on))

        pulse()

    # ══════════════════════════════════════════════════════════════════════════
    # UTILITY
    # ══════════════════════════════════════════════════════════════════════════
    def _section_label(self, parent, text):
        ctk.CTkLabel(parent, text=text,
                     font=ctk.CTkFont("Courier New", 9),
                     text_color=TEXT_FAINT,
                     anchor="w").pack(fill="x", padx=16, pady=(12, 4))

    def _divider(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color=BORDER,
                     corner_radius=0).pack(fill="x", padx=16, pady=8)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = JarvisApp()
    app.mainloop()