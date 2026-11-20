"""AGENT TEAM (weekly research): observer -> strategist -> checker -> tester -> notebook.
  observer   reads where the long side stands (live health report) and what ideas were already tried
  strategist the AI (Groq first, Google AI Studio behind it, retried) invents ONE long-side rule: a true/false expression over past-only measurements
  checker    rm/agent_rule.py accepts only plain comparisons of those measurements (no code from the AI is ever run); a bad rule goes back to the AI with the reason
  tester     the same walk-forward as the lab tests: 7 separate 120-day windows, no brain, one random trade a day, against a baseline
  notebook   rm/agent_ideas/<date>_<name>.json: the idea, the rule, the numbers and the verdict (committed by the workflow)
Nothing here touches the Arbiter. A PASS only means 'worth racing as a contestant'; the live race decides. The run FAILS (so you hear about it) if no idea could be tested."""
import os, re, sys, json, time, glob, datetime as dt, requests
from . import config as C, features, ideas, agent_rule as AR
from .judge import Judge

OUT = "rm/agent_ideas"
MEANING = ("p_* = a percentile rank (0 to 1) against the coin's own recent history; x_* = the same measured across the whole market; "
           "positioning (p_glob, p_topPos, p_oi*), flow (p_timb*, p_tk*, p_vr), trend (p_r1..p_r168 = recent returns, p_pos24 = where price sits in its 24h range, don20, emat, emax, rsi14, bbz, adx14, streak), "
           "volatility (p_vol24, p_volr), leverage (p_basis*, p_hlprem*, p_hlfund24), real money (p_sshare*, p_stimb24), mood (p_btc_r1, p_btc_r24, p_fg, p_cbp*, p_dvol*, p_stab7, hour, dow)")

def health():
    try:
        r = requests.get("https://dukaelementsdotcom.github.io/autopilot/health.json", timeout=30)
        if r.status_code == 200: return r.json()
    except Exception as e: print("health report not reachable:", str(e)[:100])
    return {}

def tried():
    out = []
    for f in sorted(glob.glob(f"{OUT}/*.json")):
        try: d = json.load(open(f)); out.append(f"{d.get('rule_name')}: {d.get('rule_expr')} -> {d.get('result', {}).get('summary', {}).get('verdict')}")
        except Exception: pass
    return out

def ask(messages):
    """Groq first, Google AI Studio behind it; retried with a pause (503 'high demand' passes)"""
    for attempt in range(3):
        txt, who = ideas._groq_call(messages, 1500)
        if txt:
            a, b = txt.find("{"), txt.rfind("}")
            try: return json.loads(txt[a:b + 1]), who
            except Exception as e: print(f"AI answer was not valid JSON ({who}): {str(e)[:80]}")
        else: print(f"AI unavailable (attempt {attempt + 1}/3): {who}")
        time.sleep(20 * (attempt + 1))
    return None, None

def main():
    hh = health(); long_ = (((hh.get("referee") or {}).get("calibration") or {}).get("bySide") or {}).get("long") or {}
    ctx = (f"The long side's live shadow calls win {long_['hit']:.0%} of the time against the {long_.get('pred', 0):.0%} predicted. " if long_.get("hit") is not None else "") + "The system has no working long-side edge yet."
    prev = tried(); print("Observer:", ctx, f"| {len(prev)} earlier ideas")
    blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    J = Judge(features.build(blocks=blocks)); allowed = [c for c in AR.OKCOLS if c in J.cols]
    prompt = (f"You are a quantitative researcher for a crypto futures system (hourly data, 54 coins). {ctx}\n"
              f"Invent ONE rule that picks hours when a LONG trade (+2% target before a volatility stop, within 48h) is more likely to work than at a random hour.\n"
              f"Measurements you may use (past-only): {', '.join(allowed)}.\nMeaning: {MEANING}.\n"
              "The rule is ONE true/false expression using only those measurements, plain numbers, + - * /, abs(x), comparisons < > <= >=, joined with & | ~ ; put every comparison in parentheses. "
              "Example: ((p_r24 < 0.2) & (p_glob < 0.3) & (p_timb4 > 0.6)).\n"
              + (f"Ideas already tried (do not repeat them):\n- " + "\n- ".join(prev[-30:]) + "\n" if prev else "")
              + 'Answer with one JSON object: {"rule_name": "short_snake_case", "description": "one or two plain sentences on why it should work", "rule_expr": "the expression"}')
    msgs = [{"role": "system", "content": "You are a careful quantitative researcher. Answer with one JSON object only, no other text."}, {"role": "user", "content": prompt}]
    idea = None; who = None; result = None; err = None
    for attempt in range(3):
        idea, who = ask(msgs)
        if not idea: break
        try: result = AR.evaluate(J, idea.get("rule_expr", "")); break
        except ValueError as e:
            err = str(e); print(f"Checker rejected the rule (attempt {attempt + 1}/3): {err}")
            msgs += [{"role": "assistant", "content": json.dumps(idea)}, {"role": "user", "content": f"That rule was rejected: {err}\nFix it and answer with the same JSON object."}]
    if not result:
        print("FAILED: no idea could be tested this week." + (f" Last rejection: {err}" if err else " The AI did not answer.")); sys.exit(1)
    name = re.sub(r"[^a-z0-9_]", "", str(idea.get("rule_name", "rule")).lower().replace(" ", "_"))[:40] or "rule"
    rec = {"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "model": who, "context": ctx, "rule_name": name, "description": idea.get("description"), "rule_expr": idea.get("rule_expr"),
           "result": result, "ideasTriedSoFar": len(prev) + 1,
           "note": "history only: with many ideas tried, some pass by luck. A PASS means 'worth racing as a contestant', never 'proven'."}
    os.makedirs(OUT, exist_ok=True); path = f"{OUT}/{dt.datetime.utcnow():%Y-%m-%d}_{name}.json"; json.dump(rec, open(path, "w"), indent=1)
    print(json.dumps(result["summary"])); print("Wrote", path)

if __name__ == "__main__": main()
