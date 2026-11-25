"""Agent Team: Reads health.json, invents a valid DSL/Judge hypothesis via Gemini or Groq,
validates it against the schema & persistent memory, and stages it for testing."""
import os, json, re, time, requests
from . import config as C

GITHUB_REPO = "dukaelementsdotcom/autopilot"
GITHUB_BRANCH = "gh-pages"
HEALTH_DIR = "autopilot"


def call_llm(prompt, json_mode=True):
    """Calls Gemini (gemini-2.5-flash) or falls back to Groq (llama-3.3-70b-versatile)."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        if json_mode:
            payload["generationConfig"] = {"response_mime_type": "application/json"}
        try:
            r = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=60)
            data = r.json()
            if "candidates" in data:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            print(f"Gemini API warning: {data.get('error', data)}")
        except Exception as e:
            print(f"Gemini request failed: {e}")

    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    **({"response_format": {"type": "json_object"}} if json_mode else {}),
                },
                timeout=60,
            )
            data = r.json()
            if "choices" in data:
                return data["choices"][0]["message"]["content"]
            print(f"Groq API warning: {data}")
        except Exception as e:
            print(f"Groq request failed: {e}")

    return json.dumps({"error": "No working GEMINI_API_KEY or GROQ_API_KEY available"})


def get_latest_health():
    """Load local docs/health.json first, then GitHub gh-pages fallback."""
    local_p = "docs/health.json"
    if os.path.exists(local_p):
        try:
            return json.load(open(local_p))
        except Exception:
            pass
    try:
        r = requests.get("https://dukaelementsdotcom.github.io/autopilot/health.json", timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"Error fetching remote health.json: {e}")
    return None


def _load_agent_memory():
    p = f"{C.STATE_DIR}/agent_hypotheses.json"
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            pass
    return {"history": []}


def _save_agent_memory(mem):
    os.makedirs(C.STATE_DIR, exist_ok=True)
    p = f"{C.STATE_DIR}/agent_hypotheses.json"
    with open(p, "w") as f:
        json.dump(mem, f, indent=2)


def validate_rule_schema(rule):
    """Ensure the generated rule matches rm/judge.py event schema and has valid fields."""
    if not isinstance(rule, dict):
        return False, "not a dict"
    if rule.get("kind") != "event":
        return False, "kind must be 'event'"
    if rule.get("side") not in ("long", "short"):
        return False, "side must be 'long' or 'short'"
    when = rule.get("when")
    if not isinstance(when, list) or not (1 <= len(when) <= 4):
        return False, "when must be 1..4 conditions"
    for cond in when:
        if not isinstance(cond, dict) or "f" not in cond or "op" not in cond or "v" not in cond:
            return False, f"invalid condition {cond}"
        if cond["op"] not in (">", "<", ">=", "<="):
            return False, f"invalid op {cond['op']}"
    return True, "ok"


def run_agent_team():
    health = get_latest_health() or {}
    by_side = health.get("referee", {}).get("calibration", {}).get("bySide", {})
    long_hit = (by_side.get("long") or {}).get("hit", 0)
    short_hit = (by_side.get("short") or {}).get("hit", 0)

    mem = _load_agent_memory()
    past_names = [x.get("rule_name") for x in mem.get("history", [])[-25:]]

    prompt = f"""
You are a Quantitative Researcher for Volvox (crypto perpetuals & spot engine).
Current live telemetry: Long hit rate = {long_hit:.1%}, Short hit rate = {short_hit:.1%}.
Previously proposed rules (DO NOT repeat): {past_names}

Available panel features include:
- Macro/Mood: p_fg, p_dvol, p_stab7, p_mtrend
- Microstructure/Flow: p_timb4, p_timb24, p_oi24, p_oiz, p_basis, p_hlfund24, p_sshare
- Price/Vol: p_r1, p_r4, p_r24, p_vol24, p_volr, rsi14, bbz, adx14

Propose ONE new, non-redundant Judge event rule in strict JSON with:
- "rule_name": short snake_case identifier
- "thesis": 1-sentence economic/microstructure rationale
- "rule": {{
    "kind": "event",
    "side": "long" or "short",
    "when": [{{"f": "<feature_name>", "op": ">" or "<", "v": <float>}}, ...],
    "exit": {{"tp": 0.03, "sl": 0.015, "h": 48}},
    "cool": 24
  }}
"""
    print("Strategist is formulating a new hypothesis...")
    raw = call_llm(prompt, json_mode=True)
    clean = re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip())
    clean = re.sub(r"\s*```$", "", clean)

    try:
        idea = json.loads(clean)
        if "error" in idea:
            print(f"Agent skipped: {idea['error']}")
            return None
        ok, msg = validate_rule_schema(idea.get("rule"))
        if not ok:
            print(f"Schema validation failed: {msg}")
            return None
    except Exception as e:
        print(f"Failed to parse agent output: {e}")
        return None

    name = idea.get("rule_name", f"agent_rule_{int(time.time())}")
    if name in past_names:
        print(f"Duplicate rule '{name}' rejected.")
        return None

    entry = {
        "at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()),
        "rule_name": name,
        "thesis": idea.get("thesis", ""),
        "rule": idea["rule"],
        "status": "staged",
    }
    mem["history"].append(entry)
    mem["history"] = mem["history"][-200:]
    _save_agent_memory(mem)
    print(f"SUCCESS: Validated and staged '{name}' ({entry['rule']['side']}) -> {C.STATE_DIR}/agent_hypotheses.json")
    return entry


if __name__ == "__main__":
    run_agent_team()
