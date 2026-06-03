const state = {
  busy: false,
  recording: false,
  audioContext: null,
  mediaStream: null,
  sourceNode: null,
  recorderNode: null,
  analyserNode: null,
  audioBuffers: [],
  inputSampleRate: 48000,
  recordingMode: "manual",
  recordingStartedAt: 0,
  maxDurationTimer: null,
  silenceFrames: 0,
  voiceFrames: 0,
  levelRafId: null,
  peakLevel: 0,
  lastRecorderRms: 0,
  lastRecorderPeak: 0,
  // Speech/stream control:
  //   speaking   — TTS is currently producing audio
  //   paused     — TTS has been paused but not stopped
  //   streamAbort — AbortController for the in-flight /chat/stream fetch
  //   userStoppedStream — true when stop button (not the timeout) aborted
  speaking: false,
  paused: false,
  streamAbort: null,
  userStoppedStream: false,
};

// VAD/recording tuning (browser side)
const RECORDING = {
  MAX_DURATION_MS: 30000,      // hard safety cap
  MIN_DURATION_MS: 600,        // ignore accidental taps shorter than this
  SILENCE_THRESHOLD: 0.012,    // RMS below this = silence
  VOICE_THRESHOLD: 0.025,      // RMS above this = voice
  SILENCE_HANG_MS: 1400,       // auto-stop after this much trailing silence (when speech was detected)
  PRE_SPEECH_GRACE_MS: 6000,   // give the user this long to start speaking
};

const els = {
  apiStatus: document.querySelector("#apiStatus"),
  llmStatus: document.querySelector("#llmStatus"),
  memoryStatus: document.querySelector("#memoryStatus"),
  modelStatus: document.querySelector("#modelStatus"),
  apiPill: document.querySelector("#apiPill"),
  llmPill: document.querySelector("#llmPill"),
  memPill: document.querySelector("#memPill"),
  modelPicker: document.querySelector("#modelPicker"),
  settingsButton: document.querySelector("#settingsButton"),
  settingsPopover: document.querySelector("#settingsPopover"),
  sessionHint: document.querySelector("#sessionHint"),
  messages: document.querySelector("#messages"),
  chatForm: document.querySelector("#chatForm"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  clearButton: document.querySelector("#clearButton"),
  voiceButton: document.querySelector("#voiceButton"),
  orbButton: document.querySelector("#orbButton"),
  orbText: document.querySelector("#orbText"),
  expandButton: document.querySelector("#expandButton"),
  refreshButton: document.querySelector("#refreshButton"),
  speakToggle: document.querySelector("#speakToggle"),
  memoryToggle: document.querySelector("#memoryToggle"),
  liveSearchToggle: document.querySelector("#liveSearchToggle"),
  transcriptReview: document.querySelector("#transcriptReview"),
  transcriptInput: document.querySelector("#transcriptInput"),
  sendTranscriptButton: document.querySelector("#sendTranscriptButton"),
  cancelTranscriptButton: document.querySelector("#cancelTranscriptButton"),
  levelMeter: document.querySelector("#levelMeter"),
  levelMeterFill: document.querySelector("#levelMeterFill"),
  levelMeterLabel: document.querySelector("#levelMeterLabel"),
  micRingFill: document.querySelector("#micRingFill"),
  replyLengthGroup: document.querySelector("#replyLengthGroup"),
  creativitySlider: document.querySelector("#creativitySlider"),
  creativityValue: document.querySelector("#creativityValue"),
  ttsVoiceSelect: document.querySelector("#ttsVoiceSelect"),
  ttsRateSlider: document.querySelector("#ttsRateSlider"),
  ttsRateValue: document.querySelector("#ttsRateValue"),
  showIntentToggle: document.querySelector("#showIntentToggle"),
  memoryButton: document.querySelector("#memoryButton"),
  memoryPanel: document.querySelector("#memoryPanel"),
  memoryPanelScrim: document.querySelector("#memoryPanelScrim"),
  closeMemoryPanelButton: document.querySelector("#closeMemoryPanelButton"),
  openMemoryPanelButton: document.querySelector("#openMemoryPanelButton"),
  memoryRefreshButton: document.querySelector("#memoryRefreshButton"),
  memoryFactsList: document.querySelector("#memoryFactsList"),
  memorySessionStatus: document.querySelector("#memorySessionStatus"),
  memorySessionSummary: document.querySelector("#memorySessionSummary"),
  memoryRecentList: document.querySelector("#memoryRecentList"),
  wipeProfileFactsButton: document.querySelector("#wipeProfileFactsButton"),
  exportTranscriptButton: document.querySelector("#exportTranscriptButton"),
  micDeviceSelect: document.querySelector("#micDeviceSelect"),
  micPermissionButton: document.querySelector("#micPermissionButton"),
  profileForm: document.querySelector("#profileForm"),
  profileStatus: document.querySelector("#profileStatus"),
  projectPill: document.querySelector("#projectPill"),
  projectPillLabel: document.querySelector("#projectPillLabel"),
  projectPopover: document.querySelector("#projectPopover"),
  projectPickerList: document.querySelector("#projectPickerList"),
  clearProjectButton: document.querySelector("#clearProjectButton"),
  openProjectsPanelButton: document.querySelector("#openProjectsPanelButton"),
  newProjectButton: document.querySelector("#newProjectButton"),
  projectList: document.querySelector("#projectList"),
  projectDetail: document.querySelector("#projectDetail"),
  projectDetailBack: document.querySelector("#projectDetailBack"),
  projectDetailName: document.querySelector("#projectDetailName"),
  projectDetailStack: document.querySelector("#projectDetailStack"),
  projectDetailStatus: document.querySelector("#projectDetailStatus"),
  projectDetailDescription: document.querySelector("#projectDetailDescription"),
  projectDetailNotes: document.querySelector("#projectDetailNotes"),
  projectDetailTasks: document.querySelector("#projectDetailTasks"),
  projectDetailTaskCount: document.querySelector("#projectDetailTaskCount"),
  projectDetailTaskForm: document.querySelector("#projectDetailTaskForm"),
  projectDetailTaskInput: document.querySelector("#projectDetailTaskInput"),
  projectDetailActivate: document.querySelector("#projectDetailActivate"),
  projectDetailDelete: document.querySelector("#projectDetailDelete"),
  actionPoliciesList: document.querySelector("#actionPoliciesList"),
  actionPoliciesBadge: document.querySelector("#actionPoliciesBadge"),
  actionHistoryList: document.querySelector("#actionHistoryList"),
  actionHistoryRefresh: document.querySelector("#actionHistoryRefresh"),
  clearActionHistoryButton: document.querySelector("#clearActionHistoryButton"),
  sessionsButton: document.querySelector("#sessionsButton"),
  sessionsPanel: document.querySelector("#sessionsPanel"),
  sessionsPanelScrim: document.querySelector("#sessionsPanelScrim"),
  closeSessionsPanelButton: document.querySelector("#closeSessionsPanelButton"),
  newSessionButton: document.querySelector("#newSessionButton"),
  sessionsList: document.querySelector("#sessionsList"),
  sessionsStatus: document.querySelector("#sessionsStatus"),
  mongoDot: document.querySelector("#mongoDot"),
  muteButton: document.querySelector("#muteButton"),
  muteIconOn: document.querySelector("#muteIconOn"),
  muteIconOff: document.querySelector("#muteIconOff"),
  speechControls: document.querySelector("#speechControls"),
  speechStatus: document.querySelector("#speechStatus"),
  pauseSpeechButton: document.querySelector("#pauseSpeechButton"),
  pauseSpeechLabel: document.querySelector("#pauseSpeechLabel"),
  stopSpeechButton: document.querySelector("#stopSpeechButton"),
  roleBrainSelect: document.querySelector("#roleBrainSelect"),
  roleCoderSelect: document.querySelector("#roleCoderSelect"),
  roleFastSelect: document.querySelector("#roleFastSelect"),
  roleVisionSelect: document.querySelector("#roleVisionSelect"),
};

const MIC_RING_CIRCUMFERENCE = 2 * Math.PI * 94; // matches <circle r="94">
let modelsLoaded = false;

// User preferences live in localStorage so they survive reload without a
// backend roundtrip. The backend only learns about response_length per request.
const PREFS_KEY = "doondy.prefs.v1";
const prefs = loadPrefs();

// ---------- Console logging ----------
// Centralized so it's easy to silence by flipping HUNT_DEBUG.
// Use a single style so the logs are easy to spot in the DevTools console.
const HUNT_DEBUG = true;
const LOG_PREFIX = "%c[Hunt]";
const LOG_STYLE  = "color:#c45a3a;font-weight:700;";

const hlog = {
  info:  (...args) => HUNT_DEBUG && console.log(LOG_PREFIX, LOG_STYLE, ...args),
  warn:  (...args) => HUNT_DEBUG && console.warn(LOG_PREFIX, LOG_STYLE, ...args),
  error: (...args) => HUNT_DEBUG && console.error(LOG_PREFIX, LOG_STYLE, ...args),
  group: (label)   => HUNT_DEBUG && console.groupCollapsed(LOG_PREFIX + " " + label, LOG_STYLE),
  end:   ()        => HUNT_DEBUG && console.groupEnd(),
};

function loadPrefs() {
  const defaults = {
    replyLength: "normal",
    creativity: 0.60,
    ttsVoice: "",
    ttsRate: 1.0,
    showIntent: true,
    micDeviceId: "",  // empty = browser/OS default
    theme: "system",  // "light" | "dark" | "system"
    collapsedSections: [],  // section ids that are collapsed
  };
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return defaults;
    return { ...defaults, ...JSON.parse(raw) };
  } catch {
    return defaults;
  }
}

function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    // Quota/private-mode failures are non-fatal.
  }
}

function setHint(text) {
  els.sessionHint.textContent = text;
}

function addMessage(role, text, label = "") {
  const item = document.createElement("article");
  item.className = `message ${role}`;

  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = label || (role === "user" ? "You" : role === "system" ? "System" : "Hunt");

  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;

  item.append(meta, body);

  if (role === "assistant") {
    const actions = document.createElement("div");
    actions.className = "message-actions";

    const replay = document.createElement("button");
    replay.type = "button";
    replay.className = "msg-action";
    replay.title = "Speak this reply";
    replay.setAttribute("aria-label", "Speak this reply");
    replay.innerHTML =
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>' +
      '<path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>' +
      '<path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>' +
      '</svg>';
    replay.addEventListener("click", () => {
      replay.classList.add("speaking");
      speak(body.textContent || "", { force: true });
      // Reset the visual after the speech finishes — the global state machine
      // owns the actual playback, so just clear the highlight after the
      // estimated duration. (Conservative estimate: ~12 chars/sec.)
      const ms = Math.min(20000, Math.max(1500, (body.textContent || "").length * 80));
      setTimeout(() => replay.classList.remove("speaking"), ms);
    });

    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "msg-action";
    copy.title = "Copy reply";
    copy.setAttribute("aria-label", "Copy reply");
    copy.innerHTML =
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<rect x="9" y="9" width="13" height="13" rx="2"></rect>' +
      '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>' +
      '</svg>';
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(body.textContent || "");
        copy.classList.add("copied");
        setTimeout(() => copy.classList.remove("copied"), 1400);
      } catch {
        // Quietly ignore — clipboard can be blocked by permissions.
      }
    });

    // Diagnostics — populated by sendMessage on `done`. Until then, hidden.
    const info = document.createElement("button");
    info.type = "button";
    info.className = "msg-action msg-info";
    info.title = "Reply diagnostics";
    info.setAttribute("aria-label", "Show reply diagnostics");
    info.hidden = true;  // unhidden once data lands
    info.innerHTML =
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<circle cx="12" cy="12" r="9"></circle>' +
      '<line x1="12" y1="11" x2="12" y2="17"></line>' +
      '<circle cx="12" cy="7.5" r="0.6" fill="currentColor"></circle>' +
      '</svg>';
    info.addEventListener("click", () => toggleDiagnosticsPanel(item));

    actions.append(replay, copy, info);
    item.append(actions);
  }

  els.messages.append(item);
  els.messages.scrollTop = els.messages.scrollHeight;
  return item;
}

// ---------- Per-message diagnostics ----------
// `sendMessage` attaches a metrics object to the bubble element via
// `el._huntDiagnostics`. The info button on each assistant message renders
// it in an inline panel below the body.

function attachDiagnostics(messageEl, diagnostics) {
  if (!messageEl) return;
  messageEl._huntDiagnostics = diagnostics;
  const info = messageEl.querySelector(".msg-info");
  if (info) info.hidden = false;
}

