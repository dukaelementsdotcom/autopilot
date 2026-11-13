"""STAGING QUEUE: a strategy that survived every gate becomes ONE extra voter for the Arbiter. It enters exactly like a new witness:
its own model on the discovery years, the combiner refit with it, then a Shadow Arbiter clone (baseline voters + this voter) is
scored on every live candidate against the live Arbiter (log-loss, Brier, calibration, Grade-A results) and promoted only by the locked
sequential test over >= 40 separate market moves. At most CAP evolved voters are staged at once (each costs a model per run)."""
import os, json, time, numpy as np
from .. import config as C

CAP = 3
def _path(): return os.path.join(getattr(C, "EVO_DIR", "evo_state"), "staged.json")

def staged():
    try: return json.load(open(_path()))
    except Exception: return {"active": [], "retired": []}

def stage_arbiter_upgrade(elite, log=print):
    S = staged(); vid = "evo" + elite["key"][:8]
    if any(v["id"] == vid for v in S["active"] + S["retired"]): return False
    v = {"id": vid, "genome": elite["genome"], "what": f"Evolved voter ({elite['cell']}): {elite['text']}", "stagedAt": time.strftime("%Y-%m-%d %H:%M", time.gmtime()),
         "checks": elite.get("checks"), "fit": elite["fit"]}
    S["active"].append(v)
    while len(S["active"]) > CAP: S["retired"].append(S["active"].pop(0))         # the oldest makes room (its live record stays in the challenger history)
    os.makedirs(os.path.dirname(_path()) or ".", exist_ok=True); json.dump(S, open(_path(), "w"), indent=1)
    log(f"   staged as an Arbiter upgrade: {vid}: {elite['text']}"); return True

def witness_entries():
    """what witnesses.py adds to its list: one entry per active evolved voter"""
    out = {}
    for v in staged()["active"]:
        cols = [f"e_{v['id']}_z", f"e_{v['id']}_x"] + ([f"e_{v['id']}_f"] if v["genome"].get("flt") else [])
        out[v["id"]] = {"cols": cols, "what": v["what"]}
    return out

def voter_columns(P, log=print):
    """the active evolved voters as panel columns (z-score of the signal per coin, its rank across coins, and its filter)"""
    act = staged()["active"]
    if not act: return {}
    from . import dsl
    ctx = dsl.Ctx(P, cache=16); out = {}
    for v in act:
        g = v["genome"]
        try:
            s = ctx.eval(g["sig"]); out[f"e_{v['id']}_z"] = ctx.eval(["zs", 720, g["sig"]]); out[f"e_{v['id']}_x"] = ctx._xs(s.astype(float), "rank").astype(np.float32)
            if g.get("flt"): out[f"e_{v['id']}_f"] = ctx.eval(["zs", 720, g["flt"]["node"]])
        except Exception as e: log(f"   evolved voter {v['id']} skipped: {e}")
    return out
