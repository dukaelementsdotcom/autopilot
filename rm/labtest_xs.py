"""LAB TEST (on demand), two new approaches, walk-forward over past periods (trained only on earlier data):
A. CROSS-SECTIONAL RANKING (market-neutral): each day predict which coins will beat / lag the others over the next 24h or 72h
   (features ranked across coins at each moment, target = return minus the day's average), long the top K, short the bottom K,
   equal dollars. Reported after costs, next to simple baselines (7-day reversal, 7-day momentum).
B. PHASE JUDGE: one model reading the market-wide average of every measurement + Bitcoin's own + outside-world and mood columns,
   predicting whether the average coin is higher in 7 days. Reported: accuracy, accuracy when confident, how often it is confident."""
import json, time, math, traceback, numpy as np, pandas as pd
from . import config as C, features, referee as RF
from .judge import Judge

DAY = 86400; H = 3600
def fwd(J, rows, h):
    k = rows + h - 1; ok = (k <= J.last[rows]); kk = np.where(ok, k, rows); ok &= (J.t[kk] == J.t[rows] + (h - 1) * H)
    r = np.where(ok, J.Cl[kk] / J.O[rows] - 1, np.nan); return r

def stats(daily, per_year):
    d = np.asarray(daily, float); d = d[np.isfinite(d)]
    if len(d) < 10: return {"n": int(len(d))}
    eq = np.cumprod(1 + d); dd = float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max())
    return {"n": int(len(d)), "avg": round(float(d.mean()) * 100, 3), "winDays": round(float((d > 0).mean()) * 100, 1), "sharpe": round(float(d.mean() / (d.std() + 1e-12) * math.sqrt(per_year)), 2),
            "monthly": round(math.expm1(np.log1p(d).mean() * per_year / 12) * 100, 2), "maxDrop": round(dd * 100, 1)}

