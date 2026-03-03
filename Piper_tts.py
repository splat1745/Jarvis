import os
import subprocess
import uuid

# Get the absolute path of the project directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Path to Piper executable (must be placed inside /piper)
PIPER_PATH = os.path.join(BASE_DIR, "piper", "piper.exe")

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

    # Play generated audio file (Windows only)
    os.startfile(filename)