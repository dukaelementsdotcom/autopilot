"""COLLECTORS: data gathered NOW so it has history when the next build starts using it.
Nothing here changes any decision yet. Every source is optional, time-boxed and failure-contained.
  rec      our own server's minute recorder (order-book depth, live funding, liquidations) -> hourly tables
  macro    the outside world, daily: Nasdaq-100, S&P 500, VIX, US 10-year yield, dollar index (FRED, free, full history)
  onchain  exchange inflows / outflows / exchange balances and active addresses for BTC and ETH (Coin Metrics community, free)
  whales   what the 50 largest Hyperliquid accounts hold right now, per coin (public on-chain positions)
  news     crypto and macro headlines of the last hour with their tone (GDELT, free)"""
import os, io, json, time, gzip, datetime as dt
import numpy as np, pandas as pd, requests
from . import config as C

S = requests.Session(); S.headers["User-Agent"] = "research-machine/1.0"
RAW = lambda name: f"{C.DATA_DIR}/raw/{name}"
KEEP = lambda name: f"{C.STATE_DIR}/collect/{name}"        # hourly snapshots that cannot be re-downloaded live in the saved memory
H = 3600

def _ts(x):
    """dates/times -> unix seconds, whatever resolution pandas picks"""
    return ((pd.to_datetime(x, utc=True) - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s")).astype("int64")

def _save(df, path):
    df.to_parquet(path + ".tmp", index=False); os.replace(path + ".tmp", path)

def _merge(path, new, keys):
    if os.path.exists(path):
        try: new = pd.concat([pd.read_parquet(path), new])
        except Exception: pass
    return new.drop_duplicates(keys, keep="last").sort_values(keys).reset_index(drop=True)

# ── own server recorder ───────────────────────────────────────────────────────────────────────────
def rec(log=print):
    url = os.environ.get("RM_OWN_RELAY", ""); key = os.environ.get("RM_RELAY_KEY", "")
    if not url or not key: return {"skipped": "no own server configured"}
    base = url.split("/api/")[0]; out = {}
    st = S.get(base + "/rec/status", headers={"x-relay-key": key}, timeout=20).json()
    out["status"] = {k: st.get(k) for k in ("writes", "errors", "lastError", "ws", "uptimeMin", "node")}
    today = dt.datetime.utcnow().date()
    for kind in ("depth", "funding", "liq"):
        rows = []
        for d in (today - dt.timedelta(days=1), today):
            r = S.get(base + "/rec/get", params={"day": d.isoformat(), "kind": kind}, headers={"x-relay-key": key}, timeout=60)
            if r.status_code == 200 and r.text.strip():
                rows += [json.loads(l) for l in r.text.splitlines() if l.strip()]
        if not rows: out[kind] = 0; continue
        df = pd.DataFrame(rows); df["h"] = (df.t // H * H).astype("int64")
        if kind == "depth":       # hourly: average book near the price, spread, and how lopsided it is
            g = df.groupby(["c", "h"]).agg(spr=("spr", "mean"), b02=("b02", "mean"), a02=("a02", "mean"), b1=("b1", "mean"), a1=("a1", "mean"), n=("mid", "size")).reset_index()
            g["imb1"] = (g.b1 - g.a1) / (g.b1 + g.a1).replace(0, np.nan)
        elif kind == "funding":
            g = df.groupby(["c", "h"]).agg(fr=("fr", "last"), prem=("mark", "last"), idx=("index", "last")).reset_index()
            g["prem"] = (g.prem / g.idx - 1) * 1e4; g = g.drop(columns="idx")          # mark vs index, basis points
        else:
            g = df.groupby(["c", "h"]).agg(liqL=("long", "sum"), liqS=("short", "sum"), liqN=("n", "sum")).reset_index()
        g = g.rename(columns={"c": "coin", "h": "t"})
        path = RAW(f"rec_{kind}.parquet"); _save(_merge(path, g, ["coin", "t"]), path); out[kind] = int(len(g))
    log(f"recorder: {out}"); return out

# ── outside world (daily) ─────────────────────────────────────────────────────────────────────────
FRED = {"ndx": "NASDAQ100", "spx": "SP500", "vix": "VIXCLS", "us10y": "DGS10", "dxy": "DTWEXBGS"}
def macro(log=print):
    path = RAW("macro.parquet")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * H: return {"fresh": True}
    cols = {}
    for name, sid in FRED.items():
        try:
            r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid}, timeout=60, headers={"User-Agent": "curl/8.5.0"}); r.raise_for_status()   # FRED's CSV endpoint answers plain HTTP clients
            x = pd.read_csv(io.StringIO(r.text)); x.columns = ["date", name]
            cols[name] = pd.to_numeric(x[name], errors="coerce").values; cols.setdefault("_d", {})[name] = x.date.values
        except Exception as e: log(f"   macro {name}: {e}")
    frames = [pd.DataFrame({"date": cols["_d"][n], n: cols[n]}).set_index("date") for n in FRED if n in cols]
    if not frames: return {"error": "no series"}
    df = pd.concat(frames, axis=1).reset_index(); df["t"] = _ts(df.date); df = df.drop(columns="date")
    df = df.sort_values("t").reset_index(drop=True); _save(df, path); log(f"macro: {len(df)} days, last {pd.to_datetime(df.t.max(), unit='s').date()}")
    return {"days": int(len(df)), "series": [n for n in FRED if n in cols]}

def onchain(log=print):
    path = RAW("onchain.parquet")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * H: return {"fresh": True}
    rows, nxt = [], None
    params = {"assets": "btc,eth", "metrics": "FlowInExNtv,FlowOutExNtv,SplyExNtv,AdrActCnt", "frequency": "1d", "page_size": 10000, "start_time": "2020-01-01"}
    url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
    for _ in range(10):
        r = S.get(nxt or url, params=None if nxt else params, timeout=60); r.raise_for_status(); j = r.json()
        rows += j.get("data", []); nxt = j.get("next_page_url")
        if not nxt: break
    df = pd.DataFrame(rows)
    if df.empty: return {"error": "empty"}
    keep = ["asset", "time", "FlowInExNtv", "FlowOutExNtv", "SplyExNtv", "AdrActCnt"]
    df = df[[c for c in keep if c in df.columns]].copy(); df["t"] = _ts(df.time); df = df.drop(columns="time")
    for c in df.columns:
        if c not in ("asset", "t"): df[c] = pd.to_numeric(df[c], errors="coerce")
    _save(df, path); log(f"on-chain: {len(df)} rows"); return {"rows": int(len(df))}

# ── Hyperliquid whales (hourly snapshot) ──────────────────────────────────────────────────────────
def whales(log=print, top=50):
    os.makedirs(KEEP(""), exist_ok=True); lb = KEEP("hl_top.json")
    if not os.path.exists(lb) or time.time() - os.path.getmtime(lb) > 24 * H:      # the leaderboard file is ~40 MB: once a day
        r = S.get("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard", timeout=120); r.raise_for_status()
        rows = r.json().get("leaderboardRows", [])
        rows.sort(key=lambda x: -float(x.get("accountValue") or 0))
        json.dump([x["ethAddress"] for x in rows[:top]], open(lb, "w"))
    addrs = json.load(open(lb)); t = int(time.time()) // H * H; agg = {}
    for a in addrs:
        try:
            st = S.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": a}, timeout=20).json()
        except Exception: continue
        for p in st.get("assetPositions", []):
            pos = p.get("position", {}); c = pos.get("coin", ""); sz = float(pos.get("szi") or 0); val = float(pos.get("positionValue") or 0)
            c = ("1000" + c[1:]) if c.startswith("k") and c[1:].isupper() else c
            e = agg.setdefault(c, {"wLong": 0.0, "wShort": 0.0, "wNl": 0, "wNs": 0})
            if sz > 0: e["wLong"] += val; e["wNl"] += 1
            elif sz < 0: e["wShort"] += val; e["wNs"] += 1
        time.sleep(0.2)
    df = pd.DataFrame([{"coin": c, "t": t, **v} for c, v in agg.items()])
    if df.empty: return {"error": "no positions"}
    os.makedirs(KEEP(""), exist_ok=True); path = KEEP("hl_whales.parquet"); _save(_merge(path, df, ["coin", "t"]), path)
    log(f"whales: {len(addrs)} accounts, {len(df)} coins"); return {"accounts": len(addrs), "coins": int(len(df))}

# ── news (hourly) ─────────────────────────────────────────────────────────────────────────────────
NEWS_Q = {"crypto": "(bitcoin OR crypto OR ethereum OR binance)", "macro": "(\"federal reserve\" OR inflation OR nasdaq OR tariffs)",
          "regulation": "(SEC crypto OR crypto ban OR crypto regulation)"}
def news(log=print):
    t = int(time.time()) // H * H; path = KEEP(f"news/{dt.datetime.utcnow():%Y-%m-%d}.jsonl.gz"); os.makedirs(os.path.dirname(path), exist_ok=True); n = 0
    for old in sorted(os.listdir(os.path.dirname(path)))[:-60]: os.remove(os.path.join(os.path.dirname(path), old))     # keep 60 days
    with gzip.open(path, "at") as f:
        for topic, q in NEWS_Q.items():
            try:
                r = S.get("https://api.gdeltproject.org/api/v2/doc/doc", params={"query": q + " sourcelang:english", "mode": "artlist", "format": "json", "maxrecords": 75, "timespan": "1h", "sort": "datedesc"}, timeout=40, headers={"User-Agent": "curl/8.5.0"})
                if r.status_code != 200 or not r.text.lstrip().startswith("{"): log(f"   news {topic}: HTTP {r.status_code}"); time.sleep(6); continue
                for a in (r.json().get("articles") or []):
                    f.write(json.dumps({"t": t, "topic": topic, "title": a.get("title"), "url": a.get("url"), "domain": a.get("domain"), "seen": a.get("seendate")}) + "\n"); n += 1
            except Exception as e: log(f"   news {topic}: {str(e)[:100]}")
            time.sleep(6)                                   # GDELT allows one request every 5 seconds
    log(f"news: {n} headlines"); return {"headlines": n}

def run(log=print):
    info = {}
    for name, fn in (("recorder", rec), ("macro", macro), ("onchain", onchain), ("whales", whales), ("news", news)):
        t0 = time.time()
        try: info[name] = fn(log=log)
        except Exception as e: info[name] = {"error": f"{type(e).__name__}: {str(e)[:150]}"}; log(f"   ! collector {name}: {e}")
        info[name] = {**(info[name] or {}), "secs": round(time.time() - t0, 1)}
    return info
