"""Where ideas come from — no human needed:
  1. scan     : a systematic sweep of every measurement × extreme × direction × exit (worked through over many days)
  2. evolve   : small mutations of ideas that survived (or nearly survived) — max 2 generations deep
  3. combine  : two surviving ideas must agree (AND), the "confirmation" idea
  4. ai       : an AI researcher (Groq, free) proposes new hypotheses with a mechanism, from the machine's own notes
  5. explore  : random two-condition rules, so it also tries things nobody would think of"""
import itertools, json, random, re, os, requests
from . import config as C
from .judge import hid, valid, family
from .features import DESC

TAILS = [(0.0, 0.05), (0.0, 0.1), (0.9, 1.0), (0.95, 1.0)]
SCAN_EXITS = ["b1_1_24", "b2_2_48", "b05_15_24", "r24", "b1_2_24", "r72"]

def scan_space(cols):
    feats = sorted(c for c in cols if c.startswith(("p_", "x_")))
    out = []
    for uni in ("BTC", "ALL", "MAJ"):
        for ex in SCAN_EXITS:
            for f in feats:
                for lo, hi in TAILS:
                    for d in ("long", "short", "with", "against"):
                        out.append({"kind": "event", "uni": uni, "when": [{"f": f, "lo": lo, "hi": hi}], "dir": d, "exit": ex, "cool": 24})
            for hr in range(0, 24, 2):                       # time-of-day windows
                for d in ("long", "short", "with", "against"):
                    out.append({"kind": "event", "uni": uni, "when": [{"f": "hour", "lo": hr, "hi": hr + 1.5}], "dir": d, "exit": ex, "cool": 24})
    for f in sorted(c for c in cols if c.startswith("x_")) + ["p_glob", "p_topPos", "p_oi24", "p_tk4", "p_timb24", "p_r168", "p_vol24"]:
        for order in ("low_long", "high_long"):
            for frac in (0.2, 0.33):
                for rb in (24, 84, 168):
                    out.append({"kind": "xsec", "rank": f, "order": order, "frac": frac, "rebal": rb})
    random.Random(7).shuffle(out)                          # mixed order, fixed forever (the cursor walks through it)
    return out

# ── ideas borrowed from open-source bots (freqtrade-style classics), on hourly bars ──────────────
CLASSIC_RULES = [
    ("RSI oversold", [{"f": "rsi14", "lo": 0, "hi": 30}], "long"), ("RSI deep oversold", [{"f": "rsi14", "lo": 0, "hi": 20}], "long"),
    ("RSI overbought", [{"f": "rsi14", "lo": 70, "hi": 100}], "short"), ("RSI extreme overbought", [{"f": "rsi14", "lo": 80, "hi": 100}], "short"),
    ("RSI oversold in uptrend", [{"f": "rsi14", "lo": 0, "hi": 30}, {"f": "emat", "lo": 0, "hi": 100}], "long"),
    ("RSI overbought in downtrend", [{"f": "rsi14", "lo": 70, "hi": 100}, {"f": "emat", "lo": -100, "hi": 0}], "short"),
    ("Below lower Bollinger band", [{"f": "bbz", "lo": -20, "hi": -2}], "long"), ("Above upper Bollinger band", [{"f": "bbz", "lo": 2, "hi": 20}], "short"),
    ("Bollinger breakout up", [{"f": "bbz", "lo": 2, "hi": 20}], "long"), ("Bollinger breakdown", [{"f": "bbz", "lo": -20, "hi": -2}], "short"),
    ("EMA golden cross", [{"f": "cross", "lo": 0.5, "hi": 1.5}], "long"), ("EMA death cross", [{"f": "cross", "lo": -1.5, "hi": -0.5}], "short"),
    ("Golden cross in uptrend", [{"f": "cross", "lo": 0.5, "hi": 1.5}, {"f": "emat", "lo": 0, "hi": 100}], "long"),
    ("Death cross in downtrend", [{"f": "cross", "lo": -1.5, "hi": -0.5}, {"f": "emat", "lo": -100, "hi": 0}], "short"),
    ("Donchian 20-day breakout", [{"f": "don20", "lo": 0.98, "hi": 1.0}], "long"), ("Donchian 20-day breakdown", [{"f": "don20", "lo": 0.0, "hi": 0.02}], "short"),
    ("Fade 20-day high", [{"f": "don20", "lo": 0.98, "hi": 1.0}], "short"), ("Buy 20-day low", [{"f": "don20", "lo": 0.0, "hi": 0.02}], "long"),
    ("Strong trend up (ADX>25)", [{"f": "adx14", "lo": 25, "hi": 100}, {"f": "emax", "lo": 0, "hi": 100}], "long"),
    ("Strong trend down (ADX>25)", [{"f": "adx14", "lo": 25, "hi": 100}, {"f": "emax", "lo": -100, "hi": 0}], "short"),
    ("MACD turns up below zero", [{"f": "macdh", "lo": 0, "hi": 100}, {"f": "emax", "lo": -100, "hi": 0}], "long"),
    ("MACD turns down above zero", [{"f": "macdh", "lo": -100, "hi": 0}, {"f": "emax", "lo": 0, "hi": 100}], "short"),
    ("Oversold while the crowd is short", [{"f": "rsi14", "lo": 0, "hi": 30}, {"f": "p_glob", "lo": 0, "hi": 0.2}], "long"),
    ("Overbought while the crowd is long", [{"f": "rsi14", "lo": 70, "hi": 100}, {"f": "p_glob", "lo": 0.8, "hi": 1}], "short"),
]
def classic_space(cols):
    out = []
    for name, when, d in CLASSIC_RULES:
        for uni in ("BTC", "MAJ", "ALL"):
            for ex in ("b1_1_24", "b2_2_48", "b1_2_24", "b3_3_72", "r24", "r72"):
                h = {"kind": "event", "uni": uni, "when": when, "dir": d, "exit": ex, "cool": 24}
                if valid(h, cols): out.append((h, f"open-source classic: {name}"))
    return out

