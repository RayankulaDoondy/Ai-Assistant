#!/usr/bin/env python3
"""Debug script to test Whisper with sample audio"""

import numpy as np
import soundfile as sf
import whisper
import tempfile
import os

# Load Whisper model
model = whisper.load_model("tiny")

test_configs = [
    ("Silent", np.zeros(16000 * 2, dtype=np.float32)),
    ("Noise 0.001", np.random.randn(16000 * 2) * 0.001),
    ("Noise 0.01", np.random.randn(16000 * 2) * 0.01),
    ("Noise 0.1", np.random.randn(16000 * 2) * 0.1),
    ("Sine 0.01", np.sin(np.linspace(0, 100, 16000 * 2)) * 0.01),
    ("Sine 0.1", np.sin(np.linspace(0, 100, 16000 * 2)) * 0.1),
    ("Sine 0.5", np.sin(np.linspace(0, 100, 16000 * 2)) * 0.5),
]

for name, audio in test_configs:
    print(f"\n=== Test: {name} ===")
    min_val = audio.min()
    max_val = audio.max()
    mean_val = np.mean(np.abs(audio))
    print(f"Audio stats: min={min_val:.6f}, max={max_val:.6f}, mean_abs={mean_val:.6f}")
    
    result = model.transcribe(audio, language="en", temperature=0, condition_on_previous_text=False)
    text = result.get('text', '').strip()
    print(f"Result text: '{text}'")
    
    segments = result.get('segments', [])
    print(f"Segments count: {len(segments)}")
    if segments:
        for i, seg in enumerate(segments):
            print(f"  Segment {i}: text='{seg.get('text', '')}' no_speech_prob={seg.get('no_speech_prob'):.4f} avg_logprob={seg.get('avg_logprob'):.4f}")

print("\n=== Test Complete ===")


