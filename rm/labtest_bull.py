"""LAB TEST: a bull-market LONG edge (research only; the live system is not touched).

Protocol, fixed BEFORE looking at results:
  * settings of each idea are chosen on the DISCOVERY period (Oct 2020 - Dec 2022: one bull, one bear), then the chosen setting is
    judged UNSEEN on 2023 - Nov 2026 (two bulls, chop and the current bear), window by window
  * every idea is compared with the BASELINE "be long whenever the bull gate is on" (an edge must beat just riding the bull)
  * entries at the hour's open, one new trade a day (the strongest signal), 4% of the account at risk at the stop
  * exits: ASYMMETRIC (half at +2%, stop to break-even, the rest trails 2.5 x ATR, up to 14 days) vs the FIXED +2% / stop / 48h
  * gates: none, a causal bull SCORE (BTC above its 50d and 200d averages, 30d trend up, spot CVD positive, options fear calm), a causal
    3-state HMM on BTC (filtered probabilities only, refit each year on the past)
Signal families (long only): A spot-led accumulation, B volatility-compression breakout (Kalman velocity), C post-flush absorption."""
import json, math, time, numpy as np, pandas as pd
from . import config as C, features
from .judge import Judge

H = 3600; DAY = 86400; FEE = C.FEE; RISK = 0.04
WIN = [("bull 2020-21a", "2020-10-01", "2021-04-15"), ("bull 2021b", "2021-07-20", "2021-11-10"), ("bear 2022", "2021-11-10", "2022-12-31"),
       ("recovery 2023", "2023-01-01", "2023-09-30"), ("bull 2023-24", "2023-10-01", "2024-03-31"), ("chop 2024", "2024-04-01", "2024-09-30"),
       ("bull 2024-25", "2024-10-01", "2025-01-31"), ("mixed 2025", "2025-02-01", "2025-09-30"), ("bear 2025-26", "2025-10-01", "2026-11-15")]
TS = lambda s: int(pd.Timestamp(s, tz="UTC").timestamp())
DISC_END = TS("2023-01-01")

# ── causal helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def kalman_velocity(logp, q_l=1e-6, q_v=1e-8, r=1e-4):
    """local-linear-trend Kalman filter (scalar form); velocity at each step uses data up to that step only"""
    n = len(logp); v = np.full(n, np.nan); first = np.flatnonzero(np.isfinite(logp))
    if not len(first): return v
    L, V = float(logp[first[0]]), 0.0; p00, p01, p11 = 1.0, 0.0, 1.0
    for i in range(first[0], n):
        L, V = L + V, V                                                    # predict
        p00, p01, p11 = p00 + 2 * p01 + p11 + q_l, p01 + p11, p11 + q_v
        z = logp[i]
        if z == z:                                                         # update (skip NaN)
            S = p00 + r; k0, k1 = p00 / S, p01 / S; y = z - L
            L, V = L + k0 * y, V + k1 * y
            p00, p01, p11 = (1 - k0) * p00, (1 - k0) * p01, p11 - k1 * p01
        v[i] = V
    return v

def hmm_fit(X, K=3, iters=60, seed=0):
    """diagonal-Gaussian HMM by Baum-Welch (scaled); returns (pi, A, mu, var)"""
    rng = np.random.default_rng(seed); n, d = X.shape
    order = np.argsort(X[:, 0]); mu = np.array([X[order[int((k + 0.5) / K * n)]] for k in range(K)]); var = np.tile(X.var(0), (K, 1)) + 1e-6
    A = np.full((K, K), 0.05 / (K - 1)); np.fill_diagonal(A, 0.95); pi = np.full(K, 1 / K)
    for _ in range(iters):
        B = np.exp(-0.5 * (((X[:, None, :] - mu[None]) ** 2) / var[None]).sum(-1)) / np.sqrt((2 * np.pi * var).prod(-1))[None] + 1e-300
        al = np.zeros((n, K)); c = np.zeros(n); al[0] = pi * B[0]; c[0] = al[0].sum(); al[0] /= c[0]
        for t in range(1, n): al[t] = (al[t - 1] @ A) * B[t]; c[t] = al[t].sum(); al[t] /= c[t]
        be = np.ones((n, K))
        for t in range(n - 2, -1, -1): be[t] = (A @ (B[t + 1] * be[t + 1])) / c[t + 1]
        g = al * be; g /= g.sum(1, keepdims=True)
        xi = np.zeros((K, K))
        for t in range(n - 1): xi += np.outer(al[t], B[t + 1] * be[t + 1]) * A / c[t + 1]
        pi = g[0]; A = xi / xi.sum(1, keepdims=True); w = g.sum(0)
        mu = (g.T @ X) / w[:, None]; var = (g.T @ X ** 2) / w[:, None] - mu ** 2 + 1e-6
    return pi, A, mu, var

