/**
 * app.js – Vanilla JS frontend for Correspond
 *
 * All AI calls go through window.pywebview.api (Python bridge).
 * Long jobs return a {job_id} immediately; results arrive via
 * window.onJobEvent({job_id, event, data}).
 *
 * Constraints honoured here:
 *  - UI never freezes: all API calls are async; spinners shown.
 *  - No auto-send: Outlook "send" requires countdown confirmation.
 *  - Streaming tokens shown in real time.
 */

"use strict";

// Reload the native window in place after a UI edit; there is no need to
// close and relaunch Correspond to pick up HTML, CSS, or JavaScript changes.
window.addEventListener("keydown", event => {
  const reloadKey = event.key === "F5" || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "r");
  if (!reloadKey) return;
  event.preventDefault();
  window.location.reload();
});

/* ─────────────────────────────────────────────────────────────────
   1. BRIDGE HELPER
   pywebview exposes the api only after the DOM + Python are ready.
   We queue calls until it is available.
───────────────────────────────────────────────────────────────── */
const _queue = [];
let _apiReady = false;

function api(method, ...args) {
  return new Promise((resolve, reject) => {
    const call = () => {
      try {
        const raw = window.pywebview.api[method](...args);
        Promise.resolve(raw).then(resolve).catch(reject);
      } catch (err) {
        reject(err);
      }
    };
    if (_apiReady) call();
    else _queue.push(call);
  });
}

window.addEventListener("pywebviewready", () => {
  _apiReady = true;
  _queue.forEach(fn => fn());
  _queue.length = 0;
  boot();
});

// Fallback for dev/hot-reload without pywebview (shows a warning banner).
window.addEventListener("DOMContentLoaded", () => {
  setTimeout(() => {
    if (!_apiReady) {
      console.warn("[app.js] pywebview API not ready – mock mode");
      _apiReady = true;
      _queue.forEach(fn => fn());
      _queue.length = 0;
      boot();
    }
  }, 1500);
});

/* ─────────────────────────────────────────────────────────────────
   2. JOB EVENT DISPATCHER
   Python calls window.onJobEvent({job_id, event, data})
───────────────────────────────────────────────────────────────── */
const _jobListeners = {};

/** Register a listener for a specific job. Returns a cleanup fn. */
function onJob(jobId, listener) {
  _jobListeners[jobId] = listener;
  return () => delete _jobListeners[jobId];
}

window.onJobEvent = function (payload) {
  const { job_id, event, data } = payload;
  const listener = _jobListeners[job_id];
  if (listener) listener(event, data || {});
};

/* ─────────────────────────────────────────────────────────────────
   3. TOAST NOTIFICATIONS
───────────────────────────────────────────────────────────────── */
const toastContainer = document.getElementById("toast-container");

