"""LIQUIDATION HISTORY (Coinalyze, free API key in the COINALYZE_KEY secret): forced closures of longs and of shorts, per coin,
summed over Binance, Bybit and OKX perpetuals, in dollars.
  daily   kept by Coinalyze for years  -> the liquidation witness can be trained now
  hourly  Coinalyze keeps ~1,500-2,000 hours; we fetch it a few times a day and keep it for good (for the intraday witness later)
A day's numbers count only from 01:00 UTC the next day (no peeking). 40 API calls a minute are allowed; each symbol is one call."""
import os, json, time, numpy as np, pandas as pd, requests
from . import config as C

BASE = "https://api.coinalyze.net/v1"; DAY = 86400; H = 3600
EXCH = ("binance", "bybit", "okx")
COLS = ["w_l_net1", "w_l_size1", "w_l_net7", "w_l_size7", "w_l_long1", "w_l_short1", "w_l_mnet1", "w_l_msize1", "w_l_msize7", "w_l_rel"]
P_D = lambda: f"{C.DATA_DIR}/raw/cz_liq_d.parquet"; P_H = lambda: f"{C.DATA_DIR}/raw/cz_liq_h.parquet"; P_M = lambda: f"{C.DATA_DIR}/raw/cz_markets.json"
S = requests.Session()
_last = [0.0]

def _get(path, calls=1, **params):
    key = os.environ.get("COINALYZE_KEY", "").strip()
    for attempt in range(4):
        wait = _last[0] - time.time()
        if wait > 0: time.sleep(wait)
        r = S.get(BASE + path, params=params, headers={"api_key": key}, timeout=60)
        _last[0] = time.time() + calls * 1.6                      # stay under 40 calls a minute
        if r.status_code == 429: time.sleep(min(float(r.headers.get("Retry-After", 20)) + 1, 65)); continue
        if r.status_code in (401, 403): raise IOError(f"key not accepted (HTTP {r.status_code})")
        if r.ok: return r.json()
        time.sleep(3)
    raise IOError(f"coinalyze {path}: HTTP {r.status_code} {r.text[:120]}")

def _base(x): x = str(x).upper(); return x[4:] if x.startswith("1000") else x

def markets(log=print):
    """coin -> the perpetual symbols to sum (cached for a week)"""
    try:
        m = json.load(open(P_M()))
        if time.time() - m["at"] < 7 * DAY and m.get("map"): return m["map"]
    except Exception: pass
    ex = {str(e.get("code")): str(e.get("name", "")).lower() for e in _get("/exchanges")}
    want = {code for code, name in ex.items() if any(name.startswith(x) or x in name.split() for x in EXCH)}
    ours = {_base(c): c for c in C.COINS}; mp = {}
    for mk in _get("/future-markets"):
        if not mk.get("is_perpetual") or str(mk.get("quote_asset", "")).upper() not in ("USDT", "USD", "USDC"): continue
        if str(mk.get("exchange")) not in want: continue
        c = ours.get(_base(mk.get("base_asset", "")))
        if c: mp.setdefault(c, []).append(mk["symbol"])
    mp = {c: sorted(v, key=lambda s: ("USDT" not in s, s))[:4] for c, v in mp.items()}        # USDT-margined first; at most 4 per coin
    os.makedirs(os.path.dirname(P_M()), exist_ok=True); json.dump({"at": time.time(), "map": mp, "exchanges": {k: ex[k] for k in want}}, open(P_M(), "w"))
    log(f"   liquidations: {len(mp)} coins matched, {sum(len(v) for v in mp.values())} markets on {sorted(ex[k] for k in want)}")
    return mp

def _pull(mp, interval, t_from, t_to, budget, t0):
    sym2coin = {s: c for c, v in mp.items() for s in v}; syms = list(sym2coin); rows = []; done = 0
    for i in range(0, len(syms), 20):
        if time.time() - t0 > budget: break
        part = syms[i:i + 20]
        for item in _get("/liquidation-history", calls=len(part), symbols=",".join(part), interval=interval, **{"from": int(t_from), "to": int(t_to)}, convert_to_usd="true"):
            c = sym2coin.get(item.get("symbol"))
            for h in item.get("history") or []:
                rows.append((int(h["t"]), c, float(h.get("l") or 0), float(h.get("s") or 0)))
        done += len(part)
    if not rows: return None, done, len(syms)
    df = pd.DataFrame(rows, columns=["t", "coin", "l", "s"])
    if df.t.max() > 1e11: df["t"] = df.t // 1000
    return df.groupby(["t", "coin"], as_index=False)[["l", "s"]].sum(), done, len(syms)