def hmm_filter(X, prm):
    pi, A, mu, var = prm
    B = np.exp(-0.5 * (((X[:, None, :] - mu[None]) ** 2) / var[None]).sum(-1)) / np.sqrt((2 * np.pi * var).prod(-1))[None] + 1e-300
    al = np.zeros((len(X), len(pi))); a = pi * B[0]; al[0] = a / a.sum()
    for t in range(1, len(X)): a = (al[t - 1] @ A) * B[t]; al[t] = a / a.sum()
    return al

# ── exits ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def exit_long(J, i, stp, trail, maxh, asym=True):
    e = J.O[i]; stop = e * (1 - stp); tgt = e * 1.02; half = False; best = e; last = J.last[i]; r_half = 0.0
    for j in range(i, min(last + 1, i + maxh)):
        h, l, c = J.H[j], J.L[j], J.Cl[j]
        if l <= stop:
            px = stop; break
        if not half and h >= tgt:
            if not asym: return 0.02 - FEE, j - i + 1
            half = True; r_half = 0.5 * 0.02; stop = max(stop, e * (1 + 2 * FEE))
        if half:
            best = max(best, h); stop = max(stop, best * (1 - trail))
        px = c
    ret = (r_half + 0.5 * (px / e - 1)) if half else (px / e - 1)
    return ret - FEE, j - i + 1

def sim(trades, days):
    if not len(trades): return {"trades": 0}
    r = np.array([x[1] for x in trades]); s = np.array([x[2] for x in trades])
    a = np.maximum(RISK * r / s, -0.9); eq = np.cumprod(1 + a); dd = float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max())
    return {"trades": len(r), "win": round(float((r > 0).mean()) * 100, 1), "avg": round(float(r.mean()) * 100, 2), "best": round(float(r.max()) * 100, 1),
            "monthly": round(math.expm1(np.log1p(a).sum() / max(days, 1) * 30.4) * 100, 2), "maxDD": round(dd * 100, 1)}