def main(folds=7):
    from sklearn.ensemble import HistGradientBoostingRegressor as HGR, HistGradientBoostingClassifier as HGC
    from sklearn.linear_model import LogisticRegression
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    P = features.build(blocks=blocks); J = Judge(P); out = {"folds": []}
    fams = {k: v for k, v in RF.fam_cols(P).items() if k != "invented"}; cols = sorted({c for v in fams.values() for c in v})
    wcols = [c for c in P.columns if c.startswith("w_") and not c.startswith(("w_sec", "w_insec"))]
    print(len(cols), "measurements,", len(wcols), "market-wide columns", flush=True)
    end = int(J.t_end)
    daily = np.flatnonzero((J.t % DAY == 0) & np.isfinite(J.dvol) & np.isfinite(J.O))                 # one decision a day, at 00:00 UTC
    t_d = J.t[daily]; Xraw = pd.DataFrame(RF._X(P, daily, cols), columns=cols); Xraw["t"] = t_d
    XR = Xraw.groupby("t")[cols].rank(pct=True).astype("float32").values                              # each measurement ranked across coins that day
    r7 = np.array([J.Cl[i - 1] / J.Cl[max(i - 168, 0)] - 1 if i >= 168 and J.coin[i - 168] == J.coin[i] else np.nan for i in daily])
    F = {h: fwd(J, daily, h) for h in (24, 72, 168)}
    def demean(v): s = pd.Series(v); return (s - s.groupby(t_d).transform("mean")).values
    # market-level table for the phase judge
    Xm = Xraw.groupby("t")[cols].mean(); Xm.columns = [f"mkt_{c}" for c in cols]
    b = np.array([str(c) == "BTC" for c in J.coin[daily]]); Xb = pd.DataFrame(RF._X(P, daily[b], cols + wcols), columns=[f"btc_{c}" for c in cols] + wcols, index=t_d[b])
    M = Xm.join(Xb, how="inner"); M["breadth24"] = pd.Series(np.where(np.isfinite(F[24]), 0, 0), index=t_d).groupby(level=0).size()
    M["up7"] = pd.Series(r7 > 0, index=t_d).groupby(level=0).mean(); M["r7mkt"] = pd.Series(r7, index=t_d).groupby(level=0).mean()
    ym = pd.Series(F[168], index=t_d).groupby(level=0).mean(); M["y"] = ym.reindex(M.index)
    mcols = [c for c in M.columns if c != "y"]
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY
        if h0 - int(J.t.min()) < 700 * DAY: break
        f = {"exam": f"{pd.to_datetime(h0, unit='s').date()} to {pd.to_datetime(te, unit='s').date()}"}
        try:
            T, O, Hh, Ll, Cc = J._bars("BTC"); s = pd.Series(Cc, index=T); f["btc"] = round(float(s.asof(te) / s.asof(h0) - 1) * 100, 1)
            # A. cross-sectional ranking
            for h, step, K in ((24, 1, 5), (72, 3, 5), (24, 1, 3)):
                y = demean(F[h]); tr = (t_d < h0 - (h // 24 + 1) * DAY) & np.isfinite(y); tst = (t_d >= h0) & (t_d < te - h * H) & ((t_d // DAY) % step == 0)
                m = HGR(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200, l2_regularization=1.0, random_state=0).fit(XR[tr], np.clip(y[tr], -0.2, 0.2))
                pred = np.full(len(daily), np.nan); pred[tst] = m.predict(XR[tst])
                df = pd.DataFrame({"t": t_d[tst], "pred": pred[tst], "ret": F[h][tst], "rev": -r7[tst], "mom": r7[tst]}).dropna(subset=["ret"])
                res = {}
                for sig in ("pred", "rev", "mom"):
                    pnl, ic, prev = [], [], None; turn = []
                    for t, g in df.dropna(subset=[sig]).groupby("t"):
                        if len(g) < 4 * K: continue
                        g = g.sort_values(sig); lo, hi = g.head(K), g.tail(K)
                        cur = (frozenset(hi.index.map(lambda i: i)), frozenset(lo.index))
                        gross = 0.5 * (hi.ret.mean() - lo.ret.mean())                         # 50% of the account long, 50% short
                        pnl.append(gross - C.FEE); ic.append(g[sig].corr(g.ret, method="spearman"))
                    res[sig] = {**stats(pnl, 365 / step), "ic": round(float(np.nanmean(ic)), 4) if ic else None}
                f[f"rank {h}h top/bottom {K}"] = res
            # B. phase judge
            tr = (M.index < h0 - 8 * DAY) & M.y.notna(); tst = (M.index >= h0) & (M.index < te - 7 * DAY) & M.y.notna()
            mm = HGC(max_iter=150, learning_rate=0.05, max_leaf_nodes=8, min_samples_leaf=40, l2_regularization=1.0, random_state=0).fit(M.loc[tr, mcols].values, (M.y[tr] > 0).astype(int))
            p = mm.predict_proba(M.loc[tst, mcols].values)[:, 1]; yt = (M.y[tst] > 0).values; conf = (p >= 0.6) | (p <= 0.4)
            f["phase judge"] = {"days": int(tst.sum()), "upShare": round(float(yt.mean()), 2), "accuracy": round(float(((p > 0.5) == yt).mean()), 3),
                                "confidentShare": round(float(conf.mean()), 2), "accuracyWhenConfident": round(float(((p > 0.5) == yt)[conf].mean()), 3) if conf.any() else None,
                                "avgRetWhenSaysUp": round(float(M.y[tst].values[p >= 0.6].mean()) * 100, 2) if (p >= 0.6).any() else None,
                                "avgRetWhenSaysDown": round(float(M.y[tst].values[p <= 0.4].mean()) * 100, 2) if (p <= 0.4).any() else None}
        except Exception as e:
            f["error"] = traceback.format_exc()[-600:]
        out["folds"].append(f); print(json.dumps(f), flush=True); json.dump(out, open("labtest.json", "w"), indent=1)
    json.dump(out, open("labtest.json", "w"), indent=1); print("done", round((time.time() - t0) / 60), "min")

if __name__ == "__main__": main()
