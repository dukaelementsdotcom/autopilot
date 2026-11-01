"""Gambling lab: the same trades, different betting systems. Casinos know the betting system cannot change the odds of each bet;
it only changes how fast you grow and how likely you are to go broke. This lab shows that on the machine's own best rule.
Each system starts with $500, 10x leverage, one trade at a time, trades in the real order AND in 500 shuffled orders (luck of the order)."""
import numpy as np

LEV = 10; START = 500.0; RUIN = 50.0

def _run(rets, system, base=0.10):
    eq = START; streak = 0; last_win = None; f = base; peak = START; dd = 0.0; taken = 0
    kelly = None
    if system == "half-Kelly":
        R = np.array(rets) * LEV; v = R.var()
        kelly = float(np.clip(0.5 * R.mean() / v, 0.0, 0.4)) if v > 0 else 0.0
    for r in rets:
        if eq < RUIN: break
        if system == "fixed 10%": f = base
        elif system == "half-Kelly": f = kelly
        elif system == "martingale (double after a loss)": f = min(base / 2 * 2 ** max(0, -streak), 0.4)
        elif system == "anti-martingale (double after a win)": f = min(base / 2 * 2 ** max(0, streak), 0.4)
        elif system == "skip after 2 losses (cold hand)":
            if streak <= -2: streak = 0; continue
            f = base
        elif system == "only after a win (hot hand)":
            if last_win is False: last_win = None; continue
            f = base
        margin = eq * f
        if margin <= 0: continue
        pnl = max(-margin, margin * LEV * r); eq += pnl; taken += 1
        win = pnl > 0; last_win = win
        streak = (streak + 1 if streak >= 0 else 1) if win else (streak - 1 if streak <= 0 else -1)
        peak = max(peak, eq); dd = max(dd, 1 - eq / peak)
    return eq, dd, taken

SYSTEMS = ["fixed 10%", "half-Kelly", "martingale (double after a loss)", "anti-martingale (double after a win)",
           "skip after 2 losses (cold hand)", "only after a win (hot hand)"]

def run(rets, reps=500, seed=7):
    rets = [float(x) for x in rets if x == x]
    if len(rets) < 20: return None
    rng = np.random.default_rng(seed); out = []
    for s in SYSTEMS:
        real, dd, n = _run(rets, s)
        sims = [_run(list(rng.permutation(rets)), s) for _ in range(reps)]
        fin = np.array([x[0] for x in sims]); dds = np.array([x[1] for x in sims])
        out.append({"system": s, "real": round(real, 2), "realDD": round(dd * 100, 1), "trades": n,
                    "median": round(float(np.median(fin)), 2), "bad5": round(float(np.percentile(fin, 5)), 2), "good95": round(float(np.percentile(fin, 95)), 2),
                    "ruin": round(float((fin < RUIN).mean() * 100), 1), "medDD": round(float(np.median(dds)) * 100, 1)})
    return {"n": len(rets), "rows": out}
