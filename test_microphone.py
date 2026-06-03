#!/usr/bin/env python3
"""
Test microphone input and list available devices
"""
import sys
sys.path.insert(0, '.')

from config import settings
from voice import get_stt

def test_microphone():
    """Test microphone availability and recording"""
    print("\n=== Jarvis Microphone Test ===\n")
    
    stt = get_stt(
        settings.SPEECH_TO_TEXT_MODEL,
        settings.STT_LANGUAGE,
        settings.AUDIO_INPUT_DEVICE,
    )
    
    # List available devices
    print("📻 Available Microphone Devices:")
    devices = stt.list_input_devices()
    if not devices:
        print("  ❌ No devices found!")
        return
    
    for device in devices:
        default_marker = " ← DEFAULT" if device.get("is_default") else ""
        print(f"  ID {device['id']}: {device['name']}{default_marker}")
    
    # Test recording
    print("\n🎤 Testing Microphone Recording (3 seconds)...")
    print("Speak now:")
    
    audio_path = stt.record_microphone(duration=3, sample_rate=16000)
    if not audio_path:
        print("  ❌ Recording failed!")
        return
    
    peak = stt.last_peak_level
    avg = stt.last_average_level
    print(f"\n📊 Audio Levels:")
    print(f"  Peak: {peak:.4f} (threshold: {settings.VOICE_MIN_PEAK_LEVEL:.4f})")
    print(f"  Avg:  {avg:.4f}")
    
    if peak < settings.VOICE_MIN_PEAK_LEVEL:
        print(f"\n⚠️  Peak level too low (recorded {peak:.4f}, need {settings.VOICE_MIN_PEAK_LEVEL:.4f})")
        print("   Suggestions:")
        print("   1. Speak louder into the microphone")
        print("   2. Get closer to the microphone")
        print("   3. Check your microphone volume in system settings")
        print("   4. Lower VOICE_MIN_PEAK_LEVEL in .env (current: 0.02, try 0.001)")
    else:
        print(f"\n✓ Audio level OK")
        
        # Try transcription
        print("\n🔄 Attempting transcription...")
        transcript = stt.transcribe_file(audio_path)
        if transcript:
            print(f"✓ Transcribed: '{transcript}'")
        else:
            print("❌ Transcription failed or returned empty")

if __name__ == "__main__":
    test_microphone()
