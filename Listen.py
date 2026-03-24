import os
import torch
import numpy as np 
import sounddevice as sd
import soundfile as sf
import tempfile
from faster_whisper import WhisperModel

# config (auto-detect GPU/CPU)
model_size = "tiny"
device = "cuda" if torch.cuda.is_available() else "cpu"
compute_type = "float16" if device == "cuda" else "float32"
model = WhisperModel(model_size, device=device, compute_type=compute_type)

# this prints the detected language and probability, and the segments with timestamps and text, good for debugging
# for segment in segments:
#     print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")


def listen(model, sr=16000, silence_threshold=0.01, silence_duration=1.2, max_duration=10):
    """
    Records audio from the microphone until the user stops speaking,
    then transcribes it using Faster-Whisper.

    Instead of recording a fixed duration (old approach), this function
    watches the volume level and stops automatically after sustained silence.
    This makes the assistant feel much more natural to interact with.

    Parameters:
        model              : The loaded WhisperModel instance to use for transcription
        sr (int)           : Sample rate in Hz. Whisper expects 16000 Hz.
        silence_threshold  : RMS volume level below which audio is considered silence.
                             Tune this if recording cuts off too early (raise it)
                             or never stops (lower it). Default: 0.01
        silence_duration   : How many seconds of silence triggers end of recording.
                             1.2 seconds gives a natural pause before cutting off.
        max_duration       : Hard cap on total recording time in seconds.
                             Prevents infinite recording if silence is never detected.

    Returns:
        str: The transcribed text from the user's speech.
             Returns an empty string if nothing was detected.
    """

    # Break audio into 100ms chunks for real-time volume monitoring
    chunk_size = int(sr * 0.1)

    # Maximum number of chunks before we force-stop (based on max_duration)
    max_chunks = int(max_duration / 0.1)

    # How many consecutive silent chunks before we stop recording
    silence_chunks_needed = int(silence_duration / 0.1)

    audio_chunks = []   # Accumulate all recorded audio here
    silence_count = 0   # Tracks how many consecutive silent chunks we've seen
    started_speaking = False  # We don't stop on silence until the user has spoken

    print("Listening...")

    # Open the microphone stream and read chunks one at a time
    with sd.InputStream(samplerate=sr, channels=1, dtype="float32") as stream:
        for _ in range(max_chunks):
            chunk, _ = stream.read(chunk_size)
            audio_chunks.append(chunk.copy())

            # Calculate RMS (Root Mean Square) volume of this chunk
            # RMS gives a good measure of perceived loudness
            volume = np.sqrt(np.mean(chunk ** 2))

            if volume > silence_threshold:
                # User is speaking — reset silence counter
                started_speaking = True
                silence_count = 0
            elif started_speaking:
                # User was speaking but has gone quiet — count silent chunks
                silence_count += 1
                if silence_count >= silence_chunks_needed:
                    # Enough silence detected — stop recording
                    break
            # If started_speaking is still False, we just wait (ignore ambient noise
            # before the user starts talking)

    print("Processing...")

    # Concatenate all recorded chunks into one audio array
    audio = np.concatenate(audio_chunks, axis=0)

    # Write to a temp file, transcribe it, then clean up
    # We use a temp file because Whisper expects a file path, not a raw array
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            sf.write(tmp_path, audio, sr)

        # Transcribe the recorded audio
        segments, info = model.transcribe(tmp_path, beam_size=5)

        # Join all segments into a single string and return
        return " ".join(s.text.strip() for s in segments if s.text.strip())

    finally:
        # Always delete the temp file, even if transcription threw an error
        # Without this, temp WAV files accumulate on disk every single call
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)