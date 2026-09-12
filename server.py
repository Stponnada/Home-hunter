"""Home Hunter - map-native broker. Stdlib only, works offline.
Run: python3 server.py  -> http://localhost:8787
LLM (optional, explanations only): set LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in .env.
Sieve is deterministic so demo never breaks without a key.
"""
import json, math, os, re, urllib.request, urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data", "listings_hyd.json")
HAZ = os.path.join(ROOT, "data", "hazards.json")
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

def load_listings():
    with open(DATA) as f: return json.load(f)
def load_hazards():
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
    except: return str(n)
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
    "city": "Hyderabad", "property_type": "apartment",
    "budget_hard": 18000000, "floor_min": 5, "floor_max": 10, "floor_tol": 2,
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
        # stage 1: type
        if prefs.get("property_type") and L.get("property_type") != prefs["property_type"]:
            item["bucket"] = "rejected"; item["cut_stage"] = "type"; item["why_out"].append(f"type {L.get('property_type')} ≠ {prefs['property_type']}")
            rej.append(item); continue
        # stage 2: budget hard
        if L["price"] > prefs["budget_hard"]:
            item["bucket"] = "rejected"; item["cut_stage"] = "budget"; item["why_out"].append(f"{inr(L['price'])} over hard limit {inr(prefs['budget_hard'])}")
            rej.append(item); continue
        item["why_fit"].append(f"{inr(L['price'])} in budget")
        # stage 3: floor exact / maybe
        fl, lo, hi, tol = L.get("floor", 0), prefs["floor_min"], prefs["floor_max"], prefs.get("floor_tol", 2)
        if lo <= fl <= hi:
            item["why_fit"].append(f"floor {fl} in {lo}-{hi}")
        elif lo - tol <= fl <= hi + tol:
            item["bucket"] = "maybe"; item["why_fit"].append(f"floor {fl} close to {lo}-{hi} (maybe)")
        else:
            item["bucket"] = "rejected"; item["cut_stage"] = "floor"; item["why_out"].append(f"floor {fl} outside {lo}-{hi}±{tol}")
            rej.append(item); continue
        # stage 4: commute perimeter
        if c > prefs["max_commute_min"]:
            item["bucket"] = "rejected"; item["cut_stage"] = "commute"; item["why_out"].append(f"~{c}min > {prefs['max_commute_min']}min commute")
            rej.append(item); continue
        item["why_fit"].append(f"~{c}min commute")
        # stage 5: exclusion perimeters
        hit = None
        for h in excl:
            d = hav_km(L["lat"], L["lng"], h["lat"], h["lng"])
            if d < h.get("radius_km", 1.0):
                hit = f"{d:.1f}km from {h['name']}"; break
        if hit:
            item["bucket"] = "rejected"; item["cut_stage"] = "exclusion"; item["why_out"].append("excluded: " + hit)
            rej.append(item); continue
        # stage 6: trust
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
    fit.sort(key=lambda x: (x["price"], x["commute_min"]))
    maybe.sort(key=lambda x: (x["price"], x["commute_min"]))
    if prefs.get("near_lat") is not None and prefs.get("near_radius_km"):
        fit = [x for x in fit if x.get("near_km", 1e9) <= prefs["near_radius_km"]]
        maybe = [x for x in maybe if x.get("near_km", 1e9) <= prefs["near_radius_km"]]
    meta = {"commute_radius_km": round(commute_radius_km(prefs["max_commute_min"]), 2),
            "exclusions": excl, "counts": {"fit": len(fit), "maybe": len(maybe), "rejected": len(rej)}}
    return fit, maybe, rej, meta

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
    m = re.search(r"(\d)\s?(br|bed|bhk)", t)
    if m: p["min_beds"] = int(m.group(1))
    m = re.search(r"floor[s]?\s*(\d+)\s*[-to]+\s*(\d+)", t)
    if m: p["floor_min"], p["floor_max"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{2,3})\s?min", t)
    if m: p["max_commute_min"] = int(m.group(1))
    if "house" in t or "villa" in t: p["property_type"] = "house"
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