# ── market-neutral / structural ideas (no price prediction) ─────────────────────────────────────
PAIRS = [("ETH", "BTC"), ("SOL", "ETH"), ("SOL", "BTC"), ("BNB", "BTC"), ("LTC", "BCH"), ("ADA", "XRP"), ("DOGE", "BTC"), ("AVAX", "SOL"),
         ("LINK", "ETH"), ("DOT", "ATOM"), ("UNI", "AAVE"), ("ARB", "OP"), ("ETC", "ETH"), ("NEAR", "SOL"), ("FIL", "DOT"), ("TRX", "XRP")]
def structural_space():
    out = []
    for uni in ("BTC", "MAJ", "ALL"):
        for enter, leave in ((0.05, 0.0), (0.10, 0.0), (0.10, 0.05), (0.20, 0.05), (0.30, 0.10), (0.50, 0.10)):
            out.append(({"kind": "carry", "uni": uni, "enter": enter, "leave": leave}, "funding carry (hedged: spot long + perp short)"))
    for a, b in PAIRS:
        for z in (2.0, 2.5, 3.0):
            for look in (336, 720):
                for hold in (24, 72, 168):
                    for mode in ("revert", "trend"):
                        out.append(({"kind": "pair", "a": a, "b": b, "z": z, "look": look, "hold": hold, "mode": mode}, "pairs trading (market-neutral)"))
    random.Random(11).shuffle(out)
    return out

