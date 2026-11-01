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

def mutate(h, cols, rng):
    h = json.loads(json.dumps(h))
    if h["kind"] == "xsec":
        op = rng.choice(["frac", "rebal", "order"])
        if op == "frac": h["frac"] = rng.choice([0.2, 0.25, 0.33, 0.5])
        elif op == "rebal": h["rebal"] = rng.choice([24, 84, 168])
        else: h["order"] = "high_long" if h["order"] == "low_long" else "low_long"
        return h
    op = rng.choice(["shift", "shift", "add", "exit", "uni", "drop"])
    if op == "shift":
        c = rng.choice(h["when"]); w = rng.choice([-0.05, 0.05])
        if c["f"].startswith(("p_", "x_")): c["lo"] = round(min(max(0.0, c["lo"] + w), 0.95), 2); c["hi"] = round(min(max(c["lo"] + 0.05, c["hi"] + w), 1.0), 2)
    elif op == "add" and len(h["when"]) < 3:
        f = rng.choice(sorted(x for x in cols if x.startswith("p_") and x not in [c["f"] for c in h["when"]]))
        lo, hi = rng.choice([(0.0, 0.3), (0.7, 1.0), (0.0, 0.5), (0.5, 1.0)]); h["when"].append({"f": f, "lo": lo, "hi": hi})
    elif op == "exit": h["exit"] = rng.choice(list(C.EXITS))
    elif op == "uni": h["uni"] = rng.choice(["BTC", "MAJ", "ALL"])
    elif op == "drop" and len(h["when"]) > 1: h["when"].pop(rng.randrange(len(h["when"])))
    return h

def combine(a, b):
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
CROSS-SECTION: {"kind":"xsec","rank":FEATURE,"order":"low_long"|"high_long","frac":0.2-0.5,"rebal":24|84|168}
EXITS: """ + ", ".join(f"{k}={'hold %dh' % v[1] if v[0] == 'ret' else 'target +%g%%, stop -%g%%, max %dh' % (v[0] * 100, v[1] * 100, v[2])}" for k, v in C.EXITS.items())

def _groq(messages, max_tokens=2500):
    key = os.environ.get("GROQ_API_KEY")
    if not key: return None, "no GROQ_API_KEY"
    last = ""
    for model in ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3-32b", "openai/gpt-oss-20b"):
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=90, headers={"Authorization": f"Bearer {key}"},
                              json={"model": model, "messages": messages, "temperature": 0.8, "max_tokens": max_tokens, "response_format": {"type": "json_object"}})
            if r.ok: return r.json()["choices"][0]["message"]["content"], model
            last = f"{model}: HTTP {r.status_code} {r.text[:120]}"
        except Exception as e: last = f"{model}: {e}"
    return None, last

def ai_ideas(mem, cols, n=C.LLM_IDEAS_PER_RUN):
    feats = {k: v for k, v in DESC.items()}
    good = [t for t in mem["tests"].values() if t["status"] in ("champion", "validated", "near")][:12]
    fam = sorted(mem["families"].items(), key=lambda kv: -kv[1]["n"])[:25]
    prompt = (f"You are the head of research of an autonomous crypto research machine (paper only). It tests rules on 6 years of hourly data for 21 coins.\n"
              f"MEASUREMENTS (each available as p_<name>; some as x_<name>):\n" + "\n".join(f"- {k}: {v}" for k, v in feats.items()) + "\n\n" + GRAMMAR +
              "\n\nWHAT SURVIVED OR NEARLY SURVIVED SO FAR:\n" + ("\n".join(f"- [{t['status']}] {t['text']} | discovery {t['disc']} | validation {t.get('val')}" for t in good) or "- nothing yet") +
              "\n\nIDEA FAMILIES ALREADY TESTED (count, best discovery t):\n" + "\n".join(f"- {k}: {v['n']} tests, best t {v.get('best', 0)}, survivors {v.get('ok', 0)}" for k, v in fam) +
              "\n\nLESSONS SO FAR:\n" + "\n".join("- " + l for l in mem.get("lessons", [])[:8]) +
              f"\n\nPropose {n} NEW hypotheses that are different from what was tested. Each must have a market MECHANISM (who is forced to buy or sell, who is trapped, where liquidity is). Prefer combining positioning (crowd, top traders, open interest, taker flow) with price action. "
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
