#!/usr/bin/env python3
"""start.py — cross-platform launcher + auto-bootstrap for the JARVIS Command
Center. Works on Linux, macOS, and Windows.

On first run it creates a local `.venv`, installs the cockpit's dependencies
(hardware-aware: adds pywinpty on Windows), then launches the server. Re-runs
are instant. Optional `--voice` also bootstraps the voice/LLM stack in the root
Jarvis venv, picking the CUDA or CPU PyTorch wheel based on detected hardware.

    python start.py            # bootstrap + run the cockpit
    python start.py --voice    # also set up the voice/torch stack
"""
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # the root Jarvis project (voice stack)
VENV = HERE / ".venv"
IS_WIN = os.name == "nt"


def venv_python(vdir: Path) -> Path:
    return vdir / ("Scripts/python.exe" if IS_WIN else "bin/python")


def ensure_venv(vdir: Path):
    if not venv_python(vdir).exists():
        print(f"[bootstrap] creating venv at {vdir}")
        venv.EnvBuilder(with_pip=True).create(vdir)


def pip_install(vdir: Path, args: list[str]):
    subprocess.check_call([str(venv_python(vdir)), "-m", "pip", "install",
                           "-q", "--upgrade", *args])


def has_nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None


def bootstrap_cockpit():
    ensure_venv(VENV)
    reqs = ["fastapi>=0.110", "uvicorn[standard]>=0.29", "psutil>=5.9"]
    if IS_WIN:
        reqs.append("pywinpty>=2.0")   # ConPTY terminals on Windows
    # only install if fastapi is missing (fast path on re-runs)
    check = subprocess.run([str(venv_python(VENV)), "-c", "import fastapi, psutil"],
                           capture_output=True)
    if check.returncode != 0:
        print("[bootstrap] installing cockpit dependencies…")
        pip_install(VENV, ["pip"])
        pip_install(VENV, reqs)


def bootstrap_voice():
    """Set up the voice/LLM stack in the ROOT venv, hardware-aware."""
    vdir = ROOT / ".venv"
    ensure_venv(vdir)
    print("[bootstrap] installing voice stack (this can take a while)…")
    pip_install(vdir, ["pip"])
    # PyTorch: CUDA wheel if an NVIDIA GPU is present, else CPU
    if has_nvidia():
        print("[bootstrap] NVIDIA GPU detected → CUDA PyTorch")
        pip_install(vdir, ["torch", "torchvision", "torchaudio",
                           "--index-url", "https://download.pytorch.org/whl/cu124"])
    else:
        print("[bootstrap] no NVIDIA GPU → CPU PyTorch")
        pip_install(vdir, ["torch", "torchvision", "torchaudio"])
    pip_install(vdir, ["faster-whisper", "sounddevice", "soundfile", "numpy",
                       "transformers", "accelerate"])
    tk = "" if IS_WIN else " (GUI also needs the system 'python3-tk' package)"
    print(f"[bootstrap] voice stack ready{tk}")


def main():
    if "--voice" in sys.argv:
        bootstrap_voice()
    bootstrap_cockpit()
    print(f"[start] JARVIS Command Center on {platform.system()} — launching…")
    os.chdir(HERE)
    os.execv(str(venv_python(VENV)), [str(venv_python(VENV)), "server.py"])


if __name__ == "__main__":
    main()