def mutate(h, cols, rng):
    h = json.loads(json.dumps(h))
    if h["kind"] == "union":
        i = rng.randrange(len(h["parts"])); h["parts"][i] = mutate(h["parts"][i], cols, rng); return h
    if h["kind"] == "rotate":
        op = rng.choice(["hold", "stop", "order", "rank", "when"])
        if op == "hold": h["hold"] = rng.choice([24, 48, 72])
        elif op == "stop": h["stop"] = rng.choice([None, 0.03, 0.05, 0.08, 0.12])
        elif op == "order": h["order"] = "low" if h["order"] == "high" else "high"
        elif op == "rank": h["rank"] = rng.choice(sorted(c for c in cols if c.startswith(("p_", "x_")) and not c.startswith(("p_fg", "p_cbp", "p_dvol", "p_stab"))))
        else:
            f = rng.choice(sorted(c for c in cols if c.startswith("p_"))); lo, hi = rng.choice([(0.0, 0.3), (0.7, 1.0), (0.0, 0.5), (0.5, 1.0)])
            h["when"] = ((h.get("when") or [])[:1]) + [{"f": f, "lo": lo, "hi": hi}]
        return h
    if h["kind"] == "pair":
        op = rng.choice(["z", "look", "hold"])
        if op == "z": h["z"] = rng.choice([1.5, 2.0, 2.5, 3.0, 3.5])
        elif op == "look": h["look"] = rng.choice([168, 336, 720, 1440])
        else: h["hold"] = rng.choice([24, 72, 168])
        return h
    if h["kind"] == "carry":
        h["enter"] = rng.choice([0.05, 0.08, 0.10, 0.15, 0.20, 0.30, 0.50]); h["leave"] = min(h["leave"], h["enter"] / 2)
        if rng.random() < 0.3: h["uni"] = rng.choice(["BTC", "MAJ", "ALL"])
        return h
    if h["kind"] == "xsec":
        op = rng.choice(["frac", "rebal", "order"])
        if op == "frac": h["frac"] = rng.choice([0.2, 0.25, 0.33, 0.5])
        elif op == "rebal": h["rebal"] = rng.choice([24, 84, 168])
        else: h["order"] = "high_long" if h["order"] == "low_long" else "low_long"
        return h
    op = rng.choice(["shift", "shift", "add", "exit", "exit", "uni", "drop"])
    if op == "shift":
        c = rng.choice(h["when"]); w = rng.choice([-0.05, 0.05])
        if c["f"].startswith(("p_", "x_")): c["lo"] = round(min(max(0.0, c["lo"] + w), 0.95), 2); c["hi"] = round(min(max(c["lo"] + 0.05, c["hi"] + w), 1.0), 2)
        elif c["f"] in ("rsi14", "adx14"):
            d = rng.choice([-5, 5]); c["lo"], c["hi"] = (max(0, c["lo"] + d) if c["lo"] > 0 else 0), (min(100, c["hi"] + d) if c["hi"] < 100 else 100)
        elif c["f"] == "bbz":
            d = rng.choice([-0.5, 0.5]); c["lo"], c["hi"] = (c["lo"] + d if c["lo"] > -20 else c["lo"]), (c["hi"] + d if c["hi"] < 20 else c["hi"])
    elif op == "add" and len(h["when"]) < 3:
        f = rng.choice(sorted(x for x in cols if x.startswith("p_") and x not in [c["f"] for c in h["when"]]))
        lo, hi = rng.choice([(0.0, 0.3), (0.7, 1.0), (0.0, 0.5), (0.5, 1.0)]); h["when"].append({"f": f, "lo": lo, "hi": hi})
    elif op == "exit":
        ex = h["exit"]
        if rng.random() < 0.45:                                # volatility-unit exits: stops outside the noise, winners run
            d = dict(ex) if isinstance(ex, dict) and ex.get("unit") == "vol" else {"unit": "vol", "sl": 1.5, "trail": 2.0, "arm": 1.0, "hold": 72}
            k = rng.choice(["tp", "sl", "trail", "arm", "hold"])
            if k == "hold": d["hold"] = rng.choice([12, 24, 48, 72, 120, 168])
            elif k == "arm": d["trail"] = d.get("trail") or 2.0; d["arm"] = rng.choice([None, 0.5, 1.0, 1.5, 2.0])
            else: d[k] = rng.choice([None, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0] if k != "sl" else [0.75, 1.0, 1.5, 2.0, 2.5, 3.0])
            if d.get("arm") is not None and not d.get("trail"): d.pop("arm")
            d = {k2: v for k2, v in d.items() if v is not None}
            if any(d.get(k2) for k2 in ("tp", "sl", "trail")): h["exit"] = d
        elif isinstance(ex, dict) or rng.random() < 0.5:          # design / tune exit dials (incl. trailing stops)
            d = dict(ex) if isinstance(ex, dict) else ({"hold": C.EXITS[ex][1]} if C.EXITS[ex][0] == "ret" else {"tp": C.EXITS[ex][0], "sl": C.EXITS[ex][1], "hold": C.EXITS[ex][2]})
            k = rng.choice(["tp", "sl", "trail", "hold", "arm"])
            if k == "hold": d["hold"] = rng.choice([4, 8, 12, 24, 48, 72, 120])
            elif k == "arm": d["trail"] = d.get("trail") or 0.01; d["arm"] = rng.choice([None, 0.005, 0.01, 0.02, 0.03])
            else: d[k] = rng.choice([None, 0.005, 0.01, 0.015, 0.02, 0.03, 0.05] if k != "trail" else [None, 0.005, 0.01, 0.015, 0.02, 0.03])
            if d.get("arm") is not None and not d.get("trail"): d.pop("arm")
            h["exit"] = {k2: v for k2, v in d.items() if v is not None}
        else: h["exit"] = rng.choice(list(C.EXITS))
    elif op == "uni": h["uni"] = rng.choice(["BTC", "MAJ", "ALL"])
    elif op == "drop" and len(h["when"]) > 1: h["when"].pop(rng.randrange(len(h["when"])))
    return h

def combine(a, b):
    if a["kind"] != "event" or b["kind"] != "event": return None
    if a["kind"] != "event" or b["kind"] != "event" or a["uni"] != b["uni"] or a["dir"] != b["dir"]: return None
    fs = {c["f"] for c in a["when"]}
    when = a["when"] + [c for c in b["when"] if c["f"] not in fs]
    if len(when) > 3 or len(when) == len(a["when"]): return None
    return {**a, "when": when}