function toast(msg, type = "info", duration = 4000) {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${msg}</span>`;
  toastContainer.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

/* ─────────────────────────────────────────────────────────────────
   4. SCREEN NAVIGATION
───────────────────────────────────────────────────────────────── */
const screens = {
  "new-email": document.getElementById("screen-new-email"),
  "reply-email": document.getElementById("screen-reply-email"),
  "queue":     document.getElementById("screen-queue"),
  "settings":  document.getElementById("screen-settings"),
};

const navBtns = document.querySelectorAll(".nav-btn[data-screen]");

function showScreen(name) {
  Object.entries(screens).forEach(([key, el]) => {
    el.classList.toggle("active", key === name);
  });
  navBtns.forEach(btn => {
    btn.classList.toggle("active", btn.dataset.screen === name);
  });
  if (name === "queue") loadQueue();
  if (name === "settings") loadSettingsScreen();
}

navBtns.forEach(btn => btn.addEventListener("click", () => showScreen(btn.dataset.screen)));

const welcomeView = document.getElementById("welcome-view");
const composeView = document.getElementById("compose-view");
document.getElementById("welcome-draft-btn").addEventListener("click", openComposer);

function openComposer() {
  if (!composeView || composeView.classList.contains("is-open")) return;
  welcomeView?.classList.add("is-leaving");
  window.setTimeout(() => {
    if (welcomeView) welcomeView.style.display = "none";
    composeView.classList.add("is-open");
    window.setTimeout(() => document.getElementById("user-input")?.focus(), 240);
  }, 170);
}

/* ─────────────────────────────────────────────────────────────────
   5. SYSTEM STATUS (sidebar dot)
───────────────────────────────────────────────────────────────── */
const statusDot  = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");

async function refreshSidebarStatus() {
  try {
    const s = await api("env_status");
    if (s.ollama && s.ollama.running && s.ollama.model_present) {
      statusDot.className = "ok";
      statusText.textContent = "Local model ready";
    } else if (s.ollama && s.ollama.running) {
      statusDot.className = "warn";
      statusText.textContent = "Model missing";
    } else {
      statusDot.className = "error";
      statusText.textContent = "Ollama unavailable";
    }
    return s;
  } catch {
    statusDot.className = "error";
    statusText.textContent = "Not connected";
    return null;
  }
}

/* ─────────────────────────────────────────────────────────────────
   6. CONTACT PICKER (searchable dropdown)
───────────────────────────────────────────────────────────────── */
let allContacts = [];
let selectedContactId = null;

const contactInput      = document.getElementById("contact-input");
const contactDropdown   = document.getElementById("contact-dropdown");
const selectedContactEl = document.getElementById("contact-selected-info");
const hiddenContactId   = document.getElementById("selected-contact-id");

async function loadContacts() {
  try {
    const res = await api("list_contacts");
    allContacts = res.contacts || [];
    refreshReplyContactOptions();
  } catch (e) {
    allContacts = [];
  }
}

function renderDropdown(matches) {
  contactDropdown.innerHTML = "";
  if (!matches.length) {
    contactDropdown.classList.remove("open");
    return;
  }
  matches.forEach(c => {
    const div = document.createElement("div");
    div.className = "contact-option";
    div.setAttribute("role", "option");
    div.dataset.id = c.id;
    div.innerHTML = `<strong>${esc(c.name)}</strong><div class="opt-email">${esc(c.email)}${c.aliases ? " · " + esc(c.aliases) : ""}</div>`;
    div.addEventListener("mousedown", (e) => {
      e.preventDefault();
      selectContact(c);
    });
    contactDropdown.appendChild(div);
  });
  contactDropdown.classList.add("open");
}

function selectContact(c) {
  selectedContactId = c ? c.id : null;
  hiddenContactId.value = c ? c.id : "";
  contactInput.value = c ? c.name : "";
  contactDropdown.classList.remove("open");
  selectedContactEl.textContent = c
    ? `Email: ${c.email}${c.salutation ? " · " + c.salutation : ""}`
    : "";
}

function getRecipientEmail() {
  const selected = allContacts.find(contact => contact.id === selectedContactId);
  if (selected) return selected.email;
  const typed = contactInput.value.trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(typed) ? typed : "";
}

contactInput.addEventListener("input", () => {
  const q = contactInput.value.trim().toLowerCase();
  if (selectedContactId) {
    const selected = allContacts.find(c => c.id === selectedContactId);
    if (!selected || q !== selected.name.toLowerCase()) {
      selectedContactId = null;
      hiddenContactId.value = "";
      selectedContactEl.textContent = "";
    }
  }
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(q)) selectedContactEl.textContent = `Email: ${contactInput.value.trim()}`;
  if (!q) { contactDropdown.classList.remove("open"); return; }
  const hits = allContacts.filter(c =>
    c.name.toLowerCase().includes(q) ||
    (c.aliases || "").toLowerCase().includes(q) ||
    c.email.toLowerCase().includes(q)
  ).slice(0, 8);
  renderDropdown(hits);
});

contactInput.addEventListener("keydown", (e) => {
  const items = contactDropdown.querySelectorAll(".contact-option");
  if (!items.length) return;
  let focused = contactDropdown.querySelector(".focused");
  if (e.key === "ArrowDown") {
    e.preventDefault();
    const next = focused ? focused.nextElementSibling : items[0];
    if (next) { focused && focused.classList.remove("focused"); next.classList.add("focused"); }
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    const prev = focused ? focused.previousElementSibling : items[items.length - 1];
    if (prev) { focused && focused.classList.remove("focused"); prev.classList.add("focused"); }
  } else if (e.key === "Enter" && focused) {
    e.preventDefault();
    const id = parseInt(focused.dataset.id);
    const c = allContacts.find(x => x.id === id);
    if (c) selectContact(c);
  } else if (e.key === "Escape") {
    contactDropdown.classList.remove("open");
  }
});

contactInput.addEventListener("blur", () => {
  // Delay so mousedown on option fires first
  setTimeout(() => contactDropdown.classList.remove("open"), 180);
});

/* Auto-resolve from user's typed message */
async function tryAutoResolveContact(text) {
  if (!text || selectedContactId) return;
  try {
    const res = await api("resolve_contact", text);
    const cands = res.candidates || [];
    if (cands.length === 1) {
      selectContact(cands[0]);
      toast(`Contact found: ${cands[0].name}`, "info", 2500);
    } else if (cands.length > 1) {
      renderDropdown(cands);
      contactInput.focus();
    }
  } catch (_) {}
}

/* ─────────────────────────────────────────────────────────────────
   7. VOICE RECORDING (MediaRecorder)
───────────────────────────────────────────────────────────────── */
const micBtn      = document.getElementById("mic-btn");
const recStatus   = document.getElementById("rec-status");
const stopRecBtn  = document.getElementById("stop-rec-btn");
const userInput   = document.getElementById("user-input");

let mediaRecorder = null;
let audioChunks   = [];
let isRecording   = false;

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];
    mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
    mediaRecorder.onstop = handleRecordingStop;
    mediaRecorder.start();
    isRecording = true;
    micBtn.classList.add("recording");
    micBtn.title = "Recording…";
    recStatus.classList.remove("hidden");
  } catch (err) {
    toast("Microphone unavailable: " + err.message, "error");
  }
}

function stopRecording() {
  if (mediaRecorder && isRecording) {
    mediaRecorder.stop();
    mediaRecorder.stream.getTracks().forEach(t => t.stop());
    isRecording = false;
    micBtn.classList.remove("recording");
    micBtn.title = "Start recording";
    recStatus.classList.add("hidden");
  }
}

async function handleRecordingStop() {
  const blob = new Blob(audioChunks, { type: "audio/webm" });
  if (!blob.size) {
    toast("No audio was captured. Hold the microphone a little longer and try again.", "warn");
    return;
  }
  // Send as base64 to backend
  const reader = new FileReader();
  reader.onload = async () => {
    const base64 = reader.result.split(",")[1];
    toast("Transcribing on this device…", "info", 8000);
    try {
      const res = await api("transcribe_base64", base64);
      if (res && res.job_id) {
        micBtn.disabled = true;
        onJob(res.job_id, (event, data) => {
          if (event === "complete") {
            micBtn.disabled = false;
            const transcript = String(data.text || "").trim();
            if (!transcript) {
              toast("No speech was detected. Check your microphone and try speaking a little closer to it.", "warn", 6000);
              return;
            }
            const existing = userInput.value.trim();
            userInput.value = existing ? `${existing}\n${transcript}` : transcript;
            userInput.focus();
            userInput.setSelectionRange(userInput.value.length, userInput.value.length);
            userInput.dispatchEvent(new Event("input", { bubbles: true }));
            toast("Transcription added to your message. Review and edit it before drafting.", "success");
          } else if (event === "error") {
            micBtn.disabled = false;
            toast("Transcription failed: " + (data.message || ""), "error");
          }
        });
      } else {
        toast("Could not start transcription. Please try again.", "error");
      }
    } catch (e) {
      micBtn.disabled = false;
      toast("" + e.message, "error");
    }
  };
  reader.readAsDataURL(blob);
}

micBtn.addEventListener("click", () => {
  if (!isRecording) startRecording();
  else stopRecording();
});

stopRecBtn.addEventListener("click", stopRecording);

/* Check Whisper availability and disable mic if missing */
async function checkMicAvailability(envStatus) {
  if (envStatus && !envStatus.whisper_model_available) {
    micBtn.disabled = true;
    micBtn.title = "Whisper model missing. Run scripts/download_whisper.py.";
  }
}

/* ─────────────────────────────────────────────────────────────────
   8. DRAFT EMAIL FLOW
───────────────────────────────────────────────────────────────── */
const draftBtn         = document.getElementById("draft-btn");
const thinkingBar      = document.getElementById("thinking-bar");
const draftProgress    = document.getElementById("draft-progress");
const resultSection    = document.getElementById("result-section");
const emailSubject     = document.getElementById("email-subject");
const emailBody        = document.getElementById("email-body");
const summaryHi        = document.getElementById("summary-hi");
const streamStatus     = document.getElementById("stream-status");
const streamFields = {
  subject: { output: document.getElementById("email-subject"), reveal: document.getElementById("email-subject") },
  email_en: { output: document.getElementById("email-body"), reveal: document.getElementById("email-body") },
  summary_hi: { output: document.getElementById("summary-hi"), reveal: document.getElementById("draft-summary-card") },
};
const streamState = {};
let streamTimer = null;

let currentDraftId = null;
let currentFactCheck = null;

draftBtn.addEventListener("click", async () => {
  const text = userInput.value.trim();
  if (!text) {
    toast("Enter or speak a message first.", "warn");
    return;
  }
  composeView?.classList.add("is-drafting");

  // Try to auto-resolve contact from text if not already selected
  if (!selectedContactId && !contactInput.value.trim()) await tryAutoResolveContact(text);

  setDraftingState(true);
  resultSection.classList.add("visible");
  startFieldStream();
  document.getElementById("draft-output-card").scrollIntoView({ behavior: "smooth", block: "nearest" });

  try {
    const res = await api("draft_email", selectedContactId || null, text, getRecipientEmail());
    if (!res || !res.job_id) throw new Error("No job ID returned.");

    onJob(res.job_id, (event, data) => {
      if (event === "field") {
        updateFieldStream(data);
      } else if (event === "stage") {
        setStreamStage(data.stage);
      } else if (event === "complete") {
        finishFieldStream(data).then(() => {
          setDraftingState(false);
          transitionToFinalDraft(data);
        });
      } else if (event === "error") {
        stopFieldStream();
        setDraftingState(false);
        draftProgress.classList.add("error");
        streamStatus.textContent = "Draft failed. Please try again.";
        toast(data.message || "An unknown error occurred.", "error", 7000);
      }
    });
  } catch (e) {
    stopFieldStream();
    setDraftingState(false);
    draftProgress.classList.add("error");
    streamStatus.textContent = "Draft failed. Please try again.";
    toast("" + e.message, "error", 7000);
  }
});

function setDraftingState(active) {
  draftBtn.disabled = active;
  thinkingBar.classList.toggle("visible", active);
  micBtn.disabled = active;
}

function startFieldStream() {
  if (streamTimer) { clearInterval(streamTimer); streamTimer = null; }
  for (const [key, field] of Object.entries(streamFields)) {
    streamState[key] = { target: "", shown: "", complete: false };
    if (key === "summary_hi") field.output.textContent = "";
    else field.output.value = "";
    field.reveal.classList.add("stream-hidden");
  }
  setStreamStage("subject");
  draftProgress.classList.remove("error");
  draftProgress.classList.add("visible");
  document.getElementById("draft-refine-actions").classList.add("stream-hidden");
  document.getElementById("draft-summary-card").classList.add("stream-hidden");
}

function transitionToFinalDraft(data) {
  showResult(data);
}

function stopFieldStream() {
  if (streamTimer) { clearInterval(streamTimer); streamTimer = null; }
}

function setStreamStage(stage) {
  const copy = {
    subject: "Drafting subject…",
    email: "Drafting email…",
    translation: "Preparing Hindi translation…",
    verifying: "Checking names, dates, and amounts…",
    ready: "Draft ready to review",
  };
  if (streamStatus && copy[stage]) streamStatus.textContent = copy[stage];
}

function updateFieldStream(data) {
  const key = data.field;
  const field = streamFields[key];
  if (!field) return;
  const state = streamState[key];
  if (!state) return;
  state.target = String(data.value || "");
  state.complete = Boolean(data.complete);
  const stage = key === "subject" ? "subject" : key === "email_en" ? "email" : "translation";
  setStreamStage(stage);
  if (key === "subject" && state.complete) field.reveal.classList.remove("stream-hidden");
  if (key === "email_en" && state.target) field.reveal.classList.remove("stream-hidden");
  if (key === "summary_hi" && state.complete) field.reveal.classList.remove("stream-hidden");
  if (!streamTimer) streamTimer = setInterval(pumpFieldStream, 34);
}

function pumpFieldStream() {
  for (const [key, field] of Object.entries(streamFields)) {
    const state = streamState[key];
    if (!state || state.shown.length >= state.target.length) continue;
    const rest = state.target.slice(state.shown.length);
    const whitespace = /\s/.exec(rest);
    if (!whitespace && !state.complete) continue;
    const end = whitespace ? state.shown.length + whitespace.index + 1 : state.target.length;
    state.shown = state.target.slice(0, end);
    if (key === "summary_hi") field.output.textContent = state.shown;
    else field.output.value = state.shown;
  }
}

function finishFieldStream(draft) {
  for (const [key, dataKey] of [["subject", "subject"], ["email_en", "email_en"], ["summary_hi", "summary_hi"]]) {
    updateFieldStream({ field: key, value: draft[dataKey] || "", complete: true });
  }
  setStreamStage("verifying");
  return new Promise(resolve => {
    const finishCheck = setInterval(() => {
      pumpFieldStream();
      const done = Object.entries(streamFields).every(([key]) => {
        const state = streamState[key];
        return state?.complete && state.shown === state.target;
      });
      if (done) {
        clearInterval(finishCheck);
        if (streamTimer) { clearInterval(streamTimer); streamTimer = null; }
        setStreamStage("ready");
        resolve();
      }
    }, 28);
  });
}

function showResult(data) {
  currentDraftId  = data.id || null;
  currentFactCheck = data.factcheck || data.factcheck_json || null;

  emailSubject.value = data.subject || "";
  emailBody.value    = data.email_en || "";
  summaryHi.textContent = data.summary_hi || "";
  emailSubject.classList.remove("stream-hidden");
  emailBody.classList.remove("stream-hidden");
  document.getElementById("draft-refine-actions").classList.remove("stream-hidden");
  document.getElementById("draft-summary-card").classList.remove("stream-hidden");
  draftProgress.classList.remove("visible");

  // needs_review banner
  if (data.needs_review) {
    toast("The draft could not be fully structured. Review it carefully.", "warn", 7000);
  }

  resultSection.classList.add("visible");
  resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ─────────────────────────────────────────────────────────────────
   9. REDRAFT (make shorter / more formal)
───────────────────────────────────────────────────────────────── */
document.getElementById("redraft-shorter-btn").addEventListener("click", () =>
  doRedraft("Make the email shorter, keep all facts.")
);
document.getElementById("redraft-formal-btn").addEventListener("click", () =>
  doRedraft("Make the tone more formal and professional.")
);

async function doRedraft(instruction) {
  if (!currentDraftId) { toast("Create a draft first.", "warn"); return; }
  setDraftingState(true);
  startFieldStream();
  try {
    const res = await api("redraft", currentDraftId, instruction);
    onJob(res.job_id, (event, data) => {
      if (event === "field") {
        updateFieldStream(data);
      } else if (event === "stage") {
        setStreamStage(data.stage);
      } else if (event === "complete") {
        finishFieldStream(data).then(() => {
          setDraftingState(false);
          transitionToFinalDraft({ ...data, id: currentDraftId });
        });
      } else if (event === "error") {
        stopFieldStream();
        setDraftingState(false);
        draftProgress.classList.add("error");
        streamStatus.textContent = "Revision failed. Please try again.";
        toast("" + (data.message || ""), "error");
      }
    });
  } catch (e) {
    stopFieldStream();
    setDraftingState(false);
    draftProgress.classList.add("error");
    streamStatus.textContent = "Revision failed. Please try again.";
    toast("" + e.message, "error");
  }
}

/* ─────────────────────────────────────────────────────────────────
   9a. GRAMMAR POLISH + REPLY PROMPT
───────────────────────────────────────────────────────────────── */
async function runAssistedDraft(task, source, notes, contactId, recipientEmail = "") {
  setDraftingState(true);
  resultSection.classList.add("visible");
  startFieldStream();
  document.getElementById("draft-output-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const res = await api("assist_email", task, source, notes, contactId || null, recipientEmail);
    if (!res?.job_id) throw new Error("Could not start email assistance.");
    onJob(res.job_id, (event, data) => {
      if (event === "field") updateFieldStream(data);
      else if (event === "stage") setStreamStage(data.stage);
      else if (event === "complete") {
        finishFieldStream(data).then(() => {
          setDraftingState(false);
          transitionToFinalDraft(data);
        });
      } else if (event === "error") {
        stopFieldStream();
        setDraftingState(false);
        draftProgress.classList.add("error");
        streamStatus.textContent = "Email assistance failed. Please try again.";
        toast(data.message || "Email assistance failed.", "error", 7000);
      }
    });
  } catch (error) {
    stopFieldStream();
    setDraftingState(false);
    draftProgress.classList.add("error");
    streamStatus.textContent = "Email assistance failed. Please try again.";
    toast(error.message, "error", 7000);
  }
}

const replySource = document.getElementById("reply-source");
const replyContactInput = document.getElementById("reply-recipient-input");
const replyContactDropdown = document.getElementById("reply-contact-dropdown");
let replySelectedContactId = null;
const replyModal = document.getElementById("reply-modal-overlay");
const replyNotes = document.getElementById("reply-notes");

function refreshReplyContactOptions() {
  if (!replyContactInput || !replySelectedContactId) return;
  if (!allContacts.some(item => item.id === replySelectedContactId)) {
    replySelectedContactId = null;
    replyContactInput.value = "";
    document.getElementById("reply-recipient-email").textContent = "";
  }
}

replyContactInput.addEventListener("input", () => {
  const query = replyContactInput.value.trim().toLowerCase();
  const selected = allContacts.find(contact => contact.id === replySelectedContactId);
  if (selected && query !== selected.name.toLowerCase()) replySelectedContactId = null;
  const emailInfo = document.getElementById("reply-recipient-email");
  emailInfo.textContent = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(query) ? `Email: ${replyContactInput.value.trim()}` : "";
  replyContactDropdown.innerHTML = "";
  if (!query) { replyContactDropdown.classList.remove("open"); return; }
  const matches = allContacts.filter(contact => contact.name.toLowerCase().includes(query)
    || contact.email.toLowerCase().includes(query)
    || (contact.aliases || "").toLowerCase().includes(query)).slice(0, 8);
  matches.forEach(contact => {
    const option = document.createElement("div");
    option.className = "contact-option";
    option.setAttribute("role", "option");
    option.innerHTML = `<strong>${esc(contact.name)}</strong><div class="opt-email">${esc(contact.email)}${contact.aliases ? ` · ${esc(contact.aliases)}` : ""}</div>`;
    option.addEventListener("mousedown", event => {
      event.preventDefault();
      replySelectedContactId = contact.id;
      replyContactInput.value = contact.name;
      emailInfo.textContent = `Email: ${contact.email}`;
      replyContactDropdown.classList.remove("open");
    });
    replyContactDropdown.appendChild(option);
  });
  replyContactDropdown.classList.toggle("open", matches.length > 0);
});
replyContactInput.addEventListener("blur", () => {
  setTimeout(() => replyContactDropdown.classList.remove("open"), 180);
});

document.getElementById("reply-continue-btn").addEventListener("click", () => {
  if (!replySource.value.trim()) { toast("Paste the email you received first.", "warn"); return; }
  replyNotes.value = "";
  replyModal.classList.add("open");
  replyNotes.focus();
});
document.getElementById("reply-modal-cancel").addEventListener("click", () => replyModal.classList.remove("open"));
replyModal.addEventListener("click", event => { if (event.target === replyModal) replyModal.classList.remove("open"); });
document.getElementById("reply-modal-create").addEventListener("click", async () => {
  if (!replyNotes.value.trim()) { toast("Tell me how you want to respond.", "warn"); return; }
  const source = replySource.value.trim();
  const notes = replyNotes.value.trim();
  const contact = allContacts.find(item => item.id === replySelectedContactId);
  const typedRecipient = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(replyContactInput.value.trim()) ? replyContactInput.value.trim() : "";
  replyModal.classList.remove("open");
  showScreen("new-email");
  openComposer();
  userInput.value = `Reply to the following email:\n${source}\n\nReply with these points:\n${notes}`;
  userInput.dispatchEvent(new Event("input", { bubbles: true }));
  selectContact(contact || null);
  if (!contact && typedRecipient) {
    contactInput.value = typedRecipient;
    selectedContactId = null;
    hiddenContactId.value = "";
    selectedContactEl.textContent = `Email: ${typedRecipient}`;
  }
  await runAssistedDraft("reply", source, notes, contact?.id || null, typedRecipient);
});

/* ─────────────────────────────────────────────────────────────────
   10. ADD TO QUEUE
───────────────────────────────────────────────────────────────── */
document.getElementById("add-to-queue-btn").addEventListener("click", async () => {
  if (!currentDraftId) { toast("Create a draft first.", "warn"); return; }
  try {
    const draft = {
      id: currentDraftId,
      contact_id: selectedContactId,
      recipient_email: getRecipientEmail(),
      user_input: userInput.value.trim(),
      subject: emailSubject.value,
      email_en: emailBody.value,
      summary_hi: summaryHi.textContent,
      factcheck: currentFactCheck,
      status: "pending",
    };
    const res = await api("queue_add", draft);
    if (res.ok) {
      toast("Added to queue.", "success");
      resetNewEmail();
    } else {
      toast(res.error || "Could not add the draft to your queue.", "error");
    }
  } catch (e) {
    toast("" + e.message, "error");
  }
});

/* ─────────────────────────────────────────────────────────────────
   11. PUT IN OUTLOOK (draft mode by default, send after countdown)
───────────────────────────────────────────────────────────────── */
document.getElementById("to-outlook-btn").addEventListener("click", () => {
  if (!currentDraftId) { toast("Create a draft first.", "warn"); return; }
  const modeSelect = document.getElementById("setting-outlook-mode");
  const mode = modeSelect ? modeSelect.value : "draft";
  if (mode === "send") {
    startSendCountdown(currentDraftId);
  } else {
    sendToOutlookDraft(currentDraftId);
  }
});

/** Save to Outlook Drafts (no confirmation needed). */
async function sendToOutlookDraft(draftId) {
  try {
    await persistCurrentDraft();
    const res = await api("to_outlook", draftId, "draft", null);
    if (res.ok) {
      toast("Saved to Outlook Drafts.", "success");
      resetNewEmail();
    } else {
      toast("Outlook error: " + (res.error || ""), "error", 8000);
    }
  } catch (e) {
    toast("" + e.message, "error");
  }
}

/** Send only after the 10-second countdown expires, using the server-issued token. */
async function sendToOutlookNow(draftId, confirmationToken) {
  try {
    await persistCurrentDraft();
    const res = await api("to_outlook", draftId, "send", confirmationToken);
    if (res.ok) {
      toast("Email sent via Outlook.", "success");
      resetNewEmail();
    } else {
      toast("Outlook error: " + (res.error || ""), "error", 8000);
    }
  } catch (e) {
    toast("" + e.message, "error");
  }
}

/* ─────────────────────────────────────────────────────────────────
   12. SEND COUNTDOWN (10-second cancellable overlay)
───────────────────────────────────────────────────────────────── */
const countdownOverlay   = document.getElementById("countdown-overlay");
const countdownNum       = document.getElementById("countdown-num");
const countdownNumInline = document.getElementById("countdown-num-inline");
const cancelCountdownBtn = document.getElementById("countdown-cancel-btn");

let _countdownTimer = null;
let _pendingSendDraftId = null;
let _pendingConfirmToken = null;   // server-issued one-time token

async function startSendCountdown(draftId) {
  try { await persistCurrentDraft(); }
  catch (e) { toast(e.message || "Could not save your latest edits.", "error"); return; }
  // Step 1: get a server token (validates fact-check on backend too)
  let tokenRes;
  try {
    tokenRes = await api("begin_send_confirmation", draftId);
  } catch (e) {
    toast("" + e.message, "error"); return;
  }
  if (!tokenRes.ok) {
    toast("" + (tokenRes.error || "Cannot begin send confirmation."), "error", 7000);
    return;
  }

  _pendingSendDraftId  = draftId;
  _pendingConfirmToken = tokenRes.confirmation_token;
  let secs = tokenRes.countdown_seconds || 10;

  countdownNum.textContent = secs;
  countdownNumInline.textContent = secs;
  countdownOverlay.classList.add("open");

  _countdownTimer = setInterval(() => {
    secs--;
    countdownNum.textContent = secs;
    countdownNumInline.textContent = secs;
    if (secs <= 0) {
      clearInterval(_countdownTimer);
      countdownOverlay.classList.remove("open");
      sendToOutlookNow(_pendingSendDraftId, _pendingConfirmToken);
    }
  }, 1000);
}

async function persistCurrentDraft() {
  if (!currentDraftId) return;
  const res = await api("queue_update", currentDraftId, {
    subject: emailSubject.value,
    email_en: emailBody.value,
    contact_id: selectedContactId,
    recipient_email: getRecipientEmail(),
  });
  if (!res?.ok) throw new Error("Could not save your latest draft changes.");
}

cancelCountdownBtn.addEventListener("click", async () => {
  clearInterval(_countdownTimer);
  countdownOverlay.classList.remove("open");
  if (_pendingConfirmToken) {
    try { await api("cancel_send_confirmation", _pendingConfirmToken); } catch (_) {}
    _pendingConfirmToken = null;
  }
  toast("Send cancelled.", "info");
});

/* ─────────────────────────────────────────────────────────────────
   13. DISCARD
───────────────────────────────────────────────────────────────── */
document.getElementById("discard-btn").addEventListener("click", async () => {
  if (currentDraftId) {
    try { await api("queue_discard", currentDraftId); } catch (_) {}
  }
  resetNewEmail();
  toast("Draft discarded.", "info");
});

function resetNewEmail() {
  composeView?.classList.remove("is-drafting");
  userInput.value = "";
  emailSubject.value = "";
  emailBody.value = "";
  summaryHi.textContent = "";
  resultSection.classList.remove("visible");
  currentDraftId = null;
  currentFactCheck = null;
  selectContact(null);
  contactInput.value = "";
}

/* ─────────────────────────────────────────────────────────────────
   14. QUEUE SCREEN
───────────────────────────────────────────────────────────────── */
const queueList      = document.getElementById("queue-list");
const queueEmpty     = document.getElementById("queue-empty");
const approveAllBtn  = document.getElementById("approve-all-btn");
const queueCountLbl  = document.getElementById("queue-count-label");

document.getElementById("refresh-queue-btn").addEventListener("click", loadQueue);

async function loadQueue() {
  try {
    const res = await api("queue_list");
    renderQueue(res.drafts || []);
  } catch (e) {
    toast("Could not load the queue: " + e.message, "error");
  }
}

function renderQueue(drafts) {
  const pending = drafts.filter(d => d.status === "pending");
  queueCountLbl.textContent = pending.length
    ? `${pending.length} pending draft${pending.length > 1 ? "s" : ""}`
    : "";

  queueList.innerHTML = "";

  if (!pending.length) {
    queueEmpty.classList.remove("hidden");
    approveAllBtn.disabled = true;
    return;
  }

  queueEmpty.classList.add("hidden");

  const allPassed = pending.every(d => {
    const fc = parseFc(d);
    return fc ? fc.passed : true;
  });
  approveAllBtn.disabled = !allPassed;
  if (!allPassed) {
    approveAllBtn.title = "Some drafts failed the fact check.";
  }

  pending.forEach(d => {
    const fc = parseFc(d);
    const passed = fc ? fc.passed !== false : true;
    const card = document.createElement("div");
    card.className = `queue-card${passed ? "" : " has-issues"}`;
    card.setAttribute("role", "listitem");
    card.innerHTML = `
      <div>
        <div class="queue-card-meta">
          <span class="queue-contact-name">${esc(d.contact_name || "No contact")}</span>
          <span class="queue-subject">${esc(d.subject || d.user_input?.slice(0,60) || "—")}</span>
          <span class="queue-fc-badge ${passed ? "ok" : "issues"}">
            ${passed ? "Verified" : "Review facts"}
          </span>
        </div>
        <div class="text-sm text-muted" style="margin-top:4px;">
          ${d.summary_hi
            ? `<em>${esc(d.summary_hi.slice(0, 80))}…</em>`
            : `<em>${esc((d.email_en || "").slice(0, 80))}…</em>`}
        </div>
      </div>
      <div class="queue-card-actions">
        <button class="btn btn-sm btn-outline" data-action="review" data-id="${d.id}">Review</button>
        <button class="btn btn-sm btn-success" data-action="outlook" data-id="${d.id}" ${!passed ? 'disabled title="Review facts first"' : ""}>Outlook draft</button>
        <button class="btn btn-sm btn-secondary" data-action="remove" data-id="${d.id}" aria-label="Discard draft">Remove</button>
      </div>
    `;
    queueList.appendChild(card);
  });

  // Event delegation
  queueList.querySelectorAll("[data-action]").forEach(btn => {
    btn.addEventListener("click", () => handleQueueAction(btn.dataset.action, parseInt(btn.dataset.id)));
  });
}

function parseFc(draft) {
  if (!draft) return null;
  if (draft.factcheck && typeof draft.factcheck === "object") return draft.factcheck;
  if (draft.factcheck_json) {
    try { return JSON.parse(draft.factcheck_json); } catch (_) {}
  }
  return null;
}

async function handleQueueAction(action, id) {
  if (action === "remove") {
    if (!confirm("Discard this draft?")) return;
    await api("queue_discard", id);
    toast("Draft removed.", "info");
    loadQueue();
  } else if (action === "outlook") {
    const res = await api("to_outlook", id, "draft", null);
    if (res.ok) {
      toast("Saved to Outlook Drafts.", "success");
      loadQueue();
    } else {
      toast("" + (res.error || ""), "error");
    }
  } else if (action === "review") {
    showQueueReview(id);
  }
}

async function showQueueReview(id) {
  try {
    const res = await api("queue_list");
    const draft = (res.drafts || []).find(d => d.id === id);
    if (!draft) return;
    // Navigate to new-email screen and pre-fill
    showScreen("new-email");
    openComposer();
    userInput.value = draft.user_input || "";
    emailSubject.value = draft.subject || "";
    emailBody.value = draft.email_en || "";
    summaryHi.textContent = draft.summary_hi || "";
    currentDraftId = draft.id;
    currentFactCheck = parseFc(draft);
    resultSection.classList.add("visible");
    if (draft.contact_id) {
      const c = allContacts.find(x => x.id === draft.contact_id);
      if (c) selectContact(c);
    } else if (draft.recipient_email) {
      contactInput.value = draft.recipient_email;
      selectedContactEl.textContent = `Email: ${draft.recipient_email}`;
    }
    toast("Draft loaded for review.", "info");
  } catch (e) {
    toast("" + e.message, "error");
  }
}

approveAllBtn.addEventListener("click", async () => {
  try {
    const res = await api("queue_list");
    const ids = (res.drafts || [])
      .filter(d => d.status === "pending" && parseFc(d)?.passed !== false)
      .map(d => d.id);
    if (!ids.length) { toast("No drafts are ready to approve.", "warn"); return; }
    approveAllBtn.disabled = true;
    approveAllBtn.textContent = "⏳ …";
    const result = await api("approve_all", ids);
    const ok  = (result.results || []).filter(r => r.ok).length;
    const bad = (result.results || []).filter(r => !r.ok).length;
    toast(`${ok} Outlook draft${ok === 1 ? "" : "s"} created${bad ? `, ${bad} failed` : ""}.`, ok > 0 ? "success" : "error", 6000);
    loadQueue();
  } catch (e) {
    toast("" + e.message, "error");
  } finally {
    approveAllBtn.disabled = false;
    approveAllBtn.innerHTML = `<span class="btn-icon">✓</span> Create all Outlook drafts`;
  }
});

/* ─────────────────────────────────────────────────────────────────
   15. SETTINGS SCREEN
───────────────────────────────────────────────────────────────── */
async function loadSettingsScreen() {
  await Promise.all([
    loadGeneralSettings(),
    loadContactsTable(),
    loadTemplatesList(),
  ]);
}

/* General settings */
async function loadGeneralSettings() {
  try {
    const s = await api("get_settings");
    document.getElementById("setting-sender-name").value = s.sender_name || "";
    document.getElementById("setting-sign-off").value = s.sign_off || "Kind regards";
    document.getElementById("setting-model").value = s.model || "gemma3:4b";
    const modeEl = document.getElementById("setting-outlook-mode");
    if (modeEl) modeEl.value = s.outlook_mode || "draft";
  } catch (_) {}
}

document.getElementById("save-settings-btn").addEventListener("click", async () => {
  const modeEl = document.getElementById("setting-outlook-mode");
  const values = {
    sender_name:   document.getElementById("setting-sender-name").value.trim(),
    sign_off:      document.getElementById("setting-sign-off").value.trim(),
    model:         document.getElementById("setting-model").value,
    outlook_mode:  modeEl ? modeEl.value : "draft",
  };
  try {
    const res = await api("save_settings", values);
    if (res.ok) toast("Settings saved.", "success");
    else toast("" + (res.error || ""), "error");
  } catch (e) { toast("" + e.message, "error"); }
});

/* ─────────────────────────────────────────────────────────────────
   16. CONTACTS CRUD
───────────────────────────────────────────────────────────────── */
const contactsTbody   = document.getElementById("contacts-tbody");
const modalOverlay    = document.getElementById("modal-overlay");
const modalTitle      = document.getElementById("modal-title");
const modalContactId  = document.getElementById("modal-contact-id");
const modalName       = document.getElementById("modal-name");
const modalEmail      = document.getElementById("modal-email");
const modalAliases    = document.getElementById("modal-aliases");
const modalSalutation = document.getElementById("modal-salutation");
const modalTone       = document.getElementById("modal-tone");
const modalNotes      = document.getElementById("modal-notes");

document.getElementById("add-contact-btn").addEventListener("click", () => openContactModal(null));
document.getElementById("modal-cancel-btn").addEventListener("click", () => closeContactModal());
document.getElementById("modal-save-btn").addEventListener("click", saveContact);

// Close on overlay click
modalOverlay.addEventListener("click", e => { if (e.target === modalOverlay) closeContactModal(); });

async function loadContactsTable() {
  await loadContacts();
  contactsTbody.innerHTML = "";
  allContacts.forEach(c => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${esc(c.name)}</strong></td>
      <td>${esc(c.email)}</td>
      <td>${esc(c.aliases || "—")}</td>
      <td>${esc(c.tone || "formal")}</td>
      <td style="white-space:nowrap;">
        <button class="btn btn-sm btn-secondary" data-edit="${c.id}">Edit</button>
        <button class="btn btn-sm btn-danger" data-del="${c.id}" style="margin-left:4px;">Remove</button>
      </td>
    `;
    contactsTbody.appendChild(tr);
  });

  contactsTbody.querySelectorAll("[data-edit]").forEach(btn =>
    btn.addEventListener("click", () => {
      const c = allContacts.find(x => x.id === parseInt(btn.dataset.edit));
      if (c) openContactModal(c);
    })
  );
  contactsTbody.querySelectorAll("[data-del]").forEach(btn =>
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.del);
      const c  = allContacts.find(x => x.id === id);
      if (!confirm(`Delete "${c?.name}"?`)) return;
      const res = await api("delete_contact", id);
      if (res.ok) { toast("Contact deleted.", "info"); loadContactsTable(); }
      else toast("" + (res.error || ""), "error");
    })
  );
}

