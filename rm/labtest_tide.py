"""LAB TEST (owner, Nov 17): MARKET TYPE -> which side may trade. Walk-forward: for each past period the whole brain is retrained on data
before it and examined on the following unseen 120 days (same folds as the gate test).

Two readings of the market:
  MACRO (weeks): the existing trend reader (BTC vs its 20/50/200-day averages, slope, 7/30-day moves, breadth, S&P/Nasdaq, dollar),
                 down if <= -0.15, up if >= +0.15, otherwise mixed
  DAILY (today): three grouped witnesses, each up / down / neutral, built from data known at that hour:
                 D1 price today  - BTC 24h move, where BTC sits in its 24h range, EMA12/26 gap   (must agree)
                 D2 breadth      - share of coins up over 24h
                 D3 flow         - market-wide aggressive buying vs selling (24h), Coinbase premium (24h)
                 daily = up/down when D1 and at least one other agree; a new reading must hold 12 hours before it counts
Rules tested (one trade a day, the brain's best qualifying pick, +2% target / volatility stop, compounded at 4% risk):
  shorts: brain alone | existing gate (off when macro up) | macro down | macro down + daily down | macro not up + daily down |
          "tide only": any short candidate (no brain bar) in macro down + daily down
  longs:  brain alone | macro up | macro up + daily up | "tide only" in macro up + daily up
  both:   shorts in (macro down + daily down) and longs in (macro up + daily up)
Also: how fast the reading switched shorts OFF after falling markets ended (hours from BTC's local low to the first 'not down')."""
import json, time, numpy as np, pandas as pd
from . import config as C, features, referee as RF, compass
from .judge import Judge
from .walkforward import pol

DAY = 86400; H = 3600

def daily_state(J):
    """per BTC hour: +1 up / -1 down / 0 neutral, with a 12-hour hold. Built from real price changes (closes), and from the
    percentile columns centred on 0.5 (the p_ columns are ranks in [0, 1] against each coin's own recent history, NOT signed moves)"""
    P = J.P; b = P.coin.values == "BTC"; T = P.t.values[b]; o = np.argsort(T); T = T[o]
    g = lambda c: np.asarray(P[c].values[b][o], float) if c in P.columns else np.full(len(T), np.nan)
    sg = lambda x: np.sign(np.nan_to_num(x))
    cl = pd.Series(g("_cl"), index=T); r24 = (cl / cl.reindex(T - 24 * H).values - 1).values      # BTC's real 24h move
    d1v = (sg(r24) + sg(g("p_pos24") - 0.5) + sg(g("emax"))) / 3; d1 = np.where(d1v >= 0.34, 1, np.where(d1v <= -0.34, -1, 0))
    Q = P[["coin", "t", "_cl"]].copy(); Q = Q.sort_values(["coin", "t"])
    prev = Q.set_index(["coin", "t"])["_cl"]; key = pd.MultiIndex.from_arrays([Q.coin.values, Q.t.values - 24 * H])
    Q["r24"] = Q["_cl"].values / prev.reindex(key).values - 1
    ok = np.isfinite(Q["r24"].values)
    br = pd.Series((Q["r24"].values[ok] > 0).astype(float)).groupby(Q.t.values[ok]).mean().reindex(T).values
    d2 = np.where(br >= 0.6, 1, np.where(br <= 0.4, -1, 0))
    tb = (pd.Series(P["p_timb24"].values).groupby(P.t.values).mean().reindex(T).values - 0.5) if "p_timb24" in P.columns else np.zeros(len(T))
    d3v = (sg(tb) + sg(g("p_cbp24") - 0.5)) / 2; d3 = np.where(d3v > 0, 1, np.where(d3v < 0, -1, 0))
    raw = np.where((d1 == 1) & ((d2 == 1) | (d3 == 1)), 1, np.where((d1 == -1) & ((d2 == -1) | (d3 == -1)), -1, 0))
    st = np.zeros(len(T), int); cur = 0; cand = 0; run = 0
    for i, v in enumerate(raw):
        if v == cand: run += 1
        else: cand, run = v, 1
        if cand != cur and run >= 12: cur = cand
        st[i] = cur
    return pd.Series(st, index=T)