def explore(cols, rng):
    feats = sorted(c for c in cols if c.startswith("p_"))
    when = []
    for f in rng.sample(feats, 2):
        lo, hi = rng.choice([(0.0, 0.2), (0.8, 1.0), (0.0, 0.33), (0.67, 1.0)]); when.append({"f": f, "lo": lo, "hi": hi})
    return {"kind": "event", "uni": rng.choice(["BTC", "MAJ", "ALL"]), "when": when, "dir": rng.choice(["long", "short", "with", "against"]), "exit": rng.choice(SCAN_EXITS), "cool": 24}

# ── the AI researcher (free Groq models; optional) ─────────────────────────────
GRAMMAR = """A hypothesis is JSON. Two kinds:
EVENT: {"kind":"event","uni":"BTC"|"MAJ"|"ALL","when":[{"f":FEATURE,"lo":0-1,"hi":0-1}, ... up to 3],"dir":"long"|"short"|"with"|"against","exit":EXIT,"cool":24}
  - FEATURE "p_<name>" = that measurement's percentile vs the coin's own last 90 days (0 lowest, 1 highest); "x_<name>" = rank vs the other coins right now; "hour" (0-23 UTC, lo/hi in hours); "dow" (0=Mon).
  - "with" = trade in the direction of the last hour's move, "against" = opposite.
  - Raw classic indicators can also be used directly (not percentiles): rsi14 (0-100), bbz (Bollinger z, +/-2 = band), macdh, emax, emat (% gaps), don20 (0-1 Donchian channel position), adx14 (0-100), cross (+1/-1 EMA cross).
  - EXIT with VOLATILITY UNITS (preferred, keeps stops outside normal noise): {"unit":"vol","sl":1.5,"trail":2,"arm":1,"hold":72}
    here tp/sl/trail/arm are multiples of the coin's normal DAILY move (e.g. sl 1.5 = 1.5x its usual daily swing).
  - EXIT can also be DIALS you design: {"tp":0.003-0.2 or null,"sl":0.003-0.2 or null,"trail":0.003-0.1 or null,"arm":0-0.1 or null,"hold":1-168}
    tp = take-profit, sl = stop-loss, trail = trailing stop distance, arm = only start trailing once up this much, hold = max hours. Example: {"tp":0.03,"sl":0.015,"trail":0.01,"arm":0.015,"hold":24}
  - FEATURE can also be an INVENTED measurement u_... (and its p_u_... percentile) listed under INVENTED MEASUREMENTS.
ROTATION (all-in, NO leverage, one coin at a time): {"kind":"rotate","rank":FEATURE,"order":"high"|"low","hold":24|48|72,"stop":0.02-0.2 or null,"when":[up to 2 conditions]}
CROSS-SECTION: {"kind":"xsec","rank":FEATURE,"order":"low_long"|"high_long","frac":0.2-0.5,"rebal":24|84|168}
UNION / TEAM ("X OR Y", 2-3 event rules traded as one; a regime switch = rule A with a condition in one half + rule B with the same condition in the other half): {"kind":"union","parts":[EVENT,EVENT],"cool":24}
MARKET-WIDE features (same for all coins, use p_ versions): p_fg (Fear&Greed), p_fg7, p_cbp/p_cbp24 (Coinbase premium), p_dvol/p_dvol24/p_dvole (options fear gauge), p_stab7/p_stab30 (stablecoin inflow); per coin: streak (+n/-n hours in a row up/down).
PAIR (market-neutral): {"kind":"pair","a":COIN,"b":COIN,"z":1-4,"look":168|336|720|1440,"hold":24|72|168,"mode":"revert"|"trend"}
CARRY (hedged funding): {"kind":"carry","uni":"BTC"|"MAJ"|"ALL","enter":annual funding e.g. 0.2,"leave":lower e.g. 0.05}
EXITS: """ + ", ".join(f"{k}={'hold %dh' % v[1] if v[0] == 'ret' else 'target +%g%%, stop -%g%%, max %dh' % (v[0] * 100, v[1] * 100, v[2])}" for k, v in C.EXITS.items()) + "\nCOINS: " + " ".join(C.COINS)

PREFER = ("gpt-oss-120b", "qwen", "llama-3.3-70b", "kimi", "deepseek", "llama-4", "gpt-oss-20b")
def _groq_models(key):
    """ask Groq which models exist today (they change), best first; fall back to a fixed list"""
    try:
        r = requests.get("https://api.groq.com/openai/v1/models", timeout=30, headers={"Authorization": f"Bearer {key}"})
        names = [m["id"] for m in r.json().get("data", []) if not any(x in m["id"] for x in ("whisper", "tts", "guard", "embed", "playai", "compound"))]
        rank = lambda n: next((i for i, p in enumerate(PREFER) if p in n), len(PREFER))
        names = sorted(names, key=rank)[:6]
        if names: return names
    except Exception: pass
    return ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b"]

