"""BAYESIAN ONLINE CHANGEPOINT DETECTION (Adams & MacKay 2007) over the whole history, on complete 6-hour blocks of two series: Bitcoin's 6-hour return and the average market breadth (the share
of the core coins that rose). Each series has a Normal-Inverse-Gamma prior, so the predictive is Student-t; the two are treated as
independent given the run length (likelihoods multiply). A constant hazard 1/LAMBDA. The run-length posterior is truncated at RMAX.

Output per hour: P(run length <= 24h) = the posterior chance that the market's behaviour changed within the last day.
Strictly causal: the panel's r1 at hour t is the return of hour t-1 (already shifted when the panel is built), breadth likewise, and
the reading for hour t uses rows <= t only. Used ONLY by the 'bocpd-gate' contestant (a fast-track long unlock)."""
import math, numpy as np, pandas as pd
from scipy.special import gammaln

BLOCK = 6; LAMBDA = 120.0; RMAX = 240; THRESH = 0.8; SHORT_RUN = 24; MIN_CALLS = 15

def _series(J):
    P = J.P; b = P.coin.values == "BTC"; T = np.sort(np.unique(J.t))
    btc = pd.Series(P["r1"].values[b].astype(float), index=J.t[b]).reindex(T)
    up = pd.Series((P["r1"].values > 0).astype(float), index=J.t).where(np.isfinite(P["r1"].values))
    br = up.groupby(level=0).mean().reindex(T)
    D = pd.DataFrame({"b": btc.values, "r": br.values}, index=T)
    blk = (T // (BLOCK * 3600)); full = pd.Series(blk).map(pd.Series(blk).value_counts()).values == BLOCK
    G = D[full].groupby(blk[full]).agg({"b": "sum", "r": "mean"})          # complete 6-hour blocks: Bitcoin's 6h return, average breadth
    m = G.rolling(120, min_periods=8).mean().shift(1); v = G.rolling(120, min_periods=8).std().shift(1)      # past blocks only (30 days)
    Z = ((G - m) / (v + 1e-12)).clip(-4, 4).fillna(0.0)
    tb = (G.index.values + 1) * BLOCK * 3600                                # a block's reading is known when the block has ended
    return tb, Z.values

def run(J):
    """-> (hours, P(run_length <= 24h) per hour)"""
    T, X = _series(J); n, d = X.shape; H = 1.0 / LAMBDA
    mu0, k0, a0, b0 = 0.0, 1.0, 1.0, 1.0
    R = np.zeros(RMAX + 1); R[0] = 1.0
    mu = np.full((RMAX + 1, d), mu0); k = np.full((RMAX + 1, d), k0); a = np.full((RMAX + 1, d), a0); b = np.full((RMAX + 1, d), b0)
    out = np.zeros(n)
    for t in range(n):
        x = X[t]; df = 2 * a; sc2 = b * (k + 1) / (a * k)            # Student-t predictive per run length and dimension
        lp = (gammaln((df + 1) / 2) - gammaln(df / 2) - 0.5 * np.log(np.pi * df * sc2) - (df + 1) / 2 * np.log1p((x - mu) ** 2 / (df * sc2))).sum(1)
        pr = np.exp(lp - lp.max()) * R
        grow = pr * (1 - H); cp = (pr * H).sum()
        Rn = np.empty_like(R); Rn[0] = cp; Rn[1:] = grow[:-1]; Rn[-1] += grow[-1]; Rn /= Rn.sum()
        mu_n = (k * mu + x) / (k + 1); a_n = a + 0.5; b_n = b + k * (x - mu) ** 2 / (2 * (k + 1)); k_n = k + 1
        mu = np.vstack([np.full((1, d), mu0), mu_n[:-1]]); k = np.vstack([np.full((1, d), k0), k_n[:-1]])
        a = np.vstack([np.full((1, d), a0), a_n[:-1]]); b = np.vstack([np.full((1, d), b0), b_n[:-1]])
        R = Rn; out[t] = R[: SHORT_RUN // BLOCK + 1].sum()
    return T, out

def update(R, J):
    """the contestant's reading: a change detected (P >= THRESH within the last 72h), then the long side's graded calls SINCE the change
    must beat their break-even over >= MIN_CALLS calls (outcomes known 48h later) before longs unlock for this contestant"""
    T, p = run(J); now = int(np.max(J.t))
    hi = np.flatnonzero(p >= THRESH); cp_t = int(T[hi[-1]]) if len(hi) else None
    st = {"pNow": round(float(p[-1]), 3), "pMax72h": round(float(p[T >= now - 72 * 3600].max()), 3), "lastChange": cp_t, "threshold": THRESH, "unlocked": False}
    if cp_t is not None and now - cp_t <= 30 * 86400:                # a change in the last 30 days: judge the longs since then
        calls = [x for x in (R.get("ledger") or []) if x.get("side") == "long" and x.get("grade") and x.get("y") is not None and x["t"] >= cp_t]
        st["longCalls"] = len(calls)
        if calls:
            hit = float(np.mean([x["y"] for x in calls])); be = float(np.mean([x.get("be", 0.5) for x in calls]))
            st.update(longHit=round(hit, 3), longBe=round(be, 3))
            st["unlocked"] = bool(len(calls) >= MIN_CALLS and hit > be)
    R["bocpd"] = st; return st
