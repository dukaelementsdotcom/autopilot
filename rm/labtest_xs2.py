"""LAB TEST cycle 2. A: screened composite ranking: for each period, every measurement is scored on PAST data only as a
cross-sectional signal (daily rank correlation with what the coin did next, vs the other coins); the reliable ones (|t| >= 3)
are combined with their sign into one score; long top K / short bottom K, at 24h, 72h and 7-day horizons, after costs.
B: simpler phase judges (heavily regularised logistic on all market-wide averages; and a small one on trend, breadth, mood),
for 7 and 14 days ahead."""
import json, time, math, traceback, numpy as np, pandas as pd
from . import config as C, features, referee as RF, compass
from .judge import Judge
from .labtest_xs import fwd, stats

DAY = 86400; H = 3600
def main(folds=7):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    P = features.build(blocks=blocks); J = Judge(P); out = {"folds": []}
    fams = {k: v for k, v in RF.fam_cols(P).items() if k != "invented"}; cols = sorted({c for v in fams.values() for c in v})
    wcols = [c for c in P.columns if c.startswith("w_") and not c.startswith(("w_sec", "w_insec"))]
    end = int(J.t_end); daily = np.flatnonzero((J.t % DAY == 0) & np.isfinite(J.dvol) & np.isfinite(J.O)); t_d = J.t[daily]
    Xraw = pd.DataFrame(RF._X(P, daily, cols), columns=cols); Xraw["t"] = t_d
    XR = Xraw.groupby("t")[cols].rank(pct=True).values.astype("float64") - 0.5
    F = {h: fwd(J, daily, h) for h in (24, 72, 168, 336)}
    days = np.unique(t_d); pos = {t: np.flatnonzero(t_d == t) for t in days}
    def daily_ic(h):                                      # per day, per measurement: rank correlation with the next-h return across coins
        ic = np.full((len(days), len(cols)), np.nan)
        for a, t in enumerate(days):
            ix = pos[t]; y = F[h][ix]; ok = np.isfinite(y)
            if ok.sum() < 15: continue
            yr = pd.Series(y[ok]).rank(pct=True).values - 0.5; X = XR[ix][ok]; Xc = np.where(np.isfinite(X), X, 0.0)
            ic[a] = (Xc * yr[:, None]).sum(0) / (np.sqrt((Xc ** 2).sum(0) * (yr ** 2).sum()) + 1e-12)
        return ic
    IC = {h: daily_ic(h) for h in (24, 72, 168)}
    # market table for the phase judges
    Xm = Xraw.groupby("t")[cols].mean(); Xm.columns = [f"mkt_{c}" for c in cols]
    b = np.array([str(c) == "BTC" for c in J.coin[daily]]); Xb = pd.DataFrame(RF._X(P, daily[b], wcols), columns=wcols, index=t_d[b]); sc = compass.trend_scores(J)
    M = Xm.join(Xb, how="inner"); M["trend"] = sc.reindex(M.index).values
    for h in (168, 336): M[f"y{h}"] = pd.Series(F[h], index=t_d).groupby(level=0).mean().reindex(M.index)
    small = ["trend"] + [c for c in ("w_g_fng", "w_g_fng7", "w_vix", "w_g_spx20", "w_g_dxy20", "w_g_link", "w_shockN") if c in M.columns]
    allc = [c for c in M.columns if not c.startswith("y")]
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY
        if h0 - int(J.t.min()) < 700 * DAY: break
        f = {"exam": f"{pd.to_datetime(h0, unit='s').date()} to {pd.to_datetime(te, unit='s').date()}"}
        try:
            for h, step in ((24, 1), (72, 3), (168, 7)):
                past = days < h0 - (h // 24 + 1) * DAY; ic = IC[h][past]; n = np.isfinite(ic).sum(0)
                tstat = np.nanmean(ic, 0) / (np.nanstd(ic, 0) / np.sqrt(np.maximum(n, 1)) + 1e-12)
                sel = np.flatnonzero((np.abs(tstat) >= 3) & (n > 300)); w = np.sign(tstat[sel])
                res = {"picked": int(len(sel)), "top": [f"{'+' if tstat[j] > 0 else '-'}{cols[j]}" for j in sel[np.argsort(-np.abs(tstat[sel]))][:6]]}
                if len(sel):
                    score = np.nansum(np.where(np.isfinite(XR[:, sel]), XR[:, sel], 0.0) * w, 1)
                    for K in (3, 5, 8):
                        pnl = []
                        for t in days[(days >= h0) & (days < te - h * H) & ((days // DAY) % step == 0)]:
                            ix = pos[t]; y = F[h][ix]; ok = np.isfinite(y)
                            if ok.sum() < 4 * K: continue
                            o = np.argsort(score[ix][ok]); yy = y[ok]
                            pnl.append(0.5 * (yy[o[-K:]].mean() - yy[o[:K]].mean()) - C.FEE)
                        res[f"K{K}"] = stats(pnl, 365 / step)
                f[f"composite {h}h"] = res
            for h in (168, 336):
                y = M[f"y{h}"]; tr = (M.index < h0 - (h // 24 + 1) * DAY) & y.notna(); tst = (M.index >= h0) & (M.index < te - h * H) & y.notna()
                for nm, cc, Creg in (("all, strong shrinkage", allc, 0.02), ("small", small, 0.5)):
                    mm = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=Creg, max_iter=2000)).fit(M.loc[tr, cc].values, (y[tr] > 0).astype(int))
                    p = mm.predict_proba(M.loc[tst, cc].values)[:, 1]; yt = (y[tst] > 0).values; conf = (p >= 0.6) | (p <= 0.4)
                    f[f"phase {h // 24}d ({nm})"] = {"accuracy": round(float(((p > 0.5) == yt).mean()), 3), "confidentShare": round(float(conf.mean()), 2),
                        "accWhenConfident": round(float(((p > 0.5) == yt)[conf].mean()), 3) if conf.any() else None,
                        "retUp": round(float(y[tst].values[p >= 0.6].mean()) * 100, 2) if (p >= 0.6).any() else None, "retDown": round(float(y[tst].values[p <= 0.4].mean()) * 100, 2) if (p <= 0.4).any() else None}
        except Exception: f["error"] = traceback.format_exc()[-700:]
        out["folds"].append(f); print(json.dumps(f), flush=True); json.dump(out, open("labtest.json", "w"), indent=1)
    json.dump(out, open("labtest.json", "w"), indent=1)

if __name__ == "__main__": main()