def main():
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    P = features.build(blocks=blocks); J = Judge(P); n = len(P); out = {"windows": WIN}
    coin = P.coin.values; t = P.t.values
    # Kalman velocity per coin (on the previous bar's close: causal), scaled by the coin's hourly volatility
    kv = np.full(n, np.nan)
    st_ = sorted(a_ for a_, _ in J._blk.values()); en_ = st_[1:] + [n]
    for a_, b_ in zip(st_, en_):
        lp = np.log(np.r_[np.nan, J.Cl[a_:b_ - 1]]); kv[a_:b_] = kalman_velocity(lp)
    kvz = kv / (J.dvol / np.sqrt(24))
    # ── market gates (BTC, causal) ──
    bt = P.loc[coin == "BTC", ["t", "_cl"]].set_index("t")._cl.astype(float)
    prev = bt.shift(1)
    sma50, sma200 = prev.rolling(50 * 24, min_periods=40 * 24).mean(), prev.rolling(200 * 24, min_periods=150 * 24).mean()
    r30 = np.log(prev / prev.shift(30 * 24))
    cvdm = pd.Series(P.groupby("t").w_c_div24.mean() if "w_c_div24" in P else np.nan).rolling(168, min_periods=48).mean()
    dv = P.loc[coin == "BTC"].set_index("t")["p_dvol"] if "p_dvol" in P else pd.Series(np.nan, index=bt.index)
    score = ((prev > sma50).astype(int) + (sma50 > sma200).astype(int) + (r30 > 0).astype(int) + (cvdm.reindex(bt.index) > 0).astype(int) + (dv.reindex(bt.index) < 0.7).astype(int))
    gate_score = (score >= 4).reindex(t).fillna(False).values
    # HMM on BTC daily (return, log realized vol), refit each January on the past only, filtered forward
    dl = pd.DataFrame({"c": prev}); dl["day"] = dl.index // DAY; daily = dl.groupby("day").c.last().dropna()
    ret = np.log(daily).diff(); rv = np.log(ret.rolling(7).std()); Xd = pd.concat([ret, rv], axis=1).dropna(); Xd.columns = ["r", "v"]
    bullp = pd.Series(np.nan, index=Xd.index); hmm_info = {}
    for yr in range(2021, 2027):
        y0 = TS(f"{yr}-01-01") // DAY; tr = Xd[Xd.index < y0]
        if len(tr) < 300: continue
        mu_, sd_ = tr.mean(), tr.std(); prm = hmm_fit(((tr - mu_) / sd_).values)
        allp = hmm_filter(((Xd[Xd.index < y0 + 366] - mu_) / sd_).values, prm)           # filter runs through the past into this year
        k = int(np.argmax(prm[2][:, 0])); seg = Xd.index[Xd.index < y0 + 366]
        s = pd.Series(allp[:, k], index=seg); m_ = (s.index >= y0); bullp[s.index[m_]] = s[m_].values
        hmm_info[yr] = {"bullStateMeanRet%/day": round(float(prm[2][k, 0] * sd_["r"] + mu_["r"]) * 100, 3)}
    # a day's state is known only after the day: shift one day
    hb = bullp.copy(); hb.index = (hb.index + 1) * DAY
    gate_hmm = (pd.Series(hb).reindex(np.union1d(hb.index, np.unique(t))).ffill(limit=30).reindex(t) >= 0.6).fillna(False).values
    out["hmm"] = hmm_info
    gates = {"none": np.ones(n, bool), "score": gate_score, "hmm": gate_hmm}
    out["gateOnShare"] = {g: {w: round(float(v[(t >= TS(a)) & (t < TS(b)) & (coin == "BTC")].mean()), 2) for w, a, b in WIN} for g, v in gates.items() if g != "none"}
    print("gate on-share by window", json.dumps(out["gateOnShare"]), flush=True)
    # ── candidate rows ──
    base = (t % (3 * H) == 0) & np.isfinite(J.dvol) & np.isfinite(J.O) & ~P.coin.isin(C.DEAD).values
    g = lambda c: P[c].values if c in P else np.full(n, np.nan)
    rk = lambda c: P.groupby("coin")[c].transform(lambda s: s.rolling(720, min_periods=240).rank(pct=True)).values if c in P else np.full(n, np.nan)
    div72r = rk("w_c_div72"); cbp = g("p_cbp24"); basis = g("p_basis"); volr = g("p_volr"); pdv = g("p_dvol"); oi24 = g("p_oi24")
    r24 = g("r24"); lq = g("w_l_long1"); div24 = g("w_c_div24")
    fam = {}
    for q in (0.7, 0.8):
        for bmax in (0.5, 0.7):
            fam[f"A spot-led (cvd>={q}, cbp up, basis<={bmax})"] = ("A", (div72r >= q) & (cbp >= 0.5) & (basis <= bmax), div72r)
    for vmax in (0.3, 0.5):
        fam[f"B compression breakout (volr<={vmax}, dvol<=0.5, OI up, velocity up)"] = ("B", (volr <= vmax) & (pdv <= 0.5) & (oi24 >= 0.6) & (kvz > 0.5) & (r24 > 0), kvz)
    for lz in (1.0, 1.5):
        fam[f"C post-flush (long liq z>={lz}, spot absorbing, down >=1 daily move)"] = ("C", (lq >= lz) & (div24 > 0) & (r24 <= -J.dvol), lq)
    fam["BASELINE: any liquid coin (strongest 7d trend)"] = ("Z", np.ones(n, bool), g("r168"))
    stp = np.clip(1.25 * J.dvol, 0.015, 0.04); trail = 2.5 * 1.25 * J.dvol
    res = []
    for name, (fk, sig, strength) in fam.items():
        for gname, gv in gates.items():
            m = base & np.nan_to_num(sig, nan=0).astype(bool) & gv
            rows = np.flatnonzero(m)
            if not len(rows): continue
            df = pd.DataFrame({"i": rows, "d": t[rows] // DAY, "s": np.nan_to_num(strength[rows], nan=-9)}).sort_values(["d", "s"], ascending=[True, False]).groupby("d").head(1)
            for ex in ("asym", "fixed"):
                tr = []
                for i in df.i.values:
                    r_, hrs = exit_long(J, int(i), float(stp[i]), float(trail[i]), 14 * 24 if ex == "asym" else 48, asym=(ex == "asym"))
                    tr.append((int(t[i]), r_, float(stp[i])))
                rec = {"idea": name, "family": fk, "gate": gname, "exit": ex, "windows": {}}
                for w, a, b in WIN:
                    sel = [x for x in tr if TS(a) <= x[0] < TS(b)]; rec["windows"][w] = sim(sel, (TS(b) - TS(a)) / DAY)
                disc = [x for x in tr if x[0] < DISC_END]; rec["discovery"] = sim(disc, (DISC_END - TS("2020-10-01")) / DAY)
                test = [x for x in tr if x[0] >= DISC_END]; rec["test"] = sim(test, (TS("2026-11-15") - DISC_END) / DAY)
                res.append(rec)
        print(f"{name}: done ({time.time() - t0:.0f}s)", flush=True)
    # pick, per family x gate x exit, the setting with the best DISCOVERY month (>= 20 trades); report its unseen windows
    pick = {}
    for r in res:
        k = (r["family"], r["gate"], r["exit"]); dsc = r["discovery"]
        if dsc.get("trades", 0) >= 20 and (k not in pick or dsc["monthly"] > pick[k]["discovery"]["monthly"]): pick[k] = r
    out["chosen"] = list(pick.values()); out["all"] = res
    for r in sorted(pick.values(), key=lambda r: (r["family"], r["gate"], r["exit"])):
        line = " | ".join(f"{w.split()[0][:4]}{w.split()[1][-5:]}: {v.get('monthly', 0):+.1f}%/{v.get('trades', 0)}" for w, v in r["windows"].items())
        print(f"PICK {r['idea']} [{r['gate']}, {r['exit']}] disc {r['discovery'].get('monthly')}%/m | TEST {r['test']} || {line}", flush=True)
    json.dump(out, open("labtest.json", "w"), indent=1, default=str)

main()
