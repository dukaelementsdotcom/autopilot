"""WALK-FORWARD AUDIT (run on demand, never inside the hourly run): is the Arbiter's edge real, or one lucky window?
For several past dates it retrains the whole brain using only data before that date's exam window, then examines it on the
following 120 days it never saw. Reports, per window and side: the exam as the brain certifies it ("exam rule") and as the
Arbiter actually trades ("live rule": also at least two witnesses for, at most one against, not unfamiliar), with honest
uncertainty (bootstrap range). Also re-scores the CURRENT brain on its own exam window to explain exam-vs-live differences."""
import json, math, time, sys, numpy as np, pandas as pd
from . import config as C, features, referee as RF, challengers as CH
from .judge import Judge

DAY = 86400

def pol(J, rows, r, p, s, span_days, boot=400):
    if not len(rows): return {"trades": 0}
    df = pd.DataFrame({"t": J.t[rows], "day": J.t[rows] // DAY, "r": r, "p": p, "s": s}).sort_values(["t", "p"], ascending=[True, False]).groupby("day").head(1)
    acct = np.maximum(df.r.values * np.minimum(RF.RISK["A"] / df.s.values, RF.MAX_MARGIN * RF.LEV), -RF.MAX_MARGIN)
    mon = lambda a: math.expm1(np.log1p(a).sum() / span_days * 30.4) * 100
    rng = np.random.default_rng(0); bs = [mon(rng.choice(acct, len(acct))) for _ in range(boot)] if len(acct) >= 5 else [float("nan")]
    eq = np.cumprod(1 + acct)
    return {"trades": int(len(df)), "perWeek": round(len(df) / span_days * 7, 2), "win": round(float((df.r > 0).mean()) * 100, 1), "avg": round(float(df.r.mean()) * 100, 3),
            "monthly": round(mon(acct), 2), "lo": round(float(np.nanpercentile(bs, 5)), 1), "hi": round(float(np.nanpercentile(bs, 95)), 1),
            "maxDrop": round(float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max()) * 100, 1)}

def exam_both(J, B, a, b):
    X = RF._rows(J, a, b); S = RF.score(J, B, X); span = max(1.0, (b - a) / DAY); out = {}
    for name, sd in B["sides"].items():
        side = 1 if name == "long" else -1; stp = RF.stop_pct(J.dvol[X]); y, r = RF.barrier(J, X, side, RF.TARGET, stp, RF.HORIZON); p = np.asarray(S[name]["prob"]); be = RF.breakeven(stp)
        sup, ag = CH._support(S[name]["fams"], sd["base"]); unf = np.asarray(S[name]["unfamiliar"])
        loose = (p >= be + sd["margin"]) & ~unf; live = loose & (sup >= 2) & (ag <= 1)
        out[name] = {"margin": sd["margin"], "valMonthly": sd.get("valMonthly"), "baseHit": round(float(y.mean()), 3),
                     "examRule": pol(J, X[loose], r[loose], p[loose], stp[loose], span), "liveRule": pol(J, X[live], r[live], p[live], stp[live], span)}
    return out

def main(folds=8):
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception as e: print("no memory (invented measurements skipped):", e)
    P = features.build(blocks=blocks); J = Judge(P); out = {"at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "folds": []}
    B = RF.load()
    if B is not None:                                             # the brain in charge, re-scored today on its own exam window
        v0, h0, te = B.get("split") or [J.val0, J.hold0, J.t_end]
        out["current"] = {"trainedAt": B["trainedAt"], "official": {n: sd.get("exam") for n, sd in B["sides"].items()}, "rescoredToday": exam_both(J, B, h0, te - RF.HORIZON * 3600)}
        print("current brain:", json.dumps(out["current"]), flush=True)
    orig = RF.fam_cols; RF.fam_cols = lambda P_: {k: v for k, v in orig(P_).items() if k != "invented"}    # invented measurements were found with recent data: not allowed in past windows
    end = int(J.t_end)
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY; v0 = h0 - C.VALID_DAYS * DAY
        if v0 - int(J.t.min()) < 500 * DAY: break                 # needs at least ~1.4 years to learn from
        if time.time() - t0 > 150 * 60: print("time budget reached"); break
        J.t_end, J.hold0, J.val0 = te, h0, v0
        Bk = RF.train(J, log=lambda *a: None); res = exam_both(J, Bk, h0, te - RF.HORIZON * 3600)
        f = {"exam": f"{pd.to_datetime(h0, unit='s').date()} to {pd.to_datetime(te, unit='s').date()}", "btc": None, "sides": res, "secs": Bk.get("secs")}
        try:
            T, O, Hh, Ll, Cc = J._bars("BTC"); s = pd.Series(Cc, index=T); f["btc"] = round(float(s.asof(te) / s.asof(h0) - 1) * 100, 1)
        except Exception: pass
        out["folds"].append(f); print(json.dumps(f), flush=True)
        json.dump(out, open("walkforward.json", "w"), indent=1)
    json.dump(out, open("walkforward.json", "w"), indent=1); print("done in", round((time.time() - t0) / 60), "min")

if __name__ == "__main__": main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
