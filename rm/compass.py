"""COMPASS: the market-intelligence engine. It reads the whole world and hands the Arbiter evidence (witnesses), never decisions.
Stage 1: global markets (daily, full free history), the Fear & Greed index (since 2018), the economic calendar and the IPO calendar,
and the TREND READER (several views that must agree, five states, honest accuracy)."""
import os, json, time, numpy as np, pandas as pd, requests
from . import config as C

H = 3600; DAY = 86400
UA = {"User-Agent": "Mozilla/5.0 (research-machine)"}
TICKERS = {"spx": "^GSPC", "ndx": "^IXIC", "dji": "^DJI", "dax": "^GDAXI", "ftse": "^FTSE", "nikkei": "^N225", "hsi": "^HSI",
           "dxy": "DX-Y.NYB", "gold": "GC=F", "oil": "CL=F", "us10y": "^TNX", "vix": "^VIX", "nvda": "NVDA", "chips": "SMH", "coin": "COIN"}
RAW = lambda n: f"{C.DATA_DIR}/raw/{n}"; KEEP = lambda n: f"{C.STATE_DIR}/collect/{n}"

def markets(log=print):
    path = RAW("compass_mkt.parquet")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * H: return {"fresh": True}
    rows, bad = [], []
    for name, sym in TICKERS.items():
        try:
            j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym)}", params={"range": "10y", "interval": "1d"}, headers=UA, timeout=30).json()
            r = j["chart"]["result"][0]; cl = r["indicators"]["quote"][0]["close"]
            rows += [(int(t) // DAY * DAY, name, float(c)) for t, c in zip(r["timestamp"], cl) if c is not None]
        except Exception as e: bad.append(f"{name}: {str(e)[:60]}")
        time.sleep(0.5)
    if not rows: return {"error": "no market data", "failed": bad}
    df = pd.DataFrame(rows, columns=["t", "sym", "close"]).drop_duplicates(["t", "sym"], keep="last").sort_values(["sym", "t"])
    df.to_parquet(path + ".tmp", index=False); os.replace(path + ".tmp", path)
    log(f"compass markets: {df.sym.nunique()} markets, {len(df)} days"); return {"markets": int(df.sym.nunique()), **({"failed": bad} if bad else {})}

def fng(log=print):
    path = RAW("compass_fng.parquet")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * H: return {"fresh": True}
    d = requests.get("https://api.alternative.me/fng/", params={"limit": 0}, headers=UA, timeout=30).json()["data"]
    df = pd.DataFrame([(int(x["timestamp"]), int(x["value"])) for x in d], columns=["t", "fng"]).sort_values("t")
    df.to_parquet(path + ".tmp", index=False); os.replace(path + ".tmp", path); return {"days": int(len(df))}

def calendar(log=print):
    """economic calendar (this week, high/medium impact) and the IPO calendar (this month and next), kept forever"""
    os.makedirs(KEEP(""), exist_ok=True); out = {}
    p = KEEP("calendar.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 6 * H: return {"fresh": True}     # the calendar feed allows only a few requests
    try:
        ev = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=UA, timeout=30).json()
        p = KEEP("calendar.json"); old = json.load(open(p)) if os.path.exists(p) else []
        keyf = lambda e: (e.get("title"), e.get("country"), e.get("date"))
        merged = {keyf(e): e for e in old}; merged.update({keyf(e): {k: e.get(k) for k in ("title", "country", "date", "impact", "forecast", "previous", "actual")} for e in ev if e.get("impact") in ("High", "Medium")})
        json.dump(sorted(merged.values(), key=lambda e: e["date"] or ""), open(p, "w")); out["calendar"] = len(merged)
    except Exception as e: out["calendarError"] = str(e)[:100]
    try:
        p = KEEP("ipos.json"); old = json.load(open(p)) if os.path.exists(p) else {}
        for m in (time.strftime("%Y-%m", time.gmtime()), time.strftime("%Y-%m", time.gmtime(time.time() + 31 * DAY))):
            d = requests.get("https://api.nasdaq.com/api/ipo/calendar", params={"date": m}, headers=UA, timeout=30).json().get("data") or {}
            for part in ("priced", "upcoming", "filed"):
                rows = ((d.get(part) or {}).get("rows") or (((d.get(part) or {}).get("upcomingTable") or {}).get("rows")) or [])
                for r in rows:
                    k = r.get("proposedTickerSymbol") or r.get("companyName")
                    if k: old[k] = {**old.get(k, {}), **{kk: r.get(kk) for kk in ("companyName", "proposedExchange", "pricedDate", "expectedPriceDate", "filedDate", "dollarValueOfSharesOffered", "proposedSharePrice")}, "status": part}
        json.dump(old, open(p, "w")); out["ipos"] = len(old)
    except Exception as e: out["iposError"] = str(e)[:100]
    return out

def run(log=print):
    info = {}
    for name, fn in (("markets", markets), ("fear&greed", fng), ("calendar", calendar)):
        try: info[name] = fn(log=log)
        except Exception as e: info[name] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    return info

def upcoming(days=14):
    """high-impact economic events and IPOs in the next two weeks (shown to you, and known to the Arbiter)"""
    out = []
    try:
        for e in json.load(open(KEEP("calendar.json"))):
            t = pd.Timestamp(e["date"]).tz_convert("UTC")
            if e.get("impact") == "High" and e.get("country") in ("USD", "CNY", "EUR", "JPY") and pd.Timestamp.utcnow() <= t <= pd.Timestamp.utcnow() + pd.Timedelta(days=days):
                out.append({"when": t.strftime("%Y-%m-%d %H:%M"), "what": f"{e['country']} {e['title']}", "kind": "macro"})
    except Exception: pass
    try:
        for k, r in json.load(open(KEEP("ipos.json"))).items():
            d = r.get("expectedPriceDate") or r.get("pricedDate")
            if d and r.get("status") == "upcoming": out.append({"when": d, "what": f"IPO {r.get('companyName')} ({k})", "kind": "ipo", "size": r.get("dollarValueOfSharesOffered")})
    except Exception: pass
    return sorted(out, key=lambda x: x["when"])[:20]

# ── witness features (added to the panel, w_ prefix) ───────────────────────────────────────────────────────────────────
def features(P, ts, put, log=print):
    try:
        m = pd.read_parquet(RAW("compass_mkt.parquet"))
        from .witnesses import _daily_to_hourly
        for s in ("spx", "ndx", "dax", "nikkei", "hsi", "dxy", "gold", "oil", "nvda", "chips", "coin"):
            x = m[m.sym == s].sort_values("t")
            if len(x) < 30: continue
            v = x.close.values; t = x.t.values
            put(f"w_g_{s}5", _daily_to_hourly(ts, t[5:], v[5:] / v[:-5] - 1))
            put(f"w_g_{s}20", _daily_to_hourly(ts, t[20:], v[20:] / v[:-20] - 1))
        ccol = "_cl" if "_cl" in P else "px"; b = P.loc[P.coin.values == "BTC", ["t", ccol]].rename(columns={ccol: "Cl"})
        x = m[m.sym == "spx"].sort_values("t")
        if b is not None and len(x) > 60:                         # how tightly Bitcoin follows the S&P right now (30 trading days)
            bd = b.assign(d=b.t // DAY * DAY).groupby("d").Cl.last()
            j = pd.DataFrame({"spx": x.set_index("t").close}).join(bd.rename("btc"), how="inner").pct_change().dropna()
            corr = j.spx.rolling(30, min_periods=20).corr(j.btc)
            put("w_g_link", _daily_to_hourly(ts, corr.index.values, corr.values))
    except Exception as e: log(f"   compass market features skipped: {e}")
    try:
        f = pd.read_parquet(RAW("compass_fng.parquet")).sort_values("t")
        from .witnesses import _daily_to_hourly
        put("w_g_fng", _daily_to_hourly(ts, f.t.values, f.fng.values)); put("w_g_fng7", _daily_to_hourly(ts, f.t.values[7:], f.fng.values[7:] - f.fng.values[:-7]))
    except Exception as e: log(f"   compass mood features skipped: {e}")

# ── the TREND READER ───────────────────────────────────────────────────────────────────────────────────────────────────
STATES = ["strong down", "down", "sideways", "up", "strong up"]

def trend_scores(J):
    """per hour: a score from -1 (strong down) to +1 (strong up) from views that must agree:
    BTC vs its 20/50/200-day averages, the 50-day average's slope, BTC's 7- and 30-day move, breadth (share of coins above their own
    50-day average), the S&P 500 and Nasdaq over 20 days, the dollar (inverse). Smoothed over 12h so noise cannot flip it."""
    P = J.P; T, O, Hh, Ll, Cc = J._bars("BTC"); s = pd.Series(Cc, index=T)
    v = []
    for d in (20, 50, 200):
        ma = s.rolling(d * 24, min_periods=d * 12).mean(); v.append(np.sign(s / ma - 1))
    ma50 = s.rolling(50 * 24, min_periods=600).mean(); v.append(np.sign(ma50.diff(24 * 5)))
    v.append(np.sign(s.pct_change(24 * 7))); v.append(np.sign(s.pct_change(24 * 30)))
    if "tr50" in P:
        br = P.groupby("t").tr50.apply(lambda x: (x > 0).mean() * 2 - 1).reindex(T); v.append(br.clip(-1, 1))
    for c, w in (("w_g_spx20", 0.5), ("w_g_ndx20", 0.5), ("w_g_dxy20", -0.5)):
        if c in P: g = P.loc[P.coin.values == "BTC", ["t", c]].set_index("t")[c].reindex(T); v.append(np.sign(g) * w)
    sc = pd.concat(v, axis=1).mean(1).ewm(span=12, min_periods=1).mean()
    return sc

def state_of(score, wild):
    i = 0 if score <= -0.5 else 1 if score <= -0.15 else 2 if score < 0.15 else 3 if score < 0.5 else 4
    return STATES[i] + (" wild" if wild else "")

def regimes2(J, ts):
    sc = trend_scores(J); b = J.P[(J.P.coin == "BTC") & J.P.t.isin(ts)][["t", "p_vol24"]]
    return {int(t): state_of(float(sc.get(t, 0.0)), bool(np.isfinite(pv) and pv >= 0.7)) for t, pv in b.itertuples(index=False)}

def trend_accuracy(J):
    """honest score: for each state, what BTC and the average coin did over the next 1, 3 and 7 days"""
    sc = trend_scores(J); T, O, Hh, Ll, Cc = J._bars("BTC"); s = pd.Series(Cc, index=T)
    st = sc.apply(lambda x: state_of(x, False)); out = {}
    for name in STATES:
        m = (st == name).values[::24]                                   # one reading per day (independent-ish)
        idx = st.index.values[::24][m]
        if len(idx) < 10: continue
        row = {"days": int(len(idx))}
        for d in (1, 3, 7):
            fut = s.reindex(idx + d * DAY).values / s.reindex(idx).values - 1; fut = fut[np.isfinite(fut)]
            row[f"{d}d"] = {"avg": round(float(fut.mean()) * 100, 2), "upShare": round(float((fut > 0).mean()), 2)}
        out[name] = row
    return out