function fmtMs(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function toggleDiagnosticsPanel(messageEl) {
  if (!messageEl) return;
  const existing = messageEl.querySelector(".diagnostics-panel");
  if (existing) { existing.remove(); return; }
  const d = messageEl._huntDiagnostics;
  if (!d) return;
  const panel = document.createElement("div");
  panel.className = "diagnostics-panel";
  const rows = [
    ["Model",   d.model || "—"],
    ["Intent",  d.intent || "—"],
    ["Role",    d.role || "—"],
    ["TTFB",    fmtMs(d.ttfb_ms)],
    ["Total",   fmtMs(d.duration_ms)],
    ["Tokens",  d.tokens != null ? String(d.tokens) : "—"],
    ["Chars",   d.response_chars != null ? String(d.response_chars) : "—"],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "diagnostics-row";
    const k = document.createElement("span");
    k.className = "diagnostics-key";
    k.textContent = label;
    const v = document.createElement("span");
    v.className = "diagnostics-value";
    v.textContent = value;
    row.append(k, v);
    panel.append(row);
  }
  messageEl.append(panel);
}

function setBusy(isBusy) {
  state.busy = isBusy;
  els.sendButton.disabled = isBusy;
  els.messageInput.disabled = isBusy;
  const voiceDisabled = isBusy || !navigator.mediaDevices?.getUserMedia;
  els.voiceButton.disabled = voiceDisabled;
  els.orbButton.disabled = voiceDisabled;
  els.orbButton.classList.toggle("thinking", isBusy);
  els.orbText.textContent = isBusy ? "..." : "Hi";
  setHint(isBusy ? "Hunt is thinking..." : "Ready for text or voice input.");
}

function setRecording(isRecording) {
  state.recording = isRecording;
  els.voiceButton.classList.toggle("listening", isRecording);
  els.orbButton.classList.toggle("listening", isRecording);
  els.voiceButton.querySelector("span:last-child").textContent = isRecording ? "Tap to stop" : "Tap to speak";
  els.orbText.textContent = isRecording ? "..." : "Hi";
  setHint(isRecording ? "Listening... tap again to stop." : "Ready for text or voice input.");
  if (els.levelMeter) {
    els.levelMeter.classList.toggle("active", isRecording);
  }
  if (!isRecording) {
    setLevelMeter(0);
  }
}

function setLevelMeter(level) {
  const pct = Math.min(100, Math.max(0, level * 100));
  if (els.levelMeterFill) {
    els.levelMeterFill.style.width = `${pct}%`;
  }
  if (els.micRingFill) {
    const offset = MIC_RING_CIRCUMFERENCE * (1 - pct / 100);
    els.micRingFill.style.strokeDashoffset = `${offset}`;
  }
  if (els.levelMeterLabel) {
    els.levelMeterLabel.textContent = state.recording
      ? `Mic ${Math.round(pct)}%`
      : "Mic idle";
  }
}

function speak(text, options = {}) {
  // `options.force = true` bypasses the global Speak toggle — used by the
  // per-message Replay button so it still works when auto-speech is off.
  if (!options.force && !els.speakToggle.checked) return;
  if (!("speechSynthesis" in window)) return;
  const clean = cleanSpeechText(text || "");
  if (!clean) return;

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.rate = Number(prefs.ttsRate) || 1;
  utterance.pitch = 1;
  if (prefs.ttsVoice) {
    const voice = (window.speechSynthesis.getVoices() || [])
      .find((v) => v.voiceURI === prefs.ttsVoice || v.name === prefs.ttsVoice);
    if (voice) utterance.voice = voice;
  }
  utterance.onstart = () => {
    state.speaking = true;
    state.paused = false;
    updateSpeechControls();
  };
  utterance.onend = () => {
    state.speaking = false;
    state.paused = false;
    updateSpeechControls();
  };
  utterance.onerror = utterance.onend;
  window.speechSynthesis.speak(utterance);
  // Chrome fires onstart synchronously in some cases and not in others; flip
  // the flag eagerly so the controls appear without waiting on the callback.
  state.speaking = true;
  state.paused = false;
  updateSpeechControls();
}

function stopSpeech() {
  if ("speechSynthesis" in window) {
    try { window.speechSynthesis.cancel(); } catch {}
  }
  state.speechBuffer = "";
  state.speaking = false;
  state.paused = false;
  updateSpeechControls();
}

// Speak text WITHOUT cancelling whatever is already queued. Used during
// streaming so each finished sentence starts playing while later tokens are
// still arriving — audio and text show up together instead of "text first,
// then voice."
function speakIncremental(text, options = {}) {
  if (!options.force && !els.speakToggle.checked) return;
  if (!("speechSynthesis" in window)) return;
  const clean = cleanSpeechText(text || "");
  if (!clean) return;

  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.rate = Number(prefs.ttsRate) || 1;
  utterance.pitch = 1;
  if (prefs.ttsVoice) {
    const voice = (window.speechSynthesis.getVoices() || [])
      .find((v) => v.voiceURI === prefs.ttsVoice || v.name === prefs.ttsVoice);
    if (voice) utterance.voice = voice;
  }
  utterance.onstart = () => {
    state.speaking = true;
    state.paused = false;
    updateSpeechControls();
  };
  // Only clear the speaking flag once the queue is fully drained — multiple
  // sentences can be queued back-to-back during a stream.
  utterance.onend = () => {
    setTimeout(() => {
      const ss = window.speechSynthesis;
      if (!ss.pending && !ss.speaking) {
        state.speaking = false;
        state.paused = false;
        updateSpeechControls();
      }
    }, 40);
  };
  utterance.onerror = utterance.onend;
  window.speechSynthesis.speak(utterance);
  state.speaking = true;
  state.paused = false;
  updateSpeechControls();
}

// ---------- Code-card rendering for coder responses ----------
// During streaming we keep the bubble as plain text so partial fences don't
// flicker. On `done`, we re-parse and replace the body with text + code cards.

function renderMessageBody(bodyEl, text) {
  bodyEl.innerHTML = "";
  const fence = /```([a-z0-9+\-_.]*)\s*\n([\s\S]*?)```/gi;
  let last = 0;
  let m;
  let appendedAny = false;
  while ((m = fence.exec(text))) {
    if (m.index > last) {
      appendTextChunk(bodyEl, text.slice(last, m.index));
    }
    bodyEl.appendChild(buildCodeCard(m[1] || "code", m[2].replace(/\s+$/, "")));
    appendedAny = true;
    last = m.index + m[0].length;
  }
  if (last < text.length) appendTextChunk(bodyEl, text.slice(last));
  // If no fence was found, fall back to plain text — same as before.
  if (!appendedAny && bodyEl.children.length === 0) bodyEl.textContent = text;
}

function appendTextChunk(parent, text) {
  const trimmed = text.replace(/^\s+|\s+$/g, "");
  if (!trimmed) return;
  const span = document.createElement("div");
  span.className = "msg-text-chunk";
  span.textContent = trimmed;
  parent.appendChild(span);
}

function buildCodeCard(lang, code) {
  const card = document.createElement("div");
  card.className = "code-card";

  const head = document.createElement("div");
  head.className = "code-card-head";
  const langEl = document.createElement("span");
  langEl.className = "code-lang";
  langEl.textContent = (lang || "code").toUpperCase();
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "code-copy";
  copy.textContent = "Copy";
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(code);
      copy.textContent = "Copied";
      setTimeout(() => { copy.textContent = "Copy"; }, 1400);
    } catch {
      copy.textContent = "Failed";
      setTimeout(() => { copy.textContent = "Copy"; }, 1400);
    }
  });
  head.append(langEl, copy);
  card.append(head);

  const pre = document.createElement("pre");
  pre.className = "code-body";
  pre.textContent = code;
  card.append(pre);
  return card;
}

// Follow-up actions appended to coder replies. Each chip queues a follow-up
// message that asks the assistant to act on the code from the previous turn.
const CODER_SUGGESTIONS = [
  { label: "Explain",      prompt: "Explain the code you just wrote, step by step, briefly." },
  { label: "Optimize",     prompt: "Optimize the code you just wrote for performance and readability. Output the new code only." },
  { label: "Add comments", prompt: "Add inline comments to the code you just wrote, explaining each block. Output the commented code only." },
  { label: "Add tests",    prompt: "Write unit tests for the code you just wrote. Output the test code only." },
];

// ---------- ApprovalChips (Phase B + reused by Phase C) ----------
// Renders a row of chips below an assistant message. Each chip resolves to a
// choice value passed to onChoice(). If `dropdown` is given (Phase C profile
// promotion), the primary chip is split into a button + select. After a
// click, the chips are replaced with a single result note.
//
// spec = {
//   prompt:   "Open Chrome?",
//   options:  [{label, value, kind: 'primary'|'neutral'|'subtle'}],
//   onChoice: async (value, dropdownValue?) => { note: "Allowed." }
// }

function renderApprovalChips(messageEl, spec) {
  if (!messageEl) return null;
  const row = document.createElement("div");
  row.className = "approval-chips";

  if (spec.prompt) {
    const p = document.createElement("p");
    p.className = "approval-prompt";
    p.textContent = spec.prompt;
    row.append(p);
  }

  const chipsRow = document.createElement("div");
  chipsRow.className = "approval-chips-row";

  for (const opt of (spec.options || [])) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `approval-chip approval-chip--${opt.kind || "neutral"}`;
    btn.textContent = opt.label;
    btn.addEventListener("click", async () => {
      // Disable all chips so a fast-clicker can't fire two decisions.
      for (const b of chipsRow.querySelectorAll("button")) b.disabled = true;
      row.classList.add("approval-deciding");
      try {
        const result = await spec.onChoice(opt.value);
        showApprovalResult(row, result?.note || `Decision: ${opt.value}`, "ok");
      } catch (e) {
        showApprovalResult(row, `Failed: ${e.message}`, "error");
      } finally {
        row.classList.remove("approval-deciding");
      }
    });
    chipsRow.append(btn);
  }

  row.append(chipsRow);

  // Optional promote sub-row (Phase C: profile-field dropdown).
  // spec.promote = { label, options: [{value,label}], selected, onPromote(value) }
  if (spec.promote && Array.isArray(spec.promote.options) && spec.promote.options.length) {
    const promote = document.createElement("div");
    promote.className = "approval-promote";
    const label = document.createElement("span");
    label.className = "approval-promote-label";
    label.textContent = spec.promote.label || "Save as profile field";
    const select = document.createElement("select");
    select.className = "approval-promote-select";
    for (const opt of spec.promote.options) {
      const o = document.createElement("option");
      o.value = opt.value;
      o.textContent = opt.label;
      if (opt.value === spec.promote.selected) o.selected = true;
      select.append(o);
    }
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "approval-chip approval-chip--primary approval-promote-apply";
    apply.textContent = "Apply";
    apply.addEventListener("click", async () => {
      const value = select.value;
      // Disable everything during the request so chips can't be double-fired.
      apply.disabled = true; select.disabled = true;
      for (const b of chipsRow.querySelectorAll("button")) b.disabled = true;
      row.classList.add("approval-deciding");
      try {
        const result = await spec.promote.onPromote(value);
        showApprovalResult(row, result?.note || `Promoted to ${value}`, "ok");
      } catch (e) {
        showApprovalResult(row, `Failed: ${e.message}`, "error");
      } finally {
        row.classList.remove("approval-deciding");
      }
    });
    promote.append(label, select, apply);
    row.append(promote);
  }

  messageEl.append(row);
  return row;
}

function showApprovalResult(rowEl, text, kind) {
  rowEl.classList.add("approval-decided");
  const existing = rowEl.querySelector(".approval-chips-row");
  if (existing) existing.remove();
  const note = document.createElement("p");
  note.className = `approval-result approval-result--${kind || "ok"}`;
  note.textContent = text;
  rowEl.append(note);
}

// Phase B: respond to action_proposal / action_executed events emitted from
// the backend after `done`. The chip POSTs the user's choice; the backend
// runs the action and stores it in history.

async function handleActionProposalEvent(messageEl, event) {
  hlog.info("action_proposal", { action: event.action, params: event.params });
  renderApprovalChips(messageEl, {
    prompt: event.prompt || `Run ${event.action}?`,
    options: event.options || [
      { label: "Allow", value: "allow", kind: "primary" },
      { label: "Deny", value: "deny", kind: "neutral" },
      { label: "Always allow", value: "always", kind: "subtle" },
    ],
    onChoice: async (decision) => {
      const r = await fetch(`/actions/${encodeURIComponent(event.id)}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      hlog.info("action decided", { decision, ...data });
      // History row & policy badge change immediately — refresh in background.
      loadActionsPanel();
      let note;
      if (data.status === "denied") {
        note = "Denied. Won't run.";
      } else if (data.status === "completed") {
        note = data.policy_set
          ? `Done. Will auto-allow ${event.action} from now on.`
          : `Done. ${event.action} completed.`;
      } else if (data.status === "failed") {
        note = `Failed: ${data.detail || "unknown error"}`;
      } else {
        note = `Status: ${data.status}`;
      }
      return { note };
    },
  });
}

// ---------- Actions section in settings popover ----------

const ACTION_LABELS = {
  open_app: "Open app",
  close_app: "Close app",
  search: "Web search",
  open_browser: "Open URL",
};

function formatActionTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function summarizeParams(params) {
  if (!params) return "";
  const v = params.app_name || params.query || params.url || "";
  return String(v).slice(0, 40);
}

async function loadActionsPanel() {
  if (!els.actionHistoryList && !els.actionPoliciesList) return;
  try {
    const [hist, pol] = await Promise.all([
      fetch("/actions").then((r) => r.json()),
      fetch("/actions/policies").then((r) => r.json()),
    ]);
    renderActionPolicies(pol);
    renderActionHistory(hist.actions || []);
  } catch (e) {
    hlog.warn("loadActionsPanel failed", e?.message);
  }
}

function renderActionPolicies(data) {
  if (!els.actionPoliciesList) return;
  const policies = data?.policies || {};
  const entries = Object.entries(policies).filter(([, v]) => v === "always");
  els.actionPoliciesList.innerHTML = "";
  if (els.actionPoliciesBadge) {
    els.actionPoliciesBadge.hidden = entries.length === 0;
    els.actionPoliciesBadge.textContent = String(entries.length);
  }
  if (!entries.length) {
    const note = document.createElement("p");
    note.className = "popover-hint";
    note.textContent = "No auto-approval rules. Click \"Always allow\" on a chip to add one.";
    els.actionPoliciesList.append(note);
    return;
  }
  for (const [action] of entries) {
    const row = document.createElement("div");
    row.className = "action-policy-row";
    const label = document.createElement("span");
    label.innerHTML = `Auto-allow <strong>${ACTION_LABELS[action] || action}</strong>`;
    const revoke = document.createElement("button");
    revoke.type = "button";
    revoke.className = "action-policy-revoke";
    revoke.textContent = "Revoke";
    revoke.addEventListener("click", async () => {
      try {
        await fetch(`/actions/policies/${encodeURIComponent(action)}`, { method: "DELETE" });
        loadActionsPanel();
      } catch (e) {
        hlog.error("revoke policy failed", e?.message);
      }
    });
    row.append(label, revoke);
    els.actionPoliciesList.append(row);
  }
}

function renderActionHistory(entries) {
  if (!els.actionHistoryList) return;
  els.actionHistoryList.innerHTML = "";
  if (!entries.length) {
    const empty = document.createElement("li");
    empty.className = "memory-empty";
    empty.textContent = "No actions yet. Try \"open notepad\".";
    els.actionHistoryList.append(empty);
    return;
  }
  for (const entry of entries.slice(0, 10)) {
    const li = document.createElement("li");
    const status = (entry.status || "").toLowerCase();
    li.className = `action-row is-${entry.decision === "deny" ? "denied" : status}`;
    const dot = document.createElement("span");
    dot.className = "action-dot";
    const label = document.createElement("span");
    label.className = "action-label";
    label.innerHTML = `${ACTION_LABELS[entry.action] || entry.action} <small>${summarizeParams(entry.params)}</small>`;
    const time = document.createElement("span");
    time.className = "action-time";
    time.textContent = formatActionTime(entry.timestamp);
    li.append(dot, label, time);
    els.actionHistoryList.append(li);
  }
}

els.actionHistoryRefresh?.addEventListener("click", loadActionsPanel);
els.clearActionHistoryButton?.addEventListener("click", async () => {
  if (!confirm("Clear action history? Auto-approval rules will be kept.")) return;
  try {
    await fetch("/actions", { method: "DELETE" });
    loadActionsPanel();
  } catch (e) {
    hlog.error("clear action history failed", e?.message);
  }
});

// Re-load the actions panel whenever the settings popover opens, and after
// any chip decision so the new entry shows up immediately.
const _origToggleDecisionRefresh = () => loadActionsPanel();

async function handleFactProposalEvent(messageEl, event) {
  hlog.info("fact_proposal", { pattern: event.pattern, text: event.text });

  // Build the promote dropdown: pre-select the suggested profile field if any,
  // and list the remaining 7 fields underneath so the user can re-target.
  const suggested = event.profile_field;
  const orderedFields = suggested
    ? [suggested, ...PROFILE_FIELDS.filter((f) => f !== suggested)]
    : PROFILE_FIELDS.slice();
  const promoteOptions = orderedFields.map((f) => ({
    value: f,
    label: PROFILE_FIELD_LABELS[f] || f,
  }));

  renderApprovalChips(messageEl, {
    prompt: event.prompt || `Save: ${event.text}?`,
    options: event.options || [
      { label: "Save", value: "save", kind: "primary" },
      { label: "Skip", value: "skip", kind: "neutral" },
      { label: "Always save these", value: "always_save", kind: "subtle" },
    ],
    onChoice: async (decision) => {
      const r = await fetch(`/memory/facts/${encodeURIComponent(event.id)}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      hlog.info("fact decided", { decision, ...data });
      // Side-panel refresh so the new fact / new policy appears immediately.
      loadProfileFacts();
      if (decision === "save") return { note: "Saved as a fact." };
      if (decision === "always_save") return { note: `Saved. Always save \"${event.pattern}\" patterns from now on.` };
      if (decision === "skip") return { note: "Skipped." };
      return { note: `Status: ${data.status}` };
    },
    promote: {
      label: "Or save to profile field:",
      selected: suggested,
      options: promoteOptions,
      onPromote: async (field) => {
        const r = await fetch(`/memory/facts/${encodeURIComponent(event.id)}/decide`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision: "promote", profile_field: field }),
        });
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        hlog.info("fact promoted", { field, ...data });
        // Refresh both the profile form and the facts list.
        loadProfile();
        loadProfileFacts();
        return { note: `Saved to profile.${field}: ${data.stored}` };
      },
    },
  });
}

