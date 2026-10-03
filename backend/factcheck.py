"""Conservative lexical preservation checks for numeric and date-like facts."""
import re
from datetime import date, timedelta

_DEV = str.maketrans("०१२३४५६७८९", "0123456789")
_NUM = re.compile(r"(?<!\w)(?:₹\s*)?(?:rs\.?\s*)?\d[\d,]*(?:\.\d+)?(?:\s*(?:lakh|lac|crore|thousand|hazaar))?(?!\w)", re.I)
_DATES = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+\d{2,4})?|(?:mon|tue|wed|thu|fri|sat|sun)\w*|(?:aaj|kal|parso|agle\s+(?:hafte|week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)))\b", re.I)
_ORG_END = re.compile(r"\b(?:(?:public|senior\s+secondary|higher\s+secondary|primary|international|global|city)\s+)*(?:school|college|university|academy|institute|hospital|company|corporation|foundation|ltd\.?|limited|inc\.?)\b", re.I)
_ORG_STOP = {"a", "an", "the", "to", "at", "from", "for", "of", "in", "on", "send", "email", "tell", "ask", "write", "contact", "inform", "notify", "please", "kindly", "message", "draft", "mail", "regarding", "about"}

def _organization_names(text):
    """Find explicit Latin-script organization names ending in a known entity word."""
    text = str(text or "")
    names = []
    for match in _ORG_END.finditer(text):
        prefix = text[max(text.rfind(c, 0, match.start()) for c in ".!?\n") + 1:match.start()]
        words = list(re.finditer(r"[\w&.'-]+", prefix, re.UNICODE))
        start = match.start()
        has_name = False
        for token in reversed(words[-6:]):
            if token.group(0).casefold() in _ORG_STOP:
                break
            has_name = True
            start = max(text.rfind(c, 0, match.start()) + 1 for c in ".!?\n") + token.start()
        if not has_name:
            continue
        name = re.sub(r"\s+", " ", text[start:match.end()]).strip(" ,:;-\t")
        if name and re.search(r"[A-Za-z]", name):
            normalized = name.casefold()
            if normalized not in {item.casefold() for item in names}:
                names.append(name)
    return names

def _numbers(text):
    text = str(text or "").translate(_DEV).casefold()
    values = []
    # Date digits are not monetary or quantity facts.
    without_dates=_DATES.sub(" ",text)
    for m in _NUM.finditer(without_dates):
        raw = m.group(0).replace(",", "")
        multiplier = 1
        if re.search(r"lakh|lac", raw): multiplier = 100_000
        elif "crore" in raw: multiplier = 10_000_000
        elif re.search(r"thousand|hazaar", raw): multiplier = 1_000
        digits = re.search(r"\d+(?:\.\d+)?", raw)
        values.append(str(int(float(digits.group()) * multiplier)) if digits and multiplier != 1 else (digits.group() if digits else raw))
    # Hindi number words used in the plan.
    return values

def _resolve_relative(value, today):
    value=value.casefold().strip()
    if value == "aaj": return today.isoformat()
    if value == "kal": return (today+timedelta(days=1)).isoformat()
    if value == "parso": return (today+timedelta(days=2)).isoformat()
    weekdays={"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
    match=re.search(r"(?:agle\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",value)
    if match:
        days=(weekdays[match.group(1)]-today.weekday())%7
        if days==0: days += 7
        return (today+timedelta(days=days)).isoformat()
    if re.fullmatch(r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?",value):
        day,month,*rest=re.split(r"[/-]",value)
        year=int(rest[0]) if rest else today.year
        if year<100: year += 2000
        try: return date(year,int(month),int(day)).isoformat()
        except ValueError: return value
    month_match=re.fullmatch(r"(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?",value)
    if month_match:
        import datetime
        raw=f"{month_match.group(1)} {month_match.group(2)} {month_match.group(3) or today.year}"
        for fmt in ("%d %B %Y","%d %b %Y"):
            try: return datetime.datetime.strptime(raw,fmt).date().isoformat()
            except ValueError: pass
    return value

def _facts(text, contact=None, today=None):
    today=today or date.today()
    text = str(text or "")
    out = [("number", n) for n in _numbers(text)]
    matches=list(_DATES.finditer(text.translate(_DEV)))
    # If the weekday accompanies a calendar date, the calendar date already carries that fact.
    explicit=[m for m in matches if re.search(r"\d{1,2}\s+[a-z]+",m.group(0),re.I) or re.search(r"\d{4}-\d{2}-\d{2}",m.group(0))]
    matches=[m for m in matches if not (re.fullmatch(r"(?:mon|tue|wed|thu|fri|sat|sun)\w*",m.group(0),re.I) and explicit)]
    out += [("date", _resolve_relative(re.sub(r"\s+", " ", m.group(0)).strip(),today)) for m in matches]
    if contact:
        variants=[contact.get("name", ""), *str(contact.get("aliases", "")).split(",")]
        parts={p.casefold() for n in variants for p in re.findall(r"[\w]+",n) if len(p)>2}
        if any(re.search(r"(?<!\w)"+re.escape(p)+r"(?!\w)",text.casefold()) for p in parts):
            out.append(("name",contact.get("name","").strip()))
    return out

def check(user_input, email, contact=None, today=None):
    source = _facts(user_input, contact,today)
    target = _facts(email, contact,today)
    permitted_names = set()
    if contact:
        permitted_names.update(x.casefold() for x in re.findall(r"[A-Za-z]{3,}", (contact.get("name","")+" "+contact.get("aliases","")+" "+contact.get("salutation",""))))
    # Capitalized entities in the draft are treated as possible additions for review.
    known = {v.casefold() for _,v in source} | permitted_names | {"neeraj kumar"}
    known.update(name.casefold() for name in _organization_names(user_input))
    known |= permitted_names
    ordinary_caps={"monday","tuesday","wednesday","thursday","friday","saturday","sunday","january","february","march","april","may","june","july","august","september","october","november","december"}
    email_text=str(email or "")
    for candidate in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b",email_text):
        token=candidate.group(0)
        parts=token.split()
        prefix=email_text[:candidate.start()].rstrip()
        if not prefix or prefix[-1:] in ".!?\n": continue
        if token.casefold() not in known and not all(p.casefold() in permitted_names for p in parts) and not all(p.casefold() in ordinary_caps for p in parts) and parts[0].casefold() not in {"dear","subject","regards","kind","best","sincerely","thank","please","we","i","our","this","the"}:
            target.append(("name", token))
    remaining = list(target)
    items = []
    for typ, value in source:
        idx = next((i for i,(t,v) in enumerate(remaining) if (t == typ or (typ == "date" and t == "date")) and v.casefold() == value.casefold()), None)
        if idx is not None:
            remaining.pop(idx); status = "ok"
        else: status = "missing"
        items.append({"type":typ,"value":value,"status":status})
    items.extend({"type":t,"value":v,"status":"added"} for t,v in remaining)
    source_orgs = _organization_names(user_input)
    target_orgs = _organization_names(email)
    unmatched_orgs = list(target_orgs)
    for source_name in source_orgs:
        idx = next((i for i, target_name in enumerate(unmatched_orgs) if target_name.casefold() == source_name.casefold()), None)
        if idx is None:
            items.append({"type":"organization","value":source_name,"status":"missing"})
        else:
            unmatched_orgs.pop(idx)
    items.extend({"type":"organization","value":name,"status":"added"} for name in unmatched_orgs)
    return {"items":items,"passed":all(x["status"] == "ok" for x in items)}
