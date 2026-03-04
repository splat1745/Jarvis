import torch
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


def listen(model, duration=3, sr=16000, beam_size=5):
    # this records for 3 seconds, sample rate/audio quality at 16 kHz, and beam size (searching for best transcription) at beam 5
    rec = sd.rec(int(duration * sr), samplerate=sr, channels=1, dtype="float32")
    sd.wait()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        # write soundfile
        sf.write(tmp.name, rec, sr)

        # transcribe 
        segments, info = model.transcribe(tmp.name, beam_size=beam_size)

    # all segments combine from transcription, and return as one string
    return " ".join(s.text.strip() for s in segments if s.text.strip())
