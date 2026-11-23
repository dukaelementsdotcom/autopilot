"""KEY-LEVEL + HOLD-TIME STUDY (owner request Nov 23). Still no signal and no brain: raw questions on all the history we hold.
  A. Does WHERE you enter matter? Each hour, each coin: how far is the price from the highest high (for shorts) / lowest low (for longs) of the previous 24h, 72h, 168h?
     Buckets: already broke through it / right under it (0-0.5%) / near (0.5-1.5%) / far (>1.5%). Blitz-shaped trade (+1.125% price, +22.5% on margin at 20x, 12h cap, 0.1% cost).
  B. Does placing the STOP just beyond the level (0.3% past it, never wider than the volatility stop, never tighter than 0.4%) beat the volatility stop, for trades entered right at the level?
  C. How long should a trade be allowed to sit? Caps of 4 / 6 / 8 / 12 / 24h: average result per trade and per HOUR of capital occupied (what matters when only one trade runs at a time).
Past-only levels. Same-hour stop/target = stop first. usage: python -m rm.level_study  ->  rm/studies/level_study.json"""
import os, json, numpy as np, pandas as pd, datetime as dt
from . import config as C, features, referee as RF
from .judge import Judge
from .session_study import sim, TGT, MAXH, FEE, LEVX

BUCKETS = [("broke through (>0.5% beyond)", -9, -0.005), ("just beyond (0-0.5%)", -0.005, 0.0), ("right at it (0-0.5% away)", 0.0, 0.005), ("near (0.5-1.5% away)", 0.005, 0.015), ("far (>1.5% away)", 0.015, 9)]
CAPS = (4, 6, 8, 12, 24)

def stats(code, ret, bars):
    if len(code) == 0: return None
    return {"n": int(len(code)), "win%": round(100 * float((code == 1).mean()), 1), "stop%": round(100 * float((code == -1).mean()), 1), "avgAccountPct": round(100 * LEVX * float(ret.mean()), 2),
            "avgHoursOccupied": round(float(bars.mean()), 2), "accountPctPerHour": round(100 * LEVX * float(ret.sum() / max(1.0, bars.sum())), 3)}

def main():
    blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    J = Judge(features.build(blocks=blocks)); coins = sorted(set(J.coin.tolist())); print(len(coins), "coins")
    A = {s: {N: {b[0]: {"code": [], "ret": [], "bars": []} for b in BUCKETS} for N in (24, 72, 168)} for s in (1, -1)}
    B = {s: {"vol": {"code": [], "ret": [], "bars": []}, "level": {"code": [], "ret": [], "bars": []}} for s in (1, -1)}
    Cg = {s: {cap: {"all": {"code": [], "ret": [], "bars": []}, "atlevel": {"code": [], "ret": [], "bars": []}} for cap in CAPS} for s in (1, -1)}
    def put(d, c, r, b): d["code"].append(c); d["ret"].append(r); d["bars"].append(b)
    for coin in coins:
        T, O, Hh, Ll, Cc = J._bars(coin); L = len(T)
        if L < 800: continue
        idx = np.flatnonzero(J.coin == coin); t = J.t[idx]; j = np.searchsorted(T, t); ok = (j >= 170) & (j < L - 30); j, idx, t = j[ok], idx[ok], t[ok]
        ok = T[j] == t; j, idx = j[ok], idx[ok]; stp = np.asarray(RF.stop_pct(J.dvol[idx]), float); ok = np.isfinite(stp) & (stp > 0); j, stp = j[ok], stp[ok]
        if len(j) < 200: continue
        e0 = O[j]; hs, ls = pd.Series(Hh), pd.Series(Ll)
        for s in (1, -1):
            c, r, b = sim(O, Hh, Ll, Cc, j, e0, s, stp)
            at = np.zeros(len(j), bool)                                       # "right at the level" for the 72h level, used by B and C
            for N in (24, 72, 168):
                lvl = (ls.rolling(N).min().shift(1).values[j] if s > 0 else hs.rolling(N).max().shift(1).values[j])
                dist = (e0 / lvl - 1) if s > 0 else (lvl / e0 - 1)             # >0: price is that far from the level (longs: above support, shorts: below resistance); <0: already through it
                for lab, lo, hi in BUCKETS:
                    m = np.isfinite(dist) & (dist >= lo) & (dist < hi)
                    if m.any(): put(A[s][N][lab], c[m], r[m], b[m])
                if N == 72: at = np.isfinite(dist) & (dist >= 0.0) & (dist < 0.005); lvl72 = lvl
            if at.any():
                put(B[s]["vol"], c[at], r[at], b[at])
                dl = np.abs(lvl72[at] / e0[at] - 1) + 0.003; stl = np.clip(dl, 0.004, stp[at])
                c2, r2, b2 = sim(O, Hh, Ll, Cc, j[at], e0[at], s, stl); put(B[s]["level"], c2, r2, b2)
            for cap in CAPS:
                c3, r3, b3 = sim(O, Hh, Ll, Cc, j, e0, s, stp, maxh=cap); put(Cg[s][cap]["all"], c3, r3, b3)
                if at.any(): c4, r4, b4 = sim(O, Hh, Ll, Cc, j[at], e0[at], s, stp[at], maxh=cap); put(Cg[s][cap]["atlevel"], c4, r4, b4)
    cat = lambda d: stats(*[np.concatenate(d[k]) if d[k] else np.array([]) for k in ("code", "ret", "bars")])
    res = {"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "design": f"target +{TGT*100:.3f}% price, 12h cap unless stated, cost {FEE*100:.1f}%, every hour, all coins, no signal; levels = past high/low of 24/72/168h",
           "note": "LONG: distance above the recent LOW (support). SHORT: distance below the recent HIGH (resistance). 'accountPctPerHour' = result per hour of capital occupied (one trade at a time)"}
    for s, nm in ((1, "long"), (-1, "short")):
        res[nm] = {"A_byDistanceToLevel": {f"{N}h": {lab: cat(A[s][N][lab]) for lab, _, _ in BUCKETS} for N in (24, 72, 168)},
                   "B_stopPlacement(trades right at the 72h level)": {"volatilityStop": cat(B[s]["vol"]), "stopJustBeyondLevel": cat(B[s]["level"])},
                   "C_holdCap": {f"{cap}h": {"allEntries": cat(Cg[s][cap]["all"]), "rightAtLevel": cat(Cg[s][cap]["atlevel"])} for cap in CAPS}}
    os.makedirs("rm/studies", exist_ok=True); json.dump(res, open("rm/studies/level_study.json", "w"), indent=1)
    for nm in ("long", "short"):
        print(f"\n== {nm.upper()} A (72h level) =="); [print(f"  {k}: {v}") for k, v in res[nm]["A_byDistanceToLevel"]["72h"].items()]
        print(" B:", json.dumps(res[nm]["B_stopPlacement(trades right at the 72h level)"])); print(" C:", json.dumps({k: v["allEntries"] for k, v in res[nm]["C_holdCap"].items()}))
    print("Wrote rm/studies/level_study.json")

if __name__ == "__main__": main()