def main(folds=7):
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    J = Judge(features.build(blocks=blocks)); out = {"folds": []}
    orig = RF.fam_cols; RF.fam_cols = lambda P_: {k: v for k, v in orig(P_).items() if k != "invented"}
    DS = daily_state(J); MS = compass.trend_scores(J)
    end = int(J.t_end); tot = {}
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY; v0 = h0 - C.VALID_DAYS * DAY
        if time.time() - t0 > 95 * 60: print("time budget reached"); break
        J.t_end, J.hold0, J.val0 = te, h0, v0
        try: B = RF.train(J, log=lambda *a: None)
        except Exception as e: print("fold skipped:", str(e)[:100]); break
        X = RF._rows(J, h0, te - RF.HORIZON * H); S = RF.score(J, B, X); span = (te - h0) / DAY; tt = J.t[X]
        mac = MS.reindex(tt, method="ffill").fillna(0).values; day = DS.reindex(tt, method="ffill").fillna(0).values
        mdn, mup = mac <= -0.15, mac >= 0.15; ddn, dup = day == -1, day == 1
        T_, O_, Hh, Ll, Cc = J._bars("BTC"); s = pd.Series(Cc, index=T_)
        f = {"exam": f"{pd.to_datetime(h0, unit='s').date()} to {pd.to_datetime(te, unit='s').date()}", "btc": round(float(s.asof(te) / s.asof(h0) - 1) * 100, 1),
             "time": {"macroDown": round(float(mdn.mean()), 2), "macroUp": round(float(mup.mean()), 2), "bothDown": round(float((mdn & ddn).mean()), 2), "bothUp": round(float((mup & dup).mean()), 2)}}
        keep = {}
        for name, sd in B["sides"].items():
            side = 1 if name == "long" else -1; stp = RF.stop_pct(J.dvol[X]); p = np.asarray(S[name]["prob"]); be = RF.breakeven(stp)
            y, r = RF.barrier(J, X, side, RF.TARGET, stp, RF.HORIZON); fam = ~np.asarray(S[name]["unfamiliar"])
            pick = (p >= be + sd["margin"]) & fam
            V = ({"brain alone": pick, "existing gate (off when macro up)": pick & ~mup, "macro down": pick & mdn, "macro down + daily down": pick & mdn & ddn,
                  "macro not up + daily down": pick & ~mup & ddn, "tide only (no brain bar), macro+daily down": fam & mdn & ddn}
                 if name == "short" else
                 {"brain alone": pick, "macro up": pick & mup, "macro up + daily up": pick & mup & dup, "tide only (no brain bar), macro+daily up": fam & mup & dup})
            f[name] = {}
            for vn, m in V.items():
                f[name][vn] = pol(J, X[m], r[m], p[m] - be[m], stp[m], span) if m.any() else {"trades": 0}
                tot.setdefault(f"{name} | {vn}", []).append(f[name][vn].get("monthly", 0.0) if m.any() else 0.0)
            keep[name] = (V["macro down + daily down"] if name == "short" else V["macro up + daily up"], r, p - be, stp)
        m_s, r_s, e_s, s_s = keep["short"]; m_l, r_l, e_l, s_l = keep["long"]
        rows = np.r_[X[m_s], X[m_l]]
        f["both (shorts in down, longs in up)"] = pol(J, rows, np.r_[r_s[m_s], r_l[m_l]], np.r_[e_s[m_s], e_l[m_l]], np.r_[s_s[m_s], s_l[m_l]], span) if len(rows) else {"trades": 0}
        tot.setdefault("both", []).append(f["both (shorts in down, longs in up)"].get("monthly", 0.0))
        # how fast shorts were switched off after a fall ended: from each 10%+ BTC low to the first hour the macro+daily reading stopped saying 'down'
        hrs = []
        ss = s[(s.index >= h0) & (s.index < te)]
        if len(ss) > 48:
            lows = ss[(ss == ss.rolling(24 * 14, center=True, min_periods=24).min()) & (ss < ss.rolling(24 * 30, min_periods=24).max() * 0.9)].index
            for tl in lows[:10]:
                after = [t for t in ss.index if t >= tl]
                off = next((t for t in after if not (MS.asof(t) <= -0.15 and DS.asof(t) == -1)), None)
                if off is not None: hrs.append(int((off - tl) // H))
        f["hoursToSwitchShortsOffAfterLow"] = hrs
        out["folds"].append(f); print(json.dumps(f), flush=True); json.dump(out, open("labtest.json", "w"), indent=1)
    out["summary"] = {k: {"avgMonthly": round(float(np.mean(v)), 2), "positiveFolds": f"{sum(1 for x in v if x > 0)}/{len(v)}"} for k, v in tot.items()}
    out["dailyShare"] = {"up": round(float((DS == 1).mean()), 3), "down": round(float((DS == -1).mean()), 3)}
    print("DAILY share up/down:", out["dailyShare"], flush=True)
    print("SUMMARY", json.dumps(out["summary"], indent=1), flush=True)
    json.dump(out, open("labtest.json", "w"), indent=1)

if __name__ == "__main__": main()
