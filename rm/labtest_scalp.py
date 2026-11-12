"""LAB TEST: small targets at high leverage. For the current brain's graded calls on its unseen exam window (newest ~120 days):
how often does +x% come before the stop, how fast, and what does it earn after fees, compared with ALL candidates (no skill, just
the geometry of a near target and a far stop). Hourly bars cannot tell which came first when both are touched in the same hour,
so every result is given twice: 'stop first' (pessimistic) and 'target first' (optimistic); the truth lies between."""
import json, math, numpy as np, pandas as pd
from . import config as C, features, referee as RF, challengers as CH
from .judge import Judge
DAY = 86400; FEE = C.FEE

def path(J, rows, side, tgt, stp, hz, tie_target):
    e = J.O[rows]; n = len(rows); y = np.zeros(n, np.int8); ret = np.full(n, np.nan); hrs = np.full(n, np.nan); open_ = np.ones(n, bool); last = J.last[rows]
    tg = e * (1 + side * tgt); st = e * (1 - side * stp)
    for j in range(hz):
        k = rows + j; ok = open_ & (k <= last)
        if not ok.any(): break
        kk = np.where(ok, k, rows); hi, lo = J.H[kk], J.L[kk]
        sh = ok & ((lo <= st) if side > 0 else (hi >= st)); th = ok & ((hi >= tg) if side > 0 else (lo <= tg))
        win = th & ~sh if not tie_target else th; loss = sh & ~win
        ret[win] = tgt - FEE; y[win] = 1; hrs[win] = j + 1; ret[loss] = -stp[loss] - FEE; hrs[loss] = j + 1
        open_ &= ~(win | loss)
    k = np.minimum(rows + hz - 1, last); ret[open_] = side * (J.Cl[k[open_]] / e[open_] - 1) - FEE
    return y, ret, hrs

def acct(J, rows, r, p, span, margin=0.30, lev=25):
    """one trade a day (the best of the day), margin 30% of the account at 25x: what happens to the account"""
    if not len(rows): return {}
    df = pd.DataFrame({"t": J.t[rows], "d": J.t[rows] // DAY, "r": r, "p": p}).sort_values(["t", "p"], ascending=[True, False]).groupby("d").head(1)
    a = np.maximum(df.r.values * margin * lev, -margin)                      # a trade can lose at most its margin (liquidation)
    eq = np.cumprod(1 + a); dd = float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max())
    return {"trades": len(df), "win": round(float((df.r > 0).mean()) * 100, 1), "perTradeAcct": round(float(a.mean()) * 100, 2),
            "monthly": round(math.expm1(np.log1p(a).sum() / span * 30.4) * 100, 1), "maxDrop": round(dd * 100, 1), "end500": round(500 * float(eq[-1]), 0)}

def main():
    P = features.build(blocks=[]); J = Judge(P); B = RF.load(); out = {"trainedAt": B["trainedAt"]}
    v0, h0, te = B.get("split") or [J.val0, J.hold0, J.t_end]; b = te - RF.HORIZON * 3600
    X = RF._rows(J, h0, b); S = RF.score(J, B, X); span = max(1.0, (b - h0) / DAY); out["window"] = [str(pd.to_datetime(h0, unit="s").date()), str(pd.to_datetime(b, unit="s").date())]
    for name, sd in B["sides"].items():
        side = 1 if name == "long" else -1; stp0 = RF.stop_pct(J.dvol[X]); p = np.asarray(S[name]["prob"]); be = RF.breakeven(stp0)
        unf = np.asarray(S[name]["unfamiliar"]); g = (p >= be + sd["margin"]) & ~unf
        res = {"graded": int(g.sum()), "all": len(X), "rows": []}
        for stop_mult in (1.0, 1.5):
            stp = np.minimum(stp0 * stop_mult, 0.04)
            for tgt in (0.005, 0.0075, 0.01, 0.015, 0.02):
                for hz in (4, 12, 48):
                    row = {"target": tgt * 100, "stopX": stop_mult, "stopAvg": round(float(stp[g].mean()) * 100, 2), "hours": hz}
                    for tie, lab in ((False, "pess"), (True, "opt")):
                        yg, rg, hg = path(J, X[g], side, tgt, stp[g], hz, tie); ya, ra, ha = path(J, X, side, tgt, stp, hz, tie)
                        bew = (float(np.mean(stp[g])) + FEE) / (tgt + float(np.mean(stp[g])))
                        row[lab] = {"winGraded": round(float(yg.mean()) * 100, 1), "winAll": round(float(ya.mean()) * 100, 1), "breakEvenWin": round(bew * 100, 1),
                                    "netGraded": round(float(np.nanmean(rg)) * 100, 3), "netAll": round(float(np.nanmean(ra)) * 100, 3),
                                    "medHoursToWin": float(np.nanmedian(np.where(yg == 1, hg, np.nan))) if yg.any() else None,
                                    "account": acct(J, X[g], rg, p[g], span)}
                    res["rows"].append(row)
        out[name] = res
        print(name, json.dumps({k: v for k, v in res.items() if k != "rows"}), flush=True)
    json.dump(out, open("labtest.json", "w"), indent=1)
    for name in ("short", "long"):
        if name not in out: continue
        print(f"\n== {name}: graded {out[name]['graded']} of {out[name]['all']} candidates")
        for r in out[name]["rows"]:
            pe, op = r["pess"], r["opt"]
            print(f"tgt {r['target']:.2f}% stop x{r['stopX']} ({r['stopAvg']}%) {r['hours']:>2}h | win graded {pe['winGraded']}-{op['winGraded']}% all {pe['winAll']}-{op['winAll']}% need {pe['breakEvenWin']}% | net/trade graded {pe['netGraded']}..{op['netGraded']}% all {pe['netAll']}..{op['netAll']}% | 25x acct/month {pe['account'].get('monthly')}..{op['account'].get('monthly')}% maxDrop {pe['account'].get('maxDrop')}..{op['account'].get('maxDrop')}% | med h {pe['medHoursToWin']}")
main()
