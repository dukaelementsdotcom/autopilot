"""REGIME COMPOSITE BACKTEST (owner request Nov 27): does the macro/micro vote (rm/regime_vote.py) predict anything, on all the history we hold?
For EVERY past hour the composite is recomputed with only what was known then:
  - each factor's vote  = tanh(0.65 * polarity * robust z of its level vs its own trailing 90 days)        (the same recipe as rm/regime_vote.py)
  - each factor's weight = its trailing 30-day correlation with the 24h-forward BTC return, measured on a window that ENDS 24h ago (so the outcome was already known)
Four versions race, plus two plain baselines:
  ic_weighted   the owner's recipe (weight = max(IC, 0.01)^1.6, polarity assumed)
  equal         every factor one equal vote (does the IC weighting add anything, or is it noise-chasing?)
  signed_ic     weight = IC, may be NEGATIVE (does not trust the assumed polarity)
  btc_50d       baseline: BTC above its 50-day average = bullish
  trend_7d      baseline: BTC 7-day return sign
Questions answered: correlation with the next 24h / 48h BTC move and with the average coin's next 24h; the five score-buckets and what happened after each; the direction rule (long when bullish, short when bearish);
stability across 4 time slices. Scores are bucketed by their rank against their own PAST 90 days only.
usage: python -m rm.regime_backtest  ->  rm/studies/regime_backtest.json"""
import os, json, numpy as np, pandas as pd, datetime as dt
from . import config as C, features
from .judge import Judge
from .regime_vote import FACTOR_CATALOG

H = 3600; DAY = 24; WIN_IC = 720; WIN_Z = 2160

def _daily(a, b):                         # one sample per day (every 24th hour) so overlapping outcomes do not fake a big sample
    return a[::24], b[::24]

def corr_stats(score, fwd):
    ok = np.isfinite(score) & np.isfinite(fwd); s, f = score[ok], fwd[ok]
    if len(s) < 200: return None
    sd, fd = _daily(s, f); n = len(sd)
    c_h = float(pd.Series(s).corr(pd.Series(f), method="spearman")); c_d = float(pd.Series(sd).corr(pd.Series(fd), method="spearman")) if n > 30 else None
    se = 1 / np.sqrt(max(n - 3, 1))
    return {"hours": int(len(s)), "days": int(n), "spearmanHourly": round(c_h, 4), "spearmanDaily": None if c_d is None else round(c_d, 4), "ci95Daily(+-)": round(1.96 * se, 3)}

def quintiles(score, fwd):
    s = pd.Series(score); rk = s.rolling(WIN_Z, min_periods=720).rank(pct=True)          # rank against its own PAST 90 days only
    out = {}
    for lab, lo, hi in (("Q1 most bearish", 0, .2), ("Q2", .2, .4), ("Q3", .4, .6), ("Q4", .6, .8), ("Q5 most bullish", .8, 1.01)):
        m = ((rk >= lo) & (rk < hi) & np.isfinite(fwd)).values
        if m.sum() < 100: continue
        f = fwd[m]; out[lab] = {"hours": int(m.sum()), "avgNext24hPct": round(100 * float(f.mean()), 3), "up%": round(100 * float((f > 0).mean()), 1)}
    return out

def direction(score, fwd, thr=0.0):
    ok = np.isfinite(score) & np.isfinite(fwd); s, f = score[ok], fwd[ok]
    pos = np.where(s > thr, 1.0, np.where(s < -thr, -1.0, 0.0)); sd, fd = pos[::24], f[::24]; r = sd * fd; r = r[sd != 0]
    if len(r) < 30: return None
    return {"days": int(len(r)), "avgPerDayPct": round(100 * float(r.mean()), 3), "winDays%": round(100 * float((r > 0).mean()), 1), "tStat": round(float(r.mean() / (r.std() / np.sqrt(len(r)))), 2),
            "buyAndHoldAvgPerDayPct": round(100 * float(np.nanmean(fd)), 3)}

def slices(score, fwd, k=4):
    n = len(score); out = []
    for i in range(k):
        a, b = i * n // k, (i + 1) * n // k; cs = corr_stats(score[a:b], fwd[a:b]); out.append(None if cs is None else cs["spearmanDaily"])
    return out

