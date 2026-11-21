"""VOLVOX GROWTH: one small snapshot a day of the measurable signs of learning, so the dashboard can draw REAL curves.
Saved as collect/growth.jsonl (one line per UTC day, today's line is refreshed every run; the Data Vault copies it). Nothing here can fail a run."""
import os, json, datetime as dt
import pandas as pd
from . import config as C

PATH = lambda: f"{C.STATE_DIR}/collect/growth.jsonl"

def _gap(cal):
    """how far the odds are from reality, in points: n-weighted mean |predicted - actually reached| over the calibration bands"""
    b = [x for x in (cal or {}).get("bands", []) if x.get("n")]
    n = sum(x["n"] for x in b)
    return round(sum(abs(x["pred"] - x["hit"]) * x["n"] for x in b) / n * 100, 2) if n else None

def snapshot(mem):
    R = mem.get("referee") or {}; A = R.get("acct") or {}; ad = R.get("adapt") or {}; sh = ad.get("short") or {}
    s = {"d": dt.datetime.utcnow().strftime("%Y-%m-%d"), "oddsGap": _gap(R.get("calibration")), "scored": (R.get("calibration") or {}).get("scored"),
         "shortN": sh.get("n"), "shortHit": sh.get("hit"), "shortPred": sh.get("pred"),
         "equity": A.get("equity", A.get("cash")), "closed": len(A.get("closed") or []),
         "racing": len(R.get("chalScores") or {}), "promotions": sum(1 for h in ((R.get("champ") or {}).get("history") or []) if h.get("promoted")) - sum(1 for h in ((R.get("champ") or {}).get("history") or []) if h.get("undone")),
         "ideasTested": len(mem.get("tests") or {})}
    rc = R.get("readerContest") or {}
    s["readers"] = {k: {"calls": v.get("calls"), "right": v.get("right")} for k, v in rc.items() if isinstance(v, dict) and v.get("calls")}
    try:
        t = pd.to_numeric(pd.read_parquet(f"{C.STATE_DIR}/collect/rec_depth.parquet", columns=["t"])["t"]); t = t / 1000 if t.max() > 1e11 else t
        s["recordedDays"] = round(float((t.max() - t.min()) / 86400), 1)
    except Exception: s["recordedDays"] = None
    return s

def update(mem):
    """add or refresh today's line; return every line (oldest first)"""
    p = PATH(); rows = []
    try: rows = [json.loads(l) for l in open(p) if l.strip()]
    except Exception: pass
    s = snapshot(mem); rows = [r for r in rows if r.get("d") != s["d"]] + [s]; rows.sort(key=lambda r: r["d"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    return rows
