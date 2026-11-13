"""LAB TEST: how accurate can a market reading be, and at what cost in coverage? Bitcoin's direction over the next 1, 3 and 7 days,
one reading a day (independent-ish), walk-forward by year (train only on the past, 7-day gap). Accuracy is reported for all days and
for the most confident 50% / 25% / 10% of days. Two readers: the Compass trend score, and a learned reader using every measurement."""
import json, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier as HGB
from . import features
DAY = 86400

def curve(p, y):
    o = {}; conf = np.abs(p - 0.5); hit = ((p > 0.5) == (y > 0))
    for cov in (1.0, 0.5, 0.25, 0.10):
        k = max(5, int(len(p) * cov)); ix = np.argsort(-conf)[:k]; o[f"{int(cov * 100)}%"] = {"acc": round(float(hit[ix].mean()) * 100, 1), "days": int(k)}
    return o

def main():
    P = features.build(blocks=[]); B = P[P.coin == "BTC"].sort_values("t"); B = B[(B.t % DAY) == 0].reset_index(drop=True)
    px = P[P.coin == "BTC"].set_index("t")["px"]
    feats = [c for c in B.columns if B[c].dtype.kind == "f" and not c.startswith(("L_", "S_", "_")) and c not in ("px",)]
    out = {"features": len(feats), "days": len(B)}
    for d in (1, 3, 7):
        y = px.reindex(B.t.values + d * DAY).values / px.reindex(B.t.values).values - 1; ok = np.isfinite(y); res = {}
        if "p_mtrend" in B:
            m = ok & np.isfinite(B.p_mtrend.values) & (B.t.values >= pd.Timestamp("2022-01-01").timestamp())
            res["trendScore"] = curve(B.p_mtrend.values[m], y[m])
        ps, ys = [], []
        for yr in range(2022, 2027):
            a, b = pd.Timestamp(f"{yr}-01-01").timestamp(), pd.Timestamp(f"{yr + 1}-01-01").timestamp()
            tr = ok & (B.t.values < a - 7 * DAY); te = ok & (B.t.values >= a) & (B.t.values < b)
            if tr.sum() < 300 or te.sum() < 20: continue
            m = HGB(max_iter=200, learning_rate=0.04, max_leaf_nodes=8, min_samples_leaf=40, l2_regularization=1.0, random_state=0).fit(B.loc[tr, feats].values, (y[tr] > 0).astype(int))
            ps.append(m.predict_proba(B.loc[te, feats].values)[:, 1]); ys.append(y[te])
        if ps:
            p, yy = np.concatenate(ps), np.concatenate(ys); res["learned"] = curve(p, yy); res["baseUp"] = round(float((yy > 0).mean()) * 100, 1)
        out[f"{d}d"] = res; print(d, json.dumps(res), flush=True)
    json.dump(out, open("labtest.json", "w"), indent=1)
main()
