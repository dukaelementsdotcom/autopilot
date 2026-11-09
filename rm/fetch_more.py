"""More free data, chosen for the goal (catching 2-10% moves):
  SPOT vs FUTURES (Binance spot archive + live spot candles): is a move driven by real buyers (spot) or by leverage (perps)?
     basis      = futures price vs spot price (leverage paying up = positive)
     sshare     = spot's share of all trading (real money vs leverage)
     stimb24    = spot taker buy-minus-sell share over 24h (who is aggressively buying with real money)
  HYPERLIQUID (biggest on-chain perp exchange, public API, hourly history since 2023):
     hlprem     = Hyperliquid perp premium vs oracle (on-chain leverage paying up), hlfund24 = its funding, 24h average
Everything is incremental, written atomically, time-boxed, and every failure is contained (the machine runs without it)."""
import os, io, json, time, zipfile, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd, requests
from . import config as C
from .fetch import _get, _csv, _months, KCOLS

S = requests.Session(); S.headers["User-Agent"] = "research-machine/1.0"
SPOT_ARCHIVE = "https://data.binance.vision/data/spot"
HL_URL = "https://api.hyperliquid.xyz/info"
H = 3600

def spot_sym(coin):
    """Binance spot lists the 1000x perps under the plain name: 1000PEPE perp = 1000 x PEPE spot"""
    return (coin[4:] + "USDT", 1000.0) if coin.startswith("1000") else (coin + "USDT", 1.0)

def hl_name(coin): return ("k" + coin[4:]) if coin.startswith("1000") else coin

