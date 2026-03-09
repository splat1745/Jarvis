import os
import subprocess
import soundfile as sf
from datasets import load_dataset

# --- Config ---
DATASET        = "keithito/lj_speech"   # any HF dataset with audio + text
SPLIT          = "train"
AUDIO_COL      = "audio"
TEXT_COL       = "normalized_text"
MAX_SAMPLES    = 1000
OUTPUT_DIR     = "piper_data"
LANGUAGE       = "en-us"

wavs_dir = os.path.join(OUTPUT_DIR, "wavs")
os.makedirs(wavs_dir, exist_ok=True)

# 1. Stream dataset → save wavs + metadata.csv
print("Streaming dataset...")
ds = load_dataset(DATASET, split=SPLIT, streaming=True, trust_remote_code=True)

with open(os.path.join(OUTPUT_DIR, "metadata.csv"), "w") as meta:
    for i, sample in enumerate(ds):
        if i >= MAX_SAMPLES:
            break
        audio = sample[AUDIO_COL]
        text  = sample[TEXT_COL]
        name  = f"sample_{i:05d}"
        sf.write(os.path.join(wavs_dir, f"{name}.wav"), audio["array"], audio["sampling_rate"])
        meta.write(f"{name}|{text}|{text}\n")   # ljspeech format: id|raw|normalized
        if i % 100 == 0:
            print(f"  {i}/{MAX_SAMPLES}")

print(f"Saved {i + 1} samples.")

# 2. Preprocess (phonemize text, extract mel spectrograms)
print("Preprocessing...")
subprocess.run([
    "python", "-m", "piper_train.preprocess",
    "--language",       LANGUAGE,
    "--input-dir",      OUTPUT_DIR,
    "--output-dir",     OUTPUT_DIR,
    "--dataset-format", "ljspeech",
    "--single-speaker",
], check=True)

# 3. Train
print("Training...")
subprocess.run([
    "python", "-m", "piper_train",
    "--dataset-dir",        OUTPUT_DIR,
    "--accelerator",        "gpu",
    "--devices",            "1",
    "--batch-size",         "16",
    "--validation-split",   "0.1",
    "--num-test-examples",  "5",
    "--max-epochs",         "10000",
    "--checkpoint-epochs",  "1",
], check=True)
