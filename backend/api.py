"""Methods exposed to the pywebview JavaScript bridge.

Design notes
------------
* _submit(kind, fn) → fn() is called with NO arguments on a worker thread.
  Functions that need the job_id for streaming tokens capture it via closure.
* Long operations return {"job_id": ...} immediately.
  Progress/completion arrives via window.onJobEvent({job_id, event, data}).
* to_outlook uses a server-side confirmation-token system for "send" mode:
    1. JS calls begin_send_confirmation(draft_id) → gets {"confirmation_token": t}
    2. JS shows a 10-second countdown; user can call cancel_send_confirmation(t).
    3. After countdown JS calls to_outlook(draft_id, "send", t).
    4. Backend verifies the token has existed ≥10 s before calling mail.Send().
* queue_list enriches each draft with contact_name from the contacts table.
* outlook_mode setting ("draft" | "send") is persisted in the settings table.
"""
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import contacts, factcheck, llm, outlook, stt
from .config import ROOT, DEFAULT_MODEL
from .db import Database


class API:
    def __init__(self, window=None, db=None):
        self._window = window
        self._db = db or Database()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="email-assistant")
        self._jobs: dict = {}
        self._send_tokens: dict = {}   # token → (draft_id, expire_mono)
        self._lock = threading.Lock()

    def set_window(self, window):
        self._window = window

    # ── Internal helpers ────────────────────────────────────────────────

    def _event(self, job_id: str, event: str, data=None):
        payload = json.dumps(
            {"job_id": job_id, "event": event, "data": data or {}},
            ensure_ascii=False,
        )
        if self._window:
            try:
                self._window.evaluate_js(f"window.onJobEvent && window.onJobEvent({payload})")
            except Exception:
                pass

    def _submit(self, kind: str, fn):
        """Submit fn() to the thread pool.  fn takes NO arguments.
        Returns {"job_id": ...} immediately."""
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {"kind": kind, "status": "running"}

        def run():
            self._event(job_id, "started")
            try:
                result = fn()          # ← zero-arg call
                with self._lock:
                    self._jobs[job_id] = {"kind": kind, "status": "complete", "result": result}
                self._event(job_id, "complete", result)
            except Exception as exc:
                error = str(exc)
                with self._lock:
                    self._jobs[job_id] = {"kind": kind, "status": "error", "error": error}
                self._event(job_id, "error", {"message": error})

        self._pool.submit(run)
        return {"job_id": job_id}

    def job_status(self, job_id):
        with self._lock:
            return dict(self._jobs.get(job_id, {"status": "unknown"}))

    # ── Contacts ────────────────────────────────────────────────────────

    def list_contacts(self):
        return {"contacts": self._db.contacts()}

    def save_contact(self, contact):
        try:
            return {"ok": True, "id": self._db.save_contact(contact)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def delete_contact(self, contact_id):
        try:
            with self._db.connect() as c:
                cur = c.execute("DELETE FROM contacts WHERE id=?", (int(contact_id),))
            return {"ok": cur.rowcount > 0}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def resolve_contact(self, text):
        return {"candidates": contacts.resolve(text, self._db.contacts())}

    # ── Draft pipeline ───────────────────────────────────────────────────

    def draft_email(self, contact_id, user_input, recipient_email=""):
        # Capture job_id in closure so the token callback can fire events.
        job_id_holder = [None]

        def work():
            job_id = job_id_holder[0]
            contact = self._db.contact(int(contact_id)) if contact_id else None
            if contact_id and not contact:
                raise ValueError("The selected contact no longer exists.")
            destination = contact.get("email", "") if contact else str(recipient_email or "").strip()
            generated = llm.generate(
                user_input, contact,
                self._db.setting("sign_off", "Kind regards"),
                self._db.setting("sender_name", ""),
                self._db.samples(contact_id) if contact_id else [],
                self._db.setting("model", DEFAULT_MODEL),
                on_field=lambda field, value, complete: self._event(
                    job_id, "field", {"field": field, "value": value, "complete": complete}
                ),
            )
            self._event(job_id, "stage", {"stage": "verifying"})
            checked = factcheck.check(
                user_input,
                generated["subject"] + "\n" + generated["email_en"],
                contact,
            )
            record = {
                "contact_id": contact_id,
                "recipient_email": destination,
                "user_input": user_input,
                "subject": generated["subject"],
                "email_en": generated["email_en"],
                "summary_hi": generated["summary_hi"],
                "factcheck": checked,
                "status": "pending",
            }
            draft_id = self._db.save_draft(record)
            return {**record, "id": draft_id, "needs_review": generated.get("needs_review", False) or not checked.get("passed", False)}

        result = self._submit("draft", work)
        job_id_holder[0] = result["job_id"]   # give the closure its job_id
        return result

    def assist_email(self, task, source, notes="", contact_id=None, recipient_email=""):
        """Polish an email or draft a reply and save it into the review flow."""
        if task not in ("polish", "reply"):
            raise ValueError("Unsupported email task.")
        source = str(source or "").strip()
        notes = str(notes or "").strip()
        if not source:
            raise ValueError("Paste an email first.")
        if task == "polish":
            prompt = (
                "TASK: GRAMMAR_POLISH\nCorrect grammar, spelling, and punctuation in the email below. Preserve its meaning, facts, tone, wording where possible, paragraph structure, and existing sign-off. Do not add a subject if none was supplied.\n"
                f"EMAIL TO POLISH:\n<<<\n{source}\n>>>"
            )
        else:
            if not notes:
                raise ValueError("Tell me what you want your reply to say.")
            prompt = (
                "TASK: WRITE_REPLY\nWrite a concise, professional reply to the received email. Use only facts from the received email and the sender's reply notes. Do not claim an action has been completed unless the notes say so. Use an appropriate reply subject.\n"
                f"RECEIVED EMAIL:\n<<<\n{source}\n>>>\nSENDER'S REPLY NOTES:\n<<<\n{notes}\n>>>"
            )
        job_id_holder = [None]

        def work():
            job_id = job_id_holder[0]
            contact = self._db.contact(int(contact_id)) if contact_id else None
            if contact_id and not contact:
                raise ValueError("The selected recipient no longer exists.")
            destination = contact.get("email", "") if contact else str(recipient_email or "").strip()
            result = llm.generate(
                prompt, contact,
                self._db.setting("sign_off", "Regards,"),
                self._db.setting("sender_name", "Neeraj Kumar"),
                model=self._db.setting("model", DEFAULT_MODEL),
                temperature=0.85 if task == "polish" else 0.0,
                on_field=lambda field, value, complete: self._event(
                    job_id, "field", {"field": field, "value": value, "complete": complete}
                ),
            )
            checked = (factcheck.check(prompt, result["subject"] + "\n" + result["email_en"], contact)
                       if task == "polish" else {"passed": True, "items": []})
            draft_id = self._db.save_draft({
                "contact_id": contact_id,
                "recipient_email": destination,
                "user_input": prompt,
                "subject": result["subject"],
                "email_en": result["email_en"],
                "summary_hi": result["summary_hi"],
                "factcheck": checked,
                "status": "pending",
            })
            return {**result, "id": draft_id, "contact_id": contact_id, "recipient_email": destination, "factcheck": checked,
                    "needs_review": result.get("needs_review", False) or not checked.get("passed", False)}

        result = self._submit(task, work)
        job_id_holder[0] = result["job_id"]
        return result

    def redraft(self, draft_id, instruction):
        job_id_holder = [None]

        def work():
            job_id = job_id_holder[0]
            old = self._db.get_draft(int(draft_id))
            if not old:
                raise ValueError("Draft not found.")
            contact = self._db.contact(old.get("contact_id")) if old.get("contact_id") else None
            result = llm.generate(
                old["user_input"], contact,
                self._db.setting("sign_off", "Kind regards"),
                self._db.setting("sender_name", ""),
                model=self._db.setting("model", DEFAULT_MODEL),
                previous={
                    "subject": old["subject"],
                    "email_en": old["email_en"],
                    "summary_hi": old["summary_hi"],
                },
                instruction=instruction,
                on_field=lambda field, value, complete: self._event(
                    job_id, "field", {"field": field, "value": value, "complete": complete}
                ),
            )
            self._event(job_id, "stage", {"stage": "verifying"})
            checked = factcheck.check(
                old["user_input"],
                result["subject"] + "\n" + result["email_en"],
                contact,
            )
            self._db.update_draft(draft_id, {**result, "factcheck": checked})
            return {**result, "id": int(draft_id), "factcheck": checked, "needs_review": result.get("needs_review", False) or not checked.get("passed", False)}

        result = self._submit("redraft", work)
        job_id_holder[0] = result["job_id"]
        return result

    # ── Speech-to-text ───────────────────────────────────────────────────

    def transcribe(self, audio_path):
        return self._submit("transcribe", lambda: stt.transcribe(audio_path))

    def transcribe_base64(self, b64_audio):
        """Accept a base64-encoded audio blob from the browser MediaRecorder."""
        import base64, tempfile
        raw = base64.b64decode(b64_audio)
        tmp = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
        tmp.write(raw)
        tmp.close()
        tmp_path = tmp.name

        def work():
            try:
                return stt.transcribe(tmp_path)
            finally:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        return self._submit("transcribe", work)

    # ── Queue ────────────────────────────────────────────────────────────

    def queue_add(self, draft):
        """Add or update a draft in the queue.
        If draft.id already exists in the DB, update it; otherwise insert."""
        try:
            source = str(draft.get("user_input", "")).strip()
            if not source:
                raise ValueError("The original message is required.")
            draft_id = draft.get("id")
            if draft_id and self._db.get_draft(int(draft_id)):
                self._db.update_draft(int(draft_id), {**draft, "status": "pending"})
                return {"ok": True, "id": int(draft_id)}
            new_id = self._db.save_draft({**draft, "user_input": source, "status": "pending"})
            return {"ok": True, "id": new_id}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def queue_list(self):
        """Return pending drafts enriched with contact_name for UI display."""
        drafts = self._db.queue()
        # Build a contact lookup to avoid N+1 queries
        all_contacts = {c["id"]: c for c in self._db.contacts()}
        for d in drafts:
            cid = d.get("contact_id")
            if cid and cid in all_contacts:
                d["contact_name"] = all_contacts[cid]["name"]
                d["contact_email"] = d.get("recipient_email") or all_contacts[cid]["email"]
            else:
                d["contact_name"] = ""
                d["contact_email"] = d.get("recipient_email", "")
        return {"drafts": drafts}

    def queue_update(self, draft_id, fields):
        try:
            return {"ok": self._db.update_draft(int(draft_id), fields)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def queue_discard(self, draft_id):
        return self.queue_update(draft_id, {"status": "discarded"})

    # ── Outlook ─────────────────────────────────────────────────────────

    def begin_send_confirmation(self, draft_id):
        """Step 1 of the 2-step send flow.
        Returns a one-time token; the frontend shows a 10-second countdown."""
        draft = self._db.get_draft(int(draft_id))
        if not draft:
            return {"ok": False, "error": "Draft not found."}
        fc = draft.get("factcheck", {})
        if not fc.get("passed", True) and fc.get("items"):
            return {
                "ok": False,
                "error": "Review the flagged facts first. Create an Outlook draft, check it, then send.",
            }
        token = uuid.uuid4().hex
        # The token is valid only AFTER the 10-second countdown expires (monotonic timestamp)
        self._send_tokens[token] = (int(draft_id), time.monotonic() + 10)
        return {"ok": True, "confirmation_token": token, "countdown_seconds": 10}

    def cancel_send_confirmation(self, confirmation_token):
        """Cancel an in-progress send countdown."""
        removed = self._send_tokens.pop(str(confirmation_token), None)
        return {"ok": removed is not None}

    def to_outlook(self, draft_id, mode="draft", confirmation_token=None):
        """Create an Outlook draft or send.

        mode="draft"  → save to Drafts folder (no token needed)
        mode="send"   → requires a valid confirmation_token from begin_send_confirmation()
        """
        try:
            draft = self._db.get_draft(int(draft_id))
            if not draft:
                raise ValueError("Draft not found.")
            contact = self._db.contact(draft.get("contact_id")) if draft.get("contact_id") else None

            confirmed = False
            if mode == "send":
                # Fact-check gate
                fc = draft.get("factcheck", {})
                if not fc.get("passed", True) and fc.get("items"):
                    raise PermissionError(
                        "Review the flagged facts before sending. Use 'Put in Outlook' to create a draft first."
                    )
                # Token gate
                token = str(confirmation_token or "")
                pending = self._send_tokens.get(token)
                if not pending or pending[0] != int(draft_id):
                    raise PermissionError("Confirm this send in the app first and wait for the 10-second cancellation period.")
                if time.monotonic() < pending[1]:
                    raise PermissionError("The 10-second cancellation period is still active.")
                # Token is valid and countdown elapsed — consume it
                self._send_tokens.pop(token, None)
                confirmed = True

            result = outlook.create_draft(
                draft.get("recipient_email") or (contact.get("email", "") if contact else ""),
                draft["subject"],
                draft["email_en"],
                mode,
                confirmed,
            )
            new_status = "sent" if mode == "send" else "in_outlook"
            self._db.update_draft(draft_id, {"status": new_status})
            return {"ok": True, **result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def approve_all(self, ids):
        """Send all listed draft IDs to Outlook Drafts in one batch."""
        results = []
        for draft_id in ids:
            d = self._db.get_draft(int(draft_id))
            fc = (d or {}).get("factcheck", {})
            if not d or (fc.get("items") and not fc.get("passed", True)):
                results.append({"id": draft_id, "ok": False, "error": "This draft needs fact review."})
                continue
            results.append({"id": draft_id, **self.to_outlook(draft_id, "draft")})
        return {"results": results, "ok": all(x.get("ok") for x in results)}

    # ── Templates ───────────────────────────────────────────────────────

    def list_templates(self):
        return {"templates": self._db.templates()}

    def save_template(self, template):
        try:
            return {"ok": True, "id": self._db.save_template(template)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Settings ────────────────────────────────────────────────────────

    def get_settings(self):
        return {
            k: (d if k in ("sign_off", "sender_name") else self._db.setting(k, d))
            for k, d in (
                ("sign_off",      "Regards,"),
                ("sender_name",   "Neeraj Kumar"),
                ("model",         DEFAULT_MODEL),
                ("outlook_mode",  "draft"),   # ← persisted now
            )
        }

    def save_settings(self, values):
        if values.get("model") in (DEFAULT_MODEL, "qwen2.5:3b", "gemma3:1b"):
            self._db.set_setting("model", values["model"])
        if values.get("outlook_mode") in ("draft", "send"):
            self._db.set_setting("outlook_mode", values["outlook_mode"])
        return {"ok": True, "settings": self.get_settings()}

    # ── Environment status ───────────────────────────────────────────────

    def env_status(self):
        """Return a summary of all system components for the Settings screen."""
        ol = outlook.detect()
        # Normalize outlook mode key: backend uses "classic"/"mailto", JS expects "classic"/"mailto"
        # (JS was previously checking "com" – that was wrong; the fix is in app.js)
        try:
            import webview  # noqa: F401
            webview_ok = True
        except Exception:
            webview_ok = False

        webview2 = False
        if os.name == "nt":
            locations = []
            for var in ("ProgramFiles(x86)", "LOCALAPPDATA"):
                base = os.environ.get(var)
                if base:
                    locations.append(Path(base) / "Microsoft" / "EdgeWebView" / "Application")
            webview2 = any(p.is_dir() and any(p.glob("*")) for p in locations)

        try:
            import psutil
            ram = {
                "total_gb":     round(psutil.virtual_memory().total   / 2**30, 1),
                "available_gb": round(psutil.virtual_memory().available / 2**30, 1),
            }
            disk = {"free_gb": round(shutil.disk_usage(ROOT).free / 2**30, 1)}
        except Exception:
            ram = disk = {"available": False}

        gpu = "unknown"
        nvidia = shutil.which("nvidia-smi")
        if nvidia:
            try:
                gpu = subprocess.run(
                    [nvidia, "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=3,
                ).stdout.strip() or "unavailable"
            except Exception:
                gpu = "unavailable"

        return {
            "ollama":                  llm.status(self._db.setting("model", DEFAULT_MODEL)),
            "outlook":                 ol,    # {mode: "classic"|"mailto", available: bool, message: str}
            "pywebview_available":     webview_ok,
            "webview2_available":      webview2,
            "whisper_model_available": stt.available(),
            "ram":                     ram,
            "disk":                    disk,
            "gpu":                     gpu,
        }
