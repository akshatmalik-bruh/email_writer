"""SQLite persistence. Connections are short lived and safe across worker threads."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .config import DB_PATH

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS contacts (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, aliases TEXT DEFAULT '', email TEXT NOT NULL,
 salutation TEXT DEFAULT '', tone TEXT DEFAULT 'formal', common_topics TEXT DEFAULT '', notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS templates (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, intent_hint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
 id INTEGER PRIMARY KEY, contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
 recipient_email TEXT DEFAULT '',
 user_input TEXT NOT NULL, subject TEXT DEFAULT '', email_en TEXT DEFAULT '', summary_hi TEXT DEFAULT '',
 factcheck_json TEXT DEFAULT '{}', status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS style_samples (
 id INTEGER PRIMARY KEY, contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
 topic TEXT DEFAULT '', body_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Built-in professional templates for recurring IGL correspondence. Existing
# user edits are preserved: these defaults are inserted only when missing.
DEFAULT_TEMPLATES = [
    ("Meeting Invitation / Discussion Request", """SAVED EMAIL TEMPLATE: Meeting invitation / discussion request. Draft the email using this structure and retain the bracketed placeholders exactly as written:
Dear [Name],

I hope you are doing well.

A meeting has been scheduled to discuss [topic/project name] and the associated requirements/progress.

Date: [Date]
Time: [Time]
Venue/Meeting Link: [Venue/Link]

You are requested to kindly attend the meeting and provide your inputs regarding the above-mentioned subject.

Please let me know in case of any difficulty in attending at the scheduled time.

Regards,
[Your Name]
[Designation/Department]
IGL"""),
    ("Analysis / Report Submission", """SAVED EMAIL TEMPLATE: Analysis/report submission. Draft the email using this structure and retain the bracketed placeholders exactly as written:
Dear [Name],

Please find attached the analysis report regarding [topic/subject] for your kind review.

The report includes the analysis of [glycols/parameters/data/etc.] along with the relevant observations and findings.

Kindly review the report and let me know if any further analysis or clarification is required.

Regards,
[Your Name]
[Designation/Department]
IGL"""),
    ("Glycol Analysis Report", """SAVED EMAIL TEMPLATE: Glycol analysis report. Draft the email using this structure and retain the bracketed placeholders exactly as written:
Dear [Name],

Please find attached the glycol analysis report for the period [Date/Period].

The analysis covers the relevant glycol parameters and their observed values, along with the corresponding observations.

The report is submitted for your review and further necessary action, if required.

Please let me know if any additional analysis or information is required.

Regards,
[Your Name]
[Designation/Department]
IGL"""),
    ("Request for Information / Data", """SAVED EMAIL TEMPLATE: Request for information/data. Draft the email using this structure and retain the bracketed placeholders exactly as written:
Dear [Name],

With reference to [project/analysis/report/topic], kindly provide the required information/data related to [specific requirement].

The requested details are required for [analysis/report preparation/further processing].

It would be appreciated if the information could be shared by [Date/Time], if feasible.

Please let me know if any clarification is required regarding the requested information.

Regards,
[Your Name]
[Designation/Department]
IGL"""),
    ("Sharing Analysis Findings / Observations", """SAVED EMAIL TEMPLATE: Sharing analysis findings/observations. Draft the email using this structure and retain the bracketed placeholders exactly as written:
Dear [Name],

Based on the analysis carried out for [topic/parameter], the following observations have been made:
- [Observation 1]
- [Observation 2]
- [Observation 3]

The detailed analysis and supporting data are attached for your reference.

Kindly review the findings and provide your feedback/comments, if any.

Regards,
[Your Name]
[Designation/Department]
IGL"""),
    ("Follow-up / Reminder", """SAVED EMAIL TEMPLATE: Follow-up/reminder. Draft the email using this structure and retain the bracketed placeholders exactly as written:
Dear [Name],

This is a gentle reminder regarding my previous email concerning [subject/request].

The required [information/data/approval/feedback] is awaited for further processing of [analysis/report/project].

Kindly share the same at your convenience.

Your support in this regard would be appreciated.

Regards,
[Your Name]
[Designation/Department]
IGL"""),
    ("Sharing Final Report / Completed Work", """SAVED EMAIL TEMPLATE: Sharing final report/completed work. Draft the email using this structure and retain the bracketed placeholders exactly as written:
Dear [Name],

Please find attached the final report for [project/analysis name].

The report incorporates the analysis carried out on [topic/data/parameters] along with the relevant observations and conclusions.

