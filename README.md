# Jarvis
A Jarvis style AI that lives in your computer, ready to serve your requests.

## What can it do?
Its an Ai that lives in your computer, and can do commands like make new projects, open apps and more to come!

## Security?
Anything related to downloading files from the web, or running terminal commands are strictly limited. It can at most access the web and make repos/vs code projects. It is also blocked from the network (unless you disable it and have a good internet provider).

## Potential general pipeline?
```mermaid
graph TD
  A(Mic/STT) --> B[LLM/Agent] 
  B --> C1(Actions/Keyboard Commands)
  B --> C2(Follow up Questions)
  C2 --> C1
  C1 --> D(Final Response/Filtered Summary)
  D --> E(Piper/TTS response)
```
Input: Could be Mic or a wakeup call "Wake up daddys home" for example.

Ouptut: a pipeline of actions, creating media, even asking clarification questions as well.

## Models to be used?
So far we are using:

Agent: Qwen3.5 2b. This is big enough to have great agentic capability, whilst being able to fit on a laptop

STT: We are using faster-whisper because of its compatibility across platforms/code, there are probably better options though

TTS: Piper TTS is what we are using. Despite its quality, it can get a near **200x - 600x** real time audio speed with compatibility 
across devices of all sorts. We are looking to finetune piper to "sound" more like jarvis using huggingface datasets, but it has not been done yet.

Enviroment: So far, the idea is that it can input preset keyboard commands, though we are looking to use the Agents multimodel capabilities to control its actions (with safeguards).

## Environment setup

1. Install the requirements: `pip install -r requirements.txt`

2. Install torch with the right CUDA version for your system (see [pytorch.org](https://pytorch.org/get-started/locally/)), e.g. for CUDA 13.0:

   ```bash
   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
   ```
  other gpus may work better with different versions, so be sure to check the compatibility. If you don't have a compatible GPU, you can still run the model on CPU, but it will be much slower. 

   CPU only (if you dont have a gpu): `pip install torch torchvision torchaudio`
   
3. Download the [Piper TTS binary](https://github.com/rhasspy/piper/releases) and put it somewhere on your PATH (or next to `Piper_tts.py`)
   
4. Install Ollama [here](https://ollama.com) or
   
   For Linux:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```
   For Windows:
   ```powershell
   irm https://ollama.com/install.ps1 | iex
   ```
   
5. Then pull the model:

   ```bash
   ollama pull qwen3.5:2b
   ```

   `qwen3.5:9b` is an amazing option if you have 16 GB+ VRAM
   Ensure that you have the latest ollama version

6. Run the chatbot:
   - **Basic mode** (local transformers, no tools): `python mainchat.py`
   - **Agent mode** (Ollama + tool/MCP support): `USE_QWEN_AGENT=1 python mainchat.py`

## Voice training (optional)

To fine-tune Piper on a custom voice, install the extra deps first:

```bash
sudo apt install espeak-ng
pip install piper-phonemize --extra-index-url https://rhasspy.github.io/piper-phonemize/
pip install "piper-train @ git+https://github.com/rhasspy/piper.git#subdirectory=src/python"
```

Then run `python training/piperCustomVoice.py`

You can choose any dataset youd like, but be sure to not steal other peoples data (very bad).
we use open sourced datasets on huggingface.

# Project J.A.R.V.I.S. Roadmap (Local Build)

## Phase 1: The Core Brain (Ollama & Logic)
- [x] **Ollama Setup:** Install and pull high-reasoning models for deep thinking tasks.
- [ ] **Orchestration Layer:** Build a Python wrapper to handle system prompts and context window management.
- [ ] **Local Profile Database:** Set up a local JSON or SQLite database to store user preferences and "Self-Learn" data (isolated locally, could add RL here for perpetual improvement).
- [ ] **Keyword Trigger Engine:** Implement a listener for specific phrases:
    - [ ] "JARVIS DADDY'S HOME" -> Trigger welcome sequence and pull recent topics/projects of activity.
    - [ ] "HEY JARVIS" -> Activate non-blocking STT stream to parse user/masters requests.

## Phase 2: Voice & Interaction (STT/TTS)
- [ ] **Speech-to-Text (STT):** Integrate `Faster-Whisper` for local, low-latency transcription of commands and notes.
- [x] **Text-to-Speech (TTS):** Implement `Piper` or `Coqui TTS` for high-speed local voice generation.
- [ ] **Voice Packs:** Create system logic to swap between "Professional English" and "Gen Z Slang" based on detected mood or manual toggle.
- [ ] **Note-Taking Feature:** Map a "Make a note" voice command to automatically save transcripts to a local `.md` file or database or Obsidian visuals for easy parsing.

## Phase 3: The "Holographic" UI & Telemetry
- [ ] **System Monitor:** Use `psutil` and `GPUtil` or even `nvidia-smi` to pull real-time stats:
    - [ ] CPU / RAM / GPU Usage % / NVME read/write.
    - [ ] System Uptime and Network Latency (Ping, ingress/egress).
- [ ] **Agent Stats:** Track and display LLM "Time to First Token" and overall inference latency/throughput.
- [ ] **Visualizer UI:** - [ ] Build a transparent/dark-themed overlay (using PyQt or Kivy) to simulate a "holographic" display.
    - [ ] Display live scrolling transcripts and "listening" waveforms.
    - [ ] Display active project completion percentages (eg project 1 is 52% completed, project 2 is 34% etc)

## Phase 4: Automation & Life Integration
- [ ] **Developer Workflow Maker** Implement tools to allow jarvis to auto create workflows based on use frequency
    - [ ] Script a "Assignment Checker" (link to a local calendar or `.csv`).
    - [ ] Integrate Git commands: "Make new repo," "Open [Project] for editing."
- [ ] **Shopping/Cart Logic** Implement basic shopping website access (like amazon, temu etc) with potentially OpenCV to block clicking buy buttons/falling for click-bait ads.
    - [ ] Build a secure mock-integration for cart management.
    - [ ] Implement "Request Review" logic (e.g., "I found 3 item lists, which one should I order?").
- [ ] **Censorship/Safety:** Add a local keyword filter or prompt-injection guardrail to keep responses aligned with your preferences (could be prompt guards from HF).

## Phase 5: Testing & Polishing
- [ ] **Latency Optimization:** Fine-tune model quantization (4-bit or 8-bit) to ensure voice responses feel instantaneous.
- [ ] **Mood Detection:** Simple sentiment analysis on user input to automatically pick the right "Voice Pack." (eg Friday voice etc).