// ---------- Macro data (Phase F: voice macros) ----------
// Dispatches per-macro renderers. Each macro card sits BEFORE the assistant
// text bubble in the conversation, similar to how code cards sit before the
// trailing explanation. The script is still streamed token-by-token (handled
// by the existing token handler) so TTS rides alongside as usual.

function handleMacroDataEvent(messageEl, event) {
  hlog.info("macro_data", { macro: event.macro, side_effects: event.side_effects });
  const data = event.structured || {};
  let card = null;
  if (event.macro === "morning_brief") {
    card = renderBriefingCard(data);
  } else if (event.macro === "read_open_tasks") {
    card = renderTasksCard(data);
  } else if (event.macro === "wrap_up_session") {
    card = renderWrapUpCard(data);
  }
  if (!card) return;
  // Mount the card BEFORE the message body so the visual lands above the
  // streaming script — matches the "card-on-top, explanation-below" pattern
  // already established by code cards.
  const body = messageEl.querySelector(".message-body");
  if (body) messageEl.insertBefore(card, body);
  else messageEl.appendChild(card);
}

function renderBriefingCard(b) {
  const root = document.createElement("section");
  root.className = "macro-card macro-card--briefing";

  const head = document.createElement("header");
  head.className = "macro-card-head";
  const title = document.createElement("span");
  title.className = "macro-card-title";
  title.textContent = "Briefing";
  const stamp = document.createElement("span");
  stamp.className = "macro-card-stamp";
  stamp.textContent = `${b.date_human || ""} · ${b.time_human || ""}`;
  head.append(title, stamp);
  root.append(head);

  const grid = document.createElement("div");
  grid.className = "macro-card-grid";

  // Active project
  const proj = document.createElement("div");
  proj.className = "macro-cell";
  if (b.active_project) {
    const total = b.open_task_total || 0;
    proj.innerHTML =
      '<span class="macro-cell-title">Active project</span>' +
      `<div class="macro-cell-headline">${escAttr(b.active_project.name)}</div>` +
      (b.active_project.stack
        ? `<span class="macro-cell-tag">${escAttr(b.active_project.stack)}</span>`
        : "") +
      `<span class="macro-cell-meta">${total} open task${total === 1 ? "" : "s"}</span>`;
    if (b.top_open_tasks?.length) {
      const list = document.createElement("ul");
      list.className = "macro-cell-list";
      b.top_open_tasks.slice(0, 3).forEach((t) => {
        const li = document.createElement("li");
        li.textContent = t.text;
        list.append(li);
      });
      proj.append(list);
    }
  } else {
    proj.innerHTML =
      '<span class="macro-cell-title">Active project</span>' +
      '<div class="macro-cell-headline">—</div>' +
      '<span class="macro-cell-meta">No project active</span>';
  }
  grid.append(proj);

  // Recent actions
  const actions = document.createElement("div");
  actions.className = "macro-cell";
  actions.innerHTML = '<span class="macro-cell-title">Recent</span>';
  if (b.recent_actions?.length) {
    const list = document.createElement("ul");
    list.className = "macro-cell-list";
    b.recent_actions.slice(0, 3).forEach((a) => {
      const li = document.createElement("li");
      const label = (a.action || "").replace(/_/g, " ");
      const param = a.params?.app_name || a.params?.query || a.params?.url || "";
      li.textContent = param ? `${label} · ${param}` : label;
      list.append(li);
    });
    actions.append(list);
  } else {
    const meta = document.createElement("span");
    meta.className = "macro-cell-meta";
    meta.textContent = "No recent actions.";
    actions.append(meta);
  }
  grid.append(actions);

  // This chat + sync
  const chat = document.createElement("div");
  chat.className = "macro-cell";
  const cloudLabel =
    b.cloud_sync === "on" ? "✓ Cloud synced"
      : b.cloud_sync === "degraded" ? "⚠ Sync degraded"
      : "Cloud sync off";
  chat.innerHTML =
    '<span class="macro-cell-title">This chat</span>' +
    `<div class="macro-cell-headline">${b.session_turns ?? 0} / ${b.session_turn_max ?? 10}</div>` +
    `<span class="macro-cell-meta">${cloudLabel}</span>`;
  if (b.session_summary_preview) {
    const note = document.createElement("p");
    note.className = "macro-cell-note";
    note.textContent = b.session_summary_preview;
    chat.append(note);
  }
  grid.append(chat);

  root.append(grid);
  return root;
}

function renderTasksCard(d) {
  const root = document.createElement("section");
  root.className = "macro-card macro-card--tasks";
  const head = document.createElement("header");
  head.className = "macro-card-head";
  const title = document.createElement("span");
  title.className = "macro-card-title";
  title.textContent = "Open tasks";
  const proj = document.createElement("span");
  proj.className = "macro-card-stamp";
  proj.textContent = d.project_name || "(no active project)";
  head.append(title, proj);
  root.append(head);

  const list = document.createElement("ol");
  list.className = "macro-task-list";
  (d.tasks || []).forEach((t, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="macro-task-i">${i + 1}.</span> ${escAttr(t.text)}`;
    list.append(li);
  });
  if (!d.tasks?.length) {
    const empty = document.createElement("p");
    empty.className = "macro-cell-meta";
    empty.textContent = "Nothing left here.";
    root.append(empty);
  } else {
    root.append(list);
  }
  return root;
}

function renderWrapUpCard(d) {
  const root = document.createElement("section");
  root.className = "macro-card macro-card--wrap";
  const head = document.createElement("header");
  head.className = "macro-card-head";
  const title = document.createElement("span");
  title.className = "macro-card-title";
  title.textContent = "Session wrap";
  const meta = document.createElement("span");
  meta.className = "macro-card-stamp";
  meta.textContent = `${d.turn_count || 0} turn${d.turn_count === 1 ? "" : "s"}`;
  head.append(title, meta);
  root.append(head);

  if (d.summary) {
    const summary = document.createElement("p");
    summary.className = "macro-summary";
    summary.textContent = d.summary;
    root.append(summary);
  }
  if (d.project_name) {
    const tag = document.createElement("span");
    tag.className = "macro-cell-tag";
    tag.textContent = `Appended to ${d.project_name}`;
    root.append(tag);
  }
  return root;
}

// Small helper — local copy of the existing auroraEscape pattern so this
// block stays self-contained; the function is HTML-context-safe for the
// strings we render (project/action labels, no user-uploaded HTML).
function escAttr(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function handleActionExecutedEvent(messageEl, event) {
  hlog.info("action_executed (auto-approved)", event);
  const row = document.createElement("div");
  row.className = "approval-chips approval-decided";
  const note = document.createElement("p");
  note.className = `approval-result approval-result--${event.result?.status === "completed" ? "ok" : "error"}`;
  note.textContent = event.result?.status === "completed"
    ? `Auto-allowed: ${event.action} completed.`
    : `Auto-allowed but failed: ${event.result?.detail || "unknown"}`;
  row.append(note);
  messageEl.append(row);
  loadActionsPanel();
}

function appendCoderSuggestions(messageEl) {
  if (messageEl.querySelector(".suggestion-chips")) return;  // idempotent
  const row = document.createElement("div");
  row.className = "suggestion-chips";
  for (const s of CODER_SUGGESTIONS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "suggestion-chip";
    btn.textContent = s.label;
    btn.addEventListener("click", () => sendMessage(s.prompt, { role: "coder" }));
    row.append(btn);
  }
  messageEl.appendChild(row);
}

// Pull the first complete sentence out of the buffer. Returns null when nothing
// is ready yet. We only break on REAL sentence terminators (.!?) — breaking on
// commas/semicolons made TTS choppy (every clause restarted the speech engine),
// which the user reported as "lagging".
const SENTENCE_RE = /^[\s\S]*?[.!?](?=\s|$)/;
function extractFirstSentence(buffer) {
  // Hold off until the buffer is comfortably long. Tiny utterances ("Sure.")
  // mid-stream make the voice sound jittery.
  if (buffer.length < 20) return null;
  const m = buffer.match(SENTENCE_RE);
  if (!m) return null;
  return m[0];
}

function togglePauseSpeech() {
  if (!("speechSynthesis" in window)) return;
  if (!state.speaking) return;
  if (state.paused) {
    try { window.speechSynthesis.resume(); } catch {}
    state.paused = false;
  } else {
    try { window.speechSynthesis.pause(); } catch {}
    state.paused = true;
  }
  updateSpeechControls();
}

function stopStream() {
  if (state.streamAbort) {
    state.userStoppedStream = true;
    hlog.warn("stopStream() — aborting in-flight /chat/stream");
    try { state.streamAbort.abort(); } catch {}
    state.streamAbort = null;
  }
  updateSpeechControls();
}

function stopAll() {
  // Universal cancel — used by the Stop button and the Esc shortcut.
  stopStream();
  stopSpeech();
}

function updateSpeechControls() {
  if (!els.speechControls) return;
  const visible = state.speaking || state.streamAbort != null;
  els.speechControls.hidden = !visible;
  if (els.speechStatus) {
    els.speechStatus.textContent = state.streamAbort
      ? "Generating…"
      : state.paused ? "Paused" : "Speaking…";
  }
  if (els.pauseSpeechButton) {
    // Pause/Resume only makes sense for TTS, not for in-flight generation.
    els.pauseSpeechButton.disabled = !state.speaking;
    if (els.pauseSpeechLabel) {
      els.pauseSpeechLabel.textContent = state.paused ? "Resume" : "Pause";
    }
    els.pauseSpeechButton.setAttribute(
      "aria-label",
      state.paused ? "Resume speaking" : "Pause speaking",
    );
  }
}

els.pauseSpeechButton?.addEventListener("click", togglePauseSpeech);
els.stopSpeechButton?.addEventListener("click", stopAll);

function cleanSpeechText(text) {
  return text
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/```[\s\S]*?```/g, " code omitted ")
    .replace(/[*_`#>\[\](){}]/g, " ")
    .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
}

function setPillState(pill, state) {
  if (!pill) return;
  pill.classList.remove("ok", "warn", "bad");
  if (state) pill.classList.add(state);
}

async function refreshStatus() {
  let healthOk = false;
  let llmOk = false;
  let memoryOk = false;
  try {
    const health = await fetch("/health").then((res) => res.json());
    healthOk = true;
    llmOk = !!health.llm_connected;
    memoryOk = !!health.memory_available;
    els.apiStatus.textContent = health.status === "running" ? "Running" : (health.status || "Running");
    els.llmStatus.textContent = llmOk ? "Connected" : "Offline";
    els.memoryStatus.textContent = memoryOk ? "Ready" : "Offline";
  } catch {
    els.apiStatus.textContent = "Offline";
    els.llmStatus.textContent = "Unknown";
    els.memoryStatus.textContent = "Unknown";
  }
  setPillState(els.apiPill, healthOk ? "ok" : "bad");
  setPillState(els.llmPill, llmOk ? "ok" : "bad");
  setPillState(els.memPill, memoryOk ? "ok" : "warn");

  try {
    const status = await fetch("/status").then((res) => res.json());
    if (els.modelStatus) els.modelStatus.textContent = status.llm_model || "-";
    populateModelPicker(status.llm_model || "", status.available_models || []);
  } catch {
    if (els.modelStatus) els.modelStatus.textContent = "-";
  }

  // If the routing dropdowns failed to populate on first load (Ollama busy,
  // page raced the model-list fetch, etc.), keep retrying on each tick.
  if (!roleDropdownsReady) loadModelRoles();
}

function populateModelPicker(current, available) {
  if (!els.modelPicker) return;
  // Don't rebuild every poll — only when the model set actually changed.
  const want = JSON.stringify({ current, available });
  if (els.modelPicker.dataset.signature === want) return;
  els.modelPicker.dataset.signature = want;

  const options = available && available.length ? available : (current ? [current] : []);
  els.modelPicker.innerHTML = "";
  if (!options.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No models";
    els.modelPicker.append(opt);
    els.modelPicker.disabled = true;
    return;
  }
  els.modelPicker.disabled = false;
  for (const name of options) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === current) opt.selected = true;
    els.modelPicker.append(opt);
  }
  modelsLoaded = true;
}

async function switchModel(name) {
  if (!name) return;
  hlog.info("switching default model →", name);
  try {
    const response = await fetch("/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: name }),
    });
    if (!response.ok) {
      const err = await response.text();
      throw new Error(err || `Switch failed (${response.status})`);
    }
    addMessage("system", `Switched active model to ${name}.`);
    refreshStatus();
  } catch (error) {
    hlog.error("model switch failed", error.message);
    addMessage("system", `Could not switch model: ${error.message}`);
  }
}

