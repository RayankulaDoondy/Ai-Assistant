# Hunt × Gmail — One-time setup

> ⚠️ **Local Docker only.** Gmail integration is currently designed to run on your laptop (local Docker), NOT on the Render deployment. The OAuth credentials, refresh token, and PKCE state all live on the local filesystem at `data/google/` — which is gitignored and never reaches Render. If you visit `https://hunt-pkth.onrender.com` and try to Connect Gmail, the Integrations card will show "Setup required" forever. To enable Gmail on the cloud deployment too, see the "Cloud Hunt + Gmail" section at the bottom of this file.

This is the **user side** of Phase 3 (Gmail integration). It's a sequence of clicks in the Google Cloud Console that ends with a `client_secret.json` file you drop into Hunt's project root. After that, Hunt's OAuth flow can ask for permission to read your Gmail and the `gmail_search` / `gmail_read` tools become available to Research Mode.

**Total time: ~45-60 minutes.** Most of it is clicking through Google's UI; very little is conceptual.

**Reversible**: At any time you can disconnect Hunt from Gmail (revokes the token) and/or delete the Google Cloud project (removes all access).

---

## Step 0 — what you'll end up with

By the end of this guide you'll have:

1. A Google Cloud **project** (free, no credit card required for our usage)
2. **Gmail API** enabled on that project
3. An **OAuth consent screen** configured for personal use (Testing mode, your email as a test user)
4. An **OAuth Client ID** with `http://localhost:8001/auth/google/callback` as the authorized redirect
5. A file `client_secret.json` saved at `Jarvis/data/google/client_secret.json`

When all five exist, Hunt's `/auth/google/start` endpoint will work.

---

## Step 1 — Create a Google Cloud project

1. Go to: <https://console.cloud.google.com/>
2. Sign in with the same Google account whose Gmail you want Hunt to read
3. Top bar shows a project picker (usually says "Select a project" or your last-used project name) → click it
4. Click **"New Project"** in the dialog that opens
5. Fields:
   - **Project name**: `Hunt Personal AI` (or anything you like)
   - **Organization** / **Location**: leave default ("No organization") unless you have a Google Workspace
6. Click **Create**. Wait ~10 seconds.
7. After it creates, the picker switches to your new project. **Confirm the top bar shows "Hunt Personal AI"** before continuing — every later step needs this project active.

---

## Step 2 — Enable the Gmail API

1. Left sidebar (hamburger menu top-left) → **APIs & Services** → **Library**
2. Search for **"Gmail API"**
3. Click the **Gmail API** result
4. Click the **Enable** button
5. Wait ~10 seconds for it to enable

You should land on the Gmail API overview page with a "Manage" / "Try this API" button. That confirms it's enabled.

---

## Step 3 — Configure the OAuth consent screen

This screen is what the user sees when they grant your app permission. Since this is a personal app, we'll set it up in **Testing** mode (no Google verification needed).

1. Left sidebar → **APIs & Services** → **OAuth consent screen**
2. **User Type**: choose **External** (Internal is only for Google Workspace orgs)
3. Click **Create**

### Page 1: App information

| Field | Value |
|---|---|
| **App name** | `Hunt` |
| **User support email** | your Gmail address (only choice usually) |
| **App logo** | skip (optional) |
| **Application home page** | skip |
| **Application privacy policy link** | skip |
| **Application terms of service link** | skip |
| **Authorized domains** | skip |
| **Developer contact information** → email | your Gmail address |

Click **Save and Continue**.

### Page 2: Scopes

1. Click **Add or Remove Scopes**
2. In the filter, type `gmail`
3. Check the box for:
   - ✅ `https://www.googleapis.com/auth/gmail.readonly` — "Read all resources and their metadata—no write operations."
4. (Do NOT check `gmail.send` or `gmail.modify` — Hunt is read-only)
5. Click **Update** at the bottom of the scope panel
6. Click **Save and Continue**

⚠️ Note: `gmail.readonly` is a "Restricted" scope. In Testing mode this is fine for you personally, but if you ever want to publish Hunt for others, Google requires a security assessment ($75K+). For personal use you can stay in Testing forever.

### Page 3: Test users

This is what lets YOU (and only you) use the app while it's in Testing mode.

1. Click **Add Users**
2. Enter your Gmail address (the same one whose mail Hunt will read)
3. Click **Add**
4. Click **Save and Continue**

### Page 4: Summary

Just click **Back to Dashboard**.

⚠️ **Important**: leave the publishing status as **"Testing"** at the top of the consent screen page. Do NOT click "Publish App" — that triggers Google's verification requirement.

---

## Step 4 — Create OAuth Client ID

