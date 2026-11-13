"""LAB TEST: a long side with its OWN question. Walk-forward (each 120-day window unseen; trained on the years before, bar chosen on the
year before that). Variants, all long only, one trade a day, 4% of the account at the stop:
  L0  today's question:  +2% before the volatility stop (1.25x daily vol, 1.5-4%) within 48h
  L1  longer swing:      +4% before 1.5x the stop (2.5-6%) within 5 days
  L2  trend ride:        +6% before 2x the stop (3-8%) within 10 days
  L3  flush bounce:      L1's question, but only right after a liquidation flush / sharp drop (many longs forced out in the last day,
                         or the coin down >= 1.5 daily moves in 24h) - buying the forced selling
Each variant: one gradient-boosted model on the Arbiter's family inputs plus the liquidation measurements, trained on the discovery years."""
import json, math, time, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier as HGB
from . import config as C, features, referee as RF
from .judge import Judge
DAY = 86400; H = 3600
Q = {"L0": (0.02, 1.0, 0.015, 0.04, 48), "L1": (0.04, 1.5, 0.025, 0.06, 120), "L2": (0.06, 2.0, 0.03, 0.08, 240), "L3": (0.04, 1.5, 0.025, 0.06, 120)}

def label(J, rows, tgt, k, lo, hi, hz):
    stp = np.clip(RF.STOP_K * k * J.dvol[rows], lo, hi); y, r = RF.barrier(J, rows, 1, tgt, stp, hz); return y, r, stp

def flush(P, rows, dvol):
    r24 = P["r24"].values[rows] if "r24" in P else np.full(len(rows), np.nan)
    drop = np.nan_to_num(r24) <= -1.5 * np.nan_to_num(dvol[rows], nan=1)
    lq = P["w_l_long1"].values[rows] if "w_l_long1" in P else np.zeros(len(rows))
    return drop | (np.nan_to_num(lq) >= 1.5)

def sim(J, rows, r, p, s, span):
    if not len(rows): return {"trades": 0}
    df = pd.DataFrame({"t": J.t[rows], "day": J.t[rows] // DAY, "r": r, "p": p, "s": s}).sort_values(["t", "p"], ascending=[True, False]).groupby("day").head(1)
    a = np.maximum(df.r.values * np.minimum(RF.RISK["A"] / df.s.values, RF.MAX_MARGIN * RF.LEV), -RF.MAX_MARGIN); eq = np.cumprod(1 + a)
    return {"trades": int(len(df)), "perWeek": round(len(df) / span * 7, 2), "win": round(float((df.r > 0).mean()) * 100, 1), "avg": round(float(df.r.mean()) * 100, 3),
            "monthly": round(math.expm1(np.log1p(a).sum() / span * 30.4) * 100, 2), "maxDrop": round(float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max()) * 100, 1)}

def main(folds=7):
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    P = features.build(blocks=blocks); J = Judge(P)
    fams = {k: v for k, v in RF.fam_cols(P).items() if k != "invented"}
    cols = sorted({c for v in fams.values() for c in v} | {c for c in P.columns if c.startswith("w_l_")})
    out = {"cols": len(cols), "folds": []}; end = int(J.t_end); pool = {v: [] for v in Q}; span_all = 0.0
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY; v0 = h0 - C.VALID_DAYS * DAY
        if v0 - int(J.t.min()) < 500 * DAY or time.time() - t0 > 110 * 60: break
        f = {"exam": f"{pd.to_datetime(h0, unit='s').date()} to {pd.to_datetime(te, unit='s').date()}", "v": {}}
        btc = J._bars("BTC"); s_ = pd.Series(btc[4], index=btc[0]); f["btc"] = round(float(s_.asof(te) / s_.asof(h0) - 1) * 100, 1)
        for v, (tgt, kk, lo, hi, hz) in Q.items():
            D = RF._rows(J, int(J.t.min()), v0 - hz * H); V = RF._rows(J, v0, h0 - hz * H); X = RF._rows(J, h0, te - hz * H)
            if v == "L3": D, V, X = D[flush(P, D, J.dvol)], V[flush(P, V, J.dvol)], X[flush(P, X, J.dvol)]
            if len(D) < 3000 or len(V) < 300 or len(X) < 50: f["v"][v] = {"trades": 0, "note": "too few rows"}; continue
            yD, _, _ = label(J, D, tgt, kk, lo, hi, hz); yV, rV, sV = label(J, V, tgt, kk, lo, hi, hz); yX, rX, sX = label(J, X, tgt, kk, lo, hi, hz)
            fs = [c for c in cols if np.isfinite(P[c].values[D]).mean() > 0.2]
            m = HGB(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200, l2_regularization=1.0, random_state=0).fit(RF._X(P, D, fs), yD)
            pV = m.predict_proba(RF._X(P, V, fs))[:, 1]; pX = m.predict_proba(RF._X(P, X, fs))[:, 1]
            beV = (sV + C.FEE) / (tgt + sV); beX = (sX + C.FEE) / (tgt + sX); best, bm = None, None
            for mg in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12):           # the bar is chosen on the validation year only
                pick = pV >= beV + mg
                if pick.sum() >= 15:
                    g = sim(J, V[pick], rV[pick], pV[pick], sV[pick], max(1.0, (h0 - v0) / DAY))["monthly"]
                    if best is None or g > best: best, bm = g, mg
            if bm is None: f["v"][v] = {"trades": 0, "note": "no bar on validation"}; continue
            pick = pX >= beX + bm
            f["v"][v] = {"bar": bm, "valMonthly": best, "base": round(float(yX.mean()), 3), **sim(J, X[pick], rX[pick], pX[pick], sX[pick], max(1.0, (te - h0) / DAY)),
                         "trade": best > 0}
            if best > 0: pool[v].append(pd.DataFrame({"t": J.t[X[pick]], "r": rX[pick], "p": pX[pick], "s": sX[pick], "i": X[pick]}))
        out["folds"].append(f); span_all += (te - h0) / DAY; print(json.dumps(f), flush=True)
    for v in Q:                                                     # pooled: only windows where the validation year said 'trade'
        if pool[v]:
            d = pd.concat(pool[v]); out[f"pooled_{v}"] = sim(J, d.i.values, d.r.values, d.p.values, d.s.values, span_all)
    print("POOLED", json.dumps({k: v for k, v in out.items() if k.startswith("pooled")}), flush=True); json.dump(out, open("labtest.json", "w"), indent=1)
main()