function openContactModal(contact) {
  modalTitle.textContent = contact ? "Edit contact" : "New contact";
  modalContactId.value   = contact?.id || "";
  modalName.value        = contact?.name || "";
  modalEmail.value       = contact?.email || "";
  modalAliases.value     = contact?.aliases || "";
  modalSalutation.value  = contact?.salutation || "";
  modalTone.value        = contact?.tone || "formal";
  modalNotes.value       = contact?.notes || "";
  modalOverlay.classList.add("open");
  modalName.focus();
}

function closeContactModal() { modalOverlay.classList.remove("open"); }

async function saveContact() {
  const name  = modalName.value.trim();
  const email = modalEmail.value.trim();
  if (!name || !email) { toast("Name and email are required.", "warn"); return; }

  const contact = {
    id:         modalContactId.value ? parseInt(modalContactId.value) : undefined,
    name, email,
    aliases:    modalAliases.value.trim(),
    salutation: modalSalutation.value.trim(),
    tone:       modalTone.value,
    notes:      modalNotes.value.trim(),
  };
  try {
    const res = await api("save_contact", contact);
    if (res.ok) {
      toast("Contact saved.", "success");
      closeContactModal();
      loadContactsTable();
    } else {
      toast("" + (res.error || ""), "error");
    }
  } catch (e) { toast("" + e.message, "error"); }
}