async function sendMessage(message, options = {}) {
  const text = message.trim();
  if (!text || state.busy) {
    return;
  }

  addMessage("user", text);
  els.messageInput.value = "";
  autosizeInput();
  setBusy(true);

  const pending = addMessage("assistant", "", "Hunt");
  const bodyEl = pending.querySelector(".message-body");
  const metaEl = pending.querySelector(".message-meta");
  bodyEl.textContent = "…";

  let intent = "";
  let serverRole = "";
  let serverModel = "";
  let ttfbMs = null;
  let fullText = "";
  let receivedFinal = false;
  let tokenCount = 0;
  let sentenceCount = 0;
  state.speechBuffer = "";

  const t0 = performance.now();
  hlog.group(`send → ${text.length} chars`);
  hlog.info("payload", {
    message: text,
    include_context: els.memoryToggle.checked,
    use_live_search: els.liveSearchToggle.checked,
    voice_mode: Boolean(options.voiceMode),
    response_length: prefs.replyLength,
    temperature: prefs.creativity,
  });

  // Visible "Thinking… Ns" indicator while we wait for the first token. Cold
  // loads on big reasoning models (DeepSeek-R1) can take 30–60 s before any
  // output arrives — without this, the bubble looks frozen.
  let firstTokenReceived = false;
  const thinkingStartedAt = performance.now();
  const thinkingTickId = setInterval(() => {
    if (firstTokenReceived) {
      clearInterval(thinkingTickId);
      return;
    }
    const elapsed = (performance.now() - thinkingStartedAt) / 1000;
    if (elapsed >= 2) {
      bodyEl.textContent = `Thinking… ${Math.round(elapsed)}s`;
    }
  }, 500);

  try {
    const controller = new AbortController();
    // Long enough to outlast the Python-side LLM_REQUEST_TIMEOUT (120 s) plus
    // a cold-load buffer. If Ollama itself times out, the server will yield
    // a clean error event before this fires.
    const timeoutId = setTimeout(() => controller.abort(), 180000);
    state.streamAbort = controller;
    state.userStoppedStream = false;
    updateSpeechControls();

    const response = await fetch("/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        message: text,
        include_context: els.memoryToggle.checked,
        use_live_search: els.liveSearchToggle.checked,
        voice_mode: Boolean(options.voiceMode),
        response_length: prefs.replyLength,
        temperature: prefs.creativity,
        // Explicit role override (e.g. from quick-action chips) wins over
        // the auto-routed role derived from intent on the backend.
        ...(options.role ? { role: options.role } : {}),
      }),
    });

    if (!response.ok) {
      clearTimeout(timeoutId);
      const error = await response.text();
      throw new Error(error || `Request failed with ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newlineIdx;
      while ((newlineIdx = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newlineIdx).trim();
        buffer = buffer.slice(newlineIdx + 1);
        if (!line) continue;

        let event;
        try {
          event = JSON.parse(line);
        } catch {
          continue;
        }

        if (event.type === "meta") {
          intent = event.intent || "";
          serverRole = event.role || "";
          serverModel = event.model || "";
          metaEl.textContent = (intent && prefs.showIntent) ? `Hunt · ${intent}` : "Hunt";
          hlog.info("meta", {
            intent,
            role: serverRole,
            model: serverModel || "(router default)",
            ttfb_ms: Math.round(performance.now() - t0),
          });
        } else if (event.type === "token") {
          if (!fullText) {
            firstTokenReceived = true;
            ttfbMs = Math.round(performance.now() - t0);
            clearInterval(thinkingTickId);
            bodyEl.textContent = "";
            hlog.info(`first token after ${ttfbMs} ms`);
          }
          fullText += event.content || "";
          tokenCount += 1;
          bodyEl.textContent = fullText;
          els.messages.scrollTop = els.messages.scrollHeight;

          // Skip TTS entirely for coder replies — reading code symbols and
          // backticks aloud is unhelpful, and the response has no natural
          // sentence boundaries.
          if (serverRole !== "coder" && intent !== "code_help") {
            state.speechBuffer += event.content || "";
            let sentence;
            while ((sentence = extractFirstSentence(state.speechBuffer))) {
              state.speechBuffer = state.speechBuffer.slice(sentence.length);
              sentenceCount += 1;
              hlog.info(`tts sentence #${sentenceCount}`, sentence.trim());
              speakIncremental(sentence);
            }
          }
        } else if (event.type === "done") {
          receivedFinal = true;
          // Server sends the post-processed full text (think-stripped). Prefer it.
          const cleaned = cleanDisplayText(event.response || fullText);
          fullText = cleaned;
          if (event.intent && prefs.showIntent) metaEl.textContent = `Hunt · ${event.intent}`;

          // Coder replies: re-render the body to extract fenced code into
          // dedicated cards, and append suggestion chips so the user can
          // immediately ask to explain / optimize / add tests.
          const isCoder = (serverRole === "coder") || (event.intent === "code_help");
          if (isCoder) {
            renderMessageBody(bodyEl, cleaned);
            appendCoderSuggestions(pending);
          } else {
            // Non-coder replies: render the small markdown subset so **bold**,
            // *italic*, lists, and headings show as formatting instead of raw
            // asterisks. innerHTML is safe here — renderMarkdown escapes the
            // input before applying its narrow set of substitutions.
            bodyEl.innerHTML = renderMarkdown(cleaned);
          }

          const durationMs = Math.round(performance.now() - t0);
          attachDiagnostics(pending, {
            model: serverModel,
            intent: event.intent || intent,
            role: serverRole,
            ttfb_ms: ttfbMs,
            duration_ms: durationMs,
            tokens: tokenCount,
            response_chars: cleaned.length,
          });
          hlog.info("done", {
            intent: event.intent || intent,
            role: serverRole,
            duration_ms: durationMs,
            tokens: tokenCount,
            sentences_queued: sentenceCount,
            response_chars: cleaned.length,
          });
        } else if (event.type === "action_proposal") {
          handleActionProposalEvent(pending, event);
        } else if (event.type === "action_executed") {
          handleActionExecutedEvent(pending, event);
        } else if (event.type === "fact_proposal") {
          handleFactProposalEvent(pending, event);
        } else if (event.type === "macro_data") {
          handleMacroDataEvent(pending, event);
        } else if (event.type === "error") {
          hlog.error("server error event", event.message);
          throw new Error(event.message || "Stream error");
        }
      }
    }

    clearTimeout(timeoutId);

    if (!receivedFinal && !fullText) {
      throw new Error("Empty response from server");
    }

    // Flush any trailing text that didn't end with a sentence terminator.
    const tail = (state.speechBuffer || "").trim();
    if (tail) speakIncremental(tail);
    state.speechBuffer = "";
  } catch (error) {
    if (error.name === "AbortError" && state.userStoppedStream) {
      // The user hit Stop — treat as a graceful cancel, not an error.
      if (fullText) {
        bodyEl.textContent = fullText + "  (stopped)";
        metaEl.textContent = "Hunt · stopped";
      } else {
        pending.className = "message system";
        metaEl.textContent = "Stopped";
        bodyEl.textContent = "Generation stopped.";
      }
      hlog.warn("stopped by user", {
        tokens_received: tokenCount,
        duration_ms: Math.round(performance.now() - t0),
      });
    } else {
      pending.className = "message system";
      metaEl.textContent = "Error";
      bodyEl.textContent =
        error.name === "AbortError"
          ? "Hunt took too long. Try again, or install a smaller Ollama model for faster replies."
          : error.message;
      hlog.error("request failed", { name: error.name, message: error.message });
    }
  } finally {
    clearInterval(thinkingTickId);
    hlog.end();
    state.streamAbort = null;
    state.userStoppedStream = false;
    updateSpeechControls();
    setBusy(false);
    refreshStatus();
  }
}

// ---------- Tiny safe markdown renderer ----------
// Renders the subset of markdown Hunt's models actually emit: **bold**,
// *italic*, `inline code`, headings (# ##), and ordered/unordered lists.
// HTML is escaped first, so user/model output can't inject markup.
// Code fences (```) are intentionally left untouched here — the existing
// renderMessageBody handles them as separate code-card DOM nodes.
function escHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
  if (!text) return "";
  // 1. Escape HTML.
  let src = escHtml(text);
  // 2. Inline code FIRST so bold/italic inside backticks stays literal.
  src = src.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>');
  // 3. Bold (** or __). Non-greedy, no newlines crossed.
  src = src.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  src = src.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
  // 4. Italic (* or _) — only when the asterisk/underscore looks intentional
  // (not adjacent to a letter, to avoid stomping "abc_def" identifiers).
  src = src.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s.,;:!?)]|$)/g, "$1<em>$2</em>");
  src = src.replace(/(^|[\s(])_([^_\n]+)_(?=[\s.,;:!?)]|$)/g, "$1<em>$2</em>");

  // 5. Block parser: split into lines, group lists, paragraphs, headings.
  const out = [];
  let listType = null;     // "ul" | "ol" | null
  let para = [];

  const flushPara = () => {
    if (para.length) {
      out.push('<p class="md-p">' + para.join(" ") + "</p>");
      para = [];
    }
  };
  const flushList = () => {
    if (listType) { out.push("</" + listType + ">"); listType = null; }
  };

  for (const raw of src.split("\n")) {
    const ln = raw.trimEnd();
    const stripped = ln.trim();

    if (!stripped) { flushPara(); flushList(); continue; }

    const h = stripped.match(/^(#{1,3})\s+(.+)$/);
    if (h) {
      flushPara(); flushList();
      const lvl = Math.min(4, h[1].length + 2);  // h3..h5 to stay below the page h2s
      out.push(`<h${lvl} class="md-h">${h[2]}</h${lvl}>`);
      continue;
    }

    const ul = stripped.match(/^[-*]\s+(.+)$/);
    if (ul) {
      flushPara();
      if (listType !== "ul") { flushList(); out.push('<ul class="md-list">'); listType = "ul"; }
      out.push(`<li>${ul[1]}</li>`);
      continue;
    }

    const ol = stripped.match(/^(\d+)\.\s+(.+)$/);
    if (ol) {
      flushPara();
      if (listType !== "ol") { flushList(); out.push('<ol class="md-list">'); listType = "ol"; }
      out.push(`<li>${ol[2]}</li>`);
      continue;
    }

    // Indented continuation of the previous list item: append to it.
    if (listType && /^ {2,}/.test(raw)) {
      const lastIdx = out.length - 1;
      if (lastIdx >= 0 && out[lastIdx].startsWith("<li>")) {
        out[lastIdx] = out[lastIdx].replace("</li>", " " + stripped + "</li>");
        continue;
      }
    }

    flushList();
    para.push(stripped);
  }
  flushPara();
  flushList();

  return out.join("\n");
}

function cleanDisplayText(text) {
  return text
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function autosizeInput() {
  els.messageInput.style.height = "auto";
  els.messageInput.style.height = `${Math.min(els.messageInput.scrollHeight, 150)}px`;
}

async function startListening(mode = "manual") {
  if (state.recording || state.busy) {
    return;
  }

  // Barge-in: stop any current TTS so the assistant isn't talking over the user.
  if (state.speaking) stopSpeech();

  if (!navigator.mediaDevices?.getUserMedia) {
    addMessage("system", "Browser microphone access is not available. Use Chrome or Edge on localhost.");
    return;
  }

  try {
    state.recordingMode = mode;
    state.audioBuffers = [];
    state.silenceFrames = 0;
    state.voiceFrames = 0;
    state.peakLevel = 0;
    state.lastRecorderRms = 0;
    state.lastRecorderPeak = 0;
    state.recordingStartedAt = performance.now();

    const audioConstraints = {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    // If the user has chosen a specific input in settings, prefer it. We use
    // `ideal` (not `exact`) so the browser falls back to default cleanly when
    // the device was unplugged or renamed between sessions.
    if (prefs.micDeviceId) {
      audioConstraints.deviceId = { ideal: prefs.micDeviceId };
    }
    state.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: audioConstraints,
    });

    const AudioContext = window.AudioContext || window.webkitAudioContext;
    state.audioContext = new AudioContext();
    // Chrome on Windows often starts the context suspended; without an explicit resume,
    // the source produces silence and the meter stays at 0% even though the mic icon is lit.
    if (state.audioContext.state === "suspended") {
      try { await state.audioContext.resume(); } catch {}
    }
    state.inputSampleRate = state.audioContext.sampleRate;
    state.sourceNode = state.audioContext.createMediaStreamSource(state.mediaStream);
    state.recorderNode = state.audioContext.createScriptProcessor(4096, 1, 1);

    // Analyser node powers the level meter + VAD without depending on recorder buffer sizes.
    state.analyserNode = state.audioContext.createAnalyser();
    state.analyserNode.fftSize = 2048;
    state.analyserNode.smoothingTimeConstant = 0.4;
    state.sourceNode.connect(state.analyserNode);

    const track = state.mediaStream.getAudioTracks()[0];
    console.log(
      `🎙️ Mic stream: device="${track?.label || "(unknown)"}", enabled=${track?.enabled}, ` +
      `muted=${track?.muted}, readyState=${track?.readyState}, ctx=${state.audioContext.state}, sr=${state.inputSampleRate}`,
    );

    state.recorderNode.onaudioprocess = (event) => {
      if (!state.recording) {
        return;
      }
      const input = event.inputBuffer.getChannelData(0);
      // Copy because the AudioBuffer is reused on next callback.
      const copy = new Float32Array(input);
      state.audioBuffers.push(copy);
      // Track recorder-side level as a fallback in case the AnalyserNode misbehaves.
      let sumSq = 0;
      let peak = 0;
      for (let i = 0; i < copy.length; i += 1) {
        const v = copy[i];
        sumSq += v * v;
        const abs = v < 0 ? -v : v;
        if (abs > peak) peak = abs;
      }
      state.lastRecorderRms = Math.sqrt(sumSq / copy.length);
      state.lastRecorderPeak = peak;
    };

    state.sourceNode.connect(state.recorderNode);
    state.recorderNode.connect(state.audioContext.destination);

    setRecording(true);
    startLevelLoop();
    armSafetyTimeout();
    hlog.info("mic recording started", {
      mode,
      device: prefs.micDeviceId || "(default)",
      sample_rate: state.inputSampleRate,
    });
  } catch (error) {
    hlog.error("mic permission failed", error.message);
    addMessage("system", `Microphone permission failed: ${error.message}`);
    await cleanupRecording();
  }
}

function armSafetyTimeout() {
  if (state.maxDurationTimer) {
    clearTimeout(state.maxDurationTimer);
  }
  state.maxDurationTimer = setTimeout(() => {
    if (state.recording) {
      stopListening("max-duration");
    }
  }, RECORDING.MAX_DURATION_MS);
}

function startLevelLoop() {
  if (!state.analyserNode) return;
  const buffer = new Float32Array(state.analyserNode.fftSize);
  let lastVoiceAt = 0;

  const tick = () => {
    if (!state.recording || !state.analyserNode) {
      state.levelRafId = null;
      return;
    }
    state.analyserNode.getFloatTimeDomainData(buffer);
    let sumSq = 0;
    let peak = 0;
    for (let i = 0; i < buffer.length; i += 1) {
      const v = buffer[i];
      sumSq += v * v;
      const abs = Math.abs(v);
      if (abs > peak) peak = abs;
    }
    let rms = Math.sqrt(sumSq / buffer.length);
    // Fall back to the recorder-side measurement if the analyser path is silent
    // but the script-processor is still capturing audio (some Win/Chrome graphs do this).
    if (peak < 1e-5 && state.lastRecorderPeak > 1e-5) {
      rms = state.lastRecorderRms;
      peak = state.lastRecorderPeak;
    }
    state.peakLevel = Math.max(state.peakLevel, peak);
    // Render a slightly amplified level so quiet voices still register visually.
    setLevelMeter(Math.min(1, rms * 4));

    const now = performance.now();
    const elapsed = now - state.recordingStartedAt;

    if (rms > RECORDING.VOICE_THRESHOLD) {
      state.voiceFrames += 1;
      lastVoiceAt = now;
    } else if (rms < RECORDING.SILENCE_THRESHOLD) {
      state.silenceFrames += 1;
    }

    // VAD auto-stop: only after the user has actually spoken at least a little
    // and then went quiet for SILENCE_HANG_MS. Pre-speech grace gives them time to start.
    if (state.voiceFrames > 6 && lastVoiceAt > 0) {
      const trailingSilence = now - lastVoiceAt;
      if (trailingSilence >= RECORDING.SILENCE_HANG_MS && elapsed >= RECORDING.MIN_DURATION_MS) {
        stopListening("vad-silence");
        return;
      }
    } else if (elapsed > RECORDING.PRE_SPEECH_GRACE_MS && state.peakLevel < RECORDING.SILENCE_THRESHOLD) {
      // Nothing audible at all after grace window — stop and report.
      stopListening("vad-no-speech");
      return;
    }

    state.levelRafId = requestAnimationFrame(tick);
  };

  state.levelRafId = requestAnimationFrame(tick);
}

async function stopListening(reason = "manual-stop") {
  if (!state.recording) {
    return;
  }

  const elapsed = performance.now() - state.recordingStartedAt;
  setRecording(false);

  if (state.maxDurationTimer) {
    clearTimeout(state.maxDurationTimer);
    state.maxDurationTimer = null;
  }
  if (state.levelRafId) {
    cancelAnimationFrame(state.levelRafId);
    state.levelRafId = null;
  }

  const buffers = state.audioBuffers.slice();
  const sampleRate = state.inputSampleRate;
  const mode = state.recordingMode;
  const browserPeak = state.peakLevel;
  await cleanupRecording();

  if (reason === "vad-no-speech") {
    addMessage(
      "system",
      "I didn't hear anything. Check that the right mic is selected in Windows and that input volume is up — then tap to speak again.",
    );
    return;
  }

  if (!buffers.length) {
    if (mode !== "wake") {
      addMessage("system", "No microphone audio was captured.");
    }
    return;
  }

  if (elapsed < RECORDING.MIN_DURATION_MS) {
    addMessage("system", "That tap was too short to capture speech. Hold or click then talk for at least a second.");
    return;
  }

  setBusy(true);
  els.orbText.textContent = "...";

  try {
    const merged = mergeBuffers(buffers);
    let maxLevel = 0;
    let sumLevel = 0;
    for (let i = 0; i < merged.length; i += 1) {
      const abs = Math.abs(merged[i]);
      if (abs > maxLevel) maxLevel = abs;
      sumLevel += abs;
    }
    const avgLevel = sumLevel / merged.length;
    console.log(
      `🎤 Browser captured: ${buffers.length} buffers, ${merged.length} samples, ` +
      `max=${(maxLevel * 100).toFixed(1)}%, avg=${(avgLevel * 100).toFixed(2)}%, peak(meter)=${(browserPeak * 100).toFixed(1)}%, reason=${reason}`,
    );

    if (maxLevel < 0.01) {
      addMessage(
        "system",
        `Mic audio was almost silent (peak ${(maxLevel * 100).toFixed(1)}%). Speak closer to the mic, raise Windows input volume, or check the device.`,
      );
      return;
    }

    const wavBlob = encodeWav(downsampleBuffer(merged, sampleRate, 16000), 16000);
    console.log(`📤 WAV blob: ${wavBlob.size} bytes, 16kHz`);

    const formData = new FormData();
    formData.append("audio", wavBlob, "voice.wav");

    const response = await fetch("/voice/transcribe", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(error || `Transcription failed with ${response.status}`);
    }

    const data = await response.json();
    hlog.info("transcript", {
      text: data.transcript || "(empty)",
      reason: data.reason,
      diagnostics: data.diagnostics,
    });

    const transcript = (data.transcript || "").trim();

    if (!transcript) {
      if (mode !== "wake") {
        const hint = data.hint || "Hunt did not detect speech. Try speaking closer to the mic.";
        const diag = data.diagnostics
          ? ` (peak ${(Number(data.diagnostics.peak) * 100).toFixed(1)}%, ${Number(data.diagnostics.duration_seconds || 0).toFixed(2)}s)`
          : "";
        addMessage("system", `${hint}${diag}`);
      }
      return;
    }

    setBusy(false);
    await handleVoiceTranscript(transcript, mode);
  } catch (error) {
    if (mode !== "wake") {
      addMessage("system", `Voice input failed: ${error.message}`);
    }
  } finally {
    setBusy(false);
  }
}

async function handleVoiceTranscript(transcript, mode) {
  await sendMessage(transcript, { voiceMode: true });
}

function showTranscriptReview(transcript) {
  els.transcriptInput.value = transcript;
  els.transcriptReview.hidden = false;
  els.transcriptInput.focus();
  setHint("Review the transcript, edit if needed, then send.");
}

function hideTranscriptReview() {
  els.transcriptReview.hidden = true;
  els.transcriptInput.value = "";
  setHint("Ready for text or voice input.");
}

async function cleanupRecording() {
  if (state.recorderNode) {
    state.recorderNode.disconnect();
    state.recorderNode.onaudioprocess = null;
  }
  if (state.analyserNode) {
    try { state.analyserNode.disconnect(); } catch {}
  }
  if (state.sourceNode) {
    state.sourceNode.disconnect();
  }
  if (state.mediaStream) {
    state.mediaStream.getTracks().forEach((track) => track.stop());
  }
  if (state.audioContext && state.audioContext.state !== "closed") {
    await state.audioContext.close();
  }

  state.recorderNode = null;
  state.analyserNode = null;
  state.sourceNode = null;
  state.mediaStream = null;
  state.audioContext = null;
}

function mergeBuffers(buffers) {
  const length = buffers.reduce((total, buffer) => total + buffer.length, 0);
  const merged = new Float32Array(length);
  let offset = 0;
  buffers.forEach((buffer) => {
    merged.set(buffer, offset);
    offset += buffer.length;
  });
  return merged;
}

function downsampleBuffer(buffer, inputSampleRate, outputSampleRate) {
  if (inputSampleRate === outputSampleRate) {
    return buffer;
  }

  const ratio = inputSampleRate / outputSampleRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;

  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accumulator = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
      accumulator += buffer[i];
      count += 1;
    }
    result[offsetResult] = accumulator / Math.max(count, 1);
    offsetResult += 1;
    offsetBuffer = nextOffsetBuffer;
  }

  return result;
}

