"""Hourly panel: for every coin and hour, what was known at the START of that hour (features) and what happened
next (labels). Features only use completed earlier hours. Every feature also gets a 'p_' version: its percentile
within that coin's own last 90 days (0 = lowest, 1 = highest), so rules work across coins and years."""
import os, numpy as np, pandas as pd
from . import config as C

H = 3600
DESC = {  # feature dictionary (also shown to the AI researcher)
    "r1": "price change last 1h", "r4": "price change last 4h", "r24": "price change last 24h", "r72": "price change last 3 days", "r168": "price change last 7 days",
    "vol24": "volatility of hourly moves, last 24h", "volr": "volatility now vs its 30-day normal", "vr": "trading volume last 4h vs 7-day normal",
    "timb1": "taker buy-minus-sell share, last 1h (who is hitting the market)", "timb4": "taker buy-minus-sell share, last 4h", "timb24": "taker buy-minus-sell share, last 24h",
    "pos24": "where price sits inside its last-24h range (0 = low, 1 = high)", "oi1": "open-interest change last 1h (new leverage)", "oi4": "open-interest change last 4h",
    "oi24": "open-interest change last 24h", "oiz": "open interest vs its 30-day normal (z-score)", "glob": "long/short ratio of ALL Binance accounts (the crowd)",
    "glob24": "change of the crowd's long/short ratio over 24h", "topPos": "long/short ratio of top traders' POSITIONS", "topPos24": "change of top traders' position ratio over 24h",
    "topAcc": "long/short ratio of top traders' ACCOUNTS", "tk1": "taker buy/sell volume ratio (log), last 1h", "tk4": "taker buy/sell volume ratio (log), last 4h",
    "tr50": "price vs its 50-day average (trend)", "btc_r1": "BTC's change last 1h", "btc_r24": "BTC's change last 24h",
    "x_r24": "this coin's 24h move ranked against the other coins (0..1)", "x_r168": "7-day move ranked against the other coins", "x_glob": "crowd long/short ratio ranked against the other coins",
    "x_topPos": "top-trader positioning ranked against the other coins", "x_oi24": "24h open-interest change ranked against the other coins", "x_timb24": "24h taker buy share ranked against the other coins",
    "x_vol24": "volatility ranked against the other coins", "hour": "hour of day (UTC, 0-23)", "dow": "day of week (0 = Monday)",
    # classic signals used by open-source bots (freqtrade / hummingbot style), on hourly bars
    "rsi14": "RSI(14), 0-100 (below 30 = 'oversold', above 70 = 'overbought')", "bbz": "distance from the 20-bar average in standard deviations (Bollinger: beyond ±2 = outside the bands)",
    "macdh": "MACD histogram (12/26/9) as % of price", "emax": "EMA12 vs EMA26 gap (%)", "emat": "EMA50 vs EMA200 gap (%) (long-term trend)",
    "don20": "position inside the 20-day high/low channel (Donchian; 1 = at the 20-day high)", "adx14": "ADX(14) trend strength (above 25 = trending)",
    "cross": "EMA12/26 crossed in the last hour: +1 golden cross, -1 death cross, 0 none",
    "streak": "how many hours in a row price moved the same way (+ up, - down) - the gambler's 'hot hand / due for a reversal' question",
    # market-wide (same for every coin), from other free sources
    "fg": "Crypto Fear & Greed index (0 extreme fear - 100 extreme greed)", "fg7": "change of Fear & Greed over 7 days",
    "cbp": "Coinbase premium: BTC price on Coinbase vs Binance (%), positive = US buyers paying up", "cbp24": "Coinbase premium averaged over 24h",
    "dvol": "BTC options 'fear gauge' (Deribit DVOL, expected volatility)", "dvol24": "change of the BTC options fear gauge over 24h", "dvole": "ETH options fear gauge (DVOL)",
    "stab7": "stablecoin supply change over 7 days (%) = new money arriving", "stab30": "stablecoin supply change over 30 days (%)",
}
MARKET = ("fg", "fg7", "cbp", "cbp24", "dvol", "dvol24", "dvole", "stab7", "stab30")
PCT = [f for f in DESC if not f.startswith("x_") and f not in ("hour", "dow", "cross") and f not in MARKET]

def _ema(x, n): return x.ewm(span=n, adjust=False).mean()
def _rma(x, n): return x.ewm(alpha=1 / n, adjust=False).mean()

