"""Agent Team: Reads health.json, invents a new rule via Gemini, writes a labtest_*.py file for the engine to test."""
import os, json, re, requests
from . import config as C

# ---- CONFIG ----
GITHUB_REPO = "dukaelementsdotcom/autopilot"
GITHUB_BRANCH = "gh-pages"
HEALTH_DIR = "autopilot"
# ----------------

def call_gemini(prompt):
    """Calls Gemini. Ensure GEMINI_API_KEY is in your GitHub Secrets."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key: 
        return '{"error": "No GEMINI_API_KEY found"}'
    
    # The model name is now gemini-2.5-flash (the current stable model)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    try:
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        print(f"Gemini API Error Response: {r.text}")
        return '{"error": "Gemini API failed"}'

def get_latest_health():
    """Try to find the newest dated health file via GitHub API, fallback to static health.json"""
    
    # Method 1: GitHub API (may hit rate limit 403, but that's okay)
    try:
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{GITHUB_BRANCH}?recursive=1"
        r = requests.get(api_url, timeout=30)
        if r.status_code == 200:
            tree = r.json().get("tree", [])
            health_files = [
                f["path"] for f in tree 
                if f["path"].startswith(f"{HEALTH_DIR}/health-") and f["path"].endswith(".json")
            ]
            if health_files:
                latest_file = sorted(health_files)[-1]
                print(f"Found latest health report via API: {latest_file}")
                raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{latest_file}"
                r2 = requests.get(raw_url, timeout=30)
                if r2.status_code == 200:
                    return r2.json()
        elif r.status_code == 403:
            print("GitHub API rate limit hit. Falling back to static file.")
    except Exception as e:
        print(f"Error querying GitHub API: {e}")

    # Method 2: Static fallback (this is what worked in your log)
    print("Falling back to static health.json...")
    try:
        r = requests.get("https://dukaelementsdotcom.github.io/autopilot/health.json", timeout=30)
        if r.status_code == 200:
            return r.json()
        print(f"Static health.json returned {r.status_code}")
    except Exception as e:
        print(f"Error fetching static health.json: {e}")

    return None

def run_agent_team():
    health = get_latest_health()
    if not health:
        print("Could not obtain health report. Exiting.")
        return

    by_side = health.get("referee", {}).get("calibration", {}).get("bySide", {})
    long_stats = by_side.get("long", {})
    long_hit = long_stats.get("hit", 0)
    
    context = f"The current long side win rate is {long_hit:.1%}. The system needs a new rule to find long edges."
    print(f"Observer says: {context}")

    prompt = f"""
    You are a quantitative researcher building a crypto trading bot.
    Context: {context}
    
    Look at the available data (funding rates, order book imbalance, spot vs perp CVD, whale positioning).
    Invent ONE actionable trading rule to filter or improve the LONG side.
    
    Output a JSON object with:
    - "rule_name": A short snake_case name (e.g., "whale_accumulation_long")
    - "description": How the rule works.
    - "python_logic": A few lines of pandas/python logic to test this on historical data.
    """
    
    print("Strategist (Gemini) is thinking...")
    response = call_gemini(prompt)
    print(f"RAW GEMINI RESPONSE:\n{response}\n")
    
    try:
        clean_resp = re.sub(r"^```[a-zA-Z]*\s*", "", response)
        clean_resp = re.sub(r"\s*```$", "", clean_resp)
        idea = json.loads(clean_resp)
        if "error" in idea: raise ValueError(idea["error"])
    except Exception as e:
        print(f"Strategist failed to parse JSON: {e}")
        return

    print(f"Strategist proposed: {idea.get('rule_name')} - {idea.get('description')}")

    coder_prompt = f"""
    Write a standalone python script for a crypto research engine.
    The file will be saved as `rm/labtest_agent_generated.py`.
    
    It must test this hypothesis: {idea['description']}
    Logic: {idea['python_logic']}
    
    The script MUST:
    1. Import necessary libraries (pandas, numpy).
    2. Assume it has access to a pandas DataFrame called `P` (the panel data) which has columns like `t`, `coin`, `px`, `p_vol24`, and various `w_` features.
    3. Calculate a boolean mask for when the rule triggers.
    4. Print "PASS" if the rule is profitable on the long side, or "FAIL" if it loses money.
    5. Output ONLY the python code, no markdown.
    """
    
    print("Coder (Gemini) is writing the labtest...")
    code = call_gemini(coder_prompt)
    
    code = re.sub(r"^```python\s*", "", code)
    code = re.sub(r"\s*```$", "", code)
    
    safe_name = idea.get('rule_name', 'unnamed_rule').replace(' ', '_')
    filename = f"rm/labtest_agent_{safe_name}.py"
    
    with open(filename, "w") as f:
        f.write(f"# GENERATED BY AGENT TEAM\n")
        f.write(f"# DESCRIPTION: {idea.get('description')}\n\n")
        f.write(code)
        
    print(f"SUCCESS: Wrote new test to {filename}. The labtest workflow will run it.")

if __name__ == "__main__":
    run_agent_team()