function encodeWav(samples, sampleRate) {
  const bytesPerSample = 2;
  const blockAlign = bytesPerSample;
  const buffer = new ArrayBuffer(44 + samples.length * bytesPerSample);
  const view = new DataView(buffer);

  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * bytesPerSample, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, "data");
  view.setUint32(40, samples.length * bytesPerSample, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }

  return new Blob([view], { type: "audio/wav" });
}

function writeString(view, offset, value) {
  for (let i = 0; i < value.length; i += 1) {
    view.setUint8(offset + i, value.charCodeAt(i));
  }
}

els.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(els.messageInput.value);
});

els.messageInput.addEventListener("input", autosizeInput);
els.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.chatForm.requestSubmit();
  }
});

els.clearButton.addEventListener("click", async () => {
  els.messages.innerHTML = "";
  window.speechSynthesis?.cancel();
  hideTranscriptReview();
  try {
    await fetch("/conversation/clear", { method: "POST" });
  } catch {
    // Non-fatal: UI is already wiped; backend may just be momentarily unavailable.
  }
  addMessage("assistant", "Fresh chat started. I am ready.", "Hunt");
});

els.sendTranscriptButton.addEventListener("click", () => {
  const transcript = els.transcriptInput.value.trim();
  hideTranscriptReview();
  sendMessage(transcript);
});

els.cancelTranscriptButton.addEventListener("click", hideTranscriptReview);

els.transcriptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    els.sendTranscriptButton.click();
  }
});

els.voiceButton.addEventListener("click", toggleRecording);
els.orbButton.addEventListener("click", toggleRecording);

function toggleRecording() {
  if (state.busy) return;
  if (state.recording) {
    stopListening("manual-stop");
  } else {
    startListening("manual");
  }
}

els.voiceButton.addEventListener("touchstart", (event) => {
  event.preventDefault();
  toggleRecording();
});
els.orbButton.addEventListener("touchstart", (event) => {
  event.preventDefault();
  toggleRecording();
});

// Spacebar push-to-talk: hold space (when not typing) to record.
document.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || event.repeat) return;
  const target = event.target;
  if (target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT" || target.isContentEditable)) {
    return;
  }
  if (state.busy || state.recording) return;
  event.preventDefault();
  startListening("manual");
});

document.addEventListener("keyup", (event) => {
  if (event.code !== "Space") return;
  const target = event.target;
  if (target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT" || target.isContentEditable)) {
    return;
  }
  if (state.recording) {
    event.preventDefault();
    stopListening("manual-stop");
  }
});

if (els.expandButton) {
  els.expandButton.addEventListener("click", () => {
    els.messageInput.focus();
    els.messageInput.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

if (els.refreshButton) {
  els.refreshButton.addEventListener("click", refreshStatus);
}

if (els.modelPicker) {
  els.modelPicker.addEventListener("change", (event) => {
    const value = event.target.value;
    if (modelsLoaded && value) switchModel(value);
  });
}

if (els.settingsButton && els.settingsPopover) {
  const closeOnOutside = (event) => {
    if (els.settingsPopover.hidden) return;
    if (event.target === els.settingsButton || els.settingsButton.contains(event.target)) return;
    if (els.settingsPopover.contains(event.target)) return;
    els.settingsPopover.hidden = true;
    els.settingsButton.setAttribute("aria-expanded", "false");
  };
  els.settingsButton.addEventListener("click", () => {
    const open = els.settingsPopover.hidden;
    els.settingsPopover.hidden = !open;
    els.settingsButton.setAttribute("aria-expanded", String(open));
    // Refresh action history + policies each time the popover opens so the
    // user sees what changed since their last decision.
    if (open) loadActionsPanel();
  });
  document.addEventListener("click", closeOnOutside);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    // Priority order: close popover → close memory panel → stop speech/stream.
    // Each branch returns so a single Esc only handles one thing.
    if (!els.settingsPopover.hidden) {
      els.settingsPopover.hidden = true;
      els.settingsButton.setAttribute("aria-expanded", "false");
      return;
    }
    if (els.memoryPanel && !els.memoryPanel.hidden) {
      closeMemoryPanel();
      return;
    }
    if (state.speaking || state.streamAbort) {
      stopAll();
    }
  });
}

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    const opts = {};
    if (button.dataset.role) opts.role = button.dataset.role;
    sendMessage(button.dataset.prompt || "", opts);
  });
});

if (!navigator.mediaDevices?.getUserMedia) {
  els.voiceButton.disabled = true;
  els.orbButton.disabled = true;
  els.voiceButton.querySelector("span:last-child").textContent = "Voice unavailable";
  els.orbText.textContent = "Text";
}

// ---------- Settings popover wiring ----------

function applyReplyLengthUI() {
  if (!els.replyLengthGroup) return;
  for (const btn of els.replyLengthGroup.querySelectorAll("button[data-length]")) {
    const isActive = btn.dataset.length === prefs.replyLength;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-checked", String(isActive));
  }
}
if (els.replyLengthGroup) {
  applyReplyLengthUI();
  els.replyLengthGroup.addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-length]");
    if (!btn) return;
    prefs.replyLength = btn.dataset.length;
    savePrefs();
    applyReplyLengthUI();
  });
}

if (els.creativitySlider) {
  els.creativitySlider.value = String(Math.round(prefs.creativity * 100));
  if (els.creativityValue) els.creativityValue.textContent = prefs.creativity.toFixed(2);
  els.creativitySlider.addEventListener("input", () => {
    prefs.creativity = Number(els.creativitySlider.value) / 100;
    if (els.creativityValue) els.creativityValue.textContent = prefs.creativity.toFixed(2);
    savePrefs();
  });
}

function populateTtsVoices() {
  if (!els.ttsVoiceSelect || !("speechSynthesis" in window)) return;
  const voices = window.speechSynthesis.getVoices() || [];
  if (!voices.length) return;
  const current = prefs.ttsVoice;
  els.ttsVoiceSelect.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = "Browser default";
  els.ttsVoiceSelect.append(defaultOpt);
  // Prefer English voices at the top, but list everything.
  voices.sort((a, b) => {
    const ae = a.lang?.startsWith("en") ? 0 : 1;
    const be = b.lang?.startsWith("en") ? 0 : 1;
    return ae - be || a.name.localeCompare(b.name);
  });
  for (const v of voices) {
    const opt = document.createElement("option");
    opt.value = v.voiceURI;
    opt.textContent = `${v.name} · ${v.lang}`;
    if (v.voiceURI === current || v.name === current) opt.selected = true;
    els.ttsVoiceSelect.append(opt);
  }
}
if ("speechSynthesis" in window) {
  populateTtsVoices();
  window.speechSynthesis.addEventListener?.("voiceschanged", populateTtsVoices);
}
if (els.ttsVoiceSelect) {
  els.ttsVoiceSelect.addEventListener("change", () => {
    prefs.ttsVoice = els.ttsVoiceSelect.value;
    savePrefs();
  });
}

if (els.ttsRateSlider) {
  els.ttsRateSlider.value = String(Math.round(prefs.ttsRate * 100));
  if (els.ttsRateValue) els.ttsRateValue.textContent = `${prefs.ttsRate.toFixed(2)}x`;
  els.ttsRateSlider.addEventListener("input", () => {
    prefs.ttsRate = Number(els.ttsRateSlider.value) / 100;
    if (els.ttsRateValue) els.ttsRateValue.textContent = `${prefs.ttsRate.toFixed(2)}x`;
    savePrefs();
  });
}

if (els.showIntentToggle) {
  els.showIntentToggle.checked = !!prefs.showIntent;
  els.showIntentToggle.addEventListener("change", () => {
    prefs.showIntent = els.showIntentToggle.checked;
    savePrefs();
  });
}

// ---------- Multi-model routing ----------
// The role dropdowns let the user pick which model handles which kind of
// request. The backend (ModelRouter) routes intents → roles → models, with
// graceful fallback when the picked model isn't pulled.

const ROLE_SELECTS = [
  els.roleBrainSelect,
  els.roleCoderSelect,
  els.roleFastSelect,
  els.roleVisionSelect,
].filter(Boolean);

let roleDropdownsReady = false;

async function loadModelRoles({ force = false } = {}) {
  if (!ROLE_SELECTS.length) return;
  if (roleDropdownsReady && !force) return;
  try {
    const data = await fetch("/models/roles").then((r) => r.json());
    const roles = data.roles || {};
    const available = data.available_models || [];

    // If the available-models list is empty, Ollama may have been busy /
    // restarting. Leave the placeholder and try again next refreshStatus tick.
    if (!available.length) {
      hlog.warn("loadModelRoles: /models/roles returned no available_models — will retry");
      return;
    }

    for (const sel of ROLE_SELECTS) {
      const role = sel.dataset.role;
      const current = roles[role] || "";
      sel.innerHTML = "";

      // Always include the currently-mapped model so the user can see what's
      // configured even if it isn't pulled (with a marker).
      const seen = new Set();
      const optEmpty = document.createElement("option");
      optEmpty.value = "";
      optEmpty.textContent = "— unset —";
      sel.append(optEmpty);

      for (const name of available) {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        if (name === current) opt.selected = true;
        sel.append(opt);
        seen.add(name);
      }
      if (current && !seen.has(current)) {
        const opt = document.createElement("option");
        opt.value = current;
        opt.textContent = `${current} (not pulled)`;
        opt.selected = true;
        sel.append(opt);
      }
    }
    roleDropdownsReady = true;
    hlog.info("role dropdowns populated", { available, roles });
  } catch (e) {
    hlog.warn("loadModelRoles failed — will retry on next refresh", e?.message);
  }
}

