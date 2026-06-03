#!/usr/bin/env python3
"""
Direct sounddevice test to diagnose microphone issue
"""
import sounddevice as sd
import soundfile as sf
import numpy as np

print("=== Direct Sounddevice Test ===\n")

# Get device info
print("📻 Default input device:")
info = sd.query_devices(sd.default.device[0])
print(f"  {info}\n")

# Try all microphone devices
print("Testing each device...")
devices = sd.query_devices()
for i, dev in enumerate(devices):
    if "input" in str(dev).lower() or "mic" in str(dev).lower():
        print(f"\n🎤 Testing Device {i}: {dev['name']}")
        try:
            # Quick 1-second test
            duration = 1
            fs = 16000
            print(f"   Recording 1 second...")
            recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='int16', device=i)
            sd.wait()
            
            # Check audio levels
            peak = float(abs(recording).max()) / 32768.0
            avg = float(abs(recording).mean()) / 32768.0
            print(f"   Peak: {peak:.4f}, Avg: {avg:.4f}")
            
            if peak > 0.001:
                print(f"   ✓ Device {i} captured audio!")
        except Exception as e:
            print(f"   ❌ Error: {e}")

print("\n✓ Test complete")
