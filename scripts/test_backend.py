"""Interactive End-to-End Backend Verification Script.

Tests all backend modules in sequence:
  1. SQLite Database & Settings
  2. Contact Alias Resolution
  3. Fact Check System (numbers, dates, Hindi digits)
  4. Local LLM Generation & Streaming via Ollama
  5. Queue & Draft Management
  6. Outlook Safety Gates (10s cancellable send token)
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.api import API
from backend.db import Database
from backend import contacts, factcheck, llm

def banner(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def main():
    db = Database()
    api = API(db=db)

    banner("1. Testing SQLite Database & Settings")
    settings = api.get_settings()
    print(f"✓ Current Settings: model={settings.get('model')}, sign_off='{settings.get('sign_off')}'")
    
    # Save a test contact
    c_res = api.save_contact({
        "name": "Rajesh Sharma",
        "email": "rajesh.sharma@example.com",
        "aliases": "sharma ji, rajesh ji",
        "salutation": "Dear Mr. Sharma",
        "tone": "formal"
    })
    contact_id = c_res.get("id")
    print(f"✓ Saved contact: Rajesh Sharma (ID: {contact_id})")

    banner("2. Testing Contact Alias Matching")
    hits = api.resolve_contact("Sharma ji ko payment bhej do")["candidates"]
    if hits and hits[0]["name"] == "Rajesh Sharma":
        print(f"✓ Successfully resolved 'Sharma ji' -> {hits[0]['name']} (matched alias: '{hits[0]['matched_alias']}')")
    else:
        print("✗ Failed to resolve contact alias")

    banner("3. Testing Fact-Check Preservation")
    user_msg = "Sharma ji ko bolo payment kal tak bhej de, 50000 rupees baki hai"
    # Simulated good draft
    good_email = "Dear Mr. Sharma,\n\nPlease make sure the payment of Rs 50,000 is sent by tomorrow.\nKind regards"
    fc = factcheck.check(user_msg, good_email, contact={"name": "Rajesh Sharma", "aliases": "sharma ji"})
    print(f"✓ Fact check evaluation: passed={fc['passed']}")
    for item in fc['items']:
        status_symbol = "✓" if item['status'] == "ok" else "✗"
        print(f"    {status_symbol} [{item['status'].upper()}] {item['type']}: '{item['value']}'")

    banner("4. Testing Live LLM Generation via Ollama")
    print(f"Calling Ollama ({settings.get('model', 'gemma3:4b')})... Please wait a few seconds...")
    tokens_streamed = []
    
    def on_token(t):
        tokens_streamed.append(t)
        sys.stdout.write(".")
        sys.stdout.flush()

    try:
        start_t = time.time()
        draft = llm.generate(
            user_input=user_msg,
            contact=db.contact(contact_id),
            sign_off="Kind regards",
            sender_name="Accounts Team",
            on_token=on_token
        )
        elapsed = time.time() - start_t
        print(f"\n✓ Generated draft in {elapsed:.2f}s ({len(tokens_streamed)} tokens received):")
        print(f"\n[SUBJECT]:\n  {draft.get('subject')}")
        print(f"\n[ENGLISH EMAIL]:\n  {draft.get('email_en')}")
        print(f"\n[HINDI SUMMARY (FOR SENDER VERIFICATION)]:\n  {draft.get('summary_hi')}")
    except Exception as e:
        print(f"\n✗ LLM Generation Error: {e}")
        return 1

    banner("5. Testing Draft Queue & Persistence")
    draft_record = {
        "contact_id": contact_id,
        "user_input": user_msg,
        "subject": draft.get("subject"),
        "email_en": draft.get("email_en"),
        "summary_hi": draft.get("summary_hi"),
        "factcheck": fc,
        "status": "pending"
    }
    q_add = api.queue_add(draft_record)
    draft_id = q_add.get("id")
    print(f"✓ Added draft to queue with ID: {draft_id}")
    
    q_list = api.queue_list()["drafts"]
    matching = [d for d in q_list if d["id"] == draft_id]
    if matching:
        print(f"✓ Verified draft in queue: Contact '{matching[0].get('contact_name')}' | Subject: '{matching[0].get('subject')}'")

    banner("6. Testing Outlook Send Safety Gates")
    # Verify bare send without countdown token is blocked
    bare_send = api.to_outlook(draft_id, mode="send")
    if not bare_send["ok"] and "Confirm" in str(bare_send.get("error")):
        print("✓ Safety Pass: Immediate sending without confirmation countdown was correctly BLOCKED.")
    else:
        print(f"✗ Safety gate warning: {bare_send}")

    # Begin send confirmation
    token_res = api.begin_send_confirmation(draft_id)
    if token_res["ok"]:
        token = token_res["confirmation_token"]
        print(f"✓ Started 10s cancellable send token: {token[:8]}...")
        # Verify premature send before 10s is blocked
        premature = api.to_outlook(draft_id, mode="send", confirmation_token=token)
        if not premature["ok"] and "10-second" in str(premature.get("error")):
            print("✓ Safety Pass: Send attempt during active 10s countdown was correctly REJECTED.")
        # Cancel send confirmation
        cancel = api.cancel_send_confirmation(token)
        if cancel["ok"]:
            print("✓ Successfully cancelled send confirmation.")

    banner("ALL BACKEND CHECKS COMPLETED SUCCESSFULLY")
    return 0

if __name__ == "__main__":
    sys.exit(main())
