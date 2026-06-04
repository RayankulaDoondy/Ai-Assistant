"""
Voice System - Speech-to-Text and Text-to-Speech
"""
import logging
import os
import shutil
import tempfile
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _analyze_audio_array(audio) -> dict:
    """Return basic level statistics for a float32 numpy audio array."""
    try:
        import numpy as np
        if not hasattr(audio, "size") or audio.size == 0:
            return {"peak": 0.0, "average": 0.0, "rms": 0.0, "samples": 0, "duration_seconds": 0.0}
        abs_audio = np.abs(audio)
        peak = float(abs_audio.max())
        average = float(abs_audio.mean())
        rms = float(np.sqrt(np.mean(np.square(audio))))
        samples = int(audio.size)
        return {"peak": peak, "average": average, "rms": rms, "samples": samples}
    except Exception:
        return {"peak": 0.0, "average": 0.0, "rms": 0.0, "samples": 0}


class SpeechToText:
    """Speech recognition using Whisper"""
    
    def __init__(self, model_size: str = "base", language: str = "en", input_device: Optional[str] = None):
        """
        Initialize Speech-to-Text
        
        Args:
            model_size: Whisper model size (tiny, base, small, medium, large)
            language: Language code (e.g., 'en' for English)
            input_device: Optional sounddevice input device id or name
        """
        self.input_device = self._normalize_input_device(input_device)
        self.last_peak_level = 0.0
        self.last_average_level = 0.0
        try:
            import whisper
            self.whisper = whisper
            self.model_size = model_size
            self.language = language
            self.model = None
            logger.info(f"Whisper initialized (model: {model_size})")
        except ImportError:
            logger.error("Whisper not installed. Install with: pip install openai-whisper")
            raise

        self.microphone_available = False
        self.sounddevice = None
        self.soundfile = None
        try:
            import sounddevice as sd
            import soundfile as sf
            self.sounddevice = sd
            self.soundfile = sf
            self.microphone_available = True
            logger.info("Microphone input support initialized")
        except Exception:
            logger.warning("Microphone input not available. Install sounddevice and soundfile for voice input.")

    def _normalize_input_device(self, input_device: Optional[str]):
        """Convert numeric device strings to ints and leave names as strings."""
        if input_device is None or str(input_device).strip() == "":
            return None

        input_device = str(input_device).strip()
        if input_device.isdigit():
            return int(input_device)
        return input_device

    def _load_model(self):
        """Lazy load the model"""
        if self.model is None:
            logger.info(f"Loading Whisper model: {self.model_size}")
            self.model = self.whisper.load_model(self.model_size)
    
    def transcribe_file(self, audio_path: str) -> str:
        """Transcribe an audio file and return text only (back-compat shim)."""
        return self.transcribe_file_with_diagnostics(audio_path)["transcript"]

    def transcribe_file_with_diagnostics(self, audio_path: str) -> dict:
        """
        Transcribe audio file and return both transcript and audio diagnostics.

        Provider order:
            1. Groq hosted Whisper (whisper-large-v3-turbo) when GROQ_API_KEY +
               GROQ_WHISPER_ENABLED. ~10% WER, ~200ms typical, free tier.
            2. Local openai-whisper (whatever SPEECH_TO_TEXT_MODEL is set to)
               as a fallback for offline / Groq failures.

        Args:
            audio_path: Path to audio file

        Returns:
            dict with keys: transcript, diagnostics (peak/average/rms/samples/duration_seconds),
            reason (one of: ok, silent, low_confidence, decode_failed, error)
        """
        diagnostics = {"peak": 0.0, "average": 0.0, "rms": 0.0, "samples": 0, "duration_seconds": 0.0}
        try:
            logger.info(f"Transcribing: {audio_path}")
            audio = self._load_audio_for_whisper(audio_path)

            import numpy as np
            if isinstance(audio, np.ndarray):
                diagnostics.update(_analyze_audio_array(audio))
                # Sample rate is 16k after _load_audio_for_whisper normalisation expectation
                diagnostics["duration_seconds"] = round(diagnostics["samples"] / 16000.0, 3)
                logger.info(
                    "Audio stats: samples=%s, duration=%.3fs, peak=%.4f, rms=%.4f",
                    diagnostics["samples"], diagnostics["duration_seconds"],
                    diagnostics["peak"], diagnostics["rms"],
                )

                if diagnostics["peak"] < 0.005:
                    logger.warning("Audio appears silent (peak < 0.005). Skipping Whisper to avoid hallucination.")
                    return {"transcript": "", "diagnostics": diagnostics, "reason": "silent"}

            # Try hosted Groq Whisper first. Returns None when the provider is
            # disabled or fails; we then fall through to the local model.
            groq_result = self._try_transcribe_groq(audio_path, diagnostics)
            if groq_result is not None:
                return groq_result

            # Local Whisper fallback. Lazy-load the model so we never pay the
            # download cost when Groq is doing the work.
            self._load_model()

            # NOTE: We DO NOT pass `initial_prompt` here. Whisper has a
            # well-known failure mode where, on silence or low-confidence
            # input, it echoes the initial_prompt back verbatim as the
            # transcript. The prompt that used to live here ("The following
            # is a short voice command spoken to a personal AI assistant
            # called Jarvis.") was being returned for every empty recording,
            # which made the UI look like the user was speaking that string.
            # Leaving the prompt empty trades a tiny bit of command-style
            # bias for a much-less-confusing failure mode.
            result = self.model.transcribe(
                audio,
                language=self.language,
                temperature=0.0,
                fp16=False,
                condition_on_previous_text=False,
                no_speech_threshold=0.55,
                logprob_threshold=-1.2,
                compression_ratio_threshold=2.4,
            )

            text = result.get("text", "").strip()
            detected_lang = result.get("language", "unknown")
            segments = result.get("segments", [])

            logger.info(f"Whisper detected language: {detected_lang}")
            logger.info(f"Raw transcription result: '{text}'")
            logger.info(f"Number of segments: {len(segments)}")

            if segments:
                for i, seg in enumerate(segments):
                    logger.info(
                        f"  Segment {i}: text='{seg.get('text', '')}', "
                        f"avg_logprob={seg.get('avg_logprob', 'N/A')}, "
                        f"no_speech_prob={seg.get('no_speech_prob', 'N/A')}"
                    )

            if self._looks_like_low_confidence_result(result):
                logger.warning("Ignoring low-confidence transcription result")
                return {"transcript": "", "diagnostics": diagnostics, "reason": "low_confidence"}

            text = self._collapse_repeated_phrases(text)
            logger.info(f"Final transcribed text: '{text}'")
            return {"transcript": text, "diagnostics": diagnostics, "reason": "ok" if text else "decode_failed"}
        except Exception as e:
            logger.error(f"Transcription error: {str(e)}", exc_info=True)
            return {"transcript": "", "diagnostics": diagnostics, "reason": "error"}

    def _try_transcribe_groq(self, audio_path: str, diagnostics: dict) -> Optional[dict]:
        """POST the WAV to Groq's hosted Whisper. Returns the standard dict
        on success, or None to signal "fall back to local Whisper".

        Returns None (not an error dict) when:
            - Groq is disabled by config / no API key
            - Network/HTTP error reaching Groq
            - Empty body or unexpected schema
        These all let the caller transparently use the local model.

        Returns a result dict (with reason="ok" or "low_confidence") when:
            - Groq replied with a usable transcript (after hallucination filter)
        """
        try:
            from config.settings import settings
        except Exception:
            return None

        if not getattr(settings, "GROQ_WHISPER_ENABLED", False):
            return None
        api_key = (getattr(settings, "GROQ_API_KEY", "") or "").strip()
        if not api_key:
            return None

        base_url = (getattr(settings, "GROQ_BASE_URL", "") or "").rstrip("/")
        if not base_url:
            return None
        model = getattr(settings, "GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
        timeout = float(getattr(settings, "GROQ_WHISPER_TIMEOUT", 30) or 30)

        try:
            import requests
        except Exception as e:
            logger.warning(f"Groq Whisper: requests not available ({e}); falling back to local Whisper")
            return None

        url = f"{base_url}/audio/transcriptions"
        try:
            with open(audio_path, "rb") as fh:
                files = {"file": ("voice.wav", fh, "audio/wav")}
                data = {
                    "model": model,
                    "language": self.language or "en",
                    "temperature": "0.0",
                    "response_format": "json",
                }
                headers = {"Authorization": f"Bearer {api_key}"}
                logger.info(f"Groq Whisper: POST {url} (model={model})")
                resp = requests.post(url, headers=headers, files=files, data=data, timeout=timeout)
        except Exception as e:
            logger.warning(f"Groq Whisper request failed: {e}; falling back to local Whisper")
            return None

        if resp.status_code != 200:
            # 429 (rate limit), 401 (bad key), 5xx — all reasons to fall back.
            body_preview = (resp.text or "")[:200]
            logger.warning(
                f"Groq Whisper non-200: {resp.status_code} body={body_preview!r}; "
                f"falling back to local Whisper"
            )
            return None

        try:
            payload = resp.json()
        except Exception as e:
            logger.warning(f"Groq Whisper bad JSON: {e}; falling back to local Whisper")
            return None

        text = (payload.get("text") or "").strip()
        logger.info(f"Groq Whisper raw text: '{text}'")

        # Reuse the same hallucination filter we use for local Whisper. Groq's
        # Whisper-large-v3-turbo also occasionally returns "Thanks for watching!"
        # on near-silence, so this is real protection, not paranoia.
        fake_result = {"text": text}
        if self._looks_like_low_confidence_result(fake_result):
            return {"transcript": "", "diagnostics": diagnostics, "reason": "low_confidence"}

        text = self._collapse_repeated_phrases(text)
        return {
            "transcript": text,
            "diagnostics": diagnostics,
            "reason": "ok" if text else "decode_failed",
        }

    # Known Whisper "I have no audio" fallback phrases. These tend to come
    # from prompt echoes, dataset trailers, or out-of-domain context bleed.
    # Match is substring + case-insensitive so we catch partial echoes too.
    _HALLUCINATION_MARKERS = (
        "personal ai assistant",
        "ai assistant called jarvis",
        "voice command spoken",
        "thanks for watching",
        "thank you for watching",
        "please subscribe",
        "subscribe to my channel",
        "transcribed by",
        "captions by",
        "subtitle",
    )

    def _looks_like_low_confidence_result(self, result: dict) -> bool:
        """Detect Whisper outputs that are likely silence or noise hallucinations."""
        text = result.get("text", "").strip()

        # Empty / punctuation-only → silence
        if not text or text in (".", "..", "...", "…"):
            logger.info("Filtering: Empty or punctuation-only result")
            return True

        # Known fallback phrases (e.g. Whisper echoes the initial_prompt or
        # outputs YouTube boilerplate when fed noise). Treat as silence.
        lowered = text.lower()
        for marker in self._HALLUCINATION_MARKERS:
            if marker in lowered:
                logger.warning(f"Filtering Whisper hallucination (matched: '{marker}'): '{text}'")
                return True

        logger.info(f"Accepting result: '{text}'")
        return False

    def _collapse_repeated_phrases(self, text: str) -> str:
        """Collapse repeated Whisper phrases like 'hello. hello. hello.'."""
        parts = [part.strip() for part in text.split(".") if part.strip()]
        if len(parts) < 3:
            return text

        normalized = [part.lower() for part in parts]
        if len(set(normalized)) == 1:
            return parts[0] + "."

        return text

    def _load_audio_for_whisper(self, audio_path: str):
        """
        Load recorded WAV audio directly for Whisper.

        Whisper's file-path transcription uses ffmpeg. Microphone recordings are
        already WAV files, so loading them with soundfile avoids requiring ffmpeg
        for live voice conversations.
        """
        if self.soundfile is None:
            return audio_path

        audio, sample_rate = self.soundfile.read(audio_path, dtype="float32")
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)

        if sample_rate != 16000:
            logger.warning(
                "Recorded audio sample rate is %s Hz; Whisper expects 16000 Hz. "
                "Set AUDIO_SAMPLE_RATE=16000 for best results.",
                sample_rate,
            )

        return audio
    
    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        """Transcribe audio bytes and return text only (back-compat shim)."""
        return self.transcribe_bytes_with_diagnostics(audio_bytes)["transcript"]

    def transcribe_bytes_with_diagnostics(self, audio_bytes: bytes) -> dict:
        """
        Transcribe audio bytes and return transcript plus audio diagnostics.

        Args:
            audio_bytes: Audio data as bytes (WAV recommended)

        Returns:
            dict with keys: transcript, diagnostics, reason
        """
        empty = {"transcript": "", "diagnostics": {"peak": 0.0, "average": 0.0, "rms": 0.0, "samples": 0, "duration_seconds": 0.0}, "reason": "error"}
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            return self.transcribe_file_with_diagnostics(tmp_path)
        except Exception as e:
            logger.error(f"Error transcribing bytes: {str(e)}", exc_info=True)
            return empty
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def record_microphone(self, duration: int = 7, sample_rate: int = 16000) -> Optional[str]:
        """
        Record microphone audio for a fixed duration.

        Args:
            duration: Number of seconds to record
            sample_rate: Sampling rate for recording

        Returns:
            Path to the temporary WAV file or None if recording failed
        """
        if not self.microphone_available:
            logger.error("Microphone input not available")
            return None

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name

            logger.info(
                "Recording microphone for %s seconds to %s (device: %s)",
                duration,
                tmp_path,
                self.input_device if self.input_device is not None else "default",
            )
            recording = self.sounddevice.rec(
                int(duration * sample_rate),
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                device=self.input_device,
            )
            self.sounddevice.wait()
            self.last_peak_level, self.last_average_level = self.get_audio_levels(recording)
            logger.info(
                "Microphone level: peak=%.4f, avg=%.4f",
                self.last_peak_level,
                self.last_average_level,
            )
            self.soundfile.write(tmp_path, recording, sample_rate)
            return tmp_path
        except Exception as e:
            logger.error(f"Microphone recording error: {str(e)}")
            return None

    def get_audio_levels(self, audio) -> tuple[float, float]:
        """Return peak and average levels for recorded int16 audio."""
        try:
            peak = float(abs(audio).max()) / 32768.0
            average = float(abs(audio).mean()) / 32768.0
            return peak, average
        except Exception:
            return 0.0, 0.0

    def get_audio_level_summary(self, audio) -> str:
        """Return a compact level summary for recorded int16 audio."""
        try:
            peak, average = self.get_audio_levels(audio)
            return f"peak={peak:.4f}, avg={average:.4f}"
        except Exception as e:
            return f"unavailable ({str(e)})"

    def list_input_devices(self) -> list[dict]:
        """List sounddevice input devices."""
        if not self.microphone_available:
            return []

        devices = self.sounddevice.query_devices()
        input_devices = []
        for index, device in enumerate(devices):
            if device.get("max_input_channels", 0) > 0:
                input_devices.append(
                    {
                        "id": index,
                        "name": device.get("name", "Unknown"),
                        "channels": device.get("max_input_channels", 0),
                        "sample_rate": int(device.get("default_samplerate", 0)),
                    }
                )
        return input_devices

    def get_default_input_device(self) -> Optional[dict]:
        """Return the currently configured default input device, if available."""
        if not self.microphone_available:
            return None

        try:
            default_input_id = self.sounddevice.default.device[0]
            if default_input_id is None or default_input_id < 0:
                return None
            device = self.sounddevice.query_devices(default_input_id)
            return {
                "id": default_input_id,
                "name": device.get("name", "Unknown"),
                "channels": device.get("max_input_channels", 0),
                "sample_rate": int(device.get("default_samplerate", 0)),
            }
        except Exception as e:
            logger.error(f"Error reading default input device: {str(e)}")
            return None

    def test_microphone(self, duration: int = 3, sample_rate: int = 16000) -> Optional[dict]:
        """Record briefly and return level information without transcribing."""
        audio_path = self.record_microphone(duration, sample_rate)
        if not audio_path:
            return None

        try:
            audio, recorded_sample_rate = self.soundfile.read(audio_path, dtype="float32")
            peak = float(abs(audio).max()) if getattr(audio, "size", 0) else 0.0
            average = float(abs(audio).mean()) if getattr(audio, "size", 0) else 0.0
            return {
                "path": audio_path,
                "sample_rate": recorded_sample_rate,
                "peak": peak,
                "average": average,
                "has_signal": peak > 0.01,
            }
        finally:
            try:
                os.unlink(audio_path)
            except Exception:
                pass

    def listen(self, duration: int = 7, sample_rate: int = 16000, min_peak_level: float = 0.0) -> str:
        """
        Record from the microphone and transcribe the audio.

        Args:
            duration: Recording duration in seconds
            sample_rate: Sampling rate for recording

        Returns:
            Transcribed text
        """
        audio_path = self.record_microphone(duration, sample_rate)
        if not audio_path:
            return ""

        try:
            if self.last_peak_level < min_peak_level:
                logger.warning(
                    "Recorded audio peak %.4f is below minimum %.4f",
                    self.last_peak_level,
                    min_peak_level,
                )
                return ""
            transcript = self.transcribe_file(audio_path)
            return transcript
        finally:
            try:
                os.unlink(audio_path)
            except Exception:
                pass