/* ─────────────────────────────────────────────────────────────────
   17. TEMPLATES
───────────────────────────────────────────────────────────────── */
const templatesList   = document.getElementById("templates-list");
const templateQuickPicker = document.getElementById("template-quick-picker");
const manageTemplatesBtn = document.getElementById("manage-templates-btn");
const tmplOverlay     = document.getElementById("tmpl-modal-overlay2");
const tmplId          = document.getElementById("tmpl-id");
const tmplTitle       = document.getElementById("tmpl-title");
const tmplHint        = document.getElementById("tmpl-hint");
let savedTemplates = [];

document.getElementById("add-template-btn").addEventListener("click", () => openTemplateModal(null));
document.getElementById("tmpl-cancel-btn").addEventListener("click", () => tmplOverlay.style.display = "none");
document.getElementById("tmpl-save-btn").addEventListener("click", saveTemplate);
manageTemplatesBtn.addEventListener("click", () => showScreen("settings"));
templateQuickPicker.addEventListener("change", () => {
  const template = savedTemplates.find(item => String(item.id) === templateQuickPicker.value);
  templateQuickPicker.value = "";
  if (!template) return;
  if (userInput.value.trim() && !confirm("Replace the current message with this template instruction?")) return;
  showScreen("new-email");
  openComposer();
  userInput.value = template.intent_hint || "";
  userInput.dispatchEvent(new Event("input", { bubbles: true }));
  userInput.focus();
  toast(`Template loaded: ${template.title}. Edit the instruction, then create your draft.`, "info", 4500);
});