def _coin(coin, blocks=()):
    k = pd.read_parquet(f"{C.DATA_DIR}/raw/{coin}_k.parquet").set_index("t")
    mp = f"{C.DATA_DIR}/raw/{coin}_m.parquet"
    m = pd.read_parquet(mp).set_index("t") if os.path.exists(mp) else pd.DataFrame(index=k.index, columns=["oi", "glob", "topAcc", "topPos", "tk"])
    # live hours (relay) are stitched on ONLY where the archive has nothing yet: the archive always wins when it arrives
    lk, lm = f"{C.DATA_DIR}/live/{coin}_k.parquet", f"{C.DATA_DIR}/live/{coin}_m.parquet"
    if os.path.exists(lk):
        L = pd.read_parquet(lk).set_index("t"); L = L[L.index > k.index.max()]
        if len(L): k = pd.concat([k, L[k.columns]])
    if os.path.exists(lm) and len(m):
        L = pd.read_parquet(lm).set_index("t"); L = L[L.index > m.index.max()]
        if len(L): m = pd.concat([m, L[m.columns]])
    idx = np.arange(k.index.min(), k.index.max() + H, H)
    alive = (k.v.reindex(idx) > 0).values        # hours that really traded (missing or zero-volume hours = no market, e.g. after a delisting)
    k = k.reindex(idx); k["c"] = k.c.ffill(); [k.__setitem__(x, k[x].fillna(k.c)) for x in ("o", "h", "l")]; k[["v", "tb"]] = k[["v", "tb"]].fillna(0)
    m = m.reindex(idx)
    c = k.c; lr = np.log(c).diff()
    f = pd.DataFrame(index=idx)
    for w in (1, 4, 24, 72, 168): f[f"r{w}"] = np.log(c / c.shift(w))
    f["vol24"] = lr.rolling(24).std(); f["volr"] = f.vol24 / f.vol24.rolling(720).mean()
    f["vr"] = k.v.rolling(4).mean() / k.v.rolling(168).mean()
    for w in (1, 4, 24): f[f"timb{w}"] = 2 * k.tb.rolling(w).sum() / k.v.rolling(w).sum().replace(0, np.nan) - 1
    hi, lo = k.h.rolling(24).max(), k.l.rolling(24).min(); f["pos24"] = (c - lo) / (hi - lo).replace(0, np.nan)
    oi = m.oi.astype(float).replace(0, np.nan).ffill(limit=3)
    for w in (1, 4, 24): f[f"oi{w}"] = np.log(oi / oi.shift(w))
    f["oiz"] = (oi - oi.rolling(720).mean()) / oi.rolling(720).std()
    for col in ("glob", "topPos", "topAcc"):
        s = m[col].astype(float).ffill(limit=3); f[col] = s
        if col != "topAcc": f[col + "24"] = s - s.shift(24)
    tk = np.log(m.tk.astype(float).replace(0, np.nan)); f["tk1"] = tk; f["tk4"] = tk.rolling(4).mean()
    f["tr50"] = np.log(c / c.rolling(1200).mean())
    d = c.diff(); f["rsi14"] = 100 - 100 / (1 + _rma(d.clip(lower=0), 14) / _rma((-d).clip(lower=0), 14).replace(0, np.nan))
    f["bbz"] = (c - c.rolling(20).mean()) / c.rolling(20).std()
    e12, e26 = _ema(c, 12), _ema(c, 26); macd = e12 - e26; f["macdh"] = (macd - _ema(macd, 9)) / c * 100
    f["emax"] = (e12 / e26 - 1) * 100; f["emat"] = (_ema(c, 50) / _ema(c, 200) - 1) * 100
    s_ = np.sign(e12 - e26); f["cross"] = ((s_ > 0) & (s_.shift(1) <= 0)).astype(float) - ((s_ < 0) & (s_.shift(1) >= 0)).astype(float)
    sg = np.sign(lr).fillna(0); grp = (sg != sg.shift()).cumsum(); f["streak"] = sg * sg.groupby(grp).cumcount().add(1)
    dh, dl = k.h.rolling(480).max(), k.l.rolling(480).min(); f["don20"] = (c - dl) / (dh - dl).replace(0, np.nan)
    up, dn = k.h.diff(), -k.l.diff(); pdm = up.where((up > dn) & (up > 0), 0.0); mdm = dn.where((dn > up) & (dn > 0), 0.0)
    trr = pd.concat([k.h - k.l, (k.h - c.shift()).abs(), (k.l - c.shift()).abs()], axis=1).max(axis=1); atr = _rma(trr, 14)
    pdi, mdi = 100 * _rma(pdm, 14) / atr, 100 * _rma(mdm, 14) / atr
    f["adx14"] = _rma(100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan), 14)
    if blocks:                         # measurements invented by the AI (safe formulas, see blocks.py)
        from .blocks import compute
        env = {"o": k.o, "h": k.h, "l": k.l, "c": k.c, "v": k.v, "tb": k.tb}
        for col in ("oi", "glob", "topPos", "topAcc", "tk"): env[col] = m[col].astype(float) if col in m else pd.Series(np.nan, index=idx)
        env.update({col: f[col] for col in f.columns})
        for b in blocks:
            try: f[b["id"]] = compute(b["expr"], env)
            except Exception: f[b["id"]] = np.nan
    f = f.shift(1)                     # decision at the start of hour t uses hours ≤ t-1 only
    f["hour"] = (idx // H) % 24; f["dow"] = ((idx // 86400) + 3) % 7
    f = f.replace([np.inf, -np.inf], np.nan)
    for col in PCT + [b["id"] for b in blocks]:
        if col in f: f["p_" + col] = f[col].rolling(2160, min_periods=500).rank(pct=True)
    # labels: entry at the open of hour t
    o, h, l, cl = k.o.values, k.h.values, k.l.values, k.c.values; n = len(o)
    for name, ex in C.EXITS.items():
        if ex[0] == "ret":
            hh = ex[1]; r = np.full(n, np.nan); r[: n - hh] = cl[hh - 1: n - 1] / o[: n - hh] - 1
            f["L_" + name] = r - C.FEE; f["S_" + name] = -r - C.FEE; continue
        tp, sl, hh = ex
        for side, pre in ((1, "L_"), (-1, "S_")):
            res = np.full(n, np.nan); open_ = np.ones(n, bool); e = o
            tgt = e * (1 + side * tp); stp = e * (1 - side * sl)
            for j in range(hh):
                hj = np.roll(h, -j); lj = np.roll(l, -j)
                hitS = (lj <= stp) if side > 0 else (hj >= stp); hitT = (hj >= tgt) if side > 0 else (lj <= tgt)
                s_now = open_ & hitS; t_now = open_ & hitT & ~hitS
                res[s_now] = -sl; res[t_now] = tp; open_ &= ~(hitS | hitT)
            last = np.roll(cl, -(hh - 1)); res[open_] = side * (last[open_] / e[open_] - 1)
            res[n - hh:] = np.nan
            f[pre + name] = res - C.FEE
    dead_cols = [c for c in f.columns if c not in ("coin", "t", "px")]
    f.loc[~alive, dead_cols] = np.nan              # nothing may be learned from, or traded in, an hour with no market
    f["coin"] = coin; f["t"] = idx; f["px"] = k.o.values
    f["_hi"], f["_lo"], f["_cl"] = k.h.values, k.l.values, k.c.values      # this hour's bar: ONLY for replaying exits, never a feature
    f["_qv24"] = (k.v * k.c).rolling(24, min_periods=12).sum().shift(1).values   # dollar volume of the last 24h: ONLY for picking liquid coins
    keep64 = ("px", "_hi", "_lo", "_cl", "t")
    for col in f.columns:                          # float32 right away: ~50 coins x 6 years must fit comfortably in memory
        if col not in keep64 and f[col].dtype == "float64": f[col] = f[col].astype("float32")
    return f

def build(coins=None, log=print, blocks=()):
    coins = [c for c in (coins or C.COINS) if os.path.exists(f"{C.DATA_DIR}/raw/{c}_k.parquet")]
    parts = {}
    for c in coins:                                   # one damaged coin must never stop the whole machine
        try: parts[c] = _coin(c, blocks)
        except Exception as e: log(f"panel: {c} skipped ({type(e).__name__}: {e})")
    btc = parts.get("BTC")
    for c, f in parts.items():
        if btc is not None:
            b = btc.set_index("t")[["r1", "r24"]].reindex(f.t.values).values
            f["btc_r1"], f["btc_r24"] = b[:, 0], b[:, 1]
    P = pd.concat(parts.values(), ignore_index=True)
    P = P[P.t >= pd.Timestamp(C.START).timestamp() + 30 * 86400]
    for col in ("r24", "r168", "glob", "topPos", "oi24", "timb24", "vol24"):
        P["x_" + col] = P.groupby("t")[col].rank(pct=True)
    for col in ("btc_r1", "btc_r24"):
        if col in P: P["p_" + col] = P.groupby("coin")[col].transform(lambda s: s.rolling(2160, min_periods=500).rank(pct=True))
    try:                                               # market-wide extras (Fear & Greed, Coinbase premium, options fear, stablecoins)
        from .fetch_ext import market_frame
        btc = pd.read_parquet(f"{C.DATA_DIR}/raw/BTC_k.parquet").set_index("t").c if "BTC" in parts else None
        M = market_frame(np.sort(P.t.unique()), btc)
        if len(M.columns): P = P.merge(M, left_on="t", right_index=True, how="left")
    except Exception as e: log(f"extra features skipped: {e}")
    num = [c for c in P.select_dtypes("float64").columns if c not in ("px", "_hi", "_lo", "_cl")]; P[num] = P[num].astype("float32")
    os.makedirs(C.DATA_DIR, exist_ok=True); P.reset_index(drop=True).to_parquet(f"{C.DATA_DIR}/panel.parquet", index=False)
    log(f"panel: {len(P):,} coin-hours, {P.coin.nunique()} coins, {pd.to_datetime(P.t.min(), unit='s').date()} → {pd.to_datetime(P.t.max(), unit='s')}")
    return P
