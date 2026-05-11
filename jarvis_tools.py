from __future__ import annotations

import datetime as _datetime
import os
import platform
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path

import psutil


WORKSPACE_DIR = Path(__file__).resolve().parent
if platform.system() == "Windows":
    USER_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Jarvis"
else:
    USER_DATA_DIR = Path.home() / ".local" / "share" / "Jarvis"

NOTES_DIR = USER_DATA_DIR / "notes"
PROJECTS_DIR = USER_DATA_DIR / "projects"
README_PATH = WORKSPACE_DIR / "README.md"
PLAN_PATH = WORKSPACE_DIR / "Plan.txt"


def _ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slugify(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip()).strip("-._")
    return slug or fallback


def _timestamp() -> str:
    return _datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return _datetime.datetime.now().strftime("%Y-%m-%d")


def _looks_like_url(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value.strip()))


def _open_target(target: str) -> str:
    if _looks_like_url(target):
        webbrowser.open(target)
        return f"Opened {target}."

    if platform.system() == "Windows":
        os.startfile(target)
    else:
        subprocess.Popen(["xdg-open", target])
    return f"Opened {target}."


def launch_application(
    application: str | None = None,
    target: str | Path | None = None,
    arguments: list[str] | None = None,
) -> str:
    app_text = str(application).strip() if application else ""
    target_text = str(target).strip() if target is not None else ""
    argument_list = [str(argument) for argument in (arguments or []) if str(argument).strip()]

    if not app_text:
        if target_text:
            if _looks_like_url(target_text) or Path(target_text).exists():
                return _open_target(target_text)
            app_text = target_text
            target_text = ""
        else:
            return _open_target(str(WORKSPACE_DIR))

    command = [app_text, *argument_list]
    if target_text:
        command.append(target_text)

    try:
        subprocess.Popen(command, cwd=str(WORKSPACE_DIR))
        return f"Launched {app_text}."
    except FileNotFoundError:
        if platform.system() == "Windows":
            start_command = ["cmd", "/c", "start", "", app_text, *argument_list]
            if target_text:
                start_command.append(target_text)
            subprocess.Popen(start_command, cwd=str(WORKSPACE_DIR))
            return f"Launched {app_text}."

        if target_text and (Path(target_text).exists() or _looks_like_url(target_text)):
            return _open_target(target_text)

        raise


def open_note_file(title: str | None = None, body: str | None = None) -> str:
    _ensure_directory(NOTES_DIR)
    note_path = NOTES_DIR / f"{_today()}.md"

    if not note_path.exists():
        note_path.write_text(f"# {title or 'Jarvis Notes'}\n\n", encoding="utf-8")

    if body:
        create_note(body, title=title, note_path=note_path)

    launch_application(target=note_path)
    return f"Opened note at {note_path}."


def create_note(text: str, title: str | None = None, note_path: Path | None = None) -> str:
    clean_text = text.strip()
    if not clean_text:
        return "No note text was provided."

    _ensure_directory(NOTES_DIR)
    note_file = note_path or (NOTES_DIR / f"{_today()}.md")

    if not note_file.exists():
        note_file.write_text(f"# {title or 'Jarvis Notes'}\n\n", encoding="utf-8")

    heading = title.strip() if title else "Quick Note"
    with note_file.open("a", encoding="utf-8") as handle:
        handle.write(f"## {heading} - {_timestamp()}\n")
        handle.write(f"{clean_text}\n\n")

    return f"Saved note to {note_file}."


def create_workspace(name: str | None = None) -> str:
    _ensure_directory(PROJECTS_DIR)
    base_name = _slugify(name or f"jarvis-{_datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}", "jarvis-project")
    workspace_path = PROJECTS_DIR / base_name
    counter = 2

    while workspace_path.exists():
        workspace_path = PROJECTS_DIR / f"{base_name}-{counter}"
        counter += 1

    workspace_path.mkdir(parents=True, exist_ok=False)

    if shutil.which("git"):
        subprocess.run(["git", "init"], cwd=str(workspace_path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    launch_application(target=workspace_path)
    return f"Created workspace at {workspace_path}."


def _get_gpu_summary() -> str:
    if shutil.which("nvidia-smi"):
        command = [
            "nvidia-smi",
            "--query-gpu=name,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
            line = completed.stdout.strip().splitlines()[0]
            name, util, used, total = [part.strip() for part in line.split(",", 3)]
            return f"{name} | {util}% | {used}/{total} MB"
        except Exception:
            pass

    return "Unavailable"


def get_system_status() -> str:
    cpu = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path(WORKSPACE_DIR.anchor or WORKSPACE_DIR)))
    uptime_seconds = int(_datetime.datetime.now().timestamp() - psutil.boot_time())
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60

    battery = psutil.sensors_battery()
    battery_text = "N/A"
    if battery is not None:
        battery_text = f"{battery.percent:.0f}% {'plugged in' if battery.power_plugged else 'on battery'}"

    lines = [
        f"CPU: {cpu:.0f}%",
        f"RAM: {memory.percent:.0f}% ({memory.used / 1024**3:.1f}/{memory.total / 1024**3:.1f} GB)",
        f"Disk: {disk.percent:.0f}% ({disk.used / 1024**3:.1f}/{disk.total / 1024**3:.1f} GB)",
        f"GPU: {_get_gpu_summary()}",
        f"Battery: {battery_text}",
        f"Uptime: {hours:02d}:{minutes:02d}:{seconds:02d}",
        f"Workspace: {WORKSPACE_DIR}",
    ]
    return "\n".join(lines)


def _extract_pending_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]"):
            item = stripped[5:].strip()
            if item:
                items.append(item)
    return items


def summarize_pending_work(limit: int = 8) -> str:
    sources = []
    if README_PATH.exists():
        sources.append(("Roadmap", _extract_pending_items(README_PATH.read_text(encoding="utf-8", errors="ignore"))))

    if PLAN_PATH.exists():
        plan_lines = [
            line.strip()
            for line in PLAN_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        ]
        if plan_lines:
            sources.append(("Plan.txt", plan_lines[:limit]))

    collected: list[str] = []
    for source_name, entries in sources:
        if entries:
            collected.append(f"{source_name}:")
            collected.extend(f"- {entry}" for entry in entries[:limit])

    if not collected:
        return "No assignment or roadmap items were found."

    return "Pending work:\n" + "\n".join(collected)