AI_LOG = []          # every AI call this run: model, ok, error (goes into the health report)

def _groq(messages, max_tokens=2500):
    out = _groq_call(messages, max_tokens)
    AI_LOG.append({"ok": out[0] is not None, "model" if out[0] is not None else "error": str(out[1])[:160]}); return out

def _groq_call(messages, max_tokens=2500):
    key = os.environ.get("GROQ_API_KEY")
    if not key: return None, "no GROQ_API_KEY"
    last = ""
    for model in _groq_models(key):
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=90, headers={"Authorization": f"Bearer {key}"},
                              json={"model": model, "messages": messages, "temperature": 0.8, "max_tokens": max_tokens, "response_format": {"type": "json_object"}})
            if r.ok: return r.json()["choices"][0]["message"]["content"], model
            last = f"{model}: HTTP {r.status_code} {r.text[:120]}"
        except Exception as e: last = f"{model}: {e}"
    return None, last

PREMISE = """THE GOAL: find trades that WIN BIG and LOSE SMALL.
- Trades are taken with 10x leverage on isolated margin: a move of about -9% liquidates the trade (-100% of its margin). Tiny targets with rare big losses FAIL.
- Aim for moves of 1-10%. Put stops where the idea is proven wrong and OUTSIDE normal noise (use volatility-unit exits), and let winners run with trailing stops.
- A 35-55% win rate is fine when winners are 2-3x the losers.
- Rotation ideas (whole account, no leverage, one coin for 1-3 days, aiming for 5-10%) are welcome too.
BE BOLD AND CREATIVE: unusual, contrarian, cross-coin, behavioural and structural ideas. Most ideas will fail the exams; that is fine and costs nothing."""

def ai_ideas(mem, cols, n=C.LLM_IDEAS_PER_RUN):
    feats = {k: v for k, v in DESC.items()}
    good = [t for t in mem["tests"].values() if t["status"] in ("champion", "validated", "near")][:12]
    fam = sorted(mem["families"].items(), key=lambda kv: -kv[1]["n"])[:25]
    prompt = (f"You are the head of research of an autonomous crypto research machine (paper only). It tests rules on 6 years of hourly data for 24 coins (3 of them collapsed in 2022).\n"
              f"MEASUREMENTS (each available as p_<name>; some as x_<name>):\n" + "\n".join(f"- {k}: {v}" for k, v in feats.items()) + "\n\n" + GRAMMAR +
              "\n\nWHAT SURVIVED OR NEARLY SURVIVED SO FAR:\n" + ("\n".join(f"- [{t['status']}] {t['text']} | discovery {t['disc']} | validation {t.get('val')}" for t in good) or "- nothing yet") +
              "\n\nIDEA FAMILIES ALREADY TESTED (count, best discovery t):\n" + "\n".join(f"- {k}: {v['n']} tests, best t {v.get('best', 0)}, survivors {v.get('ok', 0)}" for k, v in fam) +
              "\n\nLESSONS SO FAR:\n" + "\n".join("- " + l for l in mem.get("lessons", [])[:8]) + _blocks_text(mem) +
              "\n\n" + PREMISE + f"\n\nPropose {n} NEW hypotheses that are different from what was tested. Think outside the box, like people who build profitable open-source bots and prediction-market arbitrage bots: structural, market-neutral and flow-based ideas (pairs, carry, cross-coin lead/lag, trapped leverage, forced liquidations, funding extremes) as well as classic indicator rules combined with positioning. "
              "Each must have a market MECHANISM (who is forced to buy or sell, who is trapped, where liquidity is). Mix the kinds: at least 2 pair or carry ideas. "
              'Answer ONLY JSON: {"ideas":[{"why":"one-sentence mechanism","h":{...hypothesis...}}]}')
    txt, model = _groq([{"role": "user", "content": prompt}])
    if not txt: return [], model
    try: data = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except Exception: return [], f"{model}: answer was not JSON"
    out = []
    for it in data.get("ideas", [])[: n * 2]:
        h = it.get("h") if isinstance(it, dict) else None
        if not isinstance(h, dict): continue
        if h.get("kind") == "event": h.setdefault("cool", 24)
        for key in ("z", "enter", "leave"):
            if key in h: h[key] = float(h[key])
        for key in ("look", "hold"):
            if key in h: h[key] = int(h[key])
        for c in h.get("when", []) if isinstance(h.get("when"), list) else []:
            if isinstance(c, dict) and c.get("f", "").startswith(("p_", "x_")): c["lo"], c["hi"] = float(c.get("lo", 0)), float(c.get("hi", 1))
        if valid(h, cols): out.append((h, str(it.get("why", ""))[:240]))
    return out, model