def _ts(x):
    x = x.astype("int64")                       # Binance spot files switched to MICROseconds in 2025; futures are milliseconds
    return np.where(x > 10 ** 14, x // 1_000_000, x // 1000).astype("int64")

def _spot_kl(blob, factor):
    x = _csv(blob, KCOLS); x.columns = KCOLS[:len(x.columns)]
    return pd.DataFrame({"t": _ts(x.open_time), "sc": x.close.astype(float) * factor, "sqv": x.quote_volume.astype(float), "stbq": x.taker_buy_quote_volume.astype(float)})

def _save(df, path):
    df.to_parquet(path + ".tmp", index=False); os.replace(path + ".tmp", path)

def _merge(path, new):
    if os.path.exists(path):
        try: new = pd.concat([pd.read_parquet(path), new])
        except Exception: pass                  # damaged: rebuilt from what arrives
    return new.drop_duplicates("t", keep="last").sort_values("t").reset_index(drop=True)

# ── spot archive + live ─────────────────────────────────────────────────────────────────────────
def update_spot(coins=None, log=print, today=None, budget_s=900):
    coins = [c for c in (coins or C.COINS) if c not in C.DEAD]; today = today or dt.datetime.utcnow().date()
    os.makedirs(f"{C.DATA_DIR}/raw", exist_ok=True); mpath = f"{C.DATA_DIR}/raw/spot_manifest.json"
    try: man = json.load(open(mpath)) if os.path.exists(mpath) else {}
    except Exception: man = {}
    t0 = time.time(); start = dt.date.fromisoformat(C.START); first = today.replace(day=1)
    last_full = (first - dt.timedelta(days=1)).replace(day=1); jobs = []
    for coin in coins:
        M = man.setdefault(coin, {"done": [], "miss": {}}); done = set(M["done"]); sym, fac = spot_sym(coin)
        for m in _months(start, last_full):
            key = "sm" + m.strftime("%Y-%m")
            if key not in done: jobs.append((coin, key, f"{SPOT_ARCHIVE}/monthly/klines/{sym}/1h/{sym}-1h-{m:%Y-%m}.zip", fac, m))
        d = last_full if ("sm" + last_full.strftime("%Y-%m")) not in done else first
        while d < today:
            key = "sd" + d.isoformat()
            if key not in done and ("sm" + d.strftime("%Y-%m")) not in done: jobs.append((coin, key, f"{SPOT_ARCHIVE}/daily/klines/{sym}/1h/{sym}-1h-{d.isoformat()}.zip", fac, d))
            d += dt.timedelta(days=1)
    def run(j):
        coin, key, url, fac, when = j
        if time.time() - t0 > budget_s: return coin, key, "later", None, when
        try:
            b = _get(url); return coin, key, ("missing" if b is None else "ok"), (None if b is None else _spot_kl(b, fac)), when
        except Exception as e: return coin, key, "error", str(e)[:100], when
    got = {}; n_ok = 0
    with ThreadPoolExecutor(24) as ex:
        for coin, key, st, df, when in ex.map(run, jobs):
            M = man[coin]
            if st == "ok": got.setdefault(coin, []).append(df); M["done"].append(key); n_ok += 1
            elif st == "missing":
                age = (today - (when if isinstance(when, dt.date) else today)).days
                recent_month = key.startswith("sm") and key[2:] >= last_full.strftime("%Y-%m")     # may simply not be published yet
                if (key.startswith("sm") and not recent_month) or (key.startswith("sd") and age > 7): M["done"].append(key)   # before listing, or never published
    for coin, dfs in got.items():
        path = f"{C.DATA_DIR}/raw/{coin}_s.parquet"; _save(_merge(path, pd.concat(dfs)), path)
    json.dump(man, open(mpath + ".tmp", "w")); os.replace(mpath + ".tmp", mpath)
    log(f"spot: {n_ok} files added, {len(jobs)} tried in {time.time()-t0:.0f}s")
    return {"added": n_ok, "tried": len(jobs)}

def update_spot_live(coins=None, log=print):
    """the newest spot hours from Binance's public market-data mirror (works from US servers)"""
    coins = [c for c in (coins or C.COINS) if c not in C.DEAD]; os.makedirs(f"{C.DATA_DIR}/live", exist_ok=True); now = time.time()
    def one(coin):
        sym, fac = spot_sym(coin)
        try:
            r = S.get("https://data-api.binance.vision/api/v3/klines", params={"symbol": sym, "interval": "1h", "limit": 100}, timeout=20)
            if r.status_code == 400: return coin, "not on spot"
            r.raise_for_status(); x = pd.DataFrame(r.json(), columns=KCOLS)
            x = x[x.close_time.astype("int64") / 1000 < now]                       # completed hours only
            df = pd.DataFrame({"t": _ts(x.open_time), "sc": x.close.astype(float) * fac, "sqv": x.quote_volume.astype(float), "stbq": x.taker_buy_quote_volume.astype(float)})
            _save(df, f"{C.DATA_DIR}/live/{coin}_s.parquet"); return coin, "ok"
        except Exception as e: return coin, str(e)[:80]
    with ThreadPoolExecutor(8) as ex: res = dict(ex.map(one, coins))
    ok = sum(v == "ok" for v in res.values()); log(f"spot live: {ok}/{len(coins)} coins")
    return {"ok": ok, "missing": [c for c, v in res.items() if v != "ok"]}

# ── Hyperliquid ───────────────────────────────────────────────────────────────────────────────
def _hl(body):
    for i in range(4):
        try:
            r = S.post(HL_URL, json=body, timeout=30)
            if r.status_code == 429: time.sleep(5 * 2 ** i); continue          # rate limit (~1200 weight/min): back off 5, 10, 20, 40 s
            r.raise_for_status(); return r.json()
        except requests.RequestException:
            time.sleep(1 + i)
    raise IOError("hyperliquid not answering")

def update_hl(coins=None, log=print, budget_s=600):          # 10 min while back-filling; once caught up each run takes seconds
    coins = [c for c in (coins or C.COINS) if c not in C.DEAD]; t0 = time.time()
    names = {u["name"] for u in _hl({"type": "meta"}).get("universe", [])}
    start_ms = int(dt.datetime(2023, 5, 1).timestamp() * 1000); added = 0; todo = []
    for coin in coins:
        if hl_name(coin) not in names: continue
        path = f"{C.DATA_DIR}/raw/{coin}_hl.parquet"; last = None
        if os.path.exists(path):
            try: last = int(pd.read_parquet(path, columns=["t"]).t.max())
            except Exception: last = None
        todo.append((coin, path, (last + H) * 1000 if last else start_ms))
    reach = {c: since for c, _, since in todo}
    todo.sort(key=lambda x: -x[2])                          # coins that are nearly up to date first (cheap), then the long back-fills
    for coin, path, since in todo:
        rows = []
        while time.time() - t0 < budget_s and since < time.time() * 1000 - H * 1000:
            try: x = _hl({"type": "fundingHistory", "coin": hl_name(coin), "startTime": int(since)})
            except Exception as e: log(f"   hyperliquid {coin}: {e} (continues next run)"); break
            if not x: break
            rows += x; since = x[-1]["time"] + 1; reach[coin] = since
            if len(x) < 400: break
            time.sleep(2.5)                     # stay well under the public rate limit; the history back-fills over the first ~day of runs
        if rows:
            df = pd.DataFrame(rows); df = pd.DataFrame({"t": (df.time.astype("int64") // 1000 // H * H).astype("int64"), "hlf": df.fundingRate.astype(float), "hlp": df.premium.astype(float)})
            _save(_merge(path, df.drop_duplicates("t", keep="last")), path); added += len(df)
        if time.time() - t0 >= budget_s: break
    behind = sum(1 for c, v in reach.items() if v < (time.time() - 3 * H) * 1000)
    log(f"hyperliquid: {added} hours added, {len(todo)} coins listed there, {behind} still back-filling ({time.time()-t0:.0f}s)")
    return {"added": added, "coins": len(todo), "backfilling": behind}

def update(log=print, deadline_s=None):
    """called once per run; each source is contained. Writes version_more.txt (changes about once a day) for the cache key."""
    info = {}
    for name, fn in (("spot", update_spot), ("spotLive", update_spot_live), ("hl", update_hl)):
        try: info[name] = fn(log=log)
        except Exception as e: info[name] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}; log(f"   ! {name}: {e}")
    try:
        spot_done = sum(len(v["done"]) for v in json.load(open(f"{C.DATA_DIR}/raw/spot_manifest.json")).values())
        open(f"{C.DATA_DIR}/raw/version_more.txt", "w").write(f"{spot_done}|{dt.datetime.utcnow():%Y-%m-%d}")
    except Exception: pass
    return info