async function saveModelRoles() {
  const roles = {};
  for (const sel of ROLE_SELECTS) {
    const role = sel.dataset.role;
    if (sel.value) roles[role] = sel.value;
  }
  hlog.info("saving role map", roles);
  try {
    await fetch("/models/roles", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ roles }),
    });
  } catch (e) {
    hlog.error("role map save failed", e.message);
    addMessage("system", `Could not update role map: ${e.message}`);
  }
}

for (const sel of ROLE_SELECTS) {
  sel.addEventListener("change", saveModelRoles);
}
loadModelRoles();

// ---------- Microphone device picker ----------
// `enumerateDevices` returns entries with empty `label` until the page has been
// granted mic permission at least once. We populate the dropdown either way; if
// labels come back blank we expose a one-shot button that requests permission
// and immediately stops the stream so we can re-enumerate with real names.

async function populateMicDevices() {
  if (!els.micDeviceSelect || !navigator.mediaDevices?.enumerateDevices) return;
  let devices = [];
  try {
    devices = await navigator.mediaDevices.enumerateDevices();
  } catch {
    return;
  }
  const inputs = devices.filter((d) => d.kind === "audioinput");

  els.micDeviceSelect.innerHTML = "";
  const defOpt = document.createElement("option");
  defOpt.value = "";
  defOpt.textContent = "System default";
  els.micDeviceSelect.append(defOpt);

  let anyLabeled = false;
  for (let i = 0; i < inputs.length; i += 1) {
    const dev = inputs[i];
    if (dev.label) anyLabeled = true;
    const opt = document.createElement("option");
    opt.value = dev.deviceId;
    opt.textContent = dev.label || `Microphone ${i + 1}`;
    if (dev.deviceId === prefs.micDeviceId) opt.selected = true;
    els.micDeviceSelect.append(opt);
  }

  // If no labels arrived, we haven't been granted mic permission yet for this
  // origin — show the helper button so the user can opt in once.
  if (els.micPermissionButton) {
    els.micPermissionButton.hidden = anyLabeled || inputs.length === 0;
  }
}

els.micDeviceSelect?.addEventListener("change", () => {
  prefs.micDeviceId = els.micDeviceSelect.value;
  savePrefs();
});

els.micPermissionButton?.addEventListener("click", async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    await populateMicDevices();
  } catch (e) {
    addMessage("system", `Could not access mic: ${e.message}`);
  }
});

// Refresh device list whenever the OS reports a hot-plug/removal, and once at
// startup. Errors from these are non-fatal — the dropdown just stays empty.
navigator.mediaDevices?.addEventListener?.("devicechange", populateMicDevices);
populateMicDevices();

// ---------- Export transcript ----------

els.exportTranscriptButton?.addEventListener("click", async () => {
  try {
    const response = await fetch("/conversation/export?format=md");
    if (!response.ok) throw new Error(await response.text());
    const markdown = await response.text();
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `hunt-transcript-${stamp}.md`;
    document.body.append(a);
    a.click();
    a.remove();
    // Revoke async so the download has time to start using the URL.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    addMessage("system", `Export failed: ${error.message}`);
  }
});

// ---------- Sessions panel (Phase D2) ----------
// Slide-in panel on the left. Lists past chats (from Mongo when configured,
// otherwise just the current session). Clicking a row resumes it; the chat
// area is wiped and the verbatim turns are replayed via addMessage().

function openSessionsPanel() {
  if (!els.sessionsPanel) return;
  els.sessionsPanel.hidden = false;
  if (els.sessionsPanelScrim) els.sessionsPanelScrim.hidden = false;
  els.sessionsButton?.setAttribute("aria-expanded", "true");
  document.body.classList.add("memory-open");  // reuse the body-scroll-lock class
  loadSessions();
}
function closeSessionsPanel() {
  if (!els.sessionsPanel) return;
  els.sessionsPanel.hidden = true;
  if (els.sessionsPanelScrim) els.sessionsPanelScrim.hidden = true;
  els.sessionsButton?.setAttribute("aria-expanded", "false");
  // Only release body-scroll-lock if the memory panel isn't also open.
  if (els.memoryPanel?.hidden) document.body.classList.remove("memory-open");
}

els.sessionsButton?.addEventListener("click", openSessionsPanel);
els.closeSessionsPanelButton?.addEventListener("click", closeSessionsPanel);
els.sessionsPanelScrim?.addEventListener("click", closeSessionsPanel);

async function loadSessions() {
  if (!els.sessionsList) return;
  try {
    const data = await fetch("/sessions?limit=30").then((r) => r.json());
    renderSessionsList(data.sessions || [], data.source);
  } catch (e) {
    hlog.warn("loadSessions failed", e?.message);
    if (els.sessionsStatus) {
      els.sessionsStatus.textContent = "Could not load sessions.";
      els.sessionsStatus.className = "sessions-status is-offline";
    }
  }
}

function fmtRelative(iso) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diffMin = Math.round((Date.now() - t) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  const diffD = Math.round(diffH / 24);
  if (diffD < 7) return `${diffD}d ago`;
  return new Date(t).toLocaleDateString();
}

function renderSessionsList(sessions, source) {
  if (!els.sessionsList) return;
  if (els.sessionsStatus) {
    if (source === "mongo") {
      els.sessionsStatus.textContent = `Cloud sync · ${sessions.length} chats`;
      els.sessionsStatus.className = "sessions-status is-online";
    } else {
      els.sessionsStatus.textContent = "Local only · enable Mongo in .env for cross-device";
      els.sessionsStatus.className = "sessions-status is-offline";
    }
  }
  els.sessionsList.innerHTML = "";
  if (!sessions.length) {
    const empty = document.createElement("li");
    empty.className = "memory-empty";
    empty.textContent = "No chats yet.";
    els.sessionsList.append(empty);
    return;
  }
  for (const s of sessions) {
    const li = document.createElement("li");
    li.className = "session-row" + (s.is_current ? " is-current" : "");
    li.dataset.sessionId = s._id;

    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = s.title || "Untitled chat";

    const meta = document.createElement("span");
    meta.className = "session-meta";
    meta.textContent = fmtRelative(s.updated_at || s.started_at);

    li.append(title, meta);

    if (s.is_current) {
      const pill = document.createElement("span");
      pill.className = "session-current-pill";
      pill.textContent = "Current";
      li.append(pill);
    }

    li.addEventListener("click", () => resumeSession(s._id, s.is_current));
    els.sessionsList.append(li);
  }
}

async function resumeSession(sessionId, isCurrent) {
  if (isCurrent) { closeSessionsPanel(); return; }
  try {
    hlog.info("resuming session", sessionId);
    const r = await fetch(`/sessions/${encodeURIComponent(sessionId)}/resume`, { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    replayVerbatim(data.title, data.verbatim || []);
    closeSessionsPanel();
    // Also refresh the memory panel data so the recent turns + summary
    // reflect the resumed session.
    loadMemoryPanel();
  } catch (e) {
    addMessage("system", `Could not resume chat: ${e.message}`);
  }
}

function replayVerbatim(title, verbatim) {
  // Wipe the messages area and rebuild it from the resumed session's turns.
  els.messages.innerHTML = "";
  addMessage("assistant", `Resumed: ${title || "Untitled chat"}.`, "Hunt");
  for (const turn of verbatim) {
    if (turn.user) addMessage("user", turn.user);
    if (turn.assistant) addMessage("assistant", turn.assistant, "Hunt");
  }
  els.messages.scrollTop = els.messages.scrollHeight;
}

els.newSessionButton?.addEventListener("click", async () => {
  try {
    const r = await fetch("/sessions/new", { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    els.messages.innerHTML = "";
    addMessage("assistant", "Fresh chat started. I am ready.", "Hunt");
    closeSessionsPanel();
    loadMemoryPanel();
  } catch (e) {
    addMessage("system", `Could not start new chat: ${e.message}`);
  }
});

// Mongo status polling — updates the small dot on the Sessions button.
async function refreshMongoStatus() {
  if (!els.mongoDot) return;
  try {
    const data = await fetch("/mongo/status").then((r) => r.json());
    if (!data.enabled || !data.configured) {
      els.mongoDot.hidden = true;
      return;
    }
    els.mongoDot.hidden = false;
    els.mongoDot.classList.remove("is-offline", "is-error");
    if (!data.connected) {
      els.mongoDot.classList.add("is-error");
      els.mongoDot.title = `MongoDB sync offline: ${data.last_err || "not connected"}`;
    } else if (data.writes_failed && data.writes_failed > 0) {
      els.mongoDot.classList.add("is-offline");
      els.mongoDot.title = `MongoDB sync degraded: ${data.writes_failed} failed write(s)`;
    } else {
      els.mongoDot.title = `MongoDB sync live · ${data.writes_attempted} writes`;
    }
  } catch {
    if (els.mongoDot) els.mongoDot.hidden = true;
  }
}
refreshMongoStatus();
setInterval(refreshMongoStatus, 30000);

// ---------- Memory side panel ----------

function openMemoryPanel() {
  if (!els.memoryPanel) return;
  els.memoryPanel.hidden = false;
  if (els.memoryPanelScrim) els.memoryPanelScrim.hidden = false;
  els.memoryButton?.setAttribute("aria-expanded", "true");
  document.body.classList.add("memory-open");
  loadMemoryPanel();
}
function closeMemoryPanel() {
  if (!els.memoryPanel) return;
  els.memoryPanel.hidden = true;
  if (els.memoryPanelScrim) els.memoryPanelScrim.hidden = true;
  els.memoryButton?.setAttribute("aria-expanded", "false");
  document.body.classList.remove("memory-open");
}
els.memoryButton?.addEventListener("click", () => {
  // Toggle: if the panel is already open, treat the icon click as "close"
  // so the user has an extra escape hatch beyond X / scrim / Esc.
  if (els.memoryPanel && !els.memoryPanel.hidden) closeMemoryPanel();
  else openMemoryPanel();
});
els.openMemoryPanelButton?.addEventListener("click", () => {
  if (!els.settingsPopover?.hidden) {
    els.settingsPopover.hidden = true;
    els.settingsButton?.setAttribute("aria-expanded", "false");
  }
  openMemoryPanel();
});
els.closeMemoryPanelButton?.addEventListener("click", closeMemoryPanel);
els.memoryPanelScrim?.addEventListener("click", closeMemoryPanel);
els.memoryRefreshButton?.addEventListener("click", loadMemoryPanel);

// Belt-and-suspenders: document-level delegated handler. If anything later
// in the script throws before the direct binding runs, or if some other
// handler swallows the bubbled click on #closeMemoryPanelButton, this catches
// it at the capture phase and forces the close. Also handles ESC.
document.addEventListener("click", (e) => {
  const t = e.target;
  if (!t || !t.closest) return;
  if (t.closest("#closeMemoryPanelButton")) {
    closeMemoryPanel();
  } else if (t.closest("#memoryPanelScrim")) {
    closeMemoryPanel();
  } else if (t.closest("#closeSessionsPanelButton")) {
    closeSessionsPanel();
  } else if (t.closest("#sessionsPanelScrim")) {
    closeSessionsPanel();
  }
}, true);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (els.memoryPanel && !els.memoryPanel.hidden) { closeMemoryPanel(); return; }
  if (els.sessionsPanel && !els.sessionsPanel.hidden) { closeSessionsPanel(); return; }
});

async function loadMemoryPanel() {
  await Promise.all([
    loadProjects(),       // Phase E1
    loadProfile(),
    loadSessionState(),
    loadProfileFacts(),
  ]);
}

// ---------- Projects (Phase E1) ----------
// Cached so the topbar pill, popover, and Memory-panel list don't each have
// to refetch on every interaction. Refreshed whenever a project mutation
// happens or the active project changes.
let _projectsCache = { projects: [], active_project_id: null, statuses: [] };
let _projectDetailId = null;  // id of project currently open in the detail view
const PROJECT_STATUS_LABELS = {
  active: "Active", paused: "Paused", done: "Done", archived: "Archived",
};

function activeProjectLabel() {
  const id = _projectsCache.active_project_id;
  if (!id) return null;
  const p = _projectsCache.projects.find((x) => x.id === id);
  return p ? p.name : null;
}

function updateActiveProjectPill() {
  if (!els.projectPill || !els.projectPillLabel) return;
  const name = activeProjectLabel();
  els.projectPillLabel.textContent = name || "No project";
  els.projectPill.classList.toggle("is-active", Boolean(name));
}

async function loadProjects() {
  try {
    const data = await fetch("/projects").then((r) => r.json());
    _projectsCache = {
      projects: data.projects || [],
      active_project_id: data.active_project_id || null,
      statuses: data.statuses || [],
    };
    updateActiveProjectPill();
    renderProjectsList();
    renderProjectPicker();
  } catch (e) {
    hlog.warn("loadProjects failed", e?.message);
  }
}

function renderProjectsList() {
  if (!els.projectList) return;
  els.projectList.innerHTML = "";
  const projects = _projectsCache.projects;
  if (!projects.length) {
    const empty = document.createElement("li");
    empty.className = "memory-empty";
    empty.textContent = "No projects yet. Click + New.";
    els.projectList.append(empty);
    return;
  }
  for (const p of projects) {
    const li = document.createElement("li");
    li.className = "project-row" + (p.id === _projectsCache.active_project_id ? " is-active" : "");

    const head = document.createElement("div");
    head.className = "project-row-head";
    const name = document.createElement("span");
    name.className = "project-name";
    name.textContent = p.name;
    head.append(name);
    if (p.stack) {
      const stack = document.createElement("span");
      stack.className = "project-stack";
      stack.textContent = p.stack;
      head.append(stack);
    }

    const pill = document.createElement("span");
    pill.className = `project-status-pill is-${p.status || "active"}`;
    pill.textContent = (PROJECT_STATUS_LABELS[p.status] || p.status || "Active").slice(0, 6);

    li.append(head, pill);
    li.addEventListener("click", () => openProjectDetail(p.id));
    els.projectList.append(li);
  }
}

function renderProjectPicker() {
  if (!els.projectPickerList) return;
  els.projectPickerList.innerHTML = "";
  if (!_projectsCache.projects.length) {
    const empty = document.createElement("li");
    empty.className = "memory-empty";
    empty.textContent = "No projects yet.";
    els.projectPickerList.append(empty);
    return;
  }
  for (const p of _projectsCache.projects) {
    const li = document.createElement("li");
    li.className = "project-pick" + (p.id === _projectsCache.active_project_id ? " is-active" : "");
    const name = document.createElement("span");
    name.className = "project-pick-name";
    name.textContent = p.name;
    const stack = document.createElement("span");
    stack.className = "project-pick-stack";
    stack.textContent = p.stack || "—";
    li.append(name, stack);
    li.addEventListener("click", async () => {
      await setActiveProject(p.id);
      els.projectPopover.hidden = true;
      els.projectPill?.setAttribute("aria-expanded", "false");
    });
    els.projectPickerList.append(li);
  }
}

async function setActiveProject(projectId) {
  try {
    const r = await fetch("/projects/active", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }),
    });
    if (!r.ok) throw new Error(await r.text());
    _projectsCache.active_project_id = projectId;
    updateActiveProjectPill();
    renderProjectsList();
    renderProjectPicker();
    hlog.info("active project set", projectId);
  } catch (e) {
    addMessage("system", `Could not activate project: ${e.message}`);
  }
}

