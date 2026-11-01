"""Extra free data the machine can 'see' beyond Binance (all public, no keys). Each source is optional:
if one fails, the machine simply runs without it that day.
  fng   : Crypto Fear & Greed index (daily, alternative.me)
  cb    : Coinbase BTC-USD hourly candles -> the 'Coinbase premium' (are US buyers paying more than the rest?)
  dvol  : Deribit DVOL (the options market's expected volatility = 'fear gauge') for BTC and ETH, hourly
  stab  : total stablecoin supply (new money arriving in crypto), daily (DefiLlama)"""
import os, time, datetime as dt
import pandas as pd, requests
from . import config as C

S = requests.Session(); S.headers["User-Agent"] = "research-machine/1.0"
EXT = lambda name: f"{C.DATA_DIR}/ext/{name}.parquet"

def _save(name, df):
    p = EXT(name)
    if os.path.exists(p): df = pd.concat([pd.read_parquet(p), df])
    df = df.drop_duplicates("t", keep="last").sort_values("t").reset_index(drop=True); df.to_parquet(p, index=False); return len(df)

def _last(name):
    p = EXT(name)
    return int(pd.read_parquet(p).t.max()) if os.path.exists(p) else None

def fng():
    r = S.get("https://api.alternative.me/fng/?limit=0&format=json", timeout=60).json()["data"]
    return _save("fng", pd.DataFrame({"t": [int(x["timestamp"]) for x in r], "fg": [float(x["value"]) for x in r]}))

def coinbase(product="BTC-USD", name="cb_BTC"):
    start = _last(name) or int(pd.Timestamp(C.START).timestamp()); now = int(time.time()); rows = []
    t = start
    while t < now - 3600:
        e = min(t + 300 * 3600, now)
        for _ in range(3):
            try:
                r = S.get(f"https://api.exchange.coinbase.com/products/{product}/candles", timeout=30,
                          params={"granularity": 3600, "start": dt.datetime.utcfromtimestamp(t).isoformat() + "Z", "end": dt.datetime.utcfromtimestamp(e).isoformat() + "Z"})
                if r.status_code == 429: time.sleep(2); continue
                rows += r.json() if r.ok else []; break
            except Exception: time.sleep(1)
        t = e; time.sleep(0.15)
    if not rows: return 0
    df = pd.DataFrame(rows, columns=["t", "l", "h", "o", "c", "v"])[["t", "c"]].astype({"t": "int64", "c": float})
    return _save(name, df)

def dvol(cur="BTC"):
    name = f"dvol_{cur}"; start = _last(name) or int(pd.Timestamp("2021-03-01").timestamp()); now = int(time.time()); rows = []
    t = start
    while t < now:
        e = min(t + 40 * 86400, now)
        try:
            r = S.get("https://www.deribit.com/api/v2/public/get_volatility_index_data", timeout=30,
                      params={"currency": cur, "start_timestamp": t * 1000, "end_timestamp": e * 1000, "resolution": 3600}).json()
            rows += r.get("result", {}).get("data", [])
        except Exception: pass
        t = e; time.sleep(0.1)
    if not rows: return 0
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c"]); df["t"] = (df.t // 1000).astype("int64")
    return _save(name, df[["t", "c"]].rename(columns={"c": "dvol"}))

def stablecoins():
    r = S.get("https://stablecoins.llama.fi/stablecoincharts/all", timeout=60).json()
    return _save("stab", pd.DataFrame({"t": [int(x["date"]) for x in r], "stab": [float((x.get("totalCirculatingUSD") or {}).get("peggedUSD") or 0) for x in r]}))

def update(log=print):
    os.makedirs(f"{C.DATA_DIR}/ext", exist_ok=True)
    for name, fn in (("fear & greed", fng), ("coinbase BTC", coinbase), ("deribit DVOL BTC", lambda: dvol("BTC")), ("deribit DVOL ETH", lambda: dvol("ETH")), ("stablecoins", stablecoins)):
        try: log(f"extra data: {name}: {fn()} rows")
        except Exception as e: log(f"extra data: {name} skipped ({str(e)[:80]})")

def market_frame(idx, btc_close):
    """hourly, market-wide features; everything shifted so the value at hour t only uses information known before t"""
    M = pd.DataFrame(index=idx)
    def daily(name, col):                     # a daily value becomes known only after that day ends
        p = EXT(name)
        if not os.path.exists(p): return None
        d = pd.read_parquet(p); s = pd.Series(d[col].values, index=d.t.values + 86400).sort_index()
        return s[~s.index.duplicated()].reindex(idx, method="ffill")
    def hourly(name, col):                    # an hourly candle that opened at t is complete at t+1h
        p = EXT(name)
        if not os.path.exists(p): return None
        d = pd.read_parquet(p); s = pd.Series(d[col].values, index=d.t.values + 3600).sort_index()
        return s[~s.index.duplicated()].reindex(idx, method="ffill", limit=3)
    fg = daily("fng", "fg")
    if fg is not None: M["fg"] = fg; M["fg7"] = fg - fg.shift(168)
    cb = hourly("cb_BTC", "c")
    if cb is not None and btc_close is not None:
        b = pd.Series(btc_close.values, index=btc_close.index + 3600).reindex(idx, method="ffill", limit=3)
        prem = (cb / b - 1) * 100; M["cbp"] = prem; M["cbp24"] = prem.rolling(24, min_periods=12).mean()
    for cur in ("BTC", "ETH"):
        v = hourly(f"dvol_{cur}", "dvol")
        if v is not None:
            k = "dvol" if cur == "BTC" else "dvole"; M[k] = v
            if cur == "BTC": M["dvol24"] = v - v.shift(24)
    st = daily("stab", "stab")
    if st is not None: st = st.replace(0, float("nan")); M["stab7"] = (st / st.shift(168) - 1) * 100; M["stab30"] = (st / st.shift(720) - 1) * 100
    for c in list(M.columns): M["p_" + c] = M[c].rolling(2160, min_periods=500).rank(pct=True)
    return M