class TextToSpeech:
    """Text-to-Speech using local engine or Piper CLI"""
    
    def __init__(self, voice: str = "en-US"):
        """
        Initialize Text-to-Speech
        
        Args:
            voice: Voice ID or name for the local TTS engine
        """
        self.voice = voice
        self.engine = None
        self.use_piper = False
        self.available = False
        try:
            import pyttsx3
            self.pyttsx3 = pyttsx3
            self.engine = pyttsx3.init()
            voices = self.engine.getProperty("voices")
            for v in voices:
                if voice.lower() in v.name.lower() or voice.lower() in getattr(v, "id", "").lower():
                    self.engine.setProperty("voice", v.id)
                    break
            self.available = True
            logger.info(f"pyttsx3 TTS initialized (voice: {voice})")
        except ImportError:
            logger.warning("pyttsx3 not installed. Install with: pip install pyttsx3")
            self._check_piper_installation()
        except Exception as e:
            logger.warning(f"TTS initialization warning: {str(e)}")
            self._check_piper_installation()
        finally:
            if self.use_piper:
                self.available = True
    
    def _check_piper_installation(self):
        """Check if Piper CLI is installed"""
        try:
            import subprocess
            result = subprocess.run(["piper", "--help"], capture_output=True)
            if result.returncode == 0:
                self.use_piper = True
                logger.info("Piper CLI found for TTS fallback")
            else:
                logger.warning("Piper CLI not found. Install with: pip install piper-tts or use pyttsx3")
        except Exception:
            logger.warning("Piper CLI not found in PATH")
    
    def synthesize_to_file(self, text: str, output_path: str) -> bool:
        """
        Synthesize text to audio file
        
        Args:
            text: Text to synthesize
            output_path: Path to save audio file
            
        Returns:
            Success status
        """
        try:
            logger.info(f"Synthesizing: {text[:50]}... to {output_path}")
            if self.engine is not None:
                self.engine.save_to_file(text, output_path)
                self.engine.runAndWait()
                return os.path.exists(output_path)
            elif self.use_piper:
                import subprocess
                cmd = [
                    "piper",
                    "--model", self.voice,
                    "--output_file", output_path
                ]
                result = subprocess.run(
                    cmd,
                    input=text.encode(),
                    capture_output=True
                )
                if result.returncode == 0:
                    logger.info("Piper synthesis completed successfully")
                    return True
                else:
                    logger.error(f"Piper error: {result.stderr.decode()}")
                    return False
            else:
                logger.error("No TTS backend available")
                return False
        except Exception as e:
            logger.error(f"Synthesis error: {str(e)}")
            return False
    
    def speak(self, text: str) -> bool:
        """
        Speak text aloud using the configured TTS backend.

        Args:
            text: Text to speak

        Returns:
            True if playback was successful, False otherwise.
        """
        try:
            logger.info(f"Speaking text: {text[:50]}...")
            if self.engine is not None:
                self.engine.say(text)
                self.engine.runAndWait()
                return True
            elif self.use_piper:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name
                if self.synthesize_to_file(text, tmp_path):
                    played = self._play_audio_file(tmp_path)
                    os.unlink(tmp_path)
                    return played
                os.unlink(tmp_path)
                return False
            else:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name
                if self.synthesize_to_file(text, tmp_path):
                    played = self._play_audio_file(tmp_path)
                    os.unlink(tmp_path)
                    return played
                os.unlink(tmp_path)
                return False
        except Exception as e:
            logger.error(f"Speak error: {str(e)}")
            return False
    
    def _play_audio_file(self, output_path: str) -> bool:
        """
        Play a WAV audio file using a local playback backend.

        Args:
            output_path: Path to WAV file

        Returns:
            True if playback succeeded, False otherwise.
        """
        try:
            if os.name == "nt":
                import winsound
                winsound.PlaySound(output_path, winsound.SND_FILENAME)
                return True
            else:
                import subprocess
                if shutil.which("ffplay"):
                    subprocess.run(["ffplay", "-nodisp", "-autoexit", output_path], capture_output=True)
                    return True
                elif shutil.which("aplay"):
                    subprocess.run(["aplay", output_path], capture_output=True)
                    return True
                else:
                    logger.error("No supported audio playback backend available")
                    return False
        except Exception as e:
            logger.error(f"Audio playback error: {str(e)}")
            return False
    
    def synthesize_to_bytes(self, text: str) -> Optional[bytes]:
        """
        Synthesize text to audio bytes
        
        Args:
            text: Text to synthesize
            
        Returns:
            Audio bytes or None if failed
        """
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            
            if self.synthesize_to_file(text, tmp_path):
                with open(tmp_path, "rb") as f:
                    audio_bytes = f.read()
                os.unlink(tmp_path)
                return audio_bytes
            else:
                os.unlink(tmp_path)
                return None
        except Exception as e:
            logger.error(f"Error synthesizing bytes: {str(e)}")
            return None