// Topbar pill → popover toggle
els.projectPill?.addEventListener("click", (event) => {
  event.stopPropagation();
  if (!els.projectPopover) return;
  const open = els.projectPopover.hidden;
  els.projectPopover.hidden = !open;
  els.projectPill.setAttribute("aria-expanded", String(open));
  if (open) loadProjects();
});
document.addEventListener("click", (event) => {
  if (!els.projectPopover || els.projectPopover.hidden) return;
  if (event.target === els.projectPill || els.projectPill.contains(event.target)) return;
  if (els.projectPopover.contains(event.target)) return;
  els.projectPopover.hidden = true;
  els.projectPill?.setAttribute("aria-expanded", "false");
});

els.clearProjectButton?.addEventListener("click", async () => {
  await setActiveProject(null);
  if (els.projectPopover) els.projectPopover.hidden = true;
});

els.openProjectsPanelButton?.addEventListener("click", () => {
  if (els.projectPopover) els.projectPopover.hidden = true;
  openMemoryPanel();
});

els.newProjectButton?.addEventListener("click", async () => {
  const name = window.prompt("Project name?");
  if (!name) return;
  try {
    const r = await fetch("/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    await loadProjects();
    openProjectDetail(data.project.id);
  } catch (e) {
    addMessage("system", `Could not create project: ${e.message}`);
  }
});

// ---- Detail view ----

function openProjectDetail(projectId) {
  _projectDetailId = projectId;
  const p = _projectsCache.projects.find((x) => x.id === projectId);
  if (!p || !els.projectDetail) return;
  els.projectList.hidden = true;
  els.projectDetail.hidden = false;
  els.projectDetailName.value = p.name || "";
  els.projectDetailStack.value = p.stack || "";
  els.projectDetailStatus.value = p.status || "active";
  els.projectDetailDescription.value = p.description || "";
  els.projectDetailNotes.value = p.notes || "";
  renderProjectTasks(p.open_tasks || []);
  const isActive = projectId === _projectsCache.active_project_id;
  els.projectDetailActivate.textContent = isActive ? "Active on this chat" : "Activate on this chat";
  els.projectDetailActivate.disabled = isActive;
}

function closeProjectDetail() {
  _projectDetailId = null;
  if (els.projectList) els.projectList.hidden = false;
  if (els.projectDetail) els.projectDetail.hidden = true;
}

els.projectDetailBack?.addEventListener("click", closeProjectDetail);

// Field-level auto-save (mirrors the Profile editor pattern). Single-field
// PATCH so concurrent edits never overwrite each other.
function bindProjectDetailField(el, field) {
  if (!el) return;
  el.addEventListener("blur", async () => {
    if (!_projectDetailId) return;
    try {
      const r = await fetch(`/projects/${_projectDetailId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: el.value }),
      });
      if (!r.ok) throw new Error(await r.text());
      el.classList.add("is-saved");
      setTimeout(() => el.classList.remove("is-saved"), 1100);
      await loadProjects();
    } catch (e) {
      hlog.error(`project.${field} save failed`, e?.message);
    }
  });
}
bindProjectDetailField(els.projectDetailName, "name");
bindProjectDetailField(els.projectDetailStack, "stack");
bindProjectDetailField(els.projectDetailDescription, "description");
bindProjectDetailField(els.projectDetailNotes, "notes");
// Status uses `change` instead of `blur` so the dropdown commits immediately.
els.projectDetailStatus?.addEventListener("change", async () => {
  if (!_projectDetailId) return;
  try {
    const r = await fetch(`/projects/${_projectDetailId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: els.projectDetailStatus.value }),
    });
    if (!r.ok) throw new Error(await r.text());
    els.projectDetailStatus.classList.add("is-saved");
    setTimeout(() => els.projectDetailStatus.classList.remove("is-saved"), 1100);
    await loadProjects();
  } catch (e) {
    hlog.error("project.status save failed", e?.message);
  }
});

function renderProjectTasks(tasks) {
  if (!els.projectDetailTasks) return;
  els.projectDetailTasks.innerHTML = "";
  const open = tasks.filter((t) => !t.done).length;
  els.projectDetailTaskCount.textContent = `${open} open · ${tasks.length} total`;
  if (!tasks.length) {
    const empty = document.createElement("li");
    empty.className = "memory-empty";
    empty.textContent = "No tasks yet.";
    els.projectDetailTasks.append(empty);
    return;
  }
  for (const t of tasks) {
    const li = document.createElement("li");
    li.className = "project-task" + (t.done ? " is-done" : "");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!t.done;
    cb.addEventListener("change", () => toggleTask(t.id, cb.checked));
    const text = document.createElement("span");
    text.className = "project-task-text";
    text.textContent = t.text;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "project-task-delete";
    del.textContent = "×";
    del.title = "Delete task";
    del.addEventListener("click", () => deleteTask(t.id));
    li.append(cb, text, del);
    els.projectDetailTasks.append(li);
  }
}

async function toggleTask(taskId, done) {
  if (!_projectDetailId) return;
  try {
    await fetch(`/projects/${_projectDetailId}/tasks/${taskId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ done }),
    });
    await loadProjects();
    if (_projectDetailId) openProjectDetail(_projectDetailId);
  } catch (e) {
    hlog.error("toggle task failed", e?.message);
  }
}

async function deleteTask(taskId) {
  if (!_projectDetailId) return;
  try {
    await fetch(`/projects/${_projectDetailId}/tasks/${taskId}`, { method: "DELETE" });
    await loadProjects();
    if (_projectDetailId) openProjectDetail(_projectDetailId);
  } catch (e) {
    hlog.error("delete task failed", e?.message);
  }
}

els.projectDetailTaskForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!_projectDetailId) return;
  const text = (els.projectDetailTaskInput.value || "").trim();
  if (!text) return;
  try {
    await fetch(`/projects/${_projectDetailId}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    els.projectDetailTaskInput.value = "";
    await loadProjects();
    if (_projectDetailId) openProjectDetail(_projectDetailId);
  } catch (e) {
    addMessage("system", `Could not add task: ${e.message}`);
  }
});

els.projectDetailActivate?.addEventListener("click", async () => {
  if (!_projectDetailId) return;
  await setActiveProject(_projectDetailId);
  openProjectDetail(_projectDetailId);
});

els.projectDetailDelete?.addEventListener("click", async () => {
  if (!_projectDetailId) return;
  if (!confirm("Delete this project? Linked sessions stay, but project metadata is gone.")) return;
  try {
    await fetch(`/projects/${_projectDetailId}`, { method: "DELETE" });
    await loadProjects();
    closeProjectDetail();
  } catch (e) {
    addMessage("system", `Could not delete project: ${e.message}`);
  }
});

// On page load, fetch projects so the pill shows the right label even
// before the user opens the Memory panel.
loadProjects();

// ---------- Structured profile editor ----------

const PROFILE_FIELDS = [
  "name", "occupation", "projects", "interests",
  "preferred_tone", "daily_schedule", "frequent_contacts", "goals",
];

const PROFILE_FIELD_LABELS = {
  name: "Name",
  occupation: "Occupation",
  projects: "Projects",
  interests: "Interests",
  preferred_tone: "Preferred tone",
  daily_schedule: "Daily schedule",
  frequent_contacts: "Frequent contacts",
  goals: "Goals",
};

function updateProfileStatus(profile) {
  if (!els.profileStatus) return;
  const filled = PROFILE_FIELDS.filter((f) => (profile[f] || "").trim()).length;
  els.profileStatus.textContent = `${filled} / ${PROFILE_FIELDS.length} fields`;
}

async function loadProfile() {
  if (!els.profileForm) return;
  try {
    const data = await fetch("/profile").then((r) => r.json());
    const profile = data.profile || {};
    for (const input of els.profileForm.querySelectorAll("input[data-field]")) {
      // Don't stomp a field the user is currently editing.
      if (document.activeElement === input) continue;
      input.value = profile[input.dataset.field] || "";
    }
    updateProfileStatus(profile);
  } catch (e) {
    hlog.warn("loadProfile failed", e?.message);
  }
}

if (els.profileForm) {
  // Auto-save on blur — single-field PATCH so we never overwrite other fields.
  // Tiny visual feedback: terracotta border while saving, mint flash on success.
  els.profileForm.addEventListener("blur", async (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.dataset.field) return;
    const field = input.dataset.field;
    const value = input.value.trim();
    input.classList.remove("is-saved");
    input.classList.add("is-saving");
    try {
      const response = await fetch("/profile", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: value }),
      });
      if (!response.ok) throw new Error(await response.text());
      const data = await response.json();
      input.classList.remove("is-saving");
      input.classList.add("is-saved");
      setTimeout(() => input.classList.remove("is-saved"), 1200);
      updateProfileStatus(data.profile || {});
      hlog.info(`profile.${field} saved`, value || "(empty)");
    } catch (e) {
      input.classList.remove("is-saving");
      hlog.error(`profile.${field} save failed`, e?.message);
    }
  }, true);
}

async function loadSessionState() {
  if (!els.memorySessionSummary || !els.memorySessionStatus) return;
  try {
    const data = await fetch("/memory/session").then((r) => r.json());
    const max = data.verbatim_max ?? 10;
    const count = data.verbatim_count ?? 0;
    els.memorySessionStatus.textContent = `${count} / ${max} turns`;
    const summary = (data.rolling_summary || "").trim();
    if (summary) {
      els.memorySessionSummary.textContent = summary;
      els.memorySessionSummary.classList.remove("is-empty");
    } else {
      els.memorySessionSummary.textContent =
        "No summary yet. A summary is created when the conversation exceeds 10 exchanges.";
      els.memorySessionSummary.classList.add("is-empty");
    }
    renderRecentTurns(data.recent || []);
  } catch {
    els.memorySessionStatus.textContent = "offline";
    renderRecentTurns([]);
  }
}

function renderRecentTurns(recent) {
  if (!els.memoryRecentList) return;
  els.memoryRecentList.innerHTML = "";
  if (!recent.length) {
    const empty = document.createElement("li");
    empty.className = "memory-empty";
    empty.textContent = "No turns yet.";
    els.memoryRecentList.append(empty);
    return;
  }
  for (const turn of recent) {
    const li = document.createElement("li");
    li.className = "memory-turn";

    if (turn.user) {
      const row = document.createElement("div");
      row.className = "memory-turn-row";
      const role = document.createElement("span");
      role.className = "memory-turn-role";
      role.textContent = "You";
      const text = document.createElement("span");
      text.className = "memory-turn-text";
      text.textContent = turn.user;
      row.append(role, text);
      li.append(row);
    }
    if (turn.assistant) {
      const row = document.createElement("div");
      row.className = "memory-turn-row";
      const role = document.createElement("span");
      role.className = "memory-turn-role";
      role.textContent = "Hunt";
      const text = document.createElement("span");
      text.className = "memory-turn-text";
      text.textContent = turn.assistant;
      row.append(role, text);
      li.append(row);
    }
    els.memoryRecentList.append(li);
  }
}

async function loadProfileFacts() {
  if (!els.memoryFactsList) return;
  try {
    const data = await fetch("/memory/profile").then((r) => r.json());
    const facts = data.facts || [];
    els.memoryFactsList.innerHTML = "";
    if (!facts.length) {
      const empty = document.createElement("li");
      empty.className = "memory-empty";
      empty.textContent = 'No facts saved yet. Say "remember that…" or "my name is…".';
      els.memoryFactsList.append(empty);
      return;
    }
    for (const fact of facts) {
      const li = document.createElement("li");
      li.className = "memory-fact";
      const text = document.createElement("span");
      text.textContent = fact.content || "";
      const del = document.createElement("button");
      del.type = "button";
      del.className = "fact-delete";
      del.setAttribute("aria-label", "Delete this fact");
      del.textContent = "×";
      del.addEventListener("click", () => deleteFact(fact.id, li));
      li.append(text, del);
      els.memoryFactsList.append(li);
    }
  } catch {
    els.memoryFactsList.innerHTML = "";
    const err = document.createElement("li");
    err.className = "memory-empty";
    err.textContent = "Could not load profile facts.";
    els.memoryFactsList.append(err);
  }
}

