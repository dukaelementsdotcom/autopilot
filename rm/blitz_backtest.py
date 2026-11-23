"""BLITZ BACKTEST ON THE ARBITER'S OWN DECISIONS (owner's process, Nov 23): before a contestant races live, replay it on past data the brain never saw.
For each of several past windows it retrains the brain on data before the window (as the walk-forward audit does), takes the trades the ARBITER would have
accepted (its real entry rule: odds above break-even plus margin, not unfamiliar, at least two witnesses for, at most one against), and runs the blitz account on them:
35% of the account as margin at 20x (7x position), the Arbiter's volatility stop, full exit at +1.125% price (+22.5% on margin), 12h cap, 0.1% cost, ONE trade at a time,
the best call each time the account is free, compounding. It also runs the same account on the LOOSE rule (what the first live blitz used: any call above break-even).
Same-hour stop and target = stop first. usage: python -m rm.blitz_backtest [folds]  ->  rm/studies/blitz_backtest.json"""
import json, math, time, sys, os, numpy as np, pandas as pd
from . import config as C, features, referee as RF, challengers as CH
from .judge import Judge
from .session_study import sim, LEVX

DAY = 86400; H = 3600; MAXH = 12

def account(J, cands, span_days, bars_cache):
    eq = 1.0; free_at = 0; peak = 1.0; dd = 0.0; trades = []; streak = 0; worst = 0
    for t, g in cands.groupby("t", sort=True):
        if t < free_at: continue
        b = g.sort_values("edge", ascending=False).iloc[0]; coin = str(b.coin)
        if coin not in bars_cache: bars_cache[coin] = J._bars(coin)
        T, O, Hh, Ll, Cc = bars_cache[coin]; j = int(np.searchsorted(T, t))
        if j >= len(T) - MAXH or T[j] != t: continue
        c, r, n = sim(O, Hh, Ll, Cc, np.array([j]), np.array([O[j]]), int(b.side), np.array([b.stp]), maxh=MAXH)
        ret = float(r[0]); eq *= (1 + LEVX * ret); free_at = t + int(n[0]) * H; peak = max(peak, eq); dd = max(dd, 1 - eq / peak)
        streak = streak + 1 if c[0] == -1 else 0; worst = max(worst, streak); trades.append((int(c[0]), ret, int(n[0])))
    n = len(trades)
    if not n: return {"trades": 0}
    code = np.array([x[0] for x in trades])
    return {"trades": n, "perWeek": round(n / span_days * 7, 1), "win%": round(100 * float((code == 1).mean()), 1), "stop%": round(100 * float((code == -1).mean()), 1),
            "monthly%": round(100 * (eq ** (30.4 / span_days) - 1), 1), "endEquityX": round(eq, 3), "maxDrawdown%": round(100 * dd, 1), "worstStopStreak": worst,
            "avgHours": round(float(np.mean([x[2] for x in trades])), 1)}

def fold_candidates(J, B, a, b):
    X = RF._rows(J, a, b); S = RF.score(J, B, X); out = {"live": [], "loose": []}
    for name, sd in B["sides"].items():
        side = 1 if name == "long" else -1; stp = np.asarray(RF.stop_pct(J.dvol[X]), float); p = np.asarray(S[name]["prob"]); be = RF.breakeven(stp)
        sup, ag = CH._support(S[name]["fams"], sd["base"]); unf = np.asarray(S[name]["unfamiliar"])
        loose = (p >= be + sd["margin"]) & ~unf; live = loose & (sup >= 2) & (ag <= 1)
        for key, m in (("live", live), ("loose", loose)):
            ok = m & np.isfinite(stp)
            out[key].append(pd.DataFrame({"t": J.t[X][ok], "coin": J.coin[X][ok], "side": side, "edge": (p - be)[ok], "stp": stp[ok], "sideName": name}))
    return {k: (pd.concat(v) if v else pd.DataFrame()) for k, v in out.items()}

def main(folds=6):
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception as e: print("no memory:", e)
    J = Judge(features.build(blocks=blocks)); orig = RF.fam_cols; RF.fam_cols = lambda P_: {k: v for k, v in orig(P_).items() if k != "invented"}
    end = int(J.t_end); out = {"at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "design": "35% margin x 20x, Arbiter volatility stop, +1.125% price full exit, 12h cap, 0.1% cost, one trade at a time, compounding", "folds": []}
    cache = {}
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY; v0 = h0 - C.VALID_DAYS * DAY
        if v0 - int(J.t.min()) < 500 * DAY: break
        if time.time() - t0 > 150 * 60: print("time budget reached"); break
        J.t_end, J.hold0, J.val0 = te, h0, v0
        try: Bk = RF.train(J, log=lambda *a: None); cands = fold_candidates(J, Bk, h0, te - RF.HORIZON * H)
        except Exception as e: print("fold skipped:", str(e)[:120]); break
        span = (te - h0) / DAY; f = {"window": f"{pd.to_datetime(h0, unit='s').date()} to {pd.to_datetime(te, unit='s').date()}"}
        try: T, O, Hh, Ll, Cc = J._bars("BTC"); s = pd.Series(Cc, index=T); f["btcChange%"] = round(float(s.asof(te) / s.asof(h0) - 1) * 100, 1)
        except Exception: pass
        for key in ("live", "loose"):
            d = cands[key]; f[key] = account(J, d, span, cache) if len(d) else {"trades": 0}
            if len(d):
                for nm in ("long", "short"):
                    dd_ = d[d.sideName == nm]; f[key][f"{nm}Candidates"] = int(len(dd_))
        out["folds"].append(f); print(json.dumps(f), flush=True)
    for key in ("live", "loose"):
        fs = [f[key] for f in out["folds"] if f.get(key, {}).get("trades")]
        if fs: out[f"summary_{key}"] = {"foldsWithTrades": len(fs), "foldsPositive": sum(1 for x in fs if x["monthly%"] > 0), "avgMonthly%": round(float(np.mean([x["monthly%"] for x in fs])), 1),
                                          "worstDrawdown%": max(x["maxDrawdown%"] for x in fs), "avgWin%": round(float(np.mean([x["win%"] for x in fs])), 1), "totalTrades": sum(x["trades"] for x in fs)}
    os.makedirs("rm/studies", exist_ok=True); json.dump(out, open("rm/studies/blitz_backtest.json", "w"), indent=1)
    print(json.dumps({k: out.get(k) for k in ("summary_live", "summary_loose")})); print("done in", round((time.time() - t0) / 60), "min")

if __name__ == "__main__": main(int(sys.argv[1]) if len(sys.argv) > 1 else 6)