def ai_lessons(mem):
    fam = sorted(mem["families"].items(), key=lambda kv: -kv[1]["n"])[:40]
    good = [t for t in mem["tests"].values() if t["status"] in ("champion", "validated")][:10]
    prompt = ("Summarise what this research machine has learned so far, for a non-trader owner. Be honest and concrete; use the numbers.\n"
              "Families (tests, best discovery t, survivors):\n" + "\n".join(f"- {k}: {v['n']}, {v.get('best', 0)}, {v.get('ok', 0)}" for k, v in fam) +
              "\nSurvivors:\n" + ("\n".join(f"- [{t['status']}] {t['text']} val {t.get('val')} live {t.get('live')}" for t in good) or "- none") +
              '\nAnswer ONLY JSON: {"lessons":["...", "... (max 8, each under 30 words)"]}')
    txt, model = _groq([{"role": "user", "content": prompt}], 1200)
    try: return [str(x)[:220] for x in json.loads(re.search(r"\{.*\}", txt, re.S).group(0))["lessons"]][:8]
    except Exception: return None

def ai_plain(text):
    """turn a technical rule into 2 plain sentences for a beginner (optional; falls back to the rule text)"""
    prompt = ("Explain this crypto trading rule to a complete beginner in at most 2 short sentences: first what situation it waits for, then what trade it makes and when it gets out. "
              "No jargon: say 'open interest' as 'the total amount of leveraged bets', 'crowd long/short ratio' as 'how many traders are betting up vs down', RSI as 'how oversold/overbought price is', "
              "'percentile vs last 90 days' as 'compared with the last 3 months'. Rule: " + text + '\nAnswer ONLY JSON: {"plain":"..."}')
    txt, model = _groq([{"role": "user", "content": prompt}], 400)
    try: p = " ".join(str(json.loads(re.search(r"\{.*\}", txt, re.S).group(0))["plain"]).split())
    except Exception: return None
    if "break it down" in p.lower() or "1." in p[:120]: return None            # an essay, not 2 plain sentences: try again next run
    parts = re.split(r"(?<=[.!?])\s+", p); return " ".join(parts[:2])[:300]

def ai_analyst(mem, cols):
    """the analyst: explains WHY the winners might win, and proposes merges only where the mechanisms fit together;
    plus a few 'wild' ideas (gambling concepts, sentiment extremes). Returns (hypotheses, notes)"""
    win = sorted([t for t in mem["tests"].values() if t["status"] in ("champion", "validated", "twin")], key=lambda t: -((t.get("hold") or t.get("val") or {}).get("t", 0)))[:10]
    if len(win) < 2: return [], None
    lines = "\n".join(f"- [{t['status']}{' #' + str(t['no']) if t.get('no') else ''}] {t['text']} | validation {t.get('val')} | final exam {t.get('hold')} | live {(t.get('live') or {}).get('n', 0)} trades, avg {(t.get('live') or {}).get('mean')}%" for t in win)
    prompt = ("You are the senior analyst of an autonomous crypto research machine (paper only). Below are the rules that survived strict out-of-sample testing.\n" + lines +
              "\n\nMEASUREMENTS:\n" + "\n".join(f"- {k}: {v}" for k, v in DESC.items()) + "\n\n" + GRAMMAR +
              "\n\n" + PREMISE + "\n\nTasks: (1) For up to 5 winners, explain in one plain sentence WHY it might work (who is forced to buy/sell, who is trapped). "
              "(2) Propose up to 4 MERGES, only where it makes sense: an AND-merge when one rule's condition confirms the other's mechanism; a UNION (OR) when two rules catch DIFFERENT situations of the same mechanism; a regime switch when each rule works in a different market state. Explain why each merge makes sense. If no merge makes sense, propose none. "
              "(3) Propose 3 WILD out-of-the-box ideas, e.g. gambling concepts (hot hand vs gambler's fallacy using streak, betting against extreme crowd sentiment like Fear&Greed extremes), Coinbase premium, options fear gauge, stablecoin inflows. "
              'Answer ONLY JSON: {"why":[{"rule":"#n or short name","reason":"..."}],"merges":[{"why":"...","h":{...}}],"wild":[{"why":"...","h":{...}}]}')
    txt, model = _groq([{"role": "user", "content": prompt}], 3500)
    if not txt: return [], None
    try: data = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except Exception: return [], None
    out, merges, wild = [], [], []
    def fix(h):
        if not isinstance(h, dict): return None
        if h.get("kind") == "union": h["parts"] = [fix(q) for q in h.get("parts", []) if isinstance(q, dict)]; h.setdefault("cool", 24); return h
        if h.get("kind") == "event":
            h.setdefault("cool", 24)
            for c in h.get("when", []) if isinstance(h.get("when"), list) else []:
                if isinstance(c, dict): c["lo"], c["hi"] = float(c.get("lo", 0)), float(c.get("hi", 1))
        return h
    for key, bucket in (("merges", merges), ("wild", wild)):
        for it in (data.get(key) or [])[:6]:
            h = fix(it.get("h")) if isinstance(it, dict) else None
            ok = h is not None and valid(h, cols)
            bucket.append({"why": str(it.get("why", ""))[:300] if isinstance(it, dict) else "", "valid": bool(ok)})
            if ok: out.append((h, ("merge: " if key == "merges" else "wild idea: ") + str(it.get("why", ""))[:220]))
    notes = {"model": model, "why": [{"rule": str(x.get("rule", ""))[:40], "reason": str(x.get("reason", ""))[:300]} for x in (data.get("why") or [])[:6] if isinstance(x, dict)],
             "merges": merges, "wild": wild}
    return out, notes

