"""Contact alias resolution and salutation defaults."""
import re

def _norm(text):
    return re.sub(r"[^\w@.+-]+", " ", str(text or "").casefold()).strip()

def salutation_for(name):
    parts = str(name or "").strip().split()
    return ("Dear " + (parts[-1] if len(parts) > 1 else parts[0])) if parts else "Dear Sir/Madam"

def resolve(text, contacts):
    haystack = _norm(text)
    hits = []
    for contact in contacts:
        variants = [contact.get("name", ""), *str(contact.get("aliases", "")).split(",")]
        matched = [v.strip() for v in variants if _norm(v) and re.search(r"(?<!\w)" + re.escape(_norm(v)) + r"(?!\w)", haystack)]
        if matched:
            hits.append({**contact, "matched_alias": matched[0]})
    return sorted(hits, key=lambda x: (-len(_norm(x["matched_alias"])), x.get("name", "").casefold()))
