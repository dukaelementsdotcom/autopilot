"""LAB TEST: is one trade a day the right cap? Walk-forward (the brain retrained before each 120-day window it never saw), the Arbiter's
live grading rule unchanged, and only the daily cap varied: 1, 2, 3 trades a day, or every qualifying coin (one per coin per day).
Trades of the same day run side by side, each sized like the Arbiter's Grade A (4% of the account at the stop), so a day's result is the
sum of its trades. Reported per window and over all windows: monthly growth, deepest drop, trades per week, win rate, and how often a
multi-trade day lost on every trade (the 'same move' risk)."""
import json, math, time, numpy as np, pandas as pd
from . import config as C, features, referee as RF, challengers as CH
from .judge import Judge
DAY = 86400; CAPS = (1, 2, 3, 99)

def cands(J, B, a, b):
    X = RF._rows(J, a, b); S = RF.score(J, B, X); out = []
    for name, sd in B["sides"].items():
        side = 1 if name == "long" else -1; stp = RF.stop_pct(J.dvol[X]); y, r = RF.barrier(J, X, side, RF.TARGET, stp, RF.HORIZON)
        p = np.asarray(S[name]["prob"]); be = RF.breakeven(stp); sup, ag = CH._support(S[name]["fams"], sd["base"]); unf = np.asarray(S[name]["unfamiliar"])
        live = (p >= be + sd["margin"]) & ~unf & (sup >= 2) & (ag <= 1)
        if live.any(): out.append(pd.DataFrame({"t": J.t[X[live]], "coin": J.coin[X[live]], "side": name, "r": r[live], "s": stp[live], "y": y[live], "ok": bool(sd.get("live", True))}))
    return pd.concat(out) if out else None, max(1.0, (b - a) / DAY)

def sim(df, cap, span):
    if df is None or not len(df): return {"trades": 0}
    d = df.sort_values("t").copy(); d["day"] = d.t // DAY
    d = d.drop_duplicates(["day", "coin"])                              # one trade per coin per day
    d = d.groupby("day").head(cap)                                      # the first `cap` in time order, as the Arbiter takes them
    size = np.minimum(RF.RISK["A"] / d.s.values, RF.MAX_MARGIN * RF.LEV)
    d["acct"] = np.maximum(d.r.values * size, -RF.MAX_MARGIN)
    day = d.groupby("day").agg(a=("acct", "sum"), n=("acct", "size"), lost=("acct", lambda x: bool((x < 0).all())))
    a = np.maximum(day.a.values, -0.95); eq = np.cumprod(1 + a)
    multi = day[day.n > 1]
    return {"trades": int(len(d)), "perWeek": round(len(d) / span * 7, 2), "win": round(float((d.r > 0).mean()) * 100, 1), "avg": round(float(d.r.mean()) * 100, 3),
            "monthly": round(math.expm1(np.log1p(a).sum() / span * 30.4) * 100, 2), "maxDrop": round(float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max()) * 100, 1),
            "multiDays": int(len(multi)), "allLostShare": round(float(multi.lost.mean()) * 100, 1) if len(multi) else None, "worstDay": round(float(a.min()) * 100, 1)}

def main(folds=7):
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    P = features.build(blocks=blocks); J = Judge(P); out = {"folds": [], "pooled": {}}
    orig = RF.fam_cols; RF.fam_cols = lambda P_: {k: v for k, v in orig(P_).items() if k != "invented"}
    end = int(J.t_end); pool = {c: [] for c in CAPS}; spans = 0.0
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY; v0 = h0 - C.VALID_DAYS * DAY
        if v0 - int(J.t.min()) < 500 * DAY or time.time() - t0 > 105 * 60: break
        J.t_end, J.hold0, J.val0 = te, h0, v0
        try: Bk = RF.train(J, log=lambda *a: None)
        except Exception as e: print("fold skipped:", str(e)[:100]); break
        df, span = cands(J, Bk, h0, te - RF.HORIZON * 3600)
        if df is not None: df = df[df.ok]                               # only sides the brain would really trade
        f = {"exam": f"{pd.to_datetime(h0, unit='s').date()} to {pd.to_datetime(te, unit='s').date()}", "caps": {str(c): sim(df, c, span) for c in CAPS}}
        out["folds"].append(f); print(json.dumps(f), flush=True); spans += span
        if df is not None and len(df):
            for c in CAPS: pool[c].append(df.assign(fold=k))
    for c in CAPS:
        if pool[c]:
            # pooled: windows chained one after another (each window's days are distinct)
            out["pooled"][str(c)] = sim(pd.concat(pool[c]), c, spans)
    print("POOLED", json.dumps(out["pooled"]), flush=True); json.dump(out, open("labtest.json", "w"), indent=1)
main()
