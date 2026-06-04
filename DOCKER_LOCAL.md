# Hunt — Stable Local via Docker

## Why this exists

Hunt's native Windows + Python 3.13 + uvicorn combo deadlocks the asyncio loop after roughly one request. We've tried every uvicorn-level mitigation (Selector policy, h11, threadpool Whisper, fresh restarts). The underlying bug is in the OS/runtime interaction, not Hunt.

Running Hunt **inside a Linux container** on your same machine bypasses the entire Windows asyncio stack. Same code, same `/v2` URL, same chat — but stable.

---

## One-time setup (≈10 min)

### Step 1 — Install Docker Desktop for Windows

Download: https://www.docker.com/products/docker-desktop/

- Click **"Download for Windows — AMD64"**
- Run the installer. Defaults are fine. Reboot if it asks.
- After reboot, open **Docker Desktop**. Wait for the whale icon in your system tray to stop animating (means engine is running).

### Step 2 — Verify Docker works

In PowerShell:

```powershell
docker --version
docker run --rm hello-world
```

If `hello-world` prints `Hello from Docker!`, you're set.

### Step 3 — Build and run Hunt

In the **same PowerShell window** (or open a new one):

```powershell
cd "C:\Users\rayan\Downloads\Ai Doonz\Jarvis"
docker compose -f docker-compose.dev.yml up --build
```

**First run takes 5–10 minutes** — Docker downloads a Python 3.11 base image, installs ffmpeg, then runs `pip install -r requirements_render.txt` (Whisper + sentence-transformers + chromadb + pymongo, etc.).

When you see:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001
```

Open your browser to: **http://127.0.0.1:8001/v2**

That's it.

---

## Daily use

### Start

```powershell
cd "C:\Users\rayan\Downloads\Ai Doonz\Jarvis"
docker compose -f docker-compose.dev.yml up
```

(No `--build` — uses the cached image. ~10 seconds to start.)

### Stop

Press **Ctrl+C** in the PowerShell window. Or run in another terminal:

```powershell
docker compose -f docker-compose.dev.yml down
```

### Edit code while running

The container **volume-mounts your local source folders** (`app/`, `brain/`, `memory/`, etc.). When you edit any `.py` file, uvicorn's `--reload` flag inside the container detects the change and restarts the worker in <2 seconds. No need to stop/start Docker.

### View logs

The PowerShell window where you ran `docker compose up` shows live logs. Or in another terminal:

```powershell
docker compose -f docker-compose.dev.yml logs -f
```

### Run a quick sanity check

In another PowerShell (while container is running):

```powershell
Invoke-WebRequest http://127.0.0.1:8001/health | Select-Object -ExpandProperty Content
```

Should print `{"status":"running","llm_connected":true,...}`.

---

## What works inside the container

| Capability | Works in Docker? | Notes |
|---|---|---|
| Chat (`/chat/stream`) | ✓ | OpenRouter / Groq / Gemini all reachable from container |
| Voice transcribe (`/voice/transcribe`) | ✓ | Whisper-small loaded on container start (cached on `hunt-data` volume) |
| Memory (Chroma) | ✓ | Persisted to `hunt-data` named volume |
| Profile / sessions / facts | ✓ | Same volume |
| Mongo Atlas sync | ✓ | Outbound network works inside the container |
| **Workspace awareness** | ⚠️ Mostly no | The container can't see your Windows desktop. Active window, clipboard, processes all return empty. The endpoint still works — just returns `{"available": false}` and macros gracefully say "workspace not available". For workspace features specifically, use native Windows (or accept that workspace queries are a no-op when in Docker). |
| Desktop actions (`open_app`, etc.) | ⚠️ No | Same reason — no host GUI from inside container. |

---

## When Mongo / OpenRouter / etc. don't connect

Container can't reach `localhost` on your Windows host (Docker network isolation). Two cases:

| Service | Fix |
|---|---|
| Mongo Atlas (cloud) | Just works — outbound to mongodb+srv:// is fine |
| OpenRouter / Groq / Gemini | Just work — outbound to api.openrouter.ai etc. is fine |
| Ollama on your Windows host | Set `OLLAMA_BASE_URL=http://host.docker.internal:11434` in `.env`. `host.docker.internal` is Docker Desktop's magic hostname for the host machine. |

The compose file leaves `OLLAMA_BASE_URL=""` by default, which makes the engine silently skip the Ollama probe (no log spam). Since you're using OpenRouter + Groq + Gemini, you don't need Ollama anyway.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `port is already allocated` | Native Hunt is still running on 8001 | `Get-Process python \| Stop-Process -Force` in PowerShell, then `docker compose up` again |
| Build fails on `pip install` | Out of disk space or RAM | Docker Desktop → Settings → Resources → bump memory to 4GB minimum |
| `docker: error during connect` | Docker Desktop isn't running | Open Docker Desktop, wait for the whale icon, retry |
| Container starts but `/v2` is blank | Wrong cache | `Ctrl+Shift+R` in browser, or close + reopen the tab |
| First request takes 30+ seconds | Whisper-small downloading (~244MB) on first call | Wait once; subsequent requests are normal |
| You want to **wipe persistent data** | Fresh start | `docker compose -f docker-compose.dev.yml down -v` (the `-v` removes the `hunt-data` volume) |

---

## Removing Hunt-Docker entirely

```powershell
docker compose -f docker-compose.dev.yml down -v
docker image rm jarvis-hunt   # or whatever the image is named
```

Then uninstall Docker Desktop from Windows "Add or remove programs" if you want it gone too.

---

## Files this added

| File | Purpose |
|---|---|
| `Dockerfile.dev` | Linux container image (Python 3.11 + ffmpeg + libsndfile + Hunt's deps) |
| `docker-compose.dev.yml` | Compose file with volume mounts + port binding + env file |
| `DOCKER_LOCAL.md` | This guide |

Nothing else changes. `run_local.py`, `run.bat`, and the native Windows path still exist — use whichever's working at the moment.
