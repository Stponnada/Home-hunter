"""Home Hunter - map-native broker. Stdlib only, works offline.
Run: python3 server.py  -> http://localhost:8787
LLM (optional, explanations only): set LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in .env.
Sieve is deterministic so demo never breaks without a key.
"""
import json, math, os, re, shutil, subprocess, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data", "listings.json")
HAZ = os.path.join(ROOT, "data", "hazards.json")
DB = os.path.join(ROOT, "data", "listings.db")
PUBLIC = os.path.join(ROOT, "public")

def _load_dotenv():
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p): return
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ: os.environ[k] = v
_load_dotenv()

LLM_BASE = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
LLM_KEY = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "") or os.environ.get("OPENROUTER_MODEL", "muse-spark-1.3-contributor")
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
EXA_KEY = os.environ.get("EXA_API_KEY", "")

def exa_search(query, num=5, domains=None):
    """Sponsor hook: Exa web search (place reviews, reddit). None when no key — links still work."""
    if not EXA_KEY: return None
    try:
        body = json.dumps({"query": query, "numResults": num, "type": "auto", "contents": {"text": True},
                           "includeDomains": domains or [], "useAutoprompt": True}).encode()
        req = urllib.request.Request("https://api.exa.ai/search", data=body,
            headers={"x-api-key": EXA_KEY, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r).get("results", [])
    except Exception as e:
        return [{"error": str(e)}]
OPENCODE_BIN = (os.path.expanduser(os.environ.get("OPENCODE_BIN", "") or "") or shutil.which("opencode")
                or os.path.expanduser("~/.nvm/versions/node/v22.23.1/bin/opencode"))
OPENCODE_MODEL = os.environ.get("OPENCODE_MODEL", "opencode-go/muse-spark-1.3-contributor")
OPENCODE_TIMEOUT = int(os.environ.get("OPENCODE_TIMEOUT", "110"))

def opencode_available():
    return bool(OPENCODE_BIN and os.path.exists(OPENCODE_BIN))

def agent_context(prefs=None):
    try:
        rows = load_listings()
        areas = sorted(set(r.get("area", "?") for r in rows))
    except Exception:
        return "HomeHunter listing data is currently unavailable."
    return (f"You are the HomeHunter agent, living inside a map app for Hyderabad's Financial District. "
            f"Index: {len(rows)} curated listings across {', '.join(areas[:10])}. "
            f"Answer concisely about homes, areas, commute and prices (INR). "
            f"Current prefs: {json.dumps(prefs) if prefs else 'defaults'}.")

def opencode_chat(message, context="", continue_session=False, session_id=None):
    """GUI shell around the opencode terminal: headless `opencode run` in the project dir."""
    if not opencode_available():
        return None, "opencode binary not found. Set OPENCODE_BIN in .env."
    prompt = (context + "\n\nUser: " + message) if context else message
    cmd = [OPENCODE_BIN, "run", "-m", OPENCODE_MODEL]
    if session_id: cmd += ["-s", session_id]
    elif continue_session: cmd += ["-c"]
    cmd.append(prompt)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=OPENCODE_TIMEOUT, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return None, f"opencode timed out after {OPENCODE_TIMEOUT}s."
    out = re.sub(r"\x1b\[[0-9;]*m", "", p.stdout or "").strip()
    if p.returncode != 0:
        return None, (out or (p.stderr or "").strip() or f"opencode exited {p.returncode}")[-800:]
    return out, None

def _db():
    import sqlite3
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def load_listings():
    # Agent query layer: SQLite first, JSON fallback (run scripts/migrate_json_to_sql.py after curating).
    if os.path.exists(DB):
        con = _db()
        rows = [dict(r) for r in con.execute("SELECT * FROM listings")]
        con.close()
        return rows
    with open(DATA) as f: return json.load(f)

def query_listings(listing_type=None, property_type=None, max_price=None, area=None, limit=200):
    """Direct SQL access for the agent — same rows the sieve uses."""
    if not os.path.exists(DB):
        rows = load_listings()
        if listing_type: rows = [r for r in rows if r.get("listing_type", "sale") == listing_type]
        if property_type: rows = [r for r in rows if r.get("property_type") == property_type]
        if max_price is not None: rows = [r for r in rows if r.get("price") is not None and r["price"] <= max_price]
        if area: rows = [r for r in rows if area.lower() in (r.get("area") or "").lower()]
        return rows[:limit]
    q, args = ["SELECT * FROM listings WHERE 1=1"], []
    if listing_type: q.append("AND listing_type = ?"); args.append(listing_type)
    if property_type: q.append("AND property_type = ?"); args.append(property_type)
    if max_price is not None: q.append("AND price IS NOT NULL AND price <= ?"); args.append(max_price)
    if area: q.append("AND LOWER(area) LIKE ?"); args.append(f"%{area.lower()}%")
    q.append("ORDER BY price NULLS LAST LIMIT ?"); args.append(limit)
    con = _db()
    rows = [dict(r) for r in con.execute(" ".join(q), args)]
    con.close()
    return rows

def load_hazards():
    if os.path.exists(DB):
        con = _db()
        rows = [dict(r) for r in con.execute("SELECT * FROM hazards")]
        con.close()
        return rows
    try:
        with open(HAZ) as f: return json.load(f)
    except: return []

def hav_km(a, b, c, d):
    R = 6371.0
    p1, p2 = math.radians(a), math.radians(c)
    dd = math.radians(c - a); ll = math.radians(d - b)
    h = math.sin(dd/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(ll/2)**2
    return 2 * R * math.asin(math.sqrt(h))

def commute_min(lat, lng, wlat, wlng):
    return round(hav_km(lat, lng, wlat, wlng) * 4 + 12)

def commute_radius_km(max_min):
    return max(0.5, (max_min - 12) / 4.0)

SCAM = ["pay token first", "token amount before", "advance before visit", "cash only", "no agreement", "gpa sale", "litigation", "whatsapp only", "pay deposit first", "before viewing", "no contract", "overseas owner", "meet at mrt"]

def inr(n):
    try: n = float(n)
    except: return "Price on request"
    if n >= 1e7: return f"₹{n/1e7:.2f} Cr".rstrip("0").rstrip(".")
    if n >= 1e5: return f"₹{n/1e5:.1f} L"
    return f"₹{int(n):,}"

def trust_score(L):
    t = L.get("seller_type", "unknown"); d = (L.get("description","") + " " + L.get("seller","")).lower()
    flags = [p for p in SCAM if p in d]
    score, notes = 70, []
    if t == "owner": score += 10; notes.append("owner direct")
    elif t == "agent": score += 5; notes.append("agent listed")
    else: score -= 10; notes.append("unknown seller")
    if flags: score -= 25; notes.append("red flag: " + flags[0])
    if "serviced" in d: notes.append("serviced premium")
    return max(0, min(100, score)), flags, notes

def streetview_url(lat, lng):
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"

DEFAULTS = {
    "city": "Hyderabad", "property_type": "apartment", "listing_type": "sale",
    "budget_hard": 25000000, "floor_min": 5, "floor_max": 10, "floor_tol": 2,
    "min_beds": 2, "max_commute_min": 30,
    "work_lat": 17.4148, "work_lng": 78.3488,
    "exclude_kinds": ["sewage", "industrial", "nuclear", "lake_ftl"],
    "near_lat": None, "near_lng": None, "near_radius_km": None,
}

def sieve(prefs):
    hazards = load_hazards()
    excl = [h for h in hazards if h.get("kind") in prefs.get("exclude_kinds", [])]
    fit, maybe, rej = [], [], []
    for L in load_listings():
        item = dict(L)
        c = commute_min(L["lat"], L["lng"], prefs["work_lat"], prefs["work_lng"])
        item["commute_min"] = c
        item["streetview"] = streetview_url(L["lat"], L["lng"])
        item["share_path"] = f"/?place={L['id']}"
        score, flags, tnotes = trust_score(L)
        item["trust"] = score; item["scam_flags"] = flags; item["trust_notes"] = tnotes
        item["why_out"] = []; item["why_fit"] = []; item["bucket"] = "fit"; item["cut_stage"] = None
        item["price_display"] = inr(L["price"])
        # stage 0: sale vs rent (buy and rent budgets are different numbers)
        if prefs.get("listing_type") not in (None, "", "any") and L.get("listing_type", "sale") != prefs["listing_type"]:
            item["bucket"] = "rejected"; item["cut_stage"] = "listing_type"; item["why_out"].append(f"{L.get('listing_type', 'sale')} listing — looking for {prefs['listing_type']}")
            rej.append(item); continue
        # stage 1: type
        if prefs.get("property_type") not in (None, "", "any") and L.get("property_type") != prefs["property_type"]:
            item["bucket"] = "rejected"; item["cut_stage"] = "type"; item["why_out"].append(f"type {L.get('property_type')} ≠ {prefs['property_type']}")
            rej.append(item); continue
        # stage 2: bedrooms
        if L.get("beds", 0) < prefs.get("min_beds", 0):
            item["bucket"] = "rejected"; item["cut_stage"] = "beds"; item["why_out"].append(f"{L.get('beds', 0)} BHK below requested {prefs['min_beds']} BHK")
            rej.append(item); continue
        if prefs.get("min_beds"):
            item["why_fit"].append(f"{L.get('beds', 0)} BHK meets your need")
        # stage 3: budget hard
        if L.get("price") is not None and L["price"] > prefs["budget_hard"]:
            item["bucket"] = "rejected"; item["cut_stage"] = "budget"; item["why_out"].append(f"{inr(L['price'])} over hard limit {inr(prefs['budget_hard'])}")
            rej.append(item); continue
        if L.get("price") is not None: item["why_fit"].append(f"{inr(L['price'])} in budget")
        else: item["bucket"] = "maybe"; item["why_fit"].append("price not disclosed")
        # stage 4: floor exact / maybe
        fl, lo, hi, tol = L.get("floor"), prefs["floor_min"], prefs["floor_max"], prefs.get("floor_tol", 2)
        if fl is None:
            item["bucket"] = "maybe"; item["why_fit"].append("floor not disclosed")
        elif lo <= fl <= hi:
            item["why_fit"].append(f"floor {fl} in {lo}-{hi}")
        elif lo - tol <= fl <= hi + tol:
            item["bucket"] = "maybe"; item["why_fit"].append(f"floor {fl} close to {lo}-{hi} (maybe)")
        else:
            item["bucket"] = "rejected"; item["cut_stage"] = "floor"; item["why_out"].append(f"floor {fl} outside {lo}-{hi}±{tol}")
            rej.append(item); continue
        # stage 5: commute perimeter
        if c > prefs["max_commute_min"]:
            item["bucket"] = "rejected"; item["cut_stage"] = "commute"; item["why_out"].append(f"~{c}min > {prefs['max_commute_min']}min commute")
            rej.append(item); continue
        item["why_fit"].append(f"~{c}min commute")
        # stage 6: exclusion perimeters
        hit = None
        for h in excl:
            d = hav_km(L["lat"], L["lng"], h["lat"], h["lng"])
            if d < h.get("radius_km", 1.0):
                hit = f"{d:.1f}km from {h['name']}"; break
        if hit:
            item["bucket"] = "rejected"; item["cut_stage"] = "exclusion"; item["why_out"].append("excluded: " + hit)
            rej.append(item); continue
        # stage 7: trust
        if score < 40:
            item["bucket"] = "rejected"; item["cut_stage"] = "trust"; item["why_out"].append(f"trust {score}/100 ({'; '.join(tnotes)})")
            rej.append(item); continue
        if score < 65:
            item["bucket"] = "maybe"; item["why_fit"].append(f"trust {score}/100 — verify seller")
        else:
            item["why_fit"].append(f"trust {score}/100")
        # near-me (soft rank, not reject unless asked)
        if prefs.get("near_lat") is not None:
            item["near_km"] = round(hav_km(L["lat"], L["lng"], prefs["near_lat"], prefs["near_lng"]), 2)
        (fit if item["bucket"] == "fit" else maybe).append(item)
    fit.sort(key=lambda x: (x.get("price") or 10**12, x["commute_min"]))
    maybe.sort(key=lambda x: (x.get("price") or 10**12, x["commute_min"]))
    if prefs.get("near_lat") is not None and prefs.get("near_radius_km"):
        fit = [x for x in fit if x.get("near_km", 1e9) <= prefs["near_radius_km"]]
        maybe = [x for x in maybe if x.get("near_km", 1e9) <= prefs["near_radius_km"]]
    meta = {"commute_radius_km": round(commute_radius_km(prefs["max_commute_min"]), 2),
            "exclusions": excl, "counts": {"fit": len(fit), "maybe": len(maybe), "rejected": len(rej)}}
    return fit, maybe, rej, meta

def is_greeting(msg):
    t = (msg or "").strip().lower()
    if not t or len(t) > 40:
        return False
    if re.search(r"\d|crore|\bcr\b|lakh|\bbhk\b|bed|floor|minute|\bmin\b|budget|flat|apartment|rent|buy|villa|commute|near|avoid|not", t):
        return False
    return bool(re.match(r"^(hi+|hello|hey|yo|namaste|howdy|good (morning|afternoon|evening)|how are you|what'?s up)\b[.! ]*$", t))

def mentioned_criteria(msg):
    t = (msg or "").lower()
    m = set()
    if re.search(r"(\d+\.?\d*)\s?(cr|crore|l\b|lakh|lac)|₹\s?\d|\bbudget\b|\bunder\b|\bbelow\b", t): m.add("budget_hard")
    if re.search(r"\b\d\s?(br|bed|bhk)", t): m.add("min_beds")
    if "floor" in t: m.add("floor")
    if re.search(r"\d{1,3}\s?(min|commute)|commute|near work|close to|far from", t): m.add("max_commute_min")
    if any(w in t for w in ["apartment", "flat", "house", "villa"]): m.add("property_type")
    if any(w in t for w in ["rent", "buy", "purchase", "lease"]): m.add("listing_type")
    if any(w in t for w in ["sewage", "lake", "noise", "quiet", "avoid", "away from"]): m.add("exclusions")
    return m

AGENT_SYSTEM = (
    "You are HomeHunter, a conversational home-search assistant for Hyderabad's Financial District. "
    "You have a 31-listing curated index (sale + rent, apartments + villas) and a deterministic sieve tool. "
    "Rules: never invent listings, prices, floors or reviews. Never assume budget, BHK, floors or commute — "
    "use ONLY what the user stated; ask 1-2 short follow-up questions for what's missing. "
    "The workplace pin is always already set on the map — never ask the user for their workplace location. "
    "When the user greets you or says hi, reply exactly: 'Hi, I'm Home Hunter — let's hunt for the right home for you.' "
    "followed by one short question (e.g. are you looking to buy or rent?). Do not mention Financial District or any location in the greeting. "
    "Unrelated questions: answer briefly, then add one line saying you're Home Hunter, a home search assistant. "
    "When the user states home criteria, end your reply with a ```sieve JSON block, e.g. "
    '{"budget_hard": 25000000, "floor_min": 5, "floor_max": 10, "min_beds": 3, "max_commute_min": 30, '
    '"property_type": "apartment", "listing_type": "sale"} — include ONLY keys the user actually stated. '
    "If the message has no home criteria, output no sieve block.")

def extract_sieve_block(text):
    m = re.search(r"```sieve\s*(\{.*?\})\s*```", text or "", re.S)
    if not m: return None, text
    try: p = json.loads(m.group(1))
    except Exception: return None, text
    clean = {}
    for k, lo, hi in (("budget_hard", 500000, 1000000000), ("floor_min", 1, 50), ("floor_max", 1, 50),
                      ("min_beds", 1, 10), ("max_commute_min", 5, 120)):
        if k in p and isinstance(p[k], (int, float)) and lo <= p[k] <= hi:
            clean[k] = int(p[k])
    for k, ok in (("property_type", ("apartment", "villa", "house", "any")),
                  ("listing_type", ("sale", "rent", "any"))):
        if k in p and p[k] in ok: clean[k] = p[k]
    if clean.get("floor_min") and clean.get("floor_max") and clean["floor_min"] > clean["floor_max"]:
        clean["floor_min"], clean["floor_max"] = clean["floor_max"], clean["floor_min"]
    reply = (text[:m.start()] + text[m.end():]).strip()
    return (clean or None), reply

def local_conversational_reply(msg, mentioned):
    t = (msg or "").strip()
    if is_greeting(t) or not t:
        return ("Hi, I'm Home Hunter — let's hunt for the right home for you. "
                "Tell me what matters: budget, BHK, floor band, commute, anything to avoid.")
    if not mentioned:
        return ("I can help with that in a moment — first, a quick note on what I am: I'm HomeHunter, "
                "a home-search assistant for Hyderabad's Financial District with 31 curated listings on the map. "
                "I can't answer that one reliably. But if you tell me your budget, BHK, floor band or commute, "
                "I'll pull matching places with reasons.")
    return None

def local_search_reply(prefs, mentioned, fit, maybe, rej):
    stated = []
    if "budget_hard" in mentioned: stated.append(f"budget {inr(prefs['budget_hard'])}")
    if "min_beds" in mentioned: stated.append(f"{prefs['min_beds']} BHK")
    if "floor" in mentioned: stated.append(f"floors {prefs['floor_min']}-{prefs['floor_max']}")
    if "max_commute_min" in mentioned: stated.append(f"≤{prefs['max_commute_min']}min commute")
    if "property_type" in mentioned: stated.append(prefs["property_type"])
    if "listing_type" in mentioned: stated.append(f"for {prefs['listing_type']}")
    missing = [k for k in ("budget_hard", "min_beds", "floor", "max_commute_min") if k not in mentioned]
    ask = {"budget_hard": "what's your hard budget?", "min_beds": "how many BHK?",
           "floor": "any floor preference?", "max_commute_min": "max commute in minutes?"}
    head = f"Got it — searching on {', '.join(stated)}. " if stated else "Searching. "
    tail = ""
    if missing:
        assumed = {"budget_hard": f"budget {inr(prefs['budget_hard'])}", "floor": f"floors {prefs['floor_min']}-{prefs['floor_max']}",
                   "max_commute_min": f"≤{prefs['max_commute_min']}min commute", "min_beds": f"{prefs['min_beds']} BHK"}
        tail = ("I'm assuming " + ", ".join(assumed[k] for k in missing if k in assumed)
                + " for now — " + ask[missing[0]])
    L = [head + f"Found {len(fit)} worth visiting, {len(maybe)} close calls." + (" " + tail if tail else "")]
    for x in fit[:5]: L.append(f"• VISIT {x['title']} — {x.get('price_display', x['price'])}, {fl_str(x)}, ~{x['commute_min']}min. {'; '.join(x['why_fit'][:2])}.")
    for x in maybe[:4]: L.append(f"• MAYBE {x['title']} — {x.get('price_display', x['price'])}, {fl_str(x)}. {'; '.join(x['why_fit'][-2:])}.")
    return "\n".join(L)

def parse_text(msg, base):
    p = dict(base); t = msg.lower().replace(",", "")
    m = re.search(r"(\d+\.?\d*)\s?(cr|crore)", t)
    if m:
        p["budget_hard"] = int(float(m.group(1)) * 1e7)
    else:
        m = re.search(r"(\d+\.?\d*)\s?(l|lakh|lac)", t)
        if m:
            p["budget_hard"] = int(float(m.group(1)) * 1e5)
        else:
            m = re.search(r"₹?\s?(\d[\d]{5,9})", msg.replace(",", ""))
            if m:
                try: p["budget_hard"] = int(m.group(1))
                except: pass
    m = re.search(r"(\d)\s?(?:br|bed(?:room)?s?|bhk)", t)
    if m: p["min_beds"] = int(m.group(1))
    m = re.search(r"(?:between\s+)?floor[s]?\s*(\d+)\s*(?:-|–|to|and)\s*(\d+)", t)
    if not m:
        m = re.search(r"between\s+(\d+)\s+(?:and|to)\s+(\d+)\s+floor", t)
    if m: p["floor_min"], p["floor_max"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(?:within|under|less than|≤)?\s*(\d{1,3})\s?(?:min|mins|minutes?)", t)
    if m: p["max_commute_min"] = int(m.group(1))
    elif "short commute" in t or "near work" in t: p["max_commute_min"] = 30
    elif "quick commute" in t: p["max_commute_min"] = 25
    if "house" in t or "villa" in t: p["property_type"] = "villa"
    if "rent" in t or "rental" in t or "lease" in t: p["listing_type"] = "rent"
    if "buy" in t or "purchase" in t or "for sale" in t: p["listing_type"] = "sale"
    if "apartment" in t or "flat" in t: p["property_type"] = "apartment"
    return p

def llm_explain(system, user):
    if not LLM_KEY: return None
    body = json.dumps({"model": LLM_MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": 450}).encode()
    req = urllib.request.Request(LLM_BASE + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + LLM_KEY, "Content-Type": "application/json",
                 "HTTP-Referer": "http://localhost:8787", "X-Title": "HomeHunter"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)["choices"][0]["message"]["content"]

def fl_str(x):
    fl = x.get("floor")
    return "floor not disclosed" if fl is None else f"floor {fl} of {x.get('total_floors', '?')}"

def local_reply(prefs, fit, maybe, rej):
    L = [f"I found {len(fit)} places worth visiting and {len(maybe)} that may be worth visiting. I used your {inr(prefs['budget_hard'])} budget, floor preference, and {prefs['max_commute_min']}-minute commute.", "Worth visiting:"]
    for x in fit[:5]: L.append(f"• {x['title']} — {x.get('price_display', x['price'])}, {fl_str(x)}, about {x['commute_min']} minutes. {'; '.join(x['why_fit'][:2])}.")
    if not fit: L.append("• Nothing meets every preference yet — loosen one detail in your next message and I’ll try again.")
    L.append("Maybe worth visiting:")
    for x in maybe[:4]: L.append(f"• {x['title']} — {x.get('price_display', x['price'])}, {fl_str(x)}. {'; '.join(x['why_fit'][-2:])}.")
    if not maybe: L.append("• No close calls right now.")
    if rej: L.append(f"I left out {len(rej)} places that missed a hard requirement or raised a trust/location concern.")
    return "\n".join(L)

def stages_summary(fit, maybe, rej):
    order = ["listing_type", "type", "beds", "budget", "floor", "commute", "exclusion", "trust"]
    labels = {"listing_type": "for-sale only (rent hidden)", "type": "type check (apartment only)", "beds": "bedroom requirement", "budget": "hard budget cap", "floor": "floor band + tolerance",
              "commute": "commute perimeter", "exclusion": "exclusion zones", "trust": "seller trust"}
    return [{"stage": s, "label": labels[s],
             "cut": [{"id": x["id"], "title": x["title"], "reason": "; ".join(x["why_out"][:1])} for x in rej if x.get("cut_stage") == s]}
            for s in order]

def exa_text(item):
    t = (item.get("text") or item.get("summary") or "").strip()
    url = item.get("url", "") or ""
    dom = urllib.parse.urlparse(url).netloc.replace("www.", "") if url else "web"
    return t, url, dom

def gather_web_evidence(places, per_place=3):
    """Exa: one review query per listed place + one reddit query per area. {} when no key."""
    ev = {}
    if not EXA_KEY or not places:
        return ev
    jobs, seen_areas = [], []
    for x in places[:6]:
        jobs.append((x["id"], f"{x.get('title', '')} {x.get('area', '')} Hyderabad review", None))
        a = x.get("area")
        if a and a not in seen_areas:
            seen_areas.append(a)
    for a in seen_areas[:3]:
        jobs.append((f"area::{a}", f"{a} Hyderabad flat water logging traffic", ["reddit.com"]))
    def one(job):
        pid, q, doms = job
        try:
            res = exa_search(q, per_place, doms)
        except Exception:
            res = None
        out = []
        for it in (res or [])[:per_place]:
            if isinstance(it, dict) and not it.get("error"):
                t, url, dom = exa_text(it)
                if t:
                    out.append({"text": t[:600], "url": url, "domain": dom})
        return pid, out
    with ThreadPoolExecutor(max_workers=5) as ex:
        for pid, out in ex.map(one, jobs):
            if out:
                ev[pid] = out
    return ev

def web_paragraphs(places):
    """Model-written paragraphs per place from Exa excerpts; extractive fallback. None when no key/evidence."""
    if not EXA_KEY or not places:
        return None
    ev = gather_web_evidence(places)
    if not ev:
        return None
    if opencode_available():
        lines = []
        for x in places[:6]:
            if x["id"] not in ev:
                continue
            lines.append(f"[{x['title']}]")
            lines += [f"- {e['text'][:400]} (source: {e['domain']})" for e in ev[x["id"]][:3]]
            akey = f"area::{x.get('area')}"
            if akey in ev:
                lines += [f"- area note: {e['text'][:300]} (source: {e['domain']})" for e in ev[akey][:2]]
        if lines:
            prompt = ("For each [place] below, write one short paragraph (2-3 sentences) on what the web says "
                      "about living there. Use ONLY the excerpts; if they say little, say so briefly. "
                      "No invented facts, prices or floors.\n---\n" + "\n".join(lines))
            text, err = opencode_chat(prompt, context="You are a careful summarizer.")
            if not err and text:
                return "\n\nWhat the web says:\n" + text.strip()
    parts = []
    for x in places[:6]:
        if x["id"] not in ev:
            continue
        e = ev[x["id"]][0]
        parts.append(f"\u2022 {x['title']}: \u201c{e['text'][:300]}\u201d (via {e['domain']})")
    return ("\n\nWhat the web says:\n" + "\n".join(parts)) if parts else None

def intel_for_area(area):
    q = f"{area} Hyderabad apartment buy review"
    links = {
        "google": "https://www.google.com/search?q=" + urllib.parse.quote_plus(q + " site:99acres.com OR site:magicbricks.com OR site:housing.com"),
        "reddit": "https://www.reddit.com/search/?q=" + urllib.parse.quote_plus(f"{area} Hyderabad flat review"),
        "maps": "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(f"{area}, Hyderabad"),
    }
    out = {"area": area, "queries": [q, f"{area} Hyderabad water logging traffic HMDA"],
           "links": links, "tavily": web_search_stub(q) if TAVILY_KEY else [], "exa": None}
    if EXA_KEY:
        out["exa"] = {"reviews": exa_search(f"{area} Hyderabad apartment review water logging", 5),
                      "reddit": exa_search(f"{area} Hyderabad flat", 5, ["reddit.com"])}
    return out

def web_search_stub(query):
    # Teammate hook: plug Tavily/Firecrawl here. Returns [] offline so demo never breaks.
    if TAVILY_KEY:
        try:
            b = json.dumps({"api_key": TAVILY_KEY, "query": query, "max_results": 5}).encode()
            req = urllib.request.Request("https://api.tavily.com/search", data=b, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r).get("results", [])
        except Exception as e:
            return [{"error": str(e)}]
    return []

class H(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, o, code=200):
        b = json.dumps(o).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urlparse(self.path); p = u.path
        if p in ("/", "/index.html"): self.path = "/index.html"; super().do_GET(); return
        if p == "/api/listings": self._json(load_listings()); return
        if p == "/api/hazards": self._json(load_hazards()); return
        if p == "/api/query":
            a = parse_qs(u.query)
            one = lambda k, f=None: (f(a[k][0]) if k in a and a[k][0] != "" else None) if f else (a[k][0] if k in a else None)
            self._json(query_listings(listing_type=one("listing_type"), property_type=one("property_type"),
                                      max_price=one("max_price", int), area=one("area"),
                                      limit=int(a["limit"][0]) if "limit" in a else 200)); return
        if p == "/api/health":
            self._json({"ok": True, "llm": bool(LLM_KEY), "model": LLM_MODEL, "tavily": bool(TAVILY_KEY),
                        "exa": bool(EXA_KEY),
                        "agent": opencode_available(), "agent_model": OPENCODE_MODEL}); return
        if p == "/api/search":
            q = parse_qs(u.query).get("q", ["apartment"])[0]
            self._json({"query": q, "results": web_search_stub(q)}); return
        if p == "/api/intel":
            area = parse_qs(u.query).get("area", ["Financial District"])[0]
            self._json(intel_for_area(area)); return
        super().do_GET()
    def do_POST(self):
        u = urlparse(self.path); p = u.path
        n = int(self.headers.get("Content-Length", 0))
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except: body = {}
        if p in ("/api/sieve", "/api/filter"):
            prefs = {**DEFAULTS, **body}
            fit, maybe, rej, meta = sieve(prefs)
            meta["stages"] = stages_summary(fit, maybe, rej)
            self._json({"prefs": prefs, "fit": fit, "maybe": maybe, "rejected": rej, "meta": meta,
                        "summary": f"{len(fit)} visit, {len(maybe)} maybe, {len(rej)} cut"}); return
        if p == "/api/chat":
            msg = body.get("message", "") or ""
            base = {**DEFAULTS, **body.get("prefs", {})}
            for k in ("work_lat", "work_lng", "near_lat", "near_lng", "near_radius_km", "budget_hard", "floor_min", "floor_max", "max_commute_min"):
                if k in body: base[k] = body[k]
            mentioned = mentioned_criteria(msg)
            # 1. reasoning agent first (opencode terminal, your subscription — no extra key)
            if opencode_available() and msg.strip():
                try:
                    work_note = (f"The user's workplace pin is already set on the map at "
                                 f"({base.get('work_lat'):.4f}, {base.get('work_lng'):.4f}). Commute is measured "
                                 f"from there — never ask for the workplace location.")
                    raw, err = opencode_chat(msg, context=AGENT_SYSTEM + "\n" + work_note + "\nIndex areas: " +
                                             ", ".join(sorted(set(r.get("area", "?") for r in load_listings()))),
                                             continue_session=bool(body.get("continue")),
                                             session_id=body.get("session_id"))
                    if not err and raw:
                        sieved, reply = extract_sieve_block(raw)
                        if sieved:
                            prefs = dict(base)
                            for k in ("budget_hard", "floor_min", "floor_max", "min_beds", "max_commute_min", "property_type", "listing_type"):
                                if k in sieved: prefs[k] = sieved[k]
                            fit, maybe, rej, meta = sieve(prefs)
                            meta["stages"] = stages_summary(fit, maybe, rej)
                            text = reply or raw
                            wp = web_paragraphs(fit + maybe)
                            if wp:
                                text += wp
                            self._json({"reply": text, "prefs": prefs, "fit": fit, "maybe": maybe,
                                        "rejected": rej, "meta": meta, "searched": True, "via": "agent"}); return
                        self._json({"reply": raw, "prefs": base, "fit": [], "maybe": [], "rejected": [],
                                    "meta": {}, "searched": False, "via": "agent"}); return
                except Exception:
                    pass
            # 2. local fallback: converse, never search by default
            if not mentioned:
                self._json({"reply": local_conversational_reply(msg, mentioned), "prefs": base,
                            "fit": [], "maybe": [], "rejected": [], "meta": {}, "searched": False, "via": "local"}); return
            prefs = parse_text(msg, base)
            fit, maybe, rej, meta = sieve(prefs)
            meta["stages"] = stages_summary(fit, maybe, rej)
            text = local_search_reply(prefs, mentioned, fit, maybe, rej)
            wp = web_paragraphs(fit + maybe)
            if wp:
                text += wp
            self._json({"reply": text, "prefs": prefs,
                        "fit": fit, "maybe": maybe, "rejected": rej, "meta": meta, "searched": True, "via": "local"}); return
        if p == "/api/agent":
            msg = (body.get("message", "") or "").strip()
            if not msg:
                self._json({"error": "empty message"}, 400); return
            prefs = {**DEFAULTS, **body.get("prefs", {})}
            reply, err = opencode_chat(msg, context=agent_context(prefs),
                                       continue_session=bool(body.get("continue")),
                                       session_id=body.get("session_id"))
            if err:
                self._json({"error": err}, 502); return
            self._json({"reply": reply, "model": OPENCODE_MODEL}); return
        self._json({"error": "not found"}, 404)

if __name__ == "__main__":
    os.chdir(PUBLIC)
    s = ThreadingHTTPServer(("127.0.0.1", 8787), H)
    print(f"Home Hunter → http://localhost:8787  (llm: {'on '+LLM_MODEL if LLM_KEY else 'off/local'})")
    s.serve_forever()
