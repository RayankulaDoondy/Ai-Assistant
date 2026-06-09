# Hunt × Desktop Helper

> ⚠️ **Local Windows only.** This helper makes Hunt's "open chrome" / "close X" / "search" / "open URL" action chips actually do something on your Windows desktop. It's NOT needed for chat, voice, Gmail, or memory queries — those work fine in Docker on their own. Only start the helper when you actively want desktop automation.

---

## What this is

Hunt runs in a Linux Docker container (for stability — Python 3.13 + Windows asyncio deadlocks otherwise). A Linux container has no path to your real Windows desktop, so `pyautogui` and similar libraries can't drive your mouse / keyboard / apps from inside Docker.

`desktop_helper.py` is the missing native half. It's a tiny Python HTTP server that runs **natively on Windows alongside Docker**. Hunt's `action_runner` POSTs every approved action to it, and the helper executes the action using `subprocess` / `webbrowser` / (later) `pyautogui` locally.

```
┌──────────────────────┐    HTTP    ┌────────────────────────┐
│ Hunt (Docker)        │  ─────────►│ desktop_helper.py      │
│ /chat → action_runner│            │ (native Windows Python)│
└──────────────────────┘            └────────────────────────┘
                                              │
                                              ▼
                            Opens / closes apps, opens URLs,
                            runs searches in your default browser
```

When the helper is **not** running, action chips resolve with a clean message: *"Desktop helper isn't running. Open a PowerShell window and run `run_helper.bat`, then try again."*

---

## When to run it

Run `run_helper.bat` whenever you want any of these to actually execute:

- `open <appname>` — Chrome, Edge, Firefox, Notepad, VS Code, Slack, Outlook, etc.
- `close <appname>` — taskkill the running process
- `open <url>` / `go to <url>` — open in default browser
- `search for <query>` — Google search in default browser

You can leave the helper running 24/7 — it's tiny (single Python process, idle CPU) and only does anything when Hunt POSTs to it.

**You don't need it for:**
- Chat / voice / Q&A
- Gmail search and read
- Hunt's memory / RAG retrieval
- File RAG / document queries
- Anything that isn't a desktop or browser action

---

## How to start it

### Easiest — double-click

