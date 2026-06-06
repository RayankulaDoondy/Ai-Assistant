// =====================================================================
// Hunt v2 — single-view orb assistant, fully wired to the real backend.
//
// Wires (no mocks):
//   POST /chat/stream     NDJSON streaming reply  (response_length, voice_mode, use_live_search, include_context)
//   POST /voice/transcribe  mic blob → text
//   GET  /health          API + LLM + Memory state for diagnostic cards
//   GET  /status          model name for hint (lazy)
//   /v1 link (back to legacy UI) sits in drawer footer
//
// Settings drawer persists everything in localStorage under hunt.v2.prefs:
//   theme, replyLength, speakReplies, speechRate, useMemory, liveSearch, neuralBoost
// =====================================================================

(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const root = document.documentElement;

  // ---------------- prefs ----------------
  const PREFS_KEY = "hunt.v2.prefs";
  const DEFAULT_PREFS = {
    theme: "dark",
    replyLength: "normal",   // short | normal | detailed
    speakReplies: true,
    speechRate: 1.0,          // 0.6 .. 1.6
    useMemory: true,
    liveSearch: false,
    neuralBoost: true,
    // Phase 1 — Information Retrieval Agent. When on, /chat/stream uses
    // the tool-use planner so the LLM can call retrieve_memory before
    // answering. Costs a second LLM call per turn — off by default.
    researchMode: false,
  };
  let prefs = loadPrefs();
  function loadPrefs() {
    try {
      const raw = localStorage.getItem(PREFS_KEY);
      return raw ? Object.assign({}, DEFAULT_PREFS, JSON.parse(raw)) : { ...DEFAULT_PREFS };
    } catch { return { ...DEFAULT_PREFS }; }
  }
  function savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch {}
  }

  // ---------------- toast ----------------
  const toastHost = (() => {
    let h = $(".toast-host");
    if (!h) { h = document.createElement("div"); h.className = "toast-host"; document.body.appendChild(h); }
    return h;
  })();
  function toast(msg, kind) {
    const t = document.createElement("div");
    t.className = "toast" + (kind ? " " + kind : "");
    t.textContent = msg;
    toastHost.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 400); }, 3200);
  }

  // ---------------- theme ----------------
  function applyTheme() {
    root.setAttribute("data-theme", prefs.theme);
    if (orb) orb.setTheme(prefs.theme);
  }

  // ---------------- ORB ----------------
  const orbCanvas = $("#orb");
  const orb = (typeof window.ObsidianNexus === "function") ? new window.ObsidianNexus(orbCanvas, { theme: prefs.theme }) : null;
  if (orb) {
    orb.onTap = () => { if (!busy) fab.click(); };
    orb.resume();
  }

  // ---------------- state machine ----------------
  let mode = "idle"; // idle | listening | thinking | speaking
  let busy = false;  // true while a chat round is in progress
  const statusEl = $("#status"), subEl = $("#substatus"), pulseHost = $("#stage");
  const vizEl = $("#viz");

  const STATUS_TEXT = {
    idle:      ["How can I help?",   "Tap the orb or type below"],
    listening: ["Listening",          "Go ahead, I'm listening"],
    thinking:  ["Thinking",           "Routing through the planner"],
    speaking:  ["Speaking",           "Streaming reply"],
  };

  function setMode(m, subOverride) {
    mode = m;
    if (orb) orb.setMode(m);
    const [main, sub] = STATUS_TEXT[m] || STATUS_TEXT.idle;
    statusEl.textContent = main;
    subEl.textContent = subOverride || sub;
    pulseHost.classList.toggle("thinking", m === "thinking");
    vizEl.classList.toggle("on", m === "speaking");
    // timeline
    const order = ["listening", "thinking", "speaking", "done"];
    $$(".tstep").forEach((st) => {
      const idx = order.indexOf(st.dataset.step);
      const cur = order.indexOf(m === "idle" ? -1 : m);
      st.classList.remove("active", "done");
      if (m === "idle") return;
      if (idx < cur) st.classList.add("done");
      else if (idx === cur) st.classList.add("active");
    });
  }

  // ---------------- DIAGNOSTIC CARDS ----------------
  const sel = {
    voice: $("#dgVoice"), ai: $("#dgAi"), lat: $("#dgLat"), neural: $("#dgNeural"),
  };
  async function refreshHealth() {
    const start = performance.now();
    try {
      const r = await fetch("/health");
      const ms = Math.round(performance.now() - start);
      const j = await r.json();
      setDg(sel.ai,     !!j.llm_connected,     j.llm_connected ? "Online" : "Offline");
      setDg(sel.neural, !!j.memory_available,  j.memory_available ? "Active" : "Idle");
      sel.lat.querySelector(".val").textContent = ms + "ms";
      sel.lat.classList.remove("bad", "warn");
      if (ms > 1500) sel.lat.classList.add("bad");
      else if (ms > 400) sel.lat.classList.add("warn");
    } catch {
      setDg(sel.ai, false, "Offline");
      setDg(sel.neural, false, "Idle");
      sel.lat.querySelector(".val").textContent = "—";
      sel.lat.classList.add("bad");
    }
  }
  function setDg(card, ok, label) {
    if (!card) return;
    card.classList.remove("bad", "warn");
    if (!ok) card.classList.add("bad");
    card.querySelector(".val").textContent = label;
  }
  // Voice card just reports whether the mic API exists
  setDg(sel.voice, !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia), navigator.mediaDevices?.getUserMedia ? "Connected" : "Unavailable");
  refreshHealth();
  // Poll /health every 30s instead of 8s — less server load, less perceived
  // jitter on the latency pill.
  setInterval(refreshHealth, 30000);

  // ---------------- CONVERSATION FEED ----------------
  const feed = $("#feed");
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function renderMd(text) {
    if (!text) return "";
    let src = escapeHtml(text);

    // Pull fenced code blocks OUT first as opaque placeholders so the
    // line-by-line block processor below doesn't eat their newlines / fold
    // them into a paragraph. We restore them at the end.
    const codeBlocks = [];
    src = src.replace(/```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g, (_, lang, body) => {
      const cls = lang ? ` class="lang-${lang.toLowerCase()}"` : "";
      // Trim only ONE leading + trailing newline so single-line code blocks
      // still render, while keeping internal indentation intact.
      const cleaned = body.replace(/^\n/, "").replace(/\n$/, "");
      codeBlocks.push(`<pre><code${cls}>${cleaned}</code></pre>`);
      return ` CODE${codeBlocks.length - 1} `;
    });

    src = src.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    src = src.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    src = src.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s.,;:!?)]|$)/g, "$1<em>$2</em>");

    const lines = src.split("\n");
    const out = []; let list = null; let para = [];
    const flushP = () => { if (para.length) { out.push("<p>" + para.join(" ") + "</p>"); para = []; } };
    const flushL = () => { if (list) { out.push("</" + list + ">"); list = null; } };
    for (const raw of lines) {
      const ln = raw.trim();
      if (!ln) { flushP(); flushL(); continue; }
      // A code-block placeholder is its own paragraph — flush surrounding state.
      const cb = ln.match(/^ CODE(\d+) $/);
      if (cb) {
        flushP(); flushL();
        out.push(codeBlocks[+cb[1]] || "");
        continue;
      }
      const h = ln.match(/^(#{1,3})\s+(.+)$/);
      if (h) {
        flushP(); flushL();
        const lvl = Math.min(4, h[1].length + 2);
        out.push(`<h${lvl}>${h[2]}</h${lvl}>`);
        continue;
      }
      const ul = ln.match(/^[-*]\s+(.+)$/);
      if (ul) { flushP(); if (list !== "ul") { flushL(); out.push("<ul>"); list = "ul"; } out.push(`<li>${ul[1]}</li>`); continue; }
      const ol = ln.match(/^\d+\.\s+(.+)$/);
      if (ol) { flushP(); if (list !== "ol") { flushL(); out.push("<ol>"); list = "ol"; } out.push(`<li>${ol[1]}</li>`); continue; }
      para.push(ln);
    }
    flushP(); flushL();

    let html = out.join("");
    // Restore code blocks that ended up inside a <p> because they were on
    // the same line as surrounding prose (rare, but possible during streaming).
    html = html.replace(/ CODE(\d+) /g, (_, i) => codeBlocks[+i] || "");
    return html;
  }
  function makeCard(who, html) {
    const c = document.createElement("div");
    c.className = "card " + who;
    c.innerHTML = `<div class="who"><i></i>${who === "user" ? "You" : "Hunt"}</div><div class="text">${html || ""}</div>`;
    feed.appendChild(c);
    c.scrollIntoView({ behavior: "smooth", block: "end" });
    return c.querySelector(".text");
  }

  // ---------------- APPROVAL CHIPS ----------------
  // Hunt's /chat/stream may emit action_proposal / fact_proposal events after
  // the `done` event. We render them as chip strips inside the assistant card
  // and POST the user's decision to /actions/{id}/decide or /memory/facts/{id}/decide.
  function chipsHost(textEl) {
    const card = textEl.parentElement; // .card.hunt
    let host = card.querySelector(".chips");
    if (!host) {
      host = document.createElement("div");
      host.className = "chips";
      card.appendChild(host);
    }
    return host;
  }
  function appendChipStrip(textEl, kind, ev) {
    const host = chipsHost(textEl);
    const strip = document.createElement("div");
    strip.className = "chip-strip " + kind;
    strip.dataset.id = ev.id;
    strip.dataset.kind = kind;

    const prompt = document.createElement("div");
    prompt.className = "chip-prompt";
    const label = (kind === "action")
      ? (ev.prompt || ev.action || "Run action?")
      : (ev.prompt || "Save fact?");
    prompt.textContent = label;
    strip.appendChild(prompt);

    const actions = document.createElement("div");
    actions.className = "chip-actions";
    (ev.options || []).forEach(opt => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip " + (opt.kind || "neutral");
      b.dataset.value = opt.value;
      b.textContent = opt.label;
      b.onclick = () => onChipClick(strip, kind, ev, opt.value, opt.label);
      actions.appendChild(b);
    });

    // Fact-proposal: add a "Promote to profile" button that opens a quick picker.
    if (kind === "fact" && ev.profile_field) {
      const promote = document.createElement("button");
      promote.type = "button";
      promote.className = "chip subtle";
      promote.textContent = `→ ${humanizeField(ev.profile_field)}`;
      promote.title = "Promote this fact into your profile";
      promote.onclick = () => onChipClick(strip, kind, ev, "promote", `Promoted → ${humanizeField(ev.profile_field)}`, { profile_field: ev.profile_field });
      actions.appendChild(promote);
    }

    strip.appendChild(actions);
    host.appendChild(strip);
    host.scrollIntoView({ behavior: "smooth", block: "end" });
  }
  function appendChipAck(textEl, kind, ev) {
    const host = chipsHost(textEl);
    const strip = document.createElement("div");
    strip.className = "chip-strip resolved ok auto " + kind;
    const detail = (ev.result && (ev.result.detail || ev.result.status)) || ev.action || "ran";
    strip.innerHTML = `<span class="chip-resolved-icon">✓</span><span>Auto-ran · ${escapeHtml(String(detail))}</span>`;
    host.appendChild(strip);
  }
  async function onChipClick(strip, kind, ev, value, labelText, bodyExtra) {
    strip.querySelectorAll("button").forEach(b => b.disabled = true);
    const url = (kind === "action")
      ? `/actions/${encodeURIComponent(ev.id)}/decide`
      : `/memory/facts/${encodeURIComponent(ev.id)}/decide`;
    const body = Object.assign({ decision: value }, bodyExtra || {});
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      resolveChip(strip, kind, value, labelText, j);
    } catch (e) {
      resolveChip(strip, kind, "error", e.message, {});
    }
  }
  function resolveChip(strip, kind, value, labelText, result) {
    let icon = "✓", text = labelText, cls = "ok";
    if (value === "error") { icon = "⚠"; text = "Error · " + labelText; cls = "err"; }
    else if (kind === "action") {
      if (value === "deny") { icon = "✕"; text = "Denied"; cls = "denied"; }
      else {
        const detail = result.detail || result.status || "ran";
        text = (value === "always" ? "Always allow · " : "Allowed · ") + detail;
      }
    } else if (kind === "fact") {
      if (value === "skip") { icon = "✕"; text = "Skipped"; cls = "denied"; }
      else if (value === "always_save") text = "Saved · pattern always-saves";
      else if (value === "promote") text = labelText || "Promoted to profile";
      else if (value === "never_again") { icon = "✕"; text = "Muted pattern"; cls = "denied"; }
      else text = "Saved to memory";
    }
    strip.classList.add("resolved", cls);
    strip.innerHTML = `<span class="chip-resolved-icon">${icon}</span><span>${escapeHtml(text)}</span>`;
  }
  function humanizeField(f) {
    return ({
      name: "Name", occupation: "Occupation", projects: "Projects",
      interests: "Interests", preferred_tone: "Tone",
      daily_schedule: "Schedule", frequent_contacts: "Contacts", goals: "Goals",
    })[f] || f;
  }

  // ---------------- CHAT STREAMING ----------------
  // currentAbort lets the user (or a timeout) cancel an in-flight chat.
  let currentAbort = null;
  // Watchdog: if no event arrives in WATCHDOG_MS, treat the request as dead.
  // Server reloads or network drops would otherwise leave the UI stuck in
  // "Thinking" forever. Research mode chats run TWO LLM calls (planner +
  // final stream) so the watchdog needs to be longer when use_tools is on.
  const WATCHDOG_MS = 45000;
  const WATCHDOG_MS_TOOLS = 90000;

  function cancelChat(reason) {
    if (!currentAbort) return;
    try { currentAbort.abort(reason || "user_cancel"); } catch {}
    currentAbort = null;
  }

  // Phrases that trigger Hunt's read_clipboard macro. We pre-detect them so
  // we can fetch the browser clipboard ahead of POSTing — required when Hunt
  // is running inside a Linux Docker container that can't see the host's
  // (Windows) clipboard. The list mirrors the patterns in
  // brain/context_manager.py so client and server agree.
  const CLIPBOARD_PHRASES = [
    "what's in my clipboard", "whats in my clipboard",
    "read my clipboard", "read clipboard",
    "what's on my clipboard", "whats on my clipboard",
    "show me my clipboard", "show my clipboard",
    "clipboard contents",
    "what did i copy",
  ];
  function looksLikeClipboardQuery(text) {
    const t = (text || "").toLowerCase();
    return CLIPBOARD_PHRASES.some(p => t.includes(p));
  }
  async function readBrowserClipboard() {
    // navigator.clipboard.readText is async and requires:
    //  - a secure context (https or localhost — we're on localhost ✓)
    //  - the user to have interacted with the page recently (the Enter key
    //    or send click counts ✓)
    //  - the user to grant the one-time permission Chrome prompts for
    // We swallow errors — if the user denies or the API is missing, the
    // backend macro just falls back to "clipboard is empty".
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      console.warn("[clipboard] navigator.clipboard.readText unavailable");
      return null;
    }
    try {
      const text = await navigator.clipboard.readText();
      const preview = (text || "").slice(0, 80).replace(/\s+/g, " ");
      console.log(`[clipboard] read ${text.length} chars: "${preview}${text.length > 80 ? "…" : ""}"`);
      return typeof text === "string" ? text : null;
    } catch (e) {
      console.warn("[clipboard] readText threw:", e && e.name, e && e.message);
      return null;
    }
  }

  async function sendMessage(text) {
    if (busy) return;
    const v = (text || $("#input").value).trim();
    if (!v) return;
    $("#input").value = "";
    busy = true;
    sendBtn.disabled = true;

    makeCard("user", escapeHtml(v));
    setMode("thinking");

    const aiText = makeCard("hunt", '<span class="cursor"></span>');
    let buffer = "";
    let firstToken = false;
    // Response Composer V2 — server emits a separate `voice` event with the
    // speakable summary the LLM appended after the [VOICE]: marker. We
    // capture it here and play it on `done` so TTS speaks AFTER the visible
    // text has settled, not over the top of streaming tokens.
    let voiceLine = "";

    // Pre-flight: if the user asked about clipboard, try to read it via the
    // browser API so it works inside Docker. The fetch happens BEFORE we
    // POST — the result is sent as `client_clipboard` in the request body.
    let clientClipboard = null;
    if (looksLikeClipboardQuery(v)) {
      clientClipboard = await readBrowserClipboard();
    }

    const ctrl = new AbortController();
    currentAbort = ctrl;
    // Watchdog timer — reset every time we receive a server event. Research
    // mode runs an extra LLM call up front (planner) before the streamed
    // answer, so we use a longer timeout when it's on.
    const watchdogMs = prefs.researchMode ? WATCHDOG_MS_TOOLS : WATCHDOG_MS;
    let watchdog = setTimeout(() => {
      try { ctrl.abort("watchdog"); } catch {}
    }, watchdogMs);
    const kickWatchdog = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => { try { ctrl.abort("watchdog"); } catch {} }, watchdogMs);
    };

    try {
      const res = await fetch("/chat/stream", {
        method: "POST",
        signal: ctrl.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: v,
          include_context: !!prefs.useMemory,
          use_live_search: !!prefs.liveSearch,
          voice_mode: false,
          response_length: prefs.replyLength,
          // Phase 1 — research mode triggers tool-use orchestration.
          use_tools: !!prefs.researchMode,
          // Only sent when the browser successfully read the clipboard —
          // backend ignores null / missing.
          ...(clientClipboard != null ? { client_clipboard: clientClipboard } : {}),
        }),
      });
      if (!res.ok || !res.body) throw new Error("HTTP " + res.status);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let leftover = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        kickWatchdog();
        leftover += dec.decode(value, { stream: true });
        const lines = leftover.split("\n");
        leftover = lines.pop() || "";
        for (const line of lines) {
          const s = line.trim();
          if (!s) continue;
          let ev;
          try { ev = JSON.parse(s); } catch { continue; }
          if (ev.type === "token") {
            if (!firstToken) { firstToken = true; setMode("speaking"); }
            buffer += ev.content || "";
            // Hide everything from the [VOICE]: marker onward while streaming —
            // the contract asks the LLM to put a speakable summary there, and
            // the user should never see it. The full buffer keeps the marker
            // for the done event to extract from.
            aiText.innerHTML = renderMd(stripVoiceMarker(buffer)) + '<span class="cursor"></span>';
            aiText.parentElement.scrollIntoView({ behavior: "smooth", block: "end" });
          } else if (ev.type === "tool_call") {
            // Phase 1 — research-mode planner is about to run a tool.
            // Surface a transient status row above the answer so the user
            // sees that Hunt is researching, not just thinking.
            showToolStatus(aiText, ev.label || `Running ${ev.name || "tool"}…`);
          } else if (ev.type === "tool_result") {
            // The tool returned; update the status row to reflect the count
            // so the user knows how much material the answer is grounded in.
            const n = (ev.count == null) ? "" : ` · ${ev.count} hit${ev.count === 1 ? "" : "s"}`;
            updateToolStatus(aiText, `Pulled from memory${n}`);
          } else if (ev.type === "voice") {
            // Server-extracted one-sentence summary intended for TTS only.
            // We stash it; the actual TTS fires on `done` so it speaks AFTER
            // the visible text settles, not over the top of streaming tokens.
            voiceLine = ev.content || "";
          } else if (ev.type === "done") {
            // Backend now returns `response` already stripped of [VOICE]:
            // and a separate `voice_response` for TTS. We trust those.
            buffer = ev.response || stripVoiceMarker(buffer);
            aiText.innerHTML = renderMd(buffer);
            // Render citation cards from any retrieve_memory calls.
            const cites = Array.isArray(ev.citations) ? ev.citations : [];
            if (cites.length) renderCitations(aiText, cites);
            const speakable = (ev.voice_response || voiceLine || stripMarkdown(buffer)).trim();
            if (prefs.speakReplies && speakable) speak(speakable);
          } else if (ev.type === "error") {
            aiText.innerHTML = `<span style="color:var(--red,#f87171)">Error: ${escapeHtml(ev.message || "unknown")}</span>`;
            toast("Chat error: " + (ev.message || "unknown"), "err");
          } else if (ev.type === "macro_data") {
            const md = ev.macro_data || {};
            const extra = md.speakable_script || "";
            if (extra) buffer += "\n\n" + extra;
            aiText.innerHTML = renderMd(buffer);
            if (prefs.speakReplies && extra) speak(stripMarkdown(extra));
          } else if (ev.type === "action_proposal") {
            // Hunt proposes a desktop action — render Allow / Deny / Always chips.
            appendChipStrip(aiText, "action", ev);
          } else if (ev.type === "action_executed") {
            // Policy was already "always" — backend ran it for us, just acknowledge.
            appendChipAck(aiText, "action", ev);
          } else if (ev.type === "fact_proposal") {
            // Hunt extracted a candidate fact about you — render Save / Skip / Always chips.
            appendChipStrip(aiText, "fact", ev);
          }
        }
      }
      // Briefly mark all timeline steps done
      $$(".tstep").forEach(st => { st.classList.add("done"); st.classList.remove("active"); });
      setTimeout(() => { if (mode !== "speaking") setMode("idle"); }, 600);
    } catch (e) {
      const aborted = (e && (e.name === "AbortError" || ctrl.signal.aborted));
      const reason = aborted ? (ctrl.signal.reason || "aborted") : (e && e.message) || "unknown";
      if (aborted && reason === "watchdog") {
        aiText.innerHTML = `<span style="color:#f87171">No reply within ${Math.round(watchdogMs/1000)}s. The server may be restarting or the model is slow. Try again.</span>`;
        toast("Hunt didn't reply in time. Server may be restarting.", "err");
      } else if (aborted) {
        aiText.innerHTML = `<span style="color:var(--faint)">— stopped —</span>`;
      } else {
        aiText.innerHTML = `<span style="color:#f87171">Couldn't reach Hunt: ${escapeHtml(reason)}</span>`;
        toast("Chat failed: " + reason, "err");
      }
    } finally {
      clearTimeout(watchdog);
      currentAbort = null;
      // If we have a TTS playing, wait for it before unlocking
      if (!ttsPending) finishRound();
    }
  }
  function finishRound() {
    busy = false;
    sendBtn.disabled = false;
    if (mode !== "listening") setMode("idle");
  }

  // ---------------- TTS ----------------
  let ttsPending = false;
  function speak(text) {
    if (!("speechSynthesis" in window) || !text || !text.trim()) return;
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = Math.max(0.5, Math.min(2.0, prefs.speechRate));
      u.pitch = 1.0;
      ttsPending = true;
      u.onend = () => { ttsPending = false; finishRound(); };
      u.onerror = () => { ttsPending = false; finishRound(); };
      window.speechSynthesis.speak(u);
    } catch { ttsPending = false; }
  }
  // Hide the [VOICE]: <speakable summary> tail the LLM emits per the
  // Response Composer V2 contract. We hide it while streaming so the user
  // never sees the marker. The match is case-insensitive and we strip
  // everything from the marker to end-of-buffer.
  function stripVoiceMarker(text) {
    if (!text) return "";
    const m = text.match(/\[\s*voice\s*\]\s*:[\s\S]*$/i);
    if (!m) return text;
    return text.slice(0, m.index).replace(/\s+$/, "");
  }

  // ---------------- TOOL STATUS + CITATIONS (Phase 1) ----------------
  // Research mode emits tool_call / tool_result / citations events. We
  // render a single status row that appears above the streaming answer,
  // updates to "Pulled X hits", and the citation cards land under the
  // answer once `done` fires.

  function statusHost(textEl) {
    const card = textEl.parentElement;
    let host = card.querySelector(".tool-status");
    if (!host) {
      host = document.createElement("div");
      host.className = "tool-status";
      // Insert BEFORE the text so it reads top-to-bottom.
      card.insertBefore(host, textEl);
    }
    return host;
  }
  function showToolStatus(textEl, label) {
    const host = statusHost(textEl);
    host.innerHTML = `<span class="dot"></span><span class="lbl">${escapeHtml(label)}</span>`;
    host.classList.remove("done");
  }
  function updateToolStatus(textEl, label) {
    const host = statusHost(textEl);
    host.querySelector(".lbl") && (host.querySelector(".lbl").textContent = label);
    host.classList.add("done");
  }

  function renderCitations(textEl, citations) {
    const card = textEl.parentElement;
    let host = card.querySelector(".citations");
    if (!host) {
      host = document.createElement("div");
      host.className = "citations";
      card.appendChild(host);
    }
    host.innerHTML = "";
    const head = document.createElement("div");
    head.className = "cite-head";
    head.textContent = `Sources (${citations.length})`;
    host.appendChild(head);

    for (const c of citations) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "cite-row";
      const kind = c.type || "memory";
      // Prefer the server-computed smart title (built from doc/project name
      // or the user's first question in the snippet). Fall back to old
      // heuristics for resilience against older server versions.
      const title = (
        c.title ||
        c.doc_title ||
        c.project_name ||
        (kind === "task" && c.snippet ? c.snippet.split("\n")[0].slice(0, 60) : "") ||
        (kind === "conversation" ? "Past chat" : kind)
      );
      const snippet = (c.snippet || "").replace(/\s+/g, " ").slice(0, 180);
      const stamp = c.timestamp ? new Date(c.timestamp).toLocaleDateString() : "";
      row.innerHTML = `
        <div class="cite-meta">
          <span class="cite-kind cite-kind--${escapeHtml(kind)}">${escapeHtml(kind)}</span>
          <span class="cite-title">${escapeHtml(title)}</span>
          ${stamp ? `<span class="cite-stamp">${escapeHtml(stamp)}</span>` : ""}
        </div>
        <div class="cite-snippet">${escapeHtml(snippet)}${snippet.length >= 180 ? "…" : ""}</div>`;
      // Click expands the row to show the full snippet inline.
      row.onclick = () => row.classList.toggle("open");
      host.appendChild(row);
    }
  }

  function stripMarkdown(t) {
    if (!t) return "";
    return t
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/\*([^*]+)\*/g, "$1")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/^[-*]\s+/gm, "")
      .replace(/^\d+\.\s+/gm, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  // ---------------- MIC / FAB ----------------
  // We capture raw PCM via Web Audio (not MediaRecorder) because the
  // backend's voice_engine uses `soundfile` which can't decode WebM/Opus —
  // it expects WAV. So we record float32 PCM, encode a real WAV blob
  // (PCM 16-bit mono), and POST that. Whisper resamples internally.
  const fab = $("#fab");
  const sendBtn = $(".composer .send");
  let micStream = null;
  let pcmCtx = null, pcmSrc = null, pcmProc = null, pcmGain = null;
  let pcmChunks = [];
  let pcmSampleRate = 0;
  let recording = false;

  function startPcmCapture(stream) {
    const AC = window.AudioContext || window.webkitAudioContext;
    pcmCtx = new AC();
    pcmSampleRate = pcmCtx.sampleRate;
    pcmSrc = pcmCtx.createMediaStreamSource(stream);
    pcmProc = pcmCtx.createScriptProcessor(4096, 1, 1);
    pcmGain = pcmCtx.createGain();
    pcmGain.gain.value = 0; // mute the playback path so we don't hear ourselves
    pcmChunks = [];
    pcmProc.onaudioprocess = (e) => {
      const ch = e.inputBuffer.getChannelData(0);
      // Copy because the buffer is reused
      pcmChunks.push(new Float32Array(ch));
    };
    pcmSrc.connect(pcmProc);
    pcmProc.connect(pcmGain);
    pcmGain.connect(pcmCtx.destination);
  }
  function stopPcmCapture() {
    try { pcmProc && pcmProc.disconnect(); } catch {}
    try { pcmSrc && pcmSrc.disconnect(); } catch {}
    try { pcmGain && pcmGain.disconnect(); } catch {}
    try { pcmCtx && pcmCtx.close(); } catch {}
    pcmProc = pcmSrc = pcmGain = pcmCtx = null;
    const chunks = pcmChunks; pcmChunks = [];
    if (!chunks.length) return null;
    let total = 0;
    for (const c of chunks) total += c.length;
    if (total < 1024) return null; // <~25ms — too short, treat as empty
    const all = new Float32Array(total);
    let off = 0;
    for (const c of chunks) { all.set(c, off); off += c.length; }
    // Downsample to 16 kHz mono. Whisper's tiny model was designed for 16 kHz
    // input and its built-in resampler is unreliable on short clips — sending
    // 16 kHz directly fixes empty-transcript hits we kept seeing at 48 kHz.
    const resampled = downsampleTo16k(all, pcmSampleRate);
    return encodeWav(resampled, 16000);
  }
  // Linear-interpolation downsampler with a moving-average pre-filter
  // (crude anti-alias). Good enough for voice; Whisper is robust to it.
  function downsampleTo16k(samples, srcRate) {
    if (!srcRate || Math.abs(srcRate - 16000) < 1) return samples;
    const ratio = srcRate / 16000;
    const window = Math.max(1, Math.floor(ratio));
    let pre = samples;
    if (window > 1) {
      pre = new Float32Array(samples.length);
      let acc = 0;
      for (let i = 0; i < samples.length; i++) {
        acc += samples[i];
        if (i >= window) acc -= samples[i - window];
        pre[i] = acc / Math.min(i + 1, window);
      }
    }
    const outLen = Math.floor(pre.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const srcPos = i * ratio;
      const idx = Math.floor(srcPos);
      const frac = srcPos - idx;
      const a = pre[idx] || 0;
      const b = (idx + 1 < pre.length) ? pre[idx + 1] : a;
      out[i] = a + (b - a) * frac;
    }
    return out;
  }
  function encodeWav(samples, sampleRate) {
    const buf = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buf);
    let p = 0;
    const writeStr = (s) => { for (let i = 0; i < s.length; i++) view.setUint8(p++, s.charCodeAt(i)); };
    const writeU32 = (v) => { view.setUint32(p, v, true); p += 4; };
    const writeU16 = (v) => { view.setUint16(p, v, true); p += 2; };
    // RIFF header
    writeStr("RIFF"); writeU32(36 + samples.length * 2); writeStr("WAVE");
    // fmt subchunk (PCM, mono, 16-bit)
    writeStr("fmt "); writeU32(16); writeU16(1); writeU16(1);
    writeU32(sampleRate); writeU32(sampleRate * 2); writeU16(2); writeU16(16);
    // data subchunk
    writeStr("data"); writeU32(samples.length * 2);
    for (let i = 0; i < samples.length; i++) {
      const v = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(p, v < 0 ? v * 0x8000 : v * 0x7FFF, true);
      p += 2;
    }
    return new Blob([buf], { type: "audio/wav" });
  }
  function cleanupMic() {
    fab.classList.remove("live");
    recording = false;
    if (orb) orb.detachStream();
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  }

  fab.onclick = async () => {
    if (busy) return;
    if (recording) {
      // Stop and submit
      const blob = stopPcmCapture();
      cleanupMic();
      if (!blob) { toast("Recording was empty.", "err"); setMode("idle"); return; }
      setMode("thinking", "Transcribing…");
      try {
        const fd = new FormData();
        fd.append("audio", blob, "clip.wav");
        const r = await fetch("/voice/transcribe", { method: "POST", body: fd });

        // Read body once as text, then parse, so we get useful diagnostics
        // when the server returns an error page or an empty body (Render
        // timeout, redeploy mid-request, gateway 5xx) instead of a flat
        // "Unexpected end of JSON input".
        let raw = "";
        try { raw = await r.text(); } catch {}
        console.log(`[voice] /voice/transcribe → ${r.status} (${raw.length} bytes)`, raw.slice(0, 200));
        if (!r.ok) {
          const snippet = raw.slice(0, 140) || "(empty body)";
          throw new Error(`HTTP ${r.status}: ${snippet}`);
        }
        if (!raw) {
          throw new Error("Empty response from /voice/transcribe (likely a Render timeout — try again in 30s)");
        }
        let j;
        try {
          j = JSON.parse(raw);
        } catch (parseErr) {
          throw new Error(`Server returned non-JSON: ${raw.slice(0, 140)}`);
        }
        const text = (j.transcript || j.text || "").trim();
        if (!text) {
          const reason = j.reason || "no_speech";
          const hint = j.hint ? " — " + j.hint : "";
          toast("Couldn't hear anything (" + reason + ")" + hint, "err");
          setMode("idle"); return;
        }
        // Auto-send the transcript — no review step. The user wanted a
        // single-action voice flow: tap mic, speak, tap mic again, Hunt
        // replies. If Whisper mis-heard, they can hit Esc to cancel the
        // in-flight chat (handled in the keydown listener above).
        sendMessage(text);
      } catch (e) {
        toast("Transcription failed: " + e.message, "err");
        setMode("idle");
      }
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      toast("Mic not available in this browser.", "err");
      return;
    }
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      if (orb) orb.attachStream(micStream);
      startPcmCapture(micStream);
      recording = true;
      fab.classList.add("live");
      setMode("listening");
    } catch (e) {
      toast("Mic permission denied.", "err");
    }
  };

  // ---------------- COMPOSER ----------------
  $("#composer").onsubmit = (e) => { e.preventDefault(); sendMessage(); };

  // ---------------- VISUALIZER BARS (driven by orb.getBands()) ----------------
  const BARS = 22;
  const viz = $("#viz");
  const barEls = [];
  for (let i = 0; i < BARS; i++) {
    const b = document.createElement("div");
    b.className = "b";
    viz.appendChild(b);
    barEls.push(b);
  }
  function rafBars(t) {
    requestAnimationFrame(rafBars);
    if (mode !== "speaking") return;
    const b = orb ? orb.getBands() : { amp: 0 };
    const micActive = orb && orb.micActive;
    const amp = micActive ? (0.3 + (b.amp || 0) * 2) : 1;
    for (let bi = 0; bi < BARS; bi++) {
      const h = 6 + Math.abs(Math.sin(t / 180 + bi * 0.5) * Math.cos(t / 90 + bi)) * 30 * amp;
      barEls[bi].style.height = Math.min(38, h) + "px";
    }
  }
  requestAnimationFrame(rafBars);

  // ---------------- SETTINGS DRAWER ----------------
  function openDrawer(o) {
    $("#drawer").classList.toggle("open", o);
    $("#scrim").classList.toggle("open", o);
  }
  $("#gear").onclick = () => openDrawer(true);
  $("#closeDrawer").onclick = () => openDrawer(false);
  $("#scrim").onclick = () => { openDrawer(false); openSessions(false); };
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    // Priority: close any drawer if open, else cancel chat if in flight, else stop TTS.
    if ($("#drawer").classList.contains("open")) { openDrawer(false); return; }
    if ($("#sessionsDrawer").classList.contains("open")) { openSessions(false); return; }
    if (currentAbort) { cancelChat("user_cancel"); return; }
    try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch {}
  });

  // ---------------- SESSIONS DRAWER ----------------
  // Lists past chats and lets the user resume one or start a new chat. Backed
  // by /sessions (list), /sessions/{id}/resume (load), /sessions/new (rotate).
  function openSessions(o) {
    $("#sessionsDrawer").classList.toggle("open", o);
    $("#scrim").classList.toggle("open", o);
    if (o) loadSessions();
  }
  $("#sessionsBtn").onclick = () => openSessions(true);
  $("#closeSessions").onclick = () => openSessions(false);

  async function loadSessions() {
    const host = $("#sessionsList");
    host.innerHTML = '<div class="sessions-empty">Loading…</div>';
    try {
      const res = await fetch("/sessions?limit=50");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      renderSessions(data.sessions || [], data.available);
    } catch (e) {
      host.innerHTML = `<div class="sessions-empty">Couldn't load chats. ${escapeHtml(String(e.message || e))}</div>`;
    }
  }

  function renderSessions(items, mongoAvailable) {
    const host = $("#sessionsList");
    if (!items.length) {
      host.innerHTML = '<div class="sessions-empty">No chats yet — start one below.</div>';
      return;
    }
    host.innerHTML = "";
    for (const s of items) {
      const row = document.createElement("div");
      row.className = "session-row" + (s.is_current ? " current" : "");
      const title = s.title || "Untitled chat";
      const when = formatSessionDate(s.started_at || s.saved_at);
      const turns = (s.verbatim_count != null) ? `${s.verbatim_count} turns` : "";
      // Trash button is hidden for the current row (CSS) — can't delete the
      // active chat without rotating to a new one first.
      const delBtn = s.is_current ? "" : `
        <button class="del" type="button" aria-label="Delete chat" title="Delete chat">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6"/>
            <path d="M10 11v6M14 11v6"/>
            <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
          </svg>
        </button>`;
      row.innerHTML = `
        ${delBtn}
        <div class="title">${escapeHtml(title)}</div>
        <div class="meta">
          ${s.is_current ? '<span class="dot" title="Current chat"></span>' : ""}
          <span>${escapeHtml(when)}</span>
          ${turns ? `<span>·</span><span>${turns}</span>` : ""}
        </div>`;
      row.onclick = (e) => {
        // Don't resume when the user clicked the trash button.
        if (e.target.closest(".del")) return;
        resumeSession(s._id, s.is_current);
      };
      const delEl = row.querySelector(".del");
      if (delEl) {
        delEl.onclick = (e) => {
          e.stopPropagation();
          deleteSession(s._id, title);
        };
      }
      host.appendChild(row);
    }
    if (!mongoAvailable) {
      const note = document.createElement("div");
      note.className = "sessions-empty";
      note.textContent = "Mongo sync is off — only the current chat is shown.";
      host.appendChild(note);
    }
  }

  function formatSessionDate(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      const now = new Date();
      const sameDay = d.toDateString() === now.toDateString();
      const opts = sameDay
        ? { hour: "numeric", minute: "2-digit" }
        : { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
      return d.toLocaleString(undefined, opts);
    } catch { return iso; }
  }

  async function resumeSession(sessionId, isCurrent) {
    if (!sessionId) return;
    if (isCurrent) { openSessions(false); return; }
    try {
      const res = await fetch(`/sessions/${encodeURIComponent(sessionId)}/resume`, { method: "POST" });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(txt || ("HTTP " + res.status));
      }
      const data = await res.json();
      replaySessionIntoFeed(data.verbatim || []);
      openSessions(false);
      toast(`Resumed chat: ${data.title || "Untitled"}`);
    } catch (e) {
      toast("Couldn't resume chat: " + (e.message || e), "err");
    }
  }

  function replaySessionIntoFeed(verbatim) {
    // Clear the feed and re-mount the verbatim turns as static cards. This
    // does NOT re-stream — we're just showing what's already been said.
    feed.innerHTML = "";
    for (const turn of verbatim) {
      const u = (turn.user || "").trim();
      const a = (turn.assistant || "").trim();
      if (u) makeCard("user", escapeHtml(u));
      if (a) {
        const aiText = makeCard("hunt", "");
        aiText.innerHTML = renderMarkdownish(a);
      }
    }
    setMode("idle");
  }

  async function deleteSession(sessionId, title) {
    if (!sessionId) return;
    const label = (title || "this chat").slice(0, 60);
    if (!confirm(`Delete "${label}"? This can't be undone.`)) return;
    try {
      const res = await fetch(`/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(txt || ("HTTP " + res.status));
      }
      toast("Chat deleted");
      loadSessions();  // refresh the list
    } catch (e) {
      toast("Couldn't delete: " + (e.message || e), "err");
    }
  }

  async function newChat() {
    try {
      const res = await fetch("/sessions/new", { method: "POST" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      feed.innerHTML = "";
      setMode("idle");
      openSessions(false);
      toast("Started a fresh chat");
    } catch (e) {
      toast("Couldn't start a new chat: " + (e.message || e), "err");
    }
  }
  $("#newChatBtn").onclick = newChat;
  $("#newChatInDrawer").onclick = newChat;

  // Light markdown for replayed assistant turns. The live stream uses the
  // existing renderer; for replay we just want code blocks and line breaks
  // to look right — nothing fancy.
  function renderMarkdownish(text) {
    const esc = escapeHtml(text);
    // Triple-backtick code blocks → <pre><code>
    const withCode = esc.replace(/```([\s\S]*?)```/g, (_, body) =>
      `<pre class="code-block"><code>${body}</code></pre>`);
    return withCode.replace(/\n/g, "<br>");
  }

  // ---- Appearance ----
  function syncSeg(group, value, attr) {
    $$(`#${group} button`).forEach(b => b.classList.toggle("on", b.dataset[attr] === value));
  }
  $$("#themeSeg button").forEach(b => b.onclick = () => {
    prefs.theme = b.dataset.th; savePrefs(); syncSeg("themeSeg", prefs.theme, "th"); applyTheme();
  });
  syncSeg("themeSeg", prefs.theme, "th");

  // ---- Reply style ----
  $$("#replySeg button").forEach(b => b.onclick = () => {
    prefs.replyLength = b.dataset.r; savePrefs(); syncSeg("replySeg", prefs.replyLength, "r");
  });
  syncSeg("replySeg", prefs.replyLength, "r");

  // ---- Toggles ----
  function bindTog(id, key) {
    const t = $(id); if (!t) return;
    t.classList.toggle("on", !!prefs[key]);
    t.onclick = () => {
      prefs[key] = !prefs[key];
      t.classList.toggle("on", prefs[key]);
      savePrefs();
      if (key === "speakReplies" && !prefs[key]) try { window.speechSynthesis.cancel(); } catch {}
    };
  }
  bindTog("#togSpeak", "speakReplies");
  bindTog("#togMemory", "useMemory");
  bindTog("#togLive", "liveSearch");
  bindTog("#togNeural", "neuralBoost");
  bindTog("#togResearch", "researchMode");

  // ---- Gmail integration ----
  // Polls /auth/google/status when the drawer opens to render the right
  // state (not connected / connected as <email> / setup required).
  // Connect button kicks off the OAuth flow in a new tab so the user
  // doesn't lose their place in Hunt while consenting.
  async function refreshGmailStatus() {
    const statusEl = $("#gmailStatus");
    const hintEl = $("#gmailHint");
    const connectBtn = $("#gmailConnect");
    const disconnectBtn = $("#gmailDisconnect");
    const setupHint = $("#gmailSetupHint");
    if (!statusEl) return;
    try {
      const res = await fetch("/auth/google/status");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const s = await res.json();
      if (!s.client_configured) {
        statusEl.textContent = "Setup required";
        statusEl.className = "integ-status integ-status--warn";
        hintEl.textContent = "One-time Google Cloud Console setup is needed before you can connect.";
        connectBtn.disabled = true;
        connectBtn.textContent = "Connect Gmail";
        connectBtn.hidden = false;
        disconnectBtn.hidden = true;
        setupHint.hidden = false;
        return;
      }
      setupHint.hidden = true;
      if (s.connected) {
        statusEl.textContent = "Connected" + (s.email ? " as " + s.email : "");
        statusEl.className = "integ-status integ-status--ok";
        hintEl.textContent = "Hunt can search and read this Gmail account during Research Mode chats.";
        connectBtn.hidden = true;
        disconnectBtn.hidden = false;
        disconnectBtn.disabled = false;
      } else {
        statusEl.textContent = "Not connected";
        statusEl.className = "integ-status integ-status--idle";
        hintEl.textContent = "Click Connect Gmail, sign in, and approve the read-only access.";
        connectBtn.disabled = false;
        connectBtn.textContent = "Connect Gmail";
        connectBtn.hidden = false;
        disconnectBtn.hidden = true;
      }
    } catch (e) {
      statusEl.textContent = "Status unavailable";
      statusEl.className = "integ-status integ-status--warn";
      hintEl.textContent = "Couldn't reach Hunt: " + (e.message || e);
      connectBtn.disabled = true;
    }
  }

  $("#gmailConnect").onclick = () => {
    // Open the OAuth start endpoint in a new tab so the user stays in
    // Hunt while Google's consent flow runs. We can't iframe Google's
    // consent screen — they block X-Frame-Origin to prevent clickjacking.
    window.open("/auth/google/start", "_blank", "noopener");
    // Poll status a few times so the UI flips to "Connected" once the
    // user finishes consent in the other tab.
    let polls = 0;
    const t = setInterval(async () => {
      polls++;
      await refreshGmailStatus();
      const stat = $("#gmailStatus")?.textContent || "";
      if (stat.startsWith("Connected") || polls >= 60) clearInterval(t);
    }, 3000);
  };

  $("#gmailDisconnect").onclick = async () => {
    if (!confirm("Disconnect Gmail? Hunt will lose access; Google revokes the token on their side too.")) return;
    const btn = $("#gmailDisconnect");
    btn.disabled = true;
    btn.textContent = "Disconnecting…";
    try {
      await fetch("/auth/google/revoke", { method: "POST" });
      toast("Gmail disconnected");
    } catch (e) {
      toast("Disconnect failed: " + (e.message || e), "err");
    }
    btn.textContent = "Disconnect";
    await refreshGmailStatus();
  };

  // Decorate the gear click so opening the drawer also re-fetches the
  // Gmail status. Avoids reassigning the openDrawer function declaration.
  $("#gear").onclick = () => { openDrawer(true); refreshGmailStatus(); };

  // ---- Speech rate ----
  const rateEl = $("#speechRate");
  if (rateEl) {
    rateEl.value = Math.round(prefs.speechRate * 100);
    rateEl.oninput = () => {
      prefs.speechRate = (+rateEl.value) / 100;
      savePrefs();
    };
  }

  // ---- Theme init ----
  applyTheme();

  // ---------------- KEYBOARD ----------------
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
    // Space hold = mic
    if (e.key === " " && !e.repeat && !busy) {
      e.preventDefault();
      fab.click();
    }
  });

  // ---------------- INITIAL ----------------
  setMode("idle");

  // Expose for console debugging
  window.HuntV2 = { sendMessage, setMode, orb, prefs };
})();