def local_reply(prefs, fit, maybe, rej):
    L = [f"Visit {len(fit)}, maybe {len(maybe)}, cut {len(rej)} — {inr(prefs['budget_hard'])} hard, fl {prefs['floor_min']}-{prefs['floor_max']}, ≤{prefs['max_commute_min']}min."]
    for x in fit[:5]: L.append(f"• VISIT {x['title']} {x.get('price_display', x['price'])} fl{x['floor']} ~{x['commute_min']}min trust{x['trust']} — {'; '.join(x['why_fit'][:3])}")
    for x in maybe[:4]: L.append(f"• MAYBE {x['title']} {x.get('price_display', x['price'])} fl{x['floor']} — {'; '.join(x['why_fit'][-2:])}")
    for x in rej[:4]: L.append(f"• CUT {x['title']} — {'; '.join(x['why_out'][:2])}")
    if not LLM_KEY: L.append("Tip: set LLM_API_KEY in .env for natural-language summaries (sieve already ran).")
    return "\n".join(L)

def stages_summary(fit, maybe, rej):
    order = ["type", "budget", "floor", "commute", "exclusion", "trust"]
    labels = {"type": "type check (apartment only)", "budget": "hard budget cap", "floor": "floor band + tolerance",
              "commute": "commute perimeter", "exclusion": "exclusion zones", "trust": "seller trust"}
    return [{"stage": s, "label": labels[s],
             "cut": [{"id": x["id"], "title": x["title"], "reason": "; ".join(x["why_out"][:1])} for x in rej if x.get("cut_stage") == s]}
            for s in order]

def intel_for_area(area):
    q = f"{area} Hyderabad apartment buy review"
    links = {
        "google": "https://www.google.com/search?q=" + urllib.parse.quote_plus(q + " site:99acres.com OR site:magicbricks.com OR site:housing.com"),
        "reddit": "https://www.reddit.com/search/?q=" + urllib.parse.quote_plus(f"{area} Hyderabad flat review"),
        "maps": "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(f"{area}, Hyderabad"),
    }
    return {"area": area, "queries": [q, f"{area} Hyderabad water logging traffic HMDA"],
            "links": links, "tavily": web_search_stub(q) if TAVILY_KEY else []}

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
        if p == "/api/health":
            self._json({"ok": True, "llm": bool(LLM_KEY), "model": LLM_MODEL, "tavily": bool(TAVILY_KEY)}); return
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
            prefs = parse_text(body.get("message", ""), {**DEFAULTS, **body.get("prefs", {})})
            for k in ("work_lat", "work_lng", "near_lat", "near_lng", "near_radius_km", "budget_hard", "floor_min", "floor_max", "max_commute_min"):
                if k in body: prefs[k] = body[k]
            fit, maybe, rej, meta = sieve(prefs)
            meta["stages"] = stages_summary(fit, maybe, rej)
            ctx = f"Prefs {inr(prefs['budget_hard'])} hard, fl {prefs['floor_min']}-{prefs['floor_max']}, <={prefs['max_commute_min']}min Hyderabad. " + \
                  "Visit: " + "; ".join(f"{x['title']} {x.get('price_display', x['price'])} fl{x['floor']} ~{x['commute_min']}min" for x in fit[:5]) + \
                  ". Maybe: " + "; ".join(f"{x['title']} ({'; '.join(x['why_fit'][-1:])})" for x in maybe[:3])
            reply = None
            if LLM_KEY:
                try: reply = llm_explain("You are HomeHunter, a map-native broker in Hyderabad. Give a natural visit plan: VISIT / MAYBE / CUT with 1-line reasons each. Mention street view + share links.", f"User: {body.get('message','')}\n{ctx}")
                except Exception as e: reply = f"(LLM error: {e})\n" + local_reply(prefs, fit, maybe, rej)
            else: reply = local_reply(prefs, fit, maybe, rej)
            self._json({"reply": reply, "prefs": prefs, "fit": fit, "maybe": maybe, "rejected": rej, "meta": meta}); return
        self._json({"error": "not found"}, 404)

if __name__ == "__main__":
    os.chdir(PUBLIC)
    s = ThreadingHTTPServer(("127.0.0.1", 8787), H)
    print(f"Home Hunter → http://localhost:8787  (llm: {'on '+LLM_MODEL if LLM_KEY else 'off/local'})")
    s.serve_forever()
