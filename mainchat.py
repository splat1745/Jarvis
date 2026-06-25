from email.mime import text
from profile_db import JarvisDB

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import os
import threading
from typing import List, Dict

# Optional: Qwen Agent adds tool/MCP support on top of any local model server.
# If the package isn't installed we fall back to the plain transformers model below.
try:
    from qwen_agent.agents import Assistant
    from qwen_agent.tools.base import BaseTool, register_tool
    HAS_QWEN_AGENT = True
except Exception:
    Assistant = None
    BaseTool = object
    def register_tool(name): return lambda cls: cls
    HAS_QWEN_AGENT = False

import json

# custom files
try:
    from Listen import listen, model
except Exception as e:
    listen = None
    model = None
    print(f"[JARVIS] Listen.py not loaded: {e}")

from Piper_tts import speak

class QwenChatbot:
    def __init__(self, model_name="Qwen/Qwen3.5-2B"):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="cuda" if torch.cuda.is_available() else "cpu"
        )
        self.db = JarvisDB()
        self.history = self.db.get_llm_context()
        print(f"[DB] Restored {len(self.history)} messages from last session")
    def trim_history(self, max_messages=20):
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]

    def generate_response(self, user_input, stream=True):
        self.trim_history()
        messages = self.history + [{"role": "user", "content": user_input}]

        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        device = next(self.model.parameters()).device
        inputs = self.tokenizer(text, return_tensors="pt").to(device)
        input_length = inputs["input_ids"].shape[-1]

        generated_ids = self.model.generate(  # type: ignore[attr-defined]
            **inputs,
            max_new_tokens=256,
            do_sample=True, # enable sampling (more natural response)
            temperature=0.7, # control randomness: lower = more focused
            top_p=0.9,
        )[0]
        response_ids = generated_ids[input_length:].tolist()
        response = self.tokenizer.decode(response_ids, skip_special_tokens=True).strip()

        # Update history
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": response})
        self.db.save_message("user", user_input)
        self.db.save_message("assistant", response)

        return response

# ----------- CUSTOM TOOL DEFINITIONS (for Qwen Agent) -----------

if HAS_QWEN_AGENT:
    @register_tool('get_current_time')
    class GetCurrentTime(BaseTool):  # type: ignore[valid-type]
        description = "Get the current date and time in ISO format."
        parameters = []

        def call(self, params: str, **kwargs) -> str:
            from datetime import datetime
            return datetime.now().isoformat()


# Example Usage
if __name__ == "__main__":

    # -------------------------------------------------------------------------
    # TWO MODES:
    #
    # MODE 1 (default) — Local transformers model, NO tool support.
    #   Just run:  python mainchat.py
    #
    # MODE 2 — qwen_agent Assistant WITH tool/MCP support.
    #   Requires a local OpenAI-compatible server. Pick one:
    #
    #   a) Ollama (easiest, CPU+GPU, any model):
    #        ollama serve
    #        ollama pull qwen3.5:2b      # or any tool-capable model
    #      Runs 100% locally. Exposes a standard chat API at localhost:11434/v1.
    #      ("OpenAI-compatible" means it uses the same HTTP format — not the company.)
    #      Best tool-calling models: qwen2.5, qwen3, llama3.1, mistral-nemo
    #
    #   b) HuggingFace Transformers built-in server (good for GPU):
    #        pip install "transformers[serving] @ git+https://github.com/huggingface/transformers.git@main"
    #        transformers serve --force-model Qwen/Qwen3.5-2B --port 8000 --continuous-batching
    #      Also runs 100% locally. Serves the model over the same HTTP format.
    #
    #   c) vLLM (fastest, GPU only):
    #        vllm serve Qwen/Qwen3.5-2B --port 8000 --enable-auto-tool-choice --tool-call-parser qwen3_coder
    #      Also local. Just a faster GPU-optimised server.
    #
    #   Then run:  USE_QWEN_AGENT=1 python mainchat.py
    #
    # Override the model or server via env vars:
    #   QWEN_MODEL=qwen3.5:2b                        # model name for your server
    #   QWEN_MODEL_SERVER=http://localhost:11434/v1  # server URL (Ollama default)
    # -------------------------------------------------------------------------


    use_agent = os.environ.get("USE_QWEN_AGENT", "0") == "1"
    print("Welcome to JARVIS. Say 'exit' to quit.")

    bot = None

    if use_agent and HAS_QWEN_AGENT:
        llm_cfg = {
            'model': os.environ.get("QWEN_MODEL", "qwen2.5:0.5b"),
            'model_server': os.environ.get("QWEN_MODEL_SERVER", "http://localhost:11434/v1"),
            'api_key': 'EMPTY',
        }
        tools = [
            'get_current_time',
            {'mcpServers': {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
                },
                "duckduckgo": {
                    "command": "uvx",
                    "args": ["duckduckgo-mcp-server"]
                }
            }}
        ]
        bot = Assistant(llm=llm_cfg, function_list=tools)  # type: ignore[misc]

        user_input = listen(model) if listen else input("You: ")  # type: ignore[misc]
        while user_input.lower() != "exit":
            messages = [{'role': 'user', 'content': user_input}]
            collected = ""
            try:
                for chunk in bot.run(messages=messages) if bot else []:  # type: ignore[arg-type]
                    chunk_text = str(chunk)
                    print(chunk_text, end="", flush=True)
                    collected += chunk_text
            except Exception as e:
                print("\nAgent error:", e)

            print("\n--- response ---")
            if collected:
                speak(collected)
            print("----------------")

            user_input = listen(model) if listen else input("You: ")  # type: ignore[misc]

    else:
        chatbot = QwenChatbot()
        user_input = listen(model) if listen else input("You: ")  # type: ignore[misc]
        while user_input.lower() != "exit":
            response = chatbot.generate_response(user_input)
            print(f"JARVIS: {response}")
            speak(response)
            print("----------------")
            user_input = listen(model) if listen else input("You: ")  # type: ignore[misc]