async function deleteFact(id, listItem) {
  if (!id) return;
  try {
    const response = await fetch(`/memory/profile/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!response.ok) throw new Error(await response.text());
    listItem.remove();
    if (!els.memoryFactsList.querySelector(".memory-fact")) loadProfileFacts();
  } catch (error) {
    addMessage("system", `Could not delete fact: ${error.message}`);
  }
}

els.wipeProfileFactsButton?.addEventListener("click", async () => {
  const ok = window.confirm("Wipe ALL stored profile facts? This cannot be undone.");
  if (!ok) return;
  try {
    const response = await fetch("/memory/profile", { method: "DELETE" });
    if (!response.ok) throw new Error(await response.text());
    addMessage("system", "All profile facts wiped.");
    if (!els.memoryPanel?.hidden) loadProfileFacts();
  } catch (error) {
    addMessage("system", `Wipe failed: ${error.message}`);
  }
});

// ---------- Theme switcher (Light / Dark / System) ----------
// `prefs.theme` is "light" | "dark" | "system". When "system", we follow the
// OS via prefers-color-scheme and re-apply if the user toggles their OS theme.

function effectiveTheme() {
  if (prefs.theme === "light" || prefs.theme === "dark") return prefs.theme;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark" : "light";
}

function applyTheme() {
  document.documentElement.dataset.theme = effectiveTheme();
  const group = document.querySelector("#themeGroup");
  if (group) {
    for (const btn of group.querySelectorAll("button[data-theme]")) {
      const active = btn.dataset.theme === prefs.theme;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-checked", String(active));
    }
  }
}
applyTheme();

if (window.matchMedia) {
  // Re-apply when the OS flips themes — only relevant in "system" mode.
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => { if (prefs.theme === "system") applyTheme(); };
  if (mq.addEventListener) mq.addEventListener("change", onChange);
  else if (mq.addListener) mq.addListener(onChange);
}

const themeGroup = document.querySelector("#themeGroup");
if (themeGroup) {
  themeGroup.addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-theme]");
    if (!btn) return;
    prefs.theme = btn.dataset.theme;
    savePrefs();
    applyTheme();
    hlog.info("theme set", { pref: prefs.theme, effective: effectiveTheme() });
  });
}

// ---------- Collapsible popover sections ----------

function applyCollapsedSections() {
  const collapsed = new Set(prefs.collapsedSections || []);
  for (const section of document.querySelectorAll(".popover-section[data-section]")) {
    const id = section.dataset.section;
    const title = section.querySelector("button.popover-section-title");
    if (!title) continue;
    const isCollapsed = collapsed.has(id);
    title.setAttribute("aria-expanded", String(!isCollapsed));
  }
}
applyCollapsedSections();

document.addEventListener("click", (event) => {
  const title = event.target.closest("button.popover-section-title");
  if (!title) return;
  const section = title.closest(".popover-section[data-section]");
  if (!section) return;
  const id = section.dataset.section;
  const wasExpanded = title.getAttribute("aria-expanded") !== "false";
  title.setAttribute("aria-expanded", String(!wasExpanded));
  const set = new Set(prefs.collapsedSections || []);
  if (wasExpanded) set.add(id); else set.delete(id);
  prefs.collapsedSections = Array.from(set);
  savePrefs();
});

// Fall back to the lettermark if the brand image can't be loaded (file missing,
// 404, etc.). Toggling a class on the brand wrapper keeps all the styling in CSS.
(function setupBrandFallback() {
  const logo = document.querySelector("#brandLogo");
  const brand = document.querySelector(".brand");
  if (!logo || !brand) return;
  const markMissing = () => brand.classList.add("brand--no-logo");
  if (logo.complete && logo.naturalWidth === 0) {
    markMissing();
  } else {
    logo.addEventListener("error", markMissing);
  }
})();

hlog.info("UI loaded", {
  prefs,
  hunt_debug: HUNT_DEBUG,
  speech_supported: "speechSynthesis" in window,
});

refreshStatus();
setInterval(refreshStatus, 20000);

// ---------- Mute button (Phase G) ----------
// Persistent topbar toggle that drives the existing #speakToggle. Clicking
// also cancels any current TTS so the user can shut Hunt up mid-sentence
// without diving into Settings. Keyboard shortcut: M (when not typing).

function applyMuteState() {
  const speakOn = !!els.speakToggle?.checked;
  if (els.muteButton) {
    els.muteButton.setAttribute("aria-pressed", String(!speakOn));
    els.muteButton.setAttribute("aria-label", speakOn ? "Mute Hunt" : "Unmute Hunt");
    els.muteButton.title = speakOn ? "Mute voice (M)" : "Unmute voice (M)";
  }
  // Inline SVGs sometimes ignore the `hidden` attribute; set display directly
  // too so only one icon ever shows regardless of browser quirks.
  if (els.muteIconOn) {
    els.muteIconOn.hidden = !speakOn;
    els.muteIconOn.style.display = speakOn ? "" : "none";
  }
  if (els.muteIconOff) {
    els.muteIconOff.hidden = speakOn;
    els.muteIconOff.style.display = speakOn ? "none" : "";
  }
}

function toggleMute() {
  if (!els.speakToggle) return;
  els.speakToggle.checked = !els.speakToggle.checked;
  // Reuse the existing handler that persists the change to prefs.
  els.speakToggle.dispatchEvent(new Event("change"));
  applyMuteState();
  // When muting, cut any in-progress speech immediately.
  if (!els.speakToggle.checked && typeof stopSpeech === "function") stopSpeech();
}

els.muteButton?.addEventListener("click", toggleMute);
// Keep the topbar icon in sync if the user flips speak-replies in Settings.
els.speakToggle?.addEventListener("change", applyMuteState);
// M key shortcut, but only when not in a text input.
document.addEventListener("keydown", (event) => {
  if (event.key !== "m" && event.key !== "M") return;
  const t = event.target;
  if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  event.preventDefault();
  toggleMute();
});
applyMuteState();

// =========================================================
// Aurora layout — state machine, greeting, dashboard cards,
// topbar menu, theme cycle, transitions.
// Hooks into existing chat/voice/panel handlers without
// rewriting them — observes #messages mutations for the
// idle↔active transition and listens at the capture phase
// on form submit + chip clicks.
// =========================================================

const AURORA = {
  shell: document.body,
  dateChipText: document.querySelector("#dateChipText"),
  heroHeadline: document.querySelector("#heroHeadline"),
  heroSubline: document.querySelector("#heroSubline"),
  heroGreeting: document.querySelector("#heroGreeting"),
  dashboardCards: document.querySelector("#dashboardCards"),
  activeThread: document.querySelector("#activeThread"),
  blob: document.querySelector("#blob"),
  menuButton: document.querySelector("#menuButton"),
  menuPopover: document.querySelector("#menuPopover"),
  themeToggleBtn: document.querySelector("#themeToggleBtn"),
  historyLink: document.querySelector("#historyLink"),
  menuApiRow: document.querySelector("#menuApiRow"),
  menuLlmRow: document.querySelector("#menuLlmRow"),
  menuMemRow: document.querySelector("#menuMemRow"),
  menuMongoRow: document.querySelector("#menuMongoRow"),
};
state.uiState = "idle";

function auroraEscape(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---- Date chip ----
function renderDateChip() {
  if (!AURORA.dateChipText) return;
  const now = new Date();
  const date = now.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  const time = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  AURORA.dateChipText.textContent = `${date}  ·  ${time}`;
}
renderDateChip();
setInterval(renderDateChip, 30000);

// ---- Greeting (animated word reveal) ----
function auroraWords(text) {
  if (!text) return "";
  return text.trim().split(/\s+/).map((w, i) =>
    `<span class="word" style="--word-i: ${i}">${auroraEscape(w)}</span>`
  ).join(" ");
}
async function renderGreeting() {
  if (!AURORA.heroHeadline || !AURORA.heroSubline) return;
  let name = "";
  try {
    const data = await fetch("/profile").then((r) => r.json());
    name = (data?.profile?.name || "").trim();
  } catch {}
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : h < 21 ? "Good evening" : "Good night";
  const greet = name ? `${part}, ${name}.` : `${part}.`;
  const subs = [
    "How can I help today?",
    "What are we building today?",
    "Where would you like to start?",
    "Ask me anything.",
  ];
  const sub = subs[Math.floor((Date.now() / 60000) % subs.length)];
  AURORA.heroHeadline.innerHTML = auroraWords(greet);
  AURORA.heroSubline.innerHTML = auroraWords(sub);
}

// ---- Dashboard cards ----
async function renderDashboardCards() {
  if (!AURORA.dashboardCards) return;
  let projData = null, actionsData = null, sessionData = null, mongoData = null;
  try {
    [projData, actionsData, sessionData, mongoData] = await Promise.all([
      fetch("/projects").then((r) => r.json()).catch(() => null),
      fetch("/actions?limit=3").then((r) => r.json()).catch(() => null),
      fetch("/memory/session").then((r) => r.json()).catch(() => null),
      fetch("/mongo/status").then((r) => r.json()).catch(() => null),
    ]);
  } catch {}

  const projects = projData?.projects || [];
  const active = projects.find((p) => p.id === projData?.active_project_id) || null;
  const actions = actionsData?.actions || [];

  AURORA.dashboardCards.innerHTML = "";

  // Card 1: Active project (or new-project CTA)
  const c1 = document.createElement("button");
  c1.type = "button";
  c1.className = "dash-card";
  c1.style.setProperty("--card-i", "0");
  if (active) {
    const openTasks = (active.open_tasks || []).filter((t) => !t.done).length;
    c1.innerHTML =
      '<div class="dash-card-head"><span class="dash-card-title">Active project</span></div>' +
      '<div class="dash-card-body">' +
        `<div class="dash-card-headline">${auroraEscape(active.name)}</div>` +
        (active.stack ? `<span class="dash-card-tag">${auroraEscape(active.stack)}</span>` : "") +
        `<span class="dash-card-meta">${openTasks} open task${openTasks === 1 ? "" : "s"}</span>` +
      "</div>";
    c1.addEventListener("click", () => {
      openMemoryPanel();
      setTimeout(() => { try { openProjectDetail(active.id); } catch {} }, 120);
    });
  } else {
    c1.innerHTML =
      '<div class="dash-card-head"><span class="dash-card-title">Projects</span></div>' +
      '<div class="dash-card-body">' +
        `<div class="dash-card-headline">${projects.length} project${projects.length === 1 ? "" : "s"}</div>` +
        '<span class="dash-card-meta">No active project</span>' +
        '<span class="dash-cta">Open Memory →</span>' +
      "</div>";
    c1.addEventListener("click", () => openMemoryPanel());
  }
  AURORA.dashboardCards.append(c1);

  // Card 2: Recent actions
  const c2 = document.createElement("button");
  c2.type = "button";
  c2.className = "dash-card";
  c2.style.setProperty("--card-i", "1");
  let rowsHtml = "";
  if (actions.length) {
    rowsHtml = actions.map((a) => {
      const status = (a.status || "").toLowerCase();
      const cls = a.decision === "deny" ? "is-denied" : ("is-" + status);
      const label = (typeof ACTION_LABELS !== "undefined" ? ACTION_LABELS[a.action] : null) || a.action;
      const params = (typeof summarizeParams === "function") ? summarizeParams(a.params) : "";
      const t = (typeof formatActionTime === "function") ? formatActionTime(a.timestamp) : "";
      return `<div class="dash-card-row ${cls}"><span class="dash-dot"></span>` +
             `<span class="dash-row-label">${auroraEscape(label)}${params ? " · " + auroraEscape(params) : ""}</span>` +
             `<span class="dash-row-time">${auroraEscape(t)}</span></div>`;
    }).join("");
  } else {
    rowsHtml = '<span class="dash-card-meta">No actions yet. Try "open notepad".</span>';
  }
  c2.innerHTML =
    '<div class="dash-card-head"><span class="dash-card-title">Recent actions</span></div>' +
    '<div class="dash-card-body"><div class="dash-card-rows">' + rowsHtml + '</div></div>';
  c2.addEventListener("click", () => {
    if (els.settingsPopover) {
      els.settingsPopover.hidden = false;
      els.settingsButton?.setAttribute("aria-expanded", "true");
    }
  });
  AURORA.dashboardCards.append(c2);

  // Card 3: This chat
  const c3 = document.createElement("button");
  c3.type = "button";
  c3.className = "dash-card";
  c3.style.setProperty("--card-i", "2");
  const verbCount = sessionData?.verbatim_count ?? 0;
  const max = sessionData?.verbatim_max ?? 10;
  let mongoLabel = "—";
  if (mongoData?.enabled) {
    mongoLabel = mongoData.connected ? "✓ synced" : "⚠ offline";
  }
  c3.innerHTML =
    '<div class="dash-card-head"><span class="dash-card-title">This chat</span></div>' +
    '<div class="dash-card-body">' +
      `<div class="dash-card-headline">${verbCount} / ${max} turns</div>` +
      `<span class="dash-card-meta">Cloud sync · ${mongoLabel}</span>` +
    "</div>";
  c3.addEventListener("click", () => openMemoryPanel());
  AURORA.dashboardCards.append(c3);
}

// ---- State machine ----
function transitionToActiveState() {
  if (state.uiState === "active") return;
  state.uiState = "active";
  AURORA.heroGreeting?.classList.add("is-leaving");
  AURORA.dashboardCards?.classList.add("is-leaving");
  AURORA.shell.classList.add("is-active");
  hlog.info("aurora → active");
}
function transitionToIdleState() {
  if (state.uiState === "idle") return;
  state.uiState = "idle";
  AURORA.heroGreeting?.classList.remove("is-leaving");
  AURORA.dashboardCards?.classList.remove("is-leaving");
  AURORA.shell.classList.remove("is-active");
  renderGreeting();
  renderDashboardCards();
  hlog.info("aurora → idle");
}

// ---- Hook idle → active on chat send / chip click (capture phase
// so we run before sendMessage's own handlers) ----
if (els.chatForm) {
  els.chatForm.addEventListener("submit", () => {
    if (state.uiState === "idle") transitionToActiveState();
  }, true);
}
document.querySelectorAll("[data-prompt]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (state.uiState === "idle") transitionToActiveState();
  }, true);
});

// ---- Hook active → idle when #messages is cleared by Clear / + New ----
if (els.messages) {
  let lastCount = els.messages.children.length;
  new MutationObserver(() => {
    const now = els.messages.children.length;
    if (now === 0 && lastCount > 0 && state.uiState === "active") {
      transitionToIdleState();
    }
    lastCount = now;
  }).observe(els.messages, { childList: true });
}

// ---- Topbar menu popover ----
if (AURORA.menuButton && AURORA.menuPopover) {
  AURORA.menuButton.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = AURORA.menuPopover.hidden;
    AURORA.menuPopover.hidden = !open;
    AURORA.menuButton.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (e) => {
    if (AURORA.menuPopover.hidden) return;
    if (AURORA.menuButton.contains(e.target)) return;
    if (AURORA.menuPopover.contains(e.target)) return;
    AURORA.menuPopover.hidden = true;
    AURORA.menuButton.setAttribute("aria-expanded", "false");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !AURORA.menuPopover.hidden) {
      AURORA.menuPopover.hidden = true;
      AURORA.menuButton.setAttribute("aria-expanded", "false");
    }
  });
  AURORA.menuPopover.addEventListener("click", (e) => {
    const item = e.target.closest("[data-menu]");
    if (!item) return;
    AURORA.menuPopover.hidden = true;
    AURORA.menuButton.setAttribute("aria-expanded", "false");
    const action = item.dataset.menu;
    if (action === "sessions") openSessionsPanel();
    else if (action === "memory") openMemoryPanel();
    else if (action === "projects") openMemoryPanel();
    else if (action === "settings" && els.settingsPopover) {
      els.settingsPopover.hidden = false;
      els.settingsButton?.setAttribute("aria-expanded", "true");
    }
  });
}

// ---- Theme toggle (cycle light → dark → system) ----
AURORA.themeToggleBtn?.addEventListener("click", () => {
  const order = ["light", "dark", "system"];
  const i = order.indexOf(prefs.theme);
  prefs.theme = order[(i + 1) % order.length];
  savePrefs();
  applyTheme();
  hlog.info("theme cycled →", prefs.theme);
});

// ---- History link → sessions panel ----
AURORA.historyLink?.addEventListener("click", () => openSessionsPanel());

// ---- Mirror status-pill colors into the menu rows + mongo row ----
async function syncMenuStatus() {
  function copy(pillId, rowEl) {
    if (!rowEl) return;
    const pill = document.querySelector("#" + pillId);
    if (!pill) return;
    rowEl.classList.remove("ok", "warn", "bad");
    ["ok", "warn", "bad"].forEach((k) => { if (pill.classList.contains(k)) rowEl.classList.add(k); });
  }
  copy("apiPill", AURORA.menuApiRow);
  copy("llmPill", AURORA.menuLlmRow);
  copy("memPill", AURORA.menuMemRow);
  // Mongo status comes from /mongo/status — reuse the existing fetch.
  try {
    const m = await fetch("/mongo/status").then((r) => r.json());
    if (AURORA.menuMongoRow) {
      AURORA.menuMongoRow.classList.remove("ok", "warn", "bad");
      if (!m.enabled) AURORA.menuMongoRow.classList.add("warn");
      else if (m.connected) AURORA.menuMongoRow.classList.add("ok");
      else AURORA.menuMongoRow.classList.add("bad");
    }
  } catch {}
}
syncMenuStatus();
setInterval(syncMenuStatus, 10000);

// ---- First paint ----
renderGreeting();
renderDashboardCards();
// Refresh dashboard every 60s while idle so action history + turn counts stay live
setInterval(() => { if (state.uiState === "idle") renderDashboardCards(); }, 60000);