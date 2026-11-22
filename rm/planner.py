"""WEEKLY PLANNER (owner approved Nov 22): ONE AI call that reads where Volvox stands and writes a ranked 'what to test next' list.
It reads: the live health report, the Agent Team's notebook (rm/agent_ideas), the goal ladder (rm/goals.json), the data wish-list.
It writes: rm/plans/plan.json (shown on the dashboard). That is ALL it does: it never changes a setting, never starts a trade, never
promotes anything. Code runs every test; the live race and the locked judge decide. If the AI is unavailable the old plan simply stays.
usage: python -m rm.planner"""
import os, json, glob, time, datetime as dt, requests
from . import ideas

OUT = "rm/plans/plan.json"
HERE = os.path.dirname(__file__)

def _small(v, limit=700):
    """keep short values, replace big ones by their keys, so the AI gets a readable digest"""
    if isinstance(v, dict): return {k: _small(x, limit) for k, x in list(v.items())[:40]}
    s = json.dumps(v, default=str)
    return v if len(s) <= limit else (f"<{len(v)} items>" if isinstance(v, list) else s[:limit] + "...")

def _health():
    try:
        r = requests.get("https://dukaelementsdotcom.github.io/autopilot/health.json", timeout=30)
        if r.status_code == 200: return r.json()
    except Exception as e: print("health report not reachable:", str(e)[:100])
    return {}

def _load(nm):
    try: return json.load(open(os.path.join(HERE, nm)))
    except Exception: return None

def digest():
    hh = _health(); ref = hh.get("referee") or {}
    ideas_ = []
    for f in sorted(glob.glob(os.path.join(HERE, "agent_ideas", "*.json")))[-12:]:
        try: d = json.load(open(f)); ideas_.append(f"{d.get('rule_name')}: {d.get('result', {}).get('summary', {}).get('verdict')} ({d.get('result', {}).get('summary', {}).get('avgMonthly')}%/month)")
        except Exception: pass
    goals = (_load("goals.json") or {}).get("ladder") or []
    wish = [f"{w.get('name')} [{w.get('status')}, {w.get('cost')}]" for w in ((_load("data_wishlist.json") or {}).get("items") or [])]
    return {"liveReferee": _small(ref, 900), "agentIdeasTried": ideas_, "goalLadder": _small(goals, 400), "dataWishlist": wish}

PROMPT = """You are the planner for Volvox, an autonomous PAPER crypto futures system (hourly bars, 40 liquid coins). A locked judge promotes a trader only on live evidence; contestants race in shadow accounts.
You never decide anything: you only propose what to test next, in order of expected value. The owner's goals (targets, not rules): consistent compounding, a real verifiable edge on both sides, shorts that work in any market, then bigger size once proven.
Known facts: the shorts edge has only been seen in a falling market; longs have no live edge yet; the bottleneck is independent live observations (not number of ideas); more contestants make the judge stricter; order book depth is hourly so it cannot see spoofing, executed-trade measures are safer.
Already racing (do not re-propose): shorts-only, exhaust-short, exhaust-short-wide, sniper, noregime, tide, tide-mix, confluence-long and other long contestants.
Current state (JSON): {state}
Rules: never call any edge "proven" (nothing is proven yet; say "seen so far"); do not propose anything already racing, already collecting (see dataWishlist status "collecting": whales, order book, funding, liquidations, options, news are ALREADY recorded) or already tried; prefer ideas that test something new about WHEN an edge works (market type, exhaustion, entries, costs) over more data; at most one paid item.\nAnswer with ONE JSON object: {{"headline": "one plain sentence on where we stand and what matters most this week", "items": [ up to 5 of {{"title": "short", "why": "one or two plain sentences", "how": "the cheapest honest way to test it (a lab test, a contestant, or data to collect)", "cost": "free|paid|build time"}} ]}}. Rank best first. Plain language, no hype, no invented numbers."""

def main():
    st = digest(); msgs = [{"role": "system", "content": "You are a careful research planner. Answer with one JSON object only, no other text."}, {"role": "user", "content": PROMPT.format(state=json.dumps(st, default=str)[:7000])}]
    plan = None
    for attempt in range(3):
        txt, who = ideas._groq_call(msgs, 1500)
        if txt:
            a, b = txt.find("{"), txt.rfind("}")
            try:
                j = json.loads(txt[a:b + 1])
                if isinstance(j.get("items"), list) and j["items"]: plan = {"model": who, "headline": str(j.get("headline", ""))[:300], "items": [{k: str(x.get(k, ""))[:400] for k in ("title", "why", "how", "cost")} for x in j["items"][:5] if isinstance(x, dict)]}; break
            except Exception as e: print("AI answer was not valid JSON:", str(e)[:80])
        else: print(f"AI unavailable (attempt {attempt + 1}/3): {who}")
        time.sleep(20 * (attempt + 1))
    if not plan: print("planner: no new plan this week (the old one stays)"); return
    plan["at"] = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"); plan["note"] = "A suggestion list only. Nothing here changes a setting or places a trade; the live race decides."
    os.makedirs(os.path.dirname(OUT), exist_ok=True); json.dump(plan, open(OUT, "w"), indent=1); print("Wrote", OUT, "-", plan["headline"])

if __name__ == "__main__": main()
