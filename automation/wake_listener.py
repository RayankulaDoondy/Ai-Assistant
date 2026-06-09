"""Wake-word detection for Hunt.

Runs in a background thread inside the desktop_helper process. Captures
audio from the system mic at 16kHz, feeds 80ms frames to OpenWakeWord,
and POSTs to Hunt's /voice/wakeword endpoint when the wake word fires.

Pre-trained models that ship with OpenWakeWord
----------------------------------------------
    hey_jarvis_v0.1   ← default, used here
    alexa_v0.1
    hey_mycroft_v0.1
    timer_v0.1
    weather_v0.1

The model files auto-download from openWakeWord's GitHub releases the
first time the listener starts. They're cached under the openwakeword
package's resources/models directory inside site-packages. No signup,
no API key.

Audio capture
-------------
Using `sounddevice` (PortAudio wrapper) rather than PyAudio because
sounddevice ships PortAudio binaries in its Windows wheel — pip install
Just Works. PyAudio on Windows needs a C toolchain or pipwin.

Dependencies (install via requirements_helper.txt):
    pip install openwakeword sounddevice numpy requests

When deps are missing, the listener fails gracefully — it logs the
problem and returns without starting the thread, so the rest of the
desktop helper keeps working.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class WakeListener:
    """Owns the mic capture + wake-word scoring loop.

    Public API:
        start()         — begin listening (idempotent; safe to call repeatedly)
        stop()          — stop and release mic (idempotent)
        is_running()    — bool
        status()        — diagnostic dict for the helper's /wake/status endpoint
    """

    # --- Hyperparameters ------------------------------------------------- #
    SAMPLE_RATE = 16000          # openWakeWord trained on 16k
    FRAME_DURATION_MS = 80       # 80ms frames = 1280 samples at 16k
    FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # 1280

    # Detection threshold. openWakeWord scores are in [0, 1]. OpenWakeWord's
    # default is 0.5, but in live testing on consumer mics that misses too
    # many real utterances ("Hey Jarvis" with normal speech often peaks
    # around 0.4 on average headsets). 0.35 is a better personal-use
    # starting point: fewer missed detections, occasional false-positive
    # risk that's fine for a 1-user local install. Adjust by watching the
    # "Wake tuning — peak score last 5s" log lines emitted by _run_loop.
    THRESHOLD = 0.35

    # Cooldown after a detection — prevent the wake word from firing twice
    # for one spoken phrase as the scoring window slides past it.
    COOLDOWN_SECONDS = 3.0

    def __init__(
        self,
        on_wake: Callable[[str, float], None],
        model_name: str = "hey_jarvis_v0.1",
    ):
        """`on_wake(wake_word, confidence)` runs on the listener thread when
        a detection fires. Must be thread-safe (the helper makes an HTTP
        POST from this callback)."""
        self.on_wake = on_wake
        self.model_name = model_name

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_detection_ts: Optional[float] = None
        self._ready = False
        self._error: Optional[str] = None
        self._frames_scored = 0
        self._lock = threading.Lock()
        # Score visibility — the user can't tune a threshold they can't see.
        # `_peak_score_window` holds the highest score in the current 5-second
        # window; `_peak_score_last_log_ts` paces the periodic "tuning" log
        # line. Both reset every 5s so the value tracks recent speech, not
        # a single high score from an hour ago.
        self._peak_score_window: float = 0.0
        self._peak_score_last_log_ts: float = 0.0

    # ----------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------- #

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def last_detection_str(self) -> Optional[str]:
        if self._last_detection_ts is None:
            return None
        return time.strftime("%H:%M:%S", time.localtime(self._last_detection_ts))

    def status(self) -> dict:
        return {
            "running":            self.is_running(),
            "ready":              self._ready,
            "model":              self.model_name,
            "threshold":          self.THRESHOLD,
            "peak_score_window":  round(self._peak_score_window, 3),
            "error":              self._error,
            "last_detection":     self.last_detection_str(),
            "frames_scored":      self._frames_scored,
        }

    def start(self) -> bool:
        """Spin up the listener thread. Returns True if started (or already
        running), False if dependencies are missing or model load failed."""
        with self._lock:
            if self.is_running():
                return True

            # Lazy-import — missing deps must NOT crash the helper.
            try:
                import openwakeword  # noqa: F401
                import sounddevice   # noqa: F401
                import numpy         # noqa: F401
            except ImportError as e:
                self._error = (
                    f"Wake-word deps missing ({e.name or e}). Install: "
                    "pip install openwakeword sounddevice numpy"
                )
                logger.warning(self._error)
                return False

            self._stop_event.clear()
            self._error = None
            self._frames_scored = 0
            self._thread = threading.Thread(
                target=self._run_loop,
                name="hunt-wake-listener",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        """Signal the loop to exit and wait briefly for the thread to die."""
        with self._lock:
            self._stop_event.set()
            t = self._thread
            self._thread = None
        if t is not None:
            t.join(timeout=2.0)

    # ----------------------------------------------------------------- #
    # Loop body
    # ----------------------------------------------------------------- #

    def _run_loop(self) -> None:
        """Capture + score loop. Catches every exception so a transient
        audio glitch doesn't kill the thread silently."""
        try:
            from openwakeword.model import Model
            import sounddevice as sd
        except ImportError as e:
            self._error = f"Wake-word deps disappeared at runtime: {e}"
            logger.error(self._error)
            return

        # Ensure the model file is on disk. openWakeWord 0.6 no longer
        # auto-downloads from inside Model() — we have to call download_models()
        # explicitly the first time. Idempotent on subsequent runs (skips
        # files that already exist).
        try:
            import os as _os
            import openwakeword
            import openwakeword.utils
            ow_dir = _os.path.dirname(openwakeword.__file__)
            model_path = _os.path.join(ow_dir, "resources", "models", f"{self.model_name}.onnx")
            if not _os.path.exists(model_path):
                logger.info(
                    f"Wake-word model {self.model_name!r} not found locally — "
                    f"downloading default models from GitHub (one-time, ~30MB)…"
                )
                openwakeword.utils.download_models()
                if not _os.path.exists(model_path):
                    self._error = (
                        f"Downloaded the bundle but {self.model_name!r}.onnx "
                        f"is still missing at {model_path}. Check your network "
                        f"connection or download manually from "
                        f"https://github.com/dscripka/openWakeWord/releases."
                    )
                    logger.error(self._error)
                    return
                logger.info(f"Wake-word model {self.model_name!r} downloaded.")
        except Exception as e:
            self._error = f"Wake-word model download failed: {e}"
            logger.error(self._error)
            return

        # Build the model. The first-load is ~1-2s for ONNX runtime to
        # initialize; subsequent frames score in single-digit ms.
        try:
            model = Model(
                wakeword_models=[self.model_name],
                inference_framework="onnx",
            )
        except Exception as e:
            self._error = f"Could not load wake-word model {self.model_name!r}: {e}"
            logger.error(self._error)
            return

        self._ready = True
        logger.info(f"Wake listener ready — listening for '{self.model_name}'")

        try:
            with sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=self.FRAME_SAMPLES,
            ) as stream:
                logger.info("Wake listener: mic open, scoring frames…")

                while not self._stop_event.is_set():
                    try:
                        frame, overflowed = stream.read(self.FRAME_SAMPLES)
                    except Exception as e:
                        logger.warning(f"Wake listener read failed: {e}")
                        time.sleep(0.2)
                        continue

                    if overflowed:
                        # PortAudio buffer overran (likely CPU-bound process
                        # elsewhere on the box). Score current frame anyway.
                        logger.debug("Wake listener: audio buffer overflowed")

                    # frame is shape (FRAME_SAMPLES, 1) int16; openWakeWord
                    # wants a flat array of the same dtype.
                    arr = frame[:, 0] if frame.ndim == 2 else frame

                    try:
                        scores = model.predict(arr)
                    except Exception as e:
                        logger.warning(f"Wake listener predict failed: {e}")
                        continue
                    self._frames_scored += 1

                    score = float(scores.get(self.model_name, 0.0))

                    # Track peak score for tuning visibility. Update every
                    # frame; flush + log every 5 seconds. Done BEFORE the
                    # threshold gate so we capture the user's voice scores
                    # even when they don't trigger a detection (which is
                    # exactly the case we're trying to diagnose).
                    if score > self._peak_score_window:
                        self._peak_score_window = score
                    now = time.time()
                    if now - self._peak_score_last_log_ts >= 5.0:
                        if self._peak_score_last_log_ts > 0:
                            logger.info(
                                f"Wake tuning — peak score last 5s: "
                                f"{self._peak_score_window:.2f} "
                                f"(threshold {self.THRESHOLD})"
                            )
                        self._peak_score_last_log_ts = now
                        self._peak_score_window = 0.0

                    if score < self.THRESHOLD:
                        continue

                    # Cooldown gate — same phrase shouldn't fire twice.
                    if (
                        self._last_detection_ts is not None
                        and now - self._last_detection_ts < self.COOLDOWN_SECONDS
                    ):
                        continue
                    self._last_detection_ts = now

                    logger.info(
                        f"🔔 Wake word detected: {self.model_name} "
                        f"(score={score:.2f})"
                    )
                    # Dispatch to caller. Don't let an exception in their
                    # callback take down our loop.
                    try:
                        self.on_wake(self.model_name, score)
                    except Exception as e:
                        logger.error(f"on_wake callback raised: {e}")
        except Exception as e:
            self._error = f"Wake listener crashed: {e}"
            logger.exception(self._error)
        finally:
            self._ready = False
            logger.info("Wake listener: stopped")