The report is submitted for your kind review and record.

Please let me know if any further information or clarification is required.

Regards,
[Your Name]
[Designation/Department]
IGL"""),
]

class Database:
    def __init__(self, path=None):
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            draft_columns = {row[1] for row in conn.execute("PRAGMA table_info(drafts)")}
            if "recipient_email" not in draft_columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN recipient_email TEXT DEFAULT ''")
            conn.executemany("INSERT INTO templates(title,intent_hint) SELECT ?,? WHERE NOT EXISTS (SELECT 1 FROM templates WHERE title=?)", [(title, hint, title) for title, hint in DEFAULT_TEMPLATES])
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('sign_off','Regards,')")
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('sender_name','Neeraj Kumar')")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def row(row):
        return dict(row) if row else None

    def contacts(self):
        with self.connect() as c:
            return [dict(x) for x in c.execute("SELECT * FROM contacts ORDER BY name")]

    def contact(self, contact_id):
        with self.connect() as c:
            return self.row(c.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone())

    def save_contact(self, data):
        fields = {k: str(data.get(k) or "") for k in ("name", "aliases", "email", "salutation", "tone", "common_topics", "notes")}
        if not fields["name"].strip() or not fields["email"].strip():
            raise ValueError("Contact name and email are required.")
        if fields["tone"] not in ("", "formal", "very_formal", "friendly_formal"):
            raise ValueError("Unsupported contact tone.")
        with self.connect() as c:
            if data.get("id"):
                c.execute("UPDATE contacts SET name=:name,aliases=:aliases,email=:email,salutation=:salutation,tone=:tone,common_topics=:common_topics,notes=:notes WHERE id=:id", {**fields,"id":int(data["id"])})
                return int(data["id"])
            cur = c.execute("INSERT INTO contacts(name,aliases,email,salutation,tone,common_topics,notes) VALUES(:name,:aliases,:email,:salutation,:tone,:common_topics,:notes)", fields)
            return cur.lastrowid

    def save_draft(self, data):
        with self.connect() as c:
            cur = c.execute("INSERT INTO drafts(contact_id,recipient_email,user_input,subject,email_en,summary_hi,factcheck_json,status) VALUES(?,?,?,?,?,?,?,?)", (
                data.get("contact_id"), data.get("recipient_email", ""), data["user_input"], data.get("subject", ""), data.get("email_en", ""), data.get("summary_hi", ""),
                json.dumps(data.get("factcheck", {}), ensure_ascii=False), data.get("status", "pending")))
            return cur.lastrowid

    def get_draft(self, draft_id):
        with self.connect() as c:
            row = self.row(c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())
        if row:
            row["factcheck"] = json.loads(row.pop("factcheck_json") or "{}")
        return row

    def update_draft(self, draft_id, fields):
        allowed = {k: fields[k] for k in ("subject", "email_en", "summary_hi", "status", "contact_id", "recipient_email") if k in fields}
        if "factcheck" in fields:
            allowed["factcheck_json"] = json.dumps(fields["factcheck"], ensure_ascii=False)
        if not allowed:
            return False
        assignments = ",".join(f"{k}=?" for k in allowed)
        with self.connect() as c:
            cur = c.execute(f"UPDATE drafts SET {assignments} WHERE id=?", (*allowed.values(), int(draft_id)))
            return cur.rowcount > 0

    def queue(self):
        with self.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM drafts WHERE status='pending' ORDER BY created_at,id")]
        return [self.get_draft(i) for i in ids]

    def setting(self, key, default=""):
        with self.connect() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with self.connect() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def samples(self, contact_id, limit=2):
        with self.connect() as c:
            return [dict(x) for x in c.execute("SELECT topic,body_en FROM style_samples WHERE contact_id=? ORDER BY id DESC LIMIT ?", (contact_id, limit))]

    def templates(self):
        with self.connect() as c:
            return [dict(x) for x in c.execute("SELECT * FROM templates ORDER BY title")]

    def save_template(self, data):
        title, hint = str(data.get("title", "")).strip(), str(data.get("intent_hint", "")).strip()
        if not title or not hint: raise ValueError("Template title and instruction are required.")
        with self.connect() as c:
            if data.get("id"):
                c.execute("UPDATE templates SET title=?,intent_hint=? WHERE id=?", (title,hint,int(data["id"])))
                return int(data["id"])
            return c.execute("INSERT INTO templates(title,intent_hint) VALUES(?,?)", (title,hint)).lastrowid