def _merge(path, new):
    if os.path.exists(path):
        try: new = pd.concat([pd.read_parquet(path), new])
        except Exception: pass
    new = new.drop_duplicates(["t", "coin"], keep="last").sort_values(["t", "coin"])
    new.to_parquet(path + ".tmp", index=False); os.replace(path + ".tmp", path); return new

def update(log=print, budget=600):
    if not os.environ.get("COINALYZE_KEY", "").strip(): return {"skipped": "no COINALYZE_KEY secret"}
    t0 = time.time(); now = int(time.time()); info = {}
    try: st = json.load(open(P_M() + ".state"))
    except Exception: st = {}
    have_d = os.path.exists(P_D()); have_h = os.path.exists(P_H())
    if have_d and have_h and now - st.get("at", 0) < 6 * H: return {"fresh": True, "daysStored": st.get("days"), "hoursStored": st.get("hours")}
    mp = markets(log)
    if not mp: return {"error": "no markets matched"}
    d, done, total = _pull(mp, "daily", (now - 8 * DAY) if have_d else 1598918400, now, budget, t0)
    if d is not None:
        d = d[d.t < now // DAY * DAY] if True else d                                 # completed days only
        allD = _merge(P_D(), d); info["days"] = int(allD.t.nunique()); info["dailyFrom"] = str(pd.to_datetime(allD.t.min(), unit="s").date())
    complete = done >= total
    h, doneh, _ = _pull(mp, "1hour", (now - 3 * DAY) if have_h else now - 90 * DAY, now, budget, t0)
    if h is not None:
        h = h[h.t < now // H * H]; allH = _merge(P_H(), h); info["hours"] = int(allH.t.nunique())
    complete = complete and doneh >= total
    if complete: json.dump({"at": now, **info}, open(P_M() + ".state", "w"))
    open(f"{C.DATA_DIR}/raw/version_liq.txt", "w").write(time.strftime("%Y-%m-%d", time.gmtime()) + f"|{info.get('days')}")
    info.update({"coins": len(mp), "complete": complete})
    log(f"liquidation history: {info}, {time.time() - t0:.0f}s"); return info

def features(P, log=print):
    if not os.path.exists(P_D()): return {}
    from .witnesses import _daily_to_hourly
    d = pd.read_parquet(P_D()); d["t"] = d.t // DAY * DAY
    if d.t.nunique() < 400: return {}
    L = d.pivot_table(index="t", columns="coin", values="l", aggfunc="sum").sort_index(); Sh = d.pivot_table(index="t", columns="coin", values="s", aggfunc="sum").sort_index()
    L = L.where(L.notna() | Sh.notna()).fillna(0).where(L.notna() | Sh.notna()); Sh = Sh.fillna(0).where(L.notna())
    tot = L + Sh; lz = np.log1p(tot)
    z = lambda x, n=60: (x - x.rolling(n, min_periods=20).median()) / (x.rolling(n, min_periods=20).std() + 1e-9)
    F = {"w_l_net1": (L - Sh) / tot.replace(0, np.nan), "w_l_size1": z(lz),
         "w_l_net7": (L.rolling(7, min_periods=3).sum() - Sh.rolling(7, min_periods=3).sum()) / tot.rolling(7, min_periods=3).sum().replace(0, np.nan),
         "w_l_size7": z(np.log1p(tot.rolling(7, min_periods=3).sum())), "w_l_long1": z(np.log1p(L)), "w_l_short1": z(np.log1p(Sh))}
    mL = L.sum(axis=1); mS = Sh.sum(axis=1); mT = mL + mS
    M = {"w_l_mnet1": (mL - mS) / mT.replace(0, np.nan), "w_l_msize1": z(np.log1p(mT)), "w_l_msize7": z(np.log1p(mT.rolling(7, min_periods=3).sum()))}
    ts = np.sort(P.t.unique()); tP = P.t.values; coin = P.coin.values; lag = 1 + 1 / 24
    out = {k: np.full(len(P), np.nan, "float32") for k in COLS}
    for k, s in M.items(): out[k] = pd.Series(_daily_to_hourly(ts, s.index.values, s.values, lag), index=ts).reindex(tP).astype("float32").values
    for c in L.columns:
        m = coin == c
        if not m.any(): continue
        for k, f in F.items(): out[k][m] = pd.Series(_daily_to_hourly(ts, f.index.values, f[c].values, lag), index=ts).reindex(tP[m]).values
    out["w_l_rel"] = (out["w_l_net1"] - out["w_l_mnet1"]).astype("float32")
    return out
