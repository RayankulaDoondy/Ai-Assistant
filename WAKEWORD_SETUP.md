# Wake-word setup — "Hey Jarvis"

Hunt can listen for a wake phrase and start the mic flow automatically — no clicking.

The wake word for v1 is **"Hey Jarvis"** because that's the pre-trained model that ships with OpenWakeWord. You can replace it later with a custom "Hey Hunt" model trained in Google Colab (~30 min, free) — see *Custom wake word* at the bottom.

---

## One-time install (~2 min)

The wake-word stack lives in the desktop helper on Windows. Hunt's Docker container doesn't have mic access, so the listener has to run natively.

Open a PowerShell window in the project folder:

```powershell
cd "C:\Users\rayan\Downloads\Ai Doonz\Jarvis"
pip install -r requirements_helper.txt
```

This pulls four packages:
- `openwakeword` — wake-word detection (MIT, no signup)
- `sounddevice` — PortAudio mic capture (ships PortAudio binaries with the Windows wheel)
- `numpy` — tensor I/O
- `requests` — POST detections back to Hunt

**First time `openwakeword` is imported**, it downloads the pre-trained `hey_jarvis_v0.1.onnx` model (~10 MB) from GitHub releases and caches it under `%USERPROFILE%\.cache\openwakeword\`. After that it's offline.

No account, no API key, no credit card.

---

## Run the helper

```powershell
.\run_helper.bat
```

Or directly:

```powershell
python automation\desktop_helper.py
```

You'll see something like:

```
13:42:11  Hunt desktop helper listening on http://127.0.0.1:9100
13:42:11  Wake listener ready — listening for 'hey_jarvis_v0.1'
13:42:12  Wake listener: mic open, scoring frames…
```

When you say **"Hey Jarvis"** the helper logs:

```
13:42:38  🔔 Wake word detected: hey_jarvis_v0.1 (score=0.87)
13:42:38  Wake event POSTed to Hunt (hey_jarvis_v0.1 0.87)
```

…and in your browser the orb pulses orange, the listening flow starts.

---

## Toggling it on/off

In Hunt's UI: **Settings (gear icon)** → top of the drawer → **"Wake on Hey Jarvis"** switch.

- ON (default): wake events trigger the mic flow.
- OFF: the helper still listens, but the UI ignores the events. (Cheaper than restarting the helper.)

If you want to fully stop the listener (no mic capture at all), close the helper window or set `HUNT_WAKE_AUTOSTART=0` before launching:

```powershell
$env:HUNT_WAKE_AUTOSTART = "0"
.\run_helper.bat
```

---

## Diagnostics

While the helper is running, check status from any browser or PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:9100/wake/status
```

Returns:

```json
{
  "available": true,
  "running": true,
  "ready": true,
  "model": "hey_jarvis_v0.1",
  "threshold": 0.5,
  "error": null,
  "last_detection": "13:42:38",
  "frames_scored": 8214
}
```

If `available: false`, the dependencies aren't installed — run `pip install -r requirements_helper.txt`.

If `running: true, ready: false` for more than a few seconds, the model is still loading or the mic is busy. Look at the helper console for the actual error.

---

## Tuning

The default detection threshold is `0.5`. If you're getting:

- **False positives** (Hunt wakes up when you weren't talking to it): raise the threshold. Edit `automation/wake_listener.py`, change `THRESHOLD = 0.5` to `0.6` or `0.7`, restart helper.
- **Missed detections** (you say "Hey Jarvis" and nothing happens): lower the threshold to `0.4` or `0.35`.

OpenWakeWord scores are in `[0, 1]`. The score for clear utterances is usually `0.7–0.95`. Background noise hits `0.0–0.2`. The middle band (`0.3–0.6`) is the ambiguity zone — that's where the threshold lives.

---

## Custom "Hey Hunt" wake word (optional, v2)

If you want to replace "Hey Jarvis" with an actual "Hey Hunt":

1. Open the [OpenWakeWord Synthetic Data Generation notebook](https://github.com/dscripka/openWakeWord/tree/main/notebooks).
2. Type `hey hunt` as the wake-phrase.
3. Run all cells — Colab's free GPU is enough. ~30 min total.
4. Download the resulting `hey_hunt.onnx`.
5. Put it under `automation/wake_models/hey_hunt.onnx`.
6. Set the env var before launching the helper:

   ```powershell
   $env:HUNT_WAKE_MODEL = "hey_hunt"
   .\run_helper.bat
   ```

The helper auto-uses whichever model name `HUNT_WAKE_MODEL` points at. The model file's path is resolved relative to `automation/wake_models/` first, then OpenWakeWord's built-in models.

(The "credits" prompt some people see in Colab is for **Colab Pro** — the free runtime is fine for training a wake-word. Skip the upsell.)

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Wake-word deps missing` on helper start | `pip install -r requirements_helper.txt` not run | Run it |
| Helper crashes with `PortAudioError: No Default Input Device Available` | No mic, or mic is disabled in Windows Sound settings | Plug in / enable a mic |
| Wake never triggers, console shows frames scoring at `0.0` consistently | Mic is muted or wrong device selected | Check Windows Sound mixer; sounddevice picks the OS default input |
| Wake triggers but orb does nothing | `HUNT_URL` env var points to the wrong port, or Hunt's container is down | `Invoke-RestMethod http://localhost:8001/health` should return ok; if not, restart Hunt |
| UI shows no orb pulse but helper console logs detection | Settings toggle is off, or SSE connection dropped | Toggle on, or refresh the page |
| Browser console shows `[wake] SSE dropped` repeatedly | Reverse proxy is buffering SSE | The endpoint sets `X-Accel-Buffering: no`; if you've added nginx in front, configure it to pass through SSE |
