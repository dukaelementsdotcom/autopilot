"""LAB TEST cycle 3: is the edge at SHORT horizons? Every 4 hours: predict which coins beat / lag the others over the next 4h and 8h
(learned ranking, trained on past data only). Reported before costs, after 0.04% (limit orders) and after 0.10% (market orders),
plus the average rank correlation. Also the same for the whole market's direction over the next 4h / 24h (time-series)."""
import json, math, traceback, numpy as np, pandas as pd
from . import config as C, features, referee as RF
from .judge import Judge
from .labtest_xs import fwd, stats

DAY = 86400; H = 3600
def main(folds=7):
    from sklearn.ensemble import HistGradientBoostingRegressor as HGR
    blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    P = features.build(blocks=blocks); J = Judge(P); out = {"folds": []}
    fams = {k: v for k, v in RF.fam_cols(P).items() if k != "invented"}; cols = sorted({c for v in fams.values() for c in v})
    cols = [c for c in cols if c not in ("dow", "hour")]
    end = int(J.t_end); rows = np.flatnonzero((J.t % (4 * H) == 0) & (J.t >= end - 1000 * DAY) & np.isfinite(J.dvol) & np.isfinite(J.O)); t_r = J.t[rows]
    Xraw = pd.DataFrame(RF._X(P, rows, cols), columns=cols); Xraw["t"] = t_r
    XR = Xraw.groupby("t")[cols].rank(pct=True).astype("float32").values
    XA = np.column_stack([XR, Xraw[cols].values.astype("float32")])            # ranks + raw values
    F = {h: fwd(J, rows, h) for h in (4, 8, 24)}
    def demean(v): s = pd.Series(v); return (s - s.groupby(t_r).transform("mean")).values
    mkt = {h: pd.Series(F[h]).groupby(t_r).transform("mean").values for h in (4, 24)}
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY
        if h0 - int(t_r.min()) < 400 * DAY: break
        f = {"exam": f"{pd.to_datetime(h0, unit='s').date()} to {pd.to_datetime(te, unit='s').date()}"}
        try:
            for h in (4, 8):
                y = demean(F[h]); tr = (t_r < h0 - DAY) & np.isfinite(y); tst = (t_r >= h0) & (t_r < te - h * H) & (t_r % (h * H) == 0) & np.isfinite(F[h])
                m = HGR(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=300, l2_regularization=1.0, random_state=0).fit(XR[tr], np.clip(y[tr], -0.1, 0.1))
                df = pd.DataFrame({"t": t_r[tst], "pred": m.predict(XR[tst]), "ret": F[h][tst]})
                g_, ic = [], []
                for t, g in df.groupby("t"):
                    if len(g) < 20: continue
                    g = g.sort_values("pred"); g_.append(0.5 * (g.tail(5).ret.mean() - g.head(5).ret.mean())); ic.append(g.pred.corr(g.ret, method="spearman"))
                g_ = np.array(g_); per_year = 365 * 24 / h
                f[f"rank {h}h"] = {"ic": round(float(np.nanmean(ic)), 4), "gross": stats(g_, per_year), "net 0.04%": stats(g_ - 0.0004, per_year), "net 0.10%": stats(g_ - 0.001, per_year)}
            for h in (4, 24):                                   # market direction: every coin-row predicts the market's next move (same target for all coins at a time)
                y = mkt[h]; tr = (t_r < h0 - 2 * DAY) & np.isfinite(y); tst = (t_r >= h0) & (t_r < te - h * H) & np.isfinite(y)
                m = HGR(max_iter=150, learning_rate=0.05, max_leaf_nodes=8, min_samples_leaf=2000, l2_regularization=1.0, random_state=0).fit(XA[tr][:, len(cols):], np.clip(y[tr], -0.1, 0.1))
                d = pd.DataFrame({"t": t_r[tst], "p": m.predict(XA[tst][:, len(cols):]), "y": y[tst]}).groupby("t").mean()
                d = d[(d.index % (h * H)) == 0]; pos = np.sign(d.p) * (np.abs(d.p) > np.abs(d.p).median())          # trade only the stronger half of its opinions
                pnl = (pos * d.y).values[pos.values != 0]
                f[f"market {h}h"] = {"accuracy": round(float((np.sign(d.p) == np.sign(d.y)).mean()), 3), "corr": round(float(d.p.corr(d.y)), 3),
                                     "gross": stats(pnl, 365 * 24 / h / 2), "net 0.10%": stats(pnl - 0.001, 365 * 24 / h / 2)}
        except Exception: f["error"] = traceback.format_exc()[-700:]
        out["folds"].append(f); print(json.dumps(f), flush=True); json.dump(out, open("labtest.json", "w"), indent=1)
    json.dump(out, open("labtest.json", "w"), indent=1)

if __name__ == "__main__": main()
