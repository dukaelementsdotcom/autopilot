"""LAB TEST: would a wider coin universe give the Arbiter more real Grade A setups? The CURRENT brain (unchanged) grades every hour of
its newest unseen months (its final-exam window) on (a) today's coins and (b) today's coins + 30 more liquid perpetuals. Reported:
Grade A calls per week (one per coin per 24h), how many came from the new coins, their win rate and net result per trade, the new
coins' liquidity, and whether adding coins changed the grades on today's coins (cross-coin ranks shift)."""
import json, math, numpy as np, pandas as pd
from . import config as C, features, fetch, referee as RF
from .judge import Judge
EXTRA = "GRT SAND MANA AXS GALA CHZ EGLD THETA KAVA DYDX GMX SNX COMP ENS MASK APE LRC ZIL NEO EOS XTZ JASMY CFX AR ROSE MINA 1000BONK 1000FLOKI TRB BLUR".split()
DAY = 86400; H = 3600

def graded(P, B):
    J = Judge(P); v0, h0, te = B.get("split") or [J.val0, J.hold0, J.t_end]
    rows = np.flatnonzero((J.t >= h0) & (J.t < te - RF.HORIZON * H) & np.isfinite(J.dvol) & ~P.coin.isin(C.DEAD).values)
    S = RF.score(J, B, rows); out = []
    for name, sd in B["sides"].items():
        side = 1 if name == "long" else -1; stp = RF.stop_pct(J.dvol[rows]); be = RF.breakeven(stp); s = S[name]
        g = np.array([RF.grade(s["prob"][i], {f: s["fams"][f][i] for f in s["fams"]}, sd["base"], sd["margin"], be[i], s["unfamiliar"][i]) for i in range(len(rows))], dtype=object)
        a = rows[g == "A"]
        if not len(a): continue
        y, r = RF.barrier(J, a, side, RF.TARGET, RF.stop_pct(J.dvol[a]), RF.HORIZON)
        df = pd.DataFrame({"coin": P.coin.values[a], "t": J.t[a], "y": y, "r": r, "side": name, "live": bool(sd.get("live"))}).sort_values(["coin", "t"])
        keep, last = [], {}
        for i, row in enumerate(df.itertuples()):                       # one call per coin per 24h
            if row.t - last.get(row.coin, -1e18) >= DAY: keep.append(i); last[row.coin] = row.t
        out.append(df.iloc[keep])
    weeks = max(1.0, (te - RF.HORIZON * H - h0) / (7 * DAY))
    return (pd.concat(out) if out else pd.DataFrame(columns=["coin", "t", "y", "r", "side", "live"])), weeks

def summ(df, weeks):
    if not len(df): return {"perWeek": 0}
    w = df.groupby(df.t // (6 * H)).r.mean(); tt = float(w.mean() / (w.std(ddof=1) + 1e-12) * math.sqrt(len(w))) if len(w) > 2 else 0.0
    return {"calls": int(len(df)), "perWeek": round(len(df) / weeks, 2), "win": round(float(df.y.mean()) * 100, 1), "netPerTrade": round(float(df.r.mean()) * 100, 3), "t": round(tt, 2)}

def main():
    B = RF.load(); out = {"brain": B["trainedAt"], "extra": EXTRA}
    fetch.update(coins=EXTRA, log=print)
    P0 = features.build(coins=list(C.COINS)); g0, wk = graded(P0, B); del P0
    allc = list(C.COINS) + EXTRA; P1 = features.build(coins=allc)
    liq = P1[P1.coin.isin(EXTRA)].groupby("coin")._qv24.median().sort_values(ascending=False) / 1e6
    g1, _ = graded(P1, B); del P1
    for lab_, df in (("today", g0), ("wider", g1)):
        out[lab_] = {"all": summ(df, wk), "liveSide": summ(df[df.live], wk)}
    new = g1[g1.coin.isin(EXTRA)]; old = g1[~g1.coin.isin(EXTRA)]
    out["fromNewCoins"] = {"all": summ(new, wk), "liveSide": summ(new[new.live], wk), "byCoin": new[new.live].groupby("coin").agg(n=("r", "size"), win=("y", "mean"), net=("r", "mean")).round(3).to_dict("index")}
    out["todayCoinsInWiderPanel"] = summ(old[old.live], wk)
    out["newCoinLiquidityM"] = liq.round(1).to_dict(); out["weeks"] = round(wk, 1)
    print(json.dumps(out, indent=1, default=str)); json.dump(out, open("labtest.json", "w"), indent=1, default=str)
main()
