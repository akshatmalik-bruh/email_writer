"""Minimal CLI smoke entry point: python -m backend.cli 'message' [contact_id]."""
import json
import sys
from . import factcheck, llm
from .db import Database
from .config import DEFAULT_MODEL

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv)<2:
        print("Usage: python -m backend.cli MESSAGE [CONTACT_ID]",file=sys.stderr); return 2
    message=sys.argv[1]
    db=Database()
    contact=db.contact(int(sys.argv[2])) if len(sys.argv)>2 else None
    draft=llm.generate(message,contact,db.setting("sign_off","Kind regards"),db.setting("sender_name",""),model=db.setting("model",DEFAULT_MODEL))
    draft["factcheck"]=factcheck.check(message,draft["subject"]+"\n"+draft["email_en"],contact)
    print(json.dumps(draft,ensure_ascii=False,indent=2)); return 0

if __name__ == "__main__": raise SystemExit(main())