1. Open File Explorer to `C:\Users\rayan\Downloads\Ai Doonz\Jarvis\`
2. **Double-click `run_helper.bat`**
3. A console window opens with:
   ```
   Hunt Desktop Helper
   Listening on: http://127.0.0.1:9100
   Hunt's action chips will work while this window is open.
   Press Ctrl+C to stop.
   ```
4. Leave that window open

### From PowerShell

```powershell
cd "C:\Users\rayan\Downloads\Ai Doonz\Jarvis"
python automation\desktop_helper.py
```

### Verify it's up

In a separate PowerShell tab:

```powershell
Invoke-RestMethod http://127.0.0.1:9100/health
```

Expected:
```
ok            : True
auth_required : False
```

If you see that, Hunt is now able to drive your desktop.

---

## Requirements on your Windows machine

- **Python 3.8+** installed and on PATH (you almost certainly already have this — Hunt has been running for weeks). Verify with `python --version`.
- **No pip packages needed** — the helper uses Python's standard library only. (`pyautogui` would be required for advanced future actions like `take_screenshot` / `type_text`, but the v1 helper handles `open_app` / `close_app` / `open_browser` / `search` via `subprocess` + `webbrowser` which are both stdlib.)
- Port `9100` free on `127.0.0.1`. If you have something else on that port, set `HUNT_HELPER_PORT=9101` (and update `HUNT_HELPER_URL` in `docker-compose.dev.yml` to match).

---

## What apps are recognized

`automation/desktop_helper.py` has a friendly-name → executable map at the top of the file. You can edit it freely — anything added to `_APP_ALIASES` becomes a name Hunt can launch.

Recognized out of the box: `chrome`, `edge`, `firefox`, `brave`, `notepad`, `calculator` / `calc`, `explorer`, `settings`, `vs code` / `vscode` / `code`, `terminal`, `powershell`, `cmd`, `word`, `excel`, `outlook`, `teams`, `slack`, `discord`, `spotify`.

If you say `open <anything else>`, the helper passes the name straight to Windows' `start` command. If that name resolves to anything in your PATH or Start menu, it launches.

---

## Security

The helper binds **127.0.0.1 only**, so it's reachable only from this machine. The Docker container can still reach it via `host.docker.internal:9100` because Docker Desktop's networking maps that hostname to the host loopback.

This means: anyone with a shell on your Windows machine could POST to the helper and trigger actions. For a personal laptop where you're the only user, that's not meaningfully worse than having an active terminal — they could just run `chrome.exe` themselves.

### Optional token authentication

If you share your machine or just want belt-and-suspenders:

1. Pick a random secret. PowerShell one-liner:
   ```powershell
   -join ((1..32) | ForEach-Object { [char]((97..122) + (48..57) | Get-Random) })
   ```
2. Set it on the helper side:
   ```powershell
   $env:HUNT_HELPER_TOKEN = "<your-secret>"
   python automation\desktop_helper.py
   ```
3. Pass the same value to Hunt via `docker-compose.dev.yml`:
   ```yaml
   - HUNT_HELPER_URL=http://host.docker.internal:9100
   - HUNT_HELPER_TOKEN=<your-secret>
   ```
4. Restart the container: `docker compose -f docker-compose.dev.yml restart`

With the token configured, any POST without the `Authorization: Bearer <token>` header gets a 401. The chip will fail with a friendly message and the user sees no action.

---

## Stopping the helper

- **Ctrl+C** in the helper console — clean shutdown
- **Close the console window** — also fine
- If you find yourself rebooting and forgetting it's not running, you can drop `run_helper.bat` into your Windows Startup folder so it auto-starts on login

---

## Troubleshooting

| Symptom | Likely cause + fix |
|---|---|
| Chip says *"Desktop helper isn't running..."* | The helper console isn't open. Run `run_helper.bat`. |
| Chip says *"Desktop helper is unreachable at host.docker.internal..."* | Docker Desktop's host-network bridge isn't responding. Restart Docker Desktop. |
| Chip says *"Desktop helper is running but didn't respond in time"* | The action is hanging (e.g. an interactive subprocess prompt). Check the helper window for what's stuck. |
| `python` not found when running the .bat | Python isn't installed or not on PATH. Install Python 3.x from https://python.org or `winget install Python.Python.3.12`. |
| Helper bind error: *"port already in use"* | Another process holds 9100. Set `HUNT_HELPER_PORT=9101` and update `HUNT_HELPER_URL` to match. |
| "Open chrome" says success but nothing happens | Look in the helper console — it logs every action attempt. Probably an alias mismatch; add the app to `_APP_ALIASES` in `automation/desktop_helper.py`. |
| 401 unauthorized | You set `HUNT_HELPER_TOKEN` on one side but not the other. Make both match. |

---

## What's NOT supported yet

The v1 helper deliberately covers only the four actions Hunt's regex `action_proposer` already recognizes:

- `open_app` ✅
- `close_app` ✅
- `open_browser` ✅
- `search` ✅

These don't require `pyautogui`, which is why the helper has **zero pip dependencies**. Future actions that need real mouse / keyboard control:

- `take_screenshot` — needs `pyautogui` or `mss`
- `type_text` — needs `pyautogui`
- `paste_text` — needs `pyperclip`
- `switch_window` / `focus_window` — needs `pygetwindow`
- `click` at (x, y) — needs `pyautogui`

When you actually want any of these, install the dep (`pip install pyautogui pygetwindow pyperclip mss`) and extend `REGISTRY` in `automation/desktop_helper.py` with the new method. The container side is already generic — `action_runner` will POST whatever the action_proposer emits, so no Hunt-side changes are needed.

---

## Removing the helper layer entirely

Don't want this feature? Just don't run the script — the action chips will resolve with a friendly message, and chat / Gmail / RAG keep working untouched. No teardown needed.

If you also want to remove the codepath:
1. Delete `HUNT_HELPER_URL` line from `docker-compose.dev.yml`
2. Restart the container
3. `automation/action_runner.py` skips the HTTP branch entirely — back to the in-container pyautogui dispatch (which fails with the friendly Docker message)
