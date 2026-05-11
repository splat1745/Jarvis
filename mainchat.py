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
from Listen import listen, model
from Piper_tts import speak
from jarvis_tools import (
    create_note,
    create_workspace,
    get_system_status,
    launch_application,
    open_note_file,
    summarize_pending_work,
)

class QwenChatbot:
    def __init__(self, model_name="Qwen/Qwen3.5-2B"):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(model_name).to(self.device)
        self.history = []

    def generate_response(self, user_input, stream=True):
        messages = self.history + [{"role": "user", "content": user_input}]

        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        inputs = self.tokenizer(text, return_tensors="pt").to(self.device)
        input_length = inputs["input_ids"].shape[-1]

        generated_ids = self.model.generate(
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

        return response

# ----------- CUSTOM TOOL DEFINITIONS (for Qwen Agent) -----------

@register_tool('get_current_time')
class GetCurrentTime(BaseTool):
    description = "Get the current date and time in ISO format."
    parameters = []

    def call(self, params: str, **kwargs) -> str:
        from datetime import datetime
        return datetime.now().isoformat()


@register_tool('launch_application')
class LaunchApplication(BaseTool):
    description = "Launch an application, file, folder, or URL."
    parameters = {
        'type': 'object',
        'properties': {
            'application': {
                'type': 'string',
                'description': 'Optional application executable or command to launch.'
            },
            'target': {
                'type': 'string',
                'description': 'Optional file, folder, or URL to open.'
            },
            'arguments': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Optional command-line arguments for the application.'
            },
        },
        'required': []
    }

    def call(self, params: str | dict, **kwargs) -> str:
        args = self._verify_json_format_args(params)
        return launch_application(
            application=args.get('application'),
            target=args.get('target'),
            arguments=args.get('arguments'),
        )


@register_tool('create_workspace')
class CreateWorkspace(BaseTool):
    description = "Create a new local workspace folder and open it with the default handler."
    parameters = {
        'type': 'object',
        'properties': {
            'name': {
                'type': 'string',
                'description': 'Optional workspace name. A timestamped name is used when omitted.'
            }
        },
        'required': []
    }

    def call(self, params: str | dict, **kwargs) -> str:
        args = self._verify_json_format_args(params)
        return create_workspace(args.get('name'))


@register_tool('open_note_file')
class OpenNoteFile(BaseTool):
    description = "Open the current note file for fast note taking."
    parameters = {
        'type': 'object',
        'properties': {
            'title': {
                'type': 'string',
                'description': 'Optional note title.'
            },
            'body': {
                'type': 'string',
                'description': 'Optional note text to append before opening the file.'
            }
        },
        'required': []
    }

    def call(self, params: str | dict, **kwargs) -> str:
        args = self._verify_json_format_args(params)
        return open_note_file(args.get('title'), args.get('body'))


@register_tool('create_note')
class CreateNote(BaseTool):
    description = "Append a note to the daily Jarvis notes file and open it in VS Code."
    parameters = {
        'type': 'object',
        'properties': {
            'text': {
                'type': 'string',
                'description': 'The note text to save.'
            },
            'title': {
                'type': 'string',
                'description': 'Optional title for the note section.'
            }
        },
        'required': ['text']
    }

    def call(self, params: str | dict, **kwargs) -> str:
        args = self._verify_json_format_args(params)
        return create_note(args['text'], title=args.get('title'))


@register_tool('get_system_status')
class GetSystemStatus(BaseTool):
    description = "Return a concise machine status summary for the GUI sidebar."
    parameters = {
        'type': 'object',
        'properties': {},
        'required': [],
    }

    def call(self, params: str | dict, **kwargs) -> str:
        self._verify_json_format_args(params)
        return get_system_status()


@register_tool('summarize_pending_work')
class SummarizePendingWork(BaseTool):
    description = "Summarize pending work items from the roadmap and plan files."
    parameters = {
        'type': 'object',
        'properties': {
            'limit': {
                'type': 'integer',
                'minimum': 1,
                'maximum': 20,
                'default': 8,
                'description': 'Maximum number of items to include from each source.'
            }
        },
        'required': []
    }

    def call(self, params: str | dict, **kwargs) -> str:
        args = self._verify_json_format_args(params)
        return summarize_pending_work(int(args.get('limit', 8)))


def create_local_chatbot(model_name: str | None = None) -> QwenChatbot:
    return QwenChatbot(model_name or os.environ.get("QWEN_MODEL", "Qwen/Qwen3.5-2B"))


def build_agent_tools() -> list:
    tools = [
        'get_current_time',
        'launch_application',
        'create_workspace',
        'open_note_file',
        'create_note',
        'get_system_status',
         'summarize_pending_work',
    ]

    if os.environ.get("JARVIS_ENABLE_MCP", "0") == "1":
        tools.append({
            'mcpServers': {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
                },
                "duckduckgo": {
                    "command": "uvx",
                    "args": ["duckduckgo-mcp-server"]
                }
            }
        })

    return tools


def create_agent_bot():
    if not HAS_QWEN_AGENT:
        return None

    llm_cfg = {
        'model': os.environ.get("QWEN_MODEL", "qwen3.5:2b"),
        'model_server': os.environ.get("QWEN_MODEL_SERVER", "http://localhost:11434/v1"),
        'api_key': 'EMPTY',
    }
    return Assistant(llm=llm_cfg, function_list=build_agent_tools())


def _maybe_speak(text: str) -> None:
    if text and "\n" not in text and len(text) <= 180:
        speak(text)


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

    print("Welcome to the Qwen Chatbot! Say 'exit' to quit.")

    if use_agent:
        bot = create_agent_bot()
        if bot is None:
            print("Qwen Agent is unavailable, falling back to local transformers mode.")
            chatbot = create_local_chatbot()
            user_input = listen(model)
            while user_input.lower() != "exit":
                response = chatbot.generate_response(user_input)
                print(f"Bot: {response}")
                speak(response)
                print("----------------------")
                user_input = listen(model)
        else:
            user_input = listen(model)
            while user_input.lower() != "exit":
                messages = [{'role': 'user', 'content': user_input}]
                collected = ""
                try:
                    for chunk in bot.run(messages=messages):
                        chunk_text = str(chunk)
                        print(chunk_text, end="", flush=True)
                        collected += chunk_text
                except Exception as e:
                    print("\nAgent error:", e)

                print("\n--- Final response ---")
                if collected:
                    speak(collected)

                print("----------------------")
                user_input = listen(model)

    else:
        # Fallback to local model (transformers)
        chatbot = create_local_chatbot()
        user_input = listen(model)
        while user_input.lower() != "exit":
            response = chatbot.generate_response(user_input)
            print(f"Bot: {response}")
            speak(response)
            print("----------------------")
            user_input = listen(model)

