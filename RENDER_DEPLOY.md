# Deploying Hunt to Render

Step-by-step. End state: `https://hunt-yourname.onrender.com/v2` reachable from anywhere, mic + chat both working.

---

## 0. Before you start

You need accounts on:
- **GitHub** (to host the code)
- **Render** ([render.com](https://render.com/) — sign up with GitHub)
- **OpenRouter** ([openrouter.ai/keys](https://openrouter.ai/keys) — for the cloud LLMs)
- **MongoDB Atlas** ✓ you already have this

Have these values ready (you'll paste them into Render later):
- `OPENROUTER_API_KEY` — from openrouter.ai
- `MONGODB_URI` — from Atlas → Connect → "Connect your application"

---

## 1. Push the code to GitHub

```powershell
cd "C:\Users\rayan\Downloads\Ai Doonz\Jarvis"
git init
git add .
git commit -m "Hunt v1 — first deploy"
```

Then create a **private** repo on github.com (don't make it public — `.env` style secrets live in your code history if you ever slipped them in).

```powershell
git remote add origin https://github.com/<your-username>/hunt.git
git branch -M main
git push -u origin main
```

> **Sanity check:** open the repo on GitHub. You should see `Dockerfile.render`, `render.yaml`, `requirements_render.txt`, and the `app/`, `brain/`, `voice/`, `ui/` folders. You should **NOT** see `.venv/`, `data/`, or any `__pycache__` — those are stripped by `.dockerignore` + `.gitignore`.

---

## 2. Create the Render service

1. Go to [dashboard.render.com](https://dashboard.render.com/) → **New +** → **Blueprint**
2. Connect your GitHub account if you haven't already
3. Pick your `hunt` repo
4. Render reads `render.yaml` and shows the planned service. Click **Apply**

This kicks off the first build. Expect **~5–10 minutes** — Whisper-tiny, sentence-transformers, and a few hundred MB of Python wheels.

While it builds, do step 3.

---

## 3. Set the two secret environment variables

In Render → your `hunt` service → **Environment** tab.

Two values are marked `sync: false` in `render.yaml` — Render won't auto-fill them, you have to:

| Key | Where to get it |
|---|---|
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys → "Create Key" → starts with `sk-or-…` |
| `MONGODB_URI` | MongoDB Atlas → your cluster → **Connect** → **Connect your application** → copy URI, replace `<password>` |

Paste each one, click **Save changes**. Render will redeploy automatically after you save.

---

## 4. Wait for the build to finish

Watch the **Logs** tab. You're looking for:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:10000
```

Then in the top-right, Render shows the URL: `https://hunt-xxxx.onrender.com`.

Visit **`https://hunt-xxxx.onrender.com/v2`** → you should see the orb.

---

## 5. First-load caveats on the free plan

Render's free plan **sleeps after 15 min of no traffic**. The first request after a sleep takes ~30 seconds to wake up (container restart + Whisper model warmup).

If you want it always-on, change `plan: free` → `plan: starter` in `render.yaml` and push. Starter is $7/mo, always-on, 512MB RAM.

**Persistent disk is paid-only.** On the free plan, your Chroma vector store resets every time the container restarts (which happens on deploys and after sleep). Profile facts go to MongoDB Atlas (which is durable), but the conversation embeddings in Chroma are ephemeral. If that matters, bump to Starter and add a disk:

```yaml
services:
  - type: web
    name: hunt
    # ...everything else stays the same...
    disk:
      name: hunt-data
      mountPath: /data
      sizeGB: 1
```

---

## 6. What will work / not work on Render

**Works out of the box:**
- Web UI at `/v2` (the orb, conversation, settings drawer, action chips, fact chips)
- Voice transcription (mic → `/voice/transcribe` → Whisper-tiny → text)
- Chat streaming (uses OpenRouter, so latency is fine)
- Memory (Chroma in container + MongoDB Atlas for persistence)
- Approval chips & action history

**Won't work on Render (and shouldn't):**
- Anything in `automation/` that touches a real desktop — `open notepad`, `move mouse`, etc. These run on the host, and Render's host is a Linux container with no GUI. If you ask Hunt to "open notepad" on the cloud deploy, the action will fail gracefully (the chip will show as denied / unsupported on Linux).
- Local TTS (the system speech voices are macOS/Windows only). Browser TTS still works because that's in your browser.

If you want desktop actions, run Hunt locally (use `run.bat`). The cloud deploy is for the *conversational* side.

---

## 7. Updating after the first deploy

Every `git push` to `main` triggers an auto-deploy (because `autoDeploy: true` in `render.yaml`). 

```powershell
git add .
git commit -m "fix: whatever you changed"
git push
```

Render rebuilds (~3–5 min) and rolls out. Watch logs.

To roll back: Render dashboard → your service → **Events** → find the previous deploy → **Rollback**.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build fails on `pip install`, runs out of memory | Free tier has a build-time RAM ceiling | Move to Starter ($7/mo) for the build, downgrade after if you want |
| Page loads but `/health` is red | `OPENROUTER_API_KEY` or `MONGODB_URI` missing/wrong | Check Environment tab, then **Manual Deploy → Clear build cache & deploy** |
| Mic upload returns 500 | ffmpeg or libsndfile missing | They're in `Dockerfile.render` — but verify the build used `Dockerfile.render`, not the old `Dockerfile`. Check `dockerfilePath` in `render.yaml`. |
| 502 Bad Gateway from Render | The app listens on the wrong port | Confirm the `CMD` in `Dockerfile.render` uses `${PORT:-8001}`, not a hardcoded port. |
| "Application failed to respond" | Cold start (free plan sleep) | Wait 30 seconds and reload. To prevent: bump to Starter. |

---

## What I created for you

| File | Purpose |
|---|---|
| `Dockerfile.render` | Slim Python 3.11 + ffmpeg + libsndfile image |
| `requirements_render.txt` | Cloud-only deps (no pyautogui / playwright) |
| `render.yaml` | Blueprint Render reads to provision the service |
| `.dockerignore` | Strips `.venv`, `data/`, `logs/`, secrets from the build context |
| `RENDER_DEPLOY.md` | This guide |

The local dev path (`run.bat`, the existing `Dockerfile`, etc.) is unchanged.