def _blocks_text(mem):
    B = mem.get("blocks", [])
    return ("\n\nINVENTED MEASUREMENTS (usable as u_id or p_u_id):\n" + "\n".join(f"- {b['id']} = {b['expr']}  ({b['why'][:100]}; tests {b.get('tests', 0)}, best {b.get('best', '-')})" for b in B)) if B else ""

BLOCK_HELP = """A NEW MEASUREMENT is a formula over these hourly ingredients of one coin:
  o h l c (price open/high/low/close), v (volume), tb (taker BUY volume, so sell volume = v - tb), oi (open interest = total leveraged bets in $),
  glob (crowd long/short account ratio), topPos (top traders' long/short position ratio), topAcc (top traders' account ratio), tk (taker buy/sell ratio),
  plus the machine's measurements: """

def ai_blocks(mem, n=2):
    """the AI invents up to n new measurements as safe formulas (validated + computed by blocks.py, never executed as code)"""
    from .blocks import check, slug
    names = set(["o", "h", "l", "c", "v", "tb", "oi", "glob", "topPos", "topAcc", "tk"]) | {k for k in DESC if not k.startswith("x_") and k not in ("fg", "fg7", "cbp", "cbp24", "dvol", "dvol24", "dvole", "stab7", "stab30")}
    old = [b["expr"] for b in mem.get("blocks", [])] + [b["expr"] for b in mem.get("blocks_retired", [])][-40:]
    prompt = ("You design new measurements for an autonomous crypto research machine. " + BLOCK_HELP + ", ".join(sorted(names - {"o", "h", "l", "c", "v", "tb", "oi", "glob", "topPos", "topAcc", "tk"})) +
              "\nOPERATIONS: + - * /, lag(x,H) diff(x,H) pct(x,H) mean(x,H) std(x,H) z(x,H) max(x,H) min(x,H) rank(x,H) ema(x,H) sum(x,H) with H whole hours 1-2160; abs(x) log(x) sign(x) sqrt(x); clip(x,lo,hi); corr(x,y,H)."
              "\nEXAMPLES: pct(oi,24) / std(r1,168)    |  sum(2*tb - v, 6) / sum(v, 6)    |  corr(pct(c,1), pct(oi,1), 72)    |  z(glob,720) - z(topPos,720)"
              "\nALREADY TRIED (do not repeat): " + " ; ".join(old[-25:]) +
              f"\nInvent {n} NEW measurements that could reveal forced buying/selling, trapped traders, divergences between crowd and smart money, or exhaustion. Each must be different from the examples and from what was tried. "
              'Answer ONLY JSON: {"blocks":[{"name":"short_name","expr":"formula","why":"one sentence"}]}')
    txt, model = _groq([{"role": "user", "content": prompt}], 1500)
    try: data = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except Exception: return []
    out = []
    for b in (data.get("blocks") or [])[:n]:
        if not isinstance(b, dict): continue
        expr = str(b.get("expr", "")).strip(); err = check(expr, names)
        if err or expr in old: continue
        out.append({"id": slug(b.get("name", "block"), expr), "expr": expr, "why": str(b.get("why", ""))[:200], "name": str(b.get("name", ""))[:40], "by": model})
    return out

def block_space(bid):
    """standard first tests for a newly invented measurement"""
    out = []
    for uni in ("ALL", "MAJ", "BTC"):
        for lo, hi in ((0.0, 0.05), (0.95, 1.0), (0.0, 0.1), (0.9, 1.0)):
            for d in ("long", "short"):
                for ex in ("r24", "b2_2_48"):
                    out.append({"kind": "event", "uni": uni, "when": [{"f": "p_" + bid, "lo": lo, "hi": hi}], "dir": d, "exit": ex, "cool": 24})
    random.Random(3).shuffle(out); return out[:40]