async function loadTemplatesList() {
  try {
    const res = await api("list_templates");
    savedTemplates = res.templates || [];
    renderTemplates(savedTemplates);
  templateQuickPicker.innerHTML = `<option value="">Choose a template</option>` + savedTemplates.map(t => {
      const option = document.createElement("option");
      option.value = String(t.id);
      option.textContent = t.title;
      return option.outerHTML;
    }).join("");
  } catch (_) {}
}

function renderTemplates(templates) {
  if (!templates.length) {
    templatesList.innerHTML = `<p class="text-muted text-sm">No templates yet.</p>`;
    return;
  }
  templatesList.innerHTML = templates.map(t => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #e5e7eb;">
      <div style="flex:1;">
        <strong>${esc(t.title)}</strong>
        <div class="text-sm text-muted">${esc(t.intent_hint?.slice(0, 60) || "")}…</div>
      </div>
      <button class="btn btn-sm btn-secondary" onclick="useTemplate(${t.id})">Use</button>
      <button class="btn btn-sm btn-secondary" onclick="editTemplate(${t.id})">Edit</button>
    </div>
  `).join("");
}

window.useTemplate = async function(id) {
  try {
    const res = await api("list_templates");
    const t = (res.templates || []).find(x => x.id === id);
    if (t) {
      showScreen("new-email");
      openComposer();
      userInput.value = t.intent_hint || "";
      toast(`Template loaded: ${t.title}`, "info");
    }
  } catch (_) {}
};

window.editTemplate = async function(id) {
  try {
    const res = await api("list_templates");
    const t = (res.templates || []).find(x => x.id === id);
    if (t) openTemplateModal(t);
  } catch (_) {}
};

function openTemplateModal(t) {
  tmplId.value    = t?.id || "";
  tmplTitle.value = t?.title || "";
  tmplHint.value  = t?.intent_hint || "";
  tmplOverlay.style.display = "flex";
  tmplTitle.focus();
}

async function saveTemplate() {
  const title = tmplTitle.value.trim();
  const hint  = tmplHint.value.trim();
  if (!title || !hint) { toast("Title and instruction are required.", "warn"); return; }
  const tmpl = { id: tmplId.value ? parseInt(tmplId.value) : undefined, title, intent_hint: hint };
  try {
    const res = await api("save_template", tmpl);
    if (res.ok) {
      toast("Template saved.", "success");
      tmplOverlay.style.display = "none";
      loadTemplatesList();
    } else {
      toast("" + (res.error || ""), "error");
    }
  } catch (e) { toast("" + e.message, "error"); }
}

/* ─────────────────────────────────────────────────────────────────
   18. KEYBOARD SHORTCUTS
───────────────────────────────────────────────────────────────── */
document.addEventListener("keydown", e => {
  if (e.ctrlKey && e.key === "Enter") {
    // Ctrl+Enter → draft
    if (document.getElementById("screen-new-email").classList.contains("active")) {
      document.getElementById("draft-btn").click();
    }
  }
  if (e.key === "Escape") {
    if (modalOverlay.classList.contains("open"))       closeContactModal();
    if (replyModal.classList.contains("open"))         replyModal.classList.remove("open");
    if (tmplOverlay.style.display === "flex")          tmplOverlay.style.display = "none";
    if (countdownOverlay.classList.contains("open"))   cancelCountdownBtn.click();
  }
});

/* ─────────────────────────────────────────────────────────────────
   19. UTILITIES
───────────────────────────────────────────────────────────────── */
/** Escape HTML special chars to prevent XSS inside innerHTML */
function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ─────────────────────────────────────────────────────────────────
   20. BOOT
───────────────────────────────────────────────────────────────── */
async function boot() {
  await Promise.all([loadContacts(), loadTemplatesList()]);
  const status = await refreshSidebarStatus();
  checkMicAvailability(status);
  // Periodically refresh sidebar status
  setInterval(refreshSidebarStatus, 30_000);
}

