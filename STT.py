import torch
from faster_whisper import WhisperModel

# config (auto-detect GPU/CPU)
model_size = "tiny"
device = "cuda" if torch.cuda.is_available() else "cpu"
compute_type = "float16" if device == "cuda" else "float32"

model = WhisperModel(model_size, device=device, compute_type=compute_type)

# transcribe
# 'beam_size=5' is generally the recommended balance for accuracy/speed
segments, info = model.transcribe("exampleAudio.mp3", beam_size=5)

print(f"Detected language '{info.language}' with probability {info.language_probability:.2f}")

for segment in segments:
    print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")