1. Left sidebar → **APIs & Services** → **Credentials**
2. Top of page: **+ Create Credentials** → **OAuth client ID**
3. **Application type**: choose **Web application**
4. **Name**: `Hunt Local Docker` (anything you like — internal label)
5. **Authorized JavaScript origins**: skip (we don't need this)
6. **Authorized redirect URIs**: click **+ Add URI** and paste exactly:

   ```
   http://localhost:8001/auth/google/callback
   ```

   ⚠️ The trailing slash, the port, and `localhost` (not `127.0.0.1`) all matter. If you change any of them later you'll have to add the new one here too.

7. Click **Create**
8. A dialog pops up showing your **Client ID** and **Client secret**. Click **Download JSON** at the bottom of the dialog.
9. You'll get a file named something like `client_secret_1234-abcd.apps.googleusercontent.com.json`

---

## Step 5 — Put the secret where Hunt expects it

1. In your file explorer, navigate to:
   ```
   C:\Users\rayan\Downloads\Ai Doonz\Jarvis\
   ```
2. Create a new folder named `data` if it doesn't already exist (Hunt's runtime makes this anyway, but it might not exist before first start)
3. Inside `data`, create another folder named `google`
4. Move the downloaded `client_secret_*.json` into that folder
5. **Rename** it to exactly:
   ```
   client_secret.json
   ```

Final path:
```
C:\Users\rayan\Downloads\Ai Doonz\Jarvis\data\google\client_secret.json
```

⚠️ This file contains your OAuth client secret. **Do NOT** commit it to git. Hunt's `.gitignore` already ignores all of `data/`, so as long as you keep it there it stays local.

---

## Step 6 — Confirm Docker can see the file

Hunt's `docker-compose.dev.yml` volume-mounts `data/` into the container at `/data`. So your file at `Jarvis/data/google/client_secret.json` will appear inside the container at `/data/google/client_secret.json` automatically — no rebuild needed.

You can verify by running:

```powershell
cd "C:\Users\rayan\Downloads\Ai Doonz\Jarvis"
docker compose -f docker-compose.dev.yml exec hunt-dev ls -la /data/google/
```

You should see `client_secret.json` listed.

---

## Step 7 — Test the OAuth flow

(This is what Hunt's `/auth/google/start` endpoint will do once I've shipped the backend.)

When the backend is ready:

1. Open `http://localhost:8001/auth/google/start` in your browser
2. You'll be redirected to Google's sign-in page
3. Pick your Gmail account (the one you added as a test user)
4. Google shows a warning: **"Google hasn't verified this app"** → that's expected. Click **Continue** (or "Advanced" → "Go to Hunt (unsafe)" — Google's UI varies)
5. Permission consent screen → click **Continue** to grant `Read Gmail messages`
6. Browser redirects back to `http://localhost:8001/auth/google/callback?code=...`
7. Hunt exchanges the code for tokens, saves them to `data/google/token.json`, and shows you a success page

After that:
- `GET /auth/google/status` returns `{ "connected": true, "email": "you@gmail.com" }`
- Hunt's chat can use the `gmail_search` and `gmail_read` tools when you're in Research Mode

---

## When tokens go stale

In **Testing mode**, Google **expires refresh tokens after 7 days**. After 7 days, Hunt will get an "invalid_grant" error and you'll need to re-connect by hitting `/auth/google/start` again.

This is a Google policy, not a Hunt bug. The only way to get permanent tokens is to publish the app and go through Google's verification — which for `gmail.readonly` requires a security assessment ($75K+, not realistic for personal use).

For now, plan to re-auth every week. Hunt will surface a "Gmail token expired — reconnect" toast when this happens.

---

## What to do now

1. **Complete Steps 1-6 above** — that's your hour of one-time work
2. **Confirm `data/google/client_secret.json` exists** at the right path
3. **Tell me when you're done** — I'll have the OAuth backend ready to test by then

If anything in this guide doesn't match what you see in Google's UI (Google redesigns the console occasionally), paste a screenshot of the screen where you got stuck and I'll guide you through the new flow.

---

## Disconnecting later

If you ever want to revoke Hunt's Gmail access:

- **In Hunt**: settings drawer → "Disconnect Gmail" button (Step 3.5 will add this)
- **In Google**: <https://myaccount.google.com/permissions> → find "Hunt" → Remove access

Either works. The Hunt-side disconnect is faster.

---

## Cloud Hunt + Gmail (NOT implemented yet)

This guide gets Gmail working on your **local Docker** install only. Making it work on the Render deployment too requires:

1. **Register the Render URL** as an Authorized redirect URI in your Google Cloud OAuth client:
   ```
   https://hunt-pkth.onrender.com/auth/google/callback
   ```
2. **Get `client_secret.json` to Render** — Render's container can't read your local `data/google/`. Options: paste the JSON contents into an env var like `GOOGLE_CLIENT_SECRET_JSON`, then modify `integrations/gmail_auth.py` to read from env when the file is absent.
3. **Persist the OAuth token** — Render free tier has ephemeral disk, so `data/google/token.json` is lost on every restart. Realistic options: store the token in MongoDB (already wired) or upgrade to a paid Render tier with persistent disk.

None of those are implemented today. If you want Hunt-on-Render to read your Gmail, ask for the cloud-Gmail patch and budget ~3-4 hours of work.

For personal single-user use, the local-only setup is actually preferable: your inbox content never leaves your machine, and you don't have to worry about token storage / refresh / restarts.