# ── big-move families (win big, lose small) + all-in rotation, as starting points the machine then evolves ──
VOL_EXITS = [{"unit": "vol", "sl": 1.0, "trail": 1.5, "arm": 1.0, "hold": 72}, {"unit": "vol", "sl": 1.5, "trail": 2.0, "arm": 1.5, "hold": 120},
             {"unit": "vol", "sl": 1.0, "tp": 3.0, "hold": 72}, {"unit": "vol", "sl": 2.0, "trail": 3.0, "arm": 2.0, "hold": 168}]
BIG = [
    ("Squeeze breakout up: very quiet, then price at the top of its range", [{"f": "p_vol24", "lo": 0.0, "hi": 0.1}, {"f": "p_pos24", "lo": 0.9, "hi": 1.0}], "long"),
    ("Squeeze breakdown: very quiet, then price at the bottom of its range", [{"f": "p_vol24", "lo": 0.0, "hi": 0.1}, {"f": "p_pos24", "lo": 0.0, "hi": 0.1}], "short"),
    ("Liquidation cascade up: leverage piling in while price surges", [{"f": "p_oi24", "lo": 0.9, "hi": 1.0}, {"f": "p_r4", "lo": 0.95, "hi": 1.0}], "long"),
    ("Liquidation cascade down: leverage piling in while price drops", [{"f": "p_oi24", "lo": 0.9, "hi": 1.0}, {"f": "p_r4", "lo": 0.0, "hi": 0.05}], "short"),
    ("Flush bounce, let it run: leverage wiped out after a fall", [{"f": "p_oi24", "lo": 0.0, "hi": 0.05}, {"f": "p_r72", "lo": 0.0, "hi": 0.3}], "long"),
    ("Crowd squeeze up: crowd very short, price turning up", [{"f": "p_glob", "lo": 0.0, "hi": 0.1}, {"f": "p_r4", "lo": 0.8, "hi": 1.0}], "long"),
    ("Crowd squeeze down: crowd very long, price turning down", [{"f": "p_glob", "lo": 0.9, "hi": 1.0}, {"f": "p_r4", "lo": 0.0, "hi": 0.2}], "short"),
    ("BTC leads, laggard alt follows", [{"f": "p_btc_r24", "lo": 0.9, "hi": 1.0}, {"f": "x_r24", "lo": 0.0, "hi": 0.3}], "long"),
    ("BTC falls, alt that held up follows down", [{"f": "p_btc_r24", "lo": 0.0, "hi": 0.1}, {"f": "x_r24", "lo": 0.7, "hi": 1.0}], "short"),
    ("Trend following: new 20-day high in an uptrend", [{"f": "don20", "lo": 0.98, "hi": 1.0}, {"f": "emat", "lo": 0, "hi": 100}], "long"),
    ("Trend following: new 20-day low in a downtrend", [{"f": "don20", "lo": 0.0, "hi": 0.02}, {"f": "emat", "lo": -100, "hi": 0}], "short"),
    ("Options panic + crash: buy the fear", [{"f": "p_dvol24", "lo": 0.95, "hi": 1.0}, {"f": "p_r24", "lo": 0.0, "hi": 0.05}], "long"),
]
def bigmove_space(cols):
    out = []
    for name, when, d in BIG:
        for uni in ("ALL", "MAJ", "BTC"):
            for ex in VOL_EXITS:
                h = {"kind": "event", "uni": uni, "when": when, "dir": d, "exit": ex, "cool": 24}
                if valid(h, cols): out.append((h, "big-move idea: " + name))
    return out

ROT = [("p_r24", "high", "momentum: yesterday's strongest coin"), ("p_r168", "high", "momentum: this week's strongest coin"), ("x_r24", "high", "strongest coin vs the others today"),
       ("x_r24", "low", "bounce: today's weakest coin"), ("x_r168", "low", "bounce: this week's weakest coin"), ("p_oi24", "low", "the coin whose leverage was just wiped out"),
       ("p_glob", "low", "the coin the crowd is most short"), ("x_timb24", "high", "the coin with the strongest aggressive buying"), ("x_vol24", "low", "the calmest coin"),
       ("p_vol24", "low", "the quietest coin vs its own history (squeeze)")]
def rotation_space(cols):
    out = []
    for f, order, why in ROT:
        for hold in (24, 48, 72):
            for stop in (None, 0.05, 0.08):
                h = {"kind": "rotate", "rank": f, "order": order, "hold": hold, "stop": stop}
                if valid(h, cols): out.append((h, "all-in rotation: " + why))
    return out