def main():
    blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    J = Judge(features.build(blocks=blocks)); P = J.P
    btc = P[P.coin == "BTC"].sort_values("t").drop_duplicates("t")
    t0, t1 = int(btc.t.min()), int(btc.t.max()); grid = np.arange(t0, t1 + 1, H)
    btc = btc.set_index("t").reindex(grid)
    T, O, Hh, Ll, Cc = J._bars("BTC"); close = pd.Series(Cc, index=T).reindex(grid).ffill()
    r24 = btc["r24"]; fwd24 = r24.shift(-24).values; fwd48 = ((1 + r24.shift(-24)) * (1 + r24.shift(-48)) - 1).values
    mk = P.groupby("t")["r24"].mean().reindex(grid); fwd_mkt = mk.shift(-24).values
    fwd24s = pd.Series(fwd24, index=grid)
    print(len(grid), "hours,", len(FACTOR_CATALOG), "catalog factors")
    votes, ics, cats, names = {}, {}, {}, {}
    for col, name, cat, cluster, pol in FACTOR_CATALOG:
        if col not in btc.columns: continue
        x = pd.to_numeric(btc[col], errors="coerce")
        if x.notna().sum() < 600: continue
        med = x.rolling(WIN_Z, min_periods=240).median(); mad = (x - med).abs().rolling(WIN_Z, min_periods=240).median() * 1.4826
        v = np.tanh(0.65 * pol * (x - med) / mad.clip(lower=1e-6)); v[x.isna()] = np.nan
        ic = v.rolling(WIN_IC, min_periods=300).corr(fwd24s).shift(24)                 # window ends 24h ago: the 24h outcome was already known
        votes[col] = v.values; ics[col] = ic.values; cats[col] = cat; names[col] = name
    cols = list(votes); V = np.vstack([votes[c] for c in cols]).T; IC = np.vstack([ics[c] for c in cols]).T
    macro = np.array([cats[c] == "macro" for c in cols]); print(len(cols), "factors usable")
    def comp(W):
        out = {}
        for nm, m in (("macro", macro), ("micro", ~macro)):
            v, w = V[:, m], W[:, m]; ok = np.isfinite(v) & np.isfinite(w); num = np.where(ok, v * w, 0).sum(1); den = np.where(ok, np.abs(w), 0).sum(1)
            out[nm] = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
        c = 0.45 * np.where(np.isfinite(out["macro"]), out["macro"], 0) + 0.55 * np.where(np.isfinite(out["micro"]), out["micro"], 0)
        c[~(np.isfinite(out["macro"]) | np.isfinite(out["micro"]))] = np.nan; return c, out
    W_ic = np.where(np.isfinite(IC), np.maximum(0.01, IC), np.nan) ** 1.6
    W_eq = np.where(np.isfinite(V), 1.0, np.nan); W_sg = np.where(np.isfinite(IC), IC, np.nan)
    scores = {}
    scores["ic_weighted"], parts = comp(W_ic); scores["ic_weighted_macroOnly"] = parts["macro"]; scores["ic_weighted_microOnly"] = parts["micro"]
    scores["equal"], _ = comp(W_eq); scores["signed_ic"], _ = comp(W_sg)
    sma = close.rolling(1200, min_periods=600).mean(); scores["btc_50d"] = np.sign((close / sma - 1).values)
    scores["trend_7d"] = np.sign(close.pct_change(168).values)
    nfac = np.isfinite(V).sum(1); start = int(np.argmax(nfac >= 15)) + WIN_IC + 24
    res = {"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "hours": int(len(grid) - start), "from": dt.datetime.utcfromtimestamp(int(grid[start])).strftime("%Y-%m-%d"), "to": dt.datetime.utcfromtimestamp(int(grid[-1])).strftime("%Y-%m-%d"),
           "factorsUsable": len(cols), "note": "Spearman on one sample per day; 'ci95Daily(+-)' is the noise band: a correlation inside it is indistinguishable from zero. Scores use only past information.", "versions": {}}
    for nm, sc in scores.items():
        s = sc[start:]
        res["versions"][nm] = {"vsBTCnext24h": corr_stats(s, fwd24[start:]), "vsBTCnext48h": corr_stats(s, fwd48[start:]), "vsAvgCoinNext24h": corr_stats(s, fwd_mkt[start:]),
                               "buckets": quintiles(s, fwd24[start:]), "directionRule(BTC next 24h)": direction(s, fwd24[start:]), "stabilityBy4slices(spearmanDaily)": slices(s, fwd24[start:])}
    last = {c: {"vote": round(float(votes[c][-1]), 3) if np.isfinite(votes[c][-1]) else None, "ic": round(float(ics[c][-1]), 3) if np.isfinite(ics[c][-1]) else None} for c in cols}
    res["weightsNow(top10 by IC)"] = sorted([{"factor": names[c], **last[c]} for c in cols if last[c]["ic"] is not None], key=lambda r: -abs(r["ic"]))[:10]
    os.makedirs("rm/studies", exist_ok=True); json.dump(res, open("rm/studies/regime_backtest.json", "w"), indent=1)
    print(f"{res['from']} -> {res['to']}  ({res['hours']} hours)")
    for nm, v in res["versions"].items():
        a = v["vsBTCnext24h"] or {}; d = v["directionRule(BTC next 24h)"] or {}
        print(f"{nm:24s} corr24h(daily) {a.get('spearmanDaily')} +-{a.get('ci95Daily(+-)')}  | direction avg/day {d.get('avgPerDayPct')}% t={d.get('tStat')} (hold {d.get('buyAndHoldAvgPerDayPct')}%) | slices {v['stabilityBy4slices(spearmanDaily)']}")
    print("buckets (ic_weighted):", json.dumps(res["versions"]["ic_weighted"]["buckets"]))
    print("Wrote rm/studies/regime_backtest.json")

if __name__ == "__main__": main()
