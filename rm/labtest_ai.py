"""LAB TEST: the AI clients. Gemini Flash and Pro directly, Groq as usual, the automatic fallback (Groq key removed), the evolution
route (Gemini Pro first), each answering the same strict-JSON schema the market brief uses. Nothing is written to the saved memory."""
import os, json, time
from . import ideas
P = [{"role": "user", "content": "You are a markets analyst. From ONLY these headlines answer in strict JSON with keys: brief (max 30 words), risk (integer -2..2), "
      "events (list of up to 2 objects: title, direction one of [up, down, unclear]). No other text.\n- Bitcoin falls below $67k as ETF outflows continue\n- Fed officials signal patience on rate cuts"}]
def check(name, fn):
    t0 = time.time()
    try:
        txt, model = fn(); ok = False; keys = None
        if txt:
            a, b = txt.find("{"), txt.rfind("}"); j = json.loads(txt[a:b + 1]); keys = sorted(j); ok = {"brief", "risk", "events"} <= set(j)
        print(f"{name}: {'OK' if ok else 'FAIL'} model={model} keys={keys} {time.time() - t0:.1f}s", flush=True); return ok
    except Exception as e: print(f"{name}: FAIL {e}", flush=True); return False
res = {"gemini key present": bool(os.environ.get("GEMINI_API_KEY")), "groq key present": bool(os.environ.get("GROQ_API_KEY"))}; print(res)
res["gemini flash"] = check("gemini flash", lambda: ideas._gemini(P, 400, ("gemini-2.5-flash",)))
res["gemini pro"] = check("gemini pro", lambda: ideas._gemini(P, 400, ("gemini-2.5-pro",)))
res["normal route (Groq first)"] = check("normal route", lambda: ideas._groq_call(P, 400))
g = os.environ.pop("GROQ_API_KEY", None)
res["fallback when Groq fails"] = check("fallback when Groq fails", lambda: ideas._groq_call(P, 400))
if g: os.environ["GROQ_API_KEY"] = g
res["evolution route (Gemini Pro first)"] = check("evolution route", lambda: ideas._groq_call(P, 400, prefer="gemini-pro"))
json.dump(res, open("labtest.json", "w"), indent=1)
