import os
import subprocess
import uuid
import platform
import shutil

# Get the absolute path of the project directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Path to Piper executable (must be placed inside /piper)
# Use platform-appropriate filename (piper.exe on Windows, piper otherwise)
_PIPER_EXE = "piper.exe" if platform.system() == "Windows" else "piper"
PIPER_PATH = os.path.join(BASE_DIR, "piper", _PIPER_EXE)

# Path to voice model (place inside /piper/voices)
VOICE_MODEL = os.path.join(BASE_DIR, "piper", "voices", "en_US-joe-medium.onnx")


def format_for_speech(text: str) -> str:
    """
    Cleans and formats text before sending to TTS.
    Adjust punctuation to improve pacing and tone.
    """
    # Replace exclamation marks with periods for calmer tone
    return text.replace("!", ".")


def speak(text: str, speed: float = 0.82) -> None:
    """
    Converts text to speech using Piper (local neural TTS).

    Parameters:
        text (str): The text to convert into speech.
        speed (float): Speech rate modifier.
                       Lower = slower and calmer.
                       Recommended range: 0.75 - 0.90
    """

    # Clean and prepare the text
    formatted_text = format_for_speech(text)

    # Generate unique filename to avoid overwriting
    filename = os.path.join(BASE_DIR, f"output_{uuid.uuid4().hex}.wav")

    # Start Piper process
    process = subprocess.Popen(
        [PIPER_PATH, "-m", VOICE_MODEL, "-f", filename, "-s", str(speed)],
        stdin=subprocess.PIPE,
        text=True
    )

    # Send text to Piper via stdin
    process.communicate(formatted_text)

    # Play generated audio file (cross-platform)
    def _find_audio_player():
        # Return the first available audio player on Linux. On Windows we use os.startfile.
        if platform.system() == "Windows":
            return None
        candidates = ["paplay", "aplay", "play", "ffplay"]
        for cmd in candidates:
            if shutil.which(cmd):
                return cmd
        return None

    def _play_audio(path: str):
        if platform.system() == "Windows":
            try:
                os.startfile(path)
                return
            except Exception:
                return

        player = _find_audio_player()
        if not player:
            return

        # ffplay needs flags; others take filename directly
        if player == "ffplay":
            subprocess.run([player, "-nodisp", "-autoexit", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run([player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        _play_audio(filename)
    finally:
        # Attempt to remove temporary file; ignore errors
        try:
            os.remove(filename)
        except Exception:
            pass