class WakeWordDetector:
    """Wake word detection using Porcupine"""
    
    def __init__(self, wake_word: str = "jarvis"):
        """
        Initialize wake word detector
        
        Args:
            wake_word: Wake word to detect
        """
        self.wake_word = wake_word.lower()
        logger.info(f"Wake word detector initialized (word: {wake_word})")
    
    def detect_in_text(self, text: str) -> bool:
        """
        Simple text-based wake word detection
        
        Args:
            text: Text to check
            
        Returns:
            True if wake word detected
        """
        detected = self.wake_word.lower() in text.lower()
        if detected:
            logger.info(f"Wake word '{self.wake_word}' detected")
        return detected


# Global instances
_stt = None
_tts = None
_wakeword = None


def get_stt(model_size: str = "base", language: str = "en", input_device: Optional[str] = None) -> SpeechToText:
    """Get or create STT instance"""
    global _stt
    if _stt is None:
        _stt = SpeechToText(model_size, language, input_device)
    return _stt


def get_tts(voice: str = "en-US-AmberNeural") -> TextToSpeech:
    """Get or create TTS instance"""
    global _tts
    if _tts is None:
        _tts = TextToSpeech(voice)
    return _tts


def get_wakeword_detector(wake_word: str = "jarvis") -> WakeWordDetector:
    """Get or create wake word detector"""
    global _wakeword
    if _wakeword is None:
        _wakeword = WakeWordDetector(wake_word)
    return _wakeword
