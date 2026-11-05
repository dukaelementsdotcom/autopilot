"""Incremental downloader for Binance's free public archive (data.binance.vision).
Per coin it keeps two small files: hourly candles (with real taker-buy volume) and hourly positioning
(open interest, long/short ratios of all accounts and of top traders, taker buy/sell ratio).
Only what is new is downloaded; missing days are retried for a week, then skipped."""
import io, os, json, zipfile, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd, requests
from . import config as C

S = requests.Session(); S.headers["User-Agent"] = "research-machine/1.0"
KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]

def _get(url):
    for _ in range(3):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 404: return None
            if r.ok: return r.content
        except requests.RequestException: pass
    raise IOError("download failed: " + url)

def _csv(blob, names=None):
    z = zipfile.ZipFile(io.BytesIO(blob)); raw = z.read(z.namelist()[0]).decode()
    hdr = not raw[:1].isdigit()
    return pd.read_csv(io.StringIO(raw), header=0 if hdr else None, names=None if hdr else names)

def _months(a, b):
    out, d = [], dt.date(a.year, a.month, 1)
    while d <= b: out.append(d); d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out

def _kl(blob):
    x = _csv(blob, KCOLS); x.columns = KCOLS[:len(x.columns)]
    return pd.DataFrame({"t": (x.open_time.astype("int64") // 1000).astype("int64"), "o": x.open.astype(float), "h": x.high.astype(float), "l": x.low.astype(float),
                         "c": x.close.astype(float), "v": x.volume.astype(float), "tb": x.taker_buy_volume.astype(float)})

def _met(blob):
    x = _csv(blob)
    x["t"] = pd.to_datetime(x.create_time).values.astype("datetime64[s]").astype("int64")
    x["hour"] = x.t // 3600 * 3600
    g = x.groupby("hour")
    out = pd.DataFrame({"oi": g.sum_open_interest_value.last(), "glob": g.count_long_short_ratio.last(), "topAcc": g.count_toptrader_long_short_ratio.last(),
                        "topPos": g.sum_toptrader_long_short_ratio.last(), "tk": g.sum_taker_long_short_vol_ratio.mean()})
    out.index.name = "t"; return out.reset_index()

def _fund(blob):
    x = _csv(blob)
    return pd.DataFrame({"t": (x.calc_time.astype("int64") // 1000).astype("int64"), "fr": x.last_funding_rate.astype(float)})

def _job(coin, kind, key):
    sym = f"{coin}USDT"
    if kind == "km": url = f"{C.ARCHIVE}/monthly/klines/{sym}/1h/{sym}-1h-{key}.zip"
    elif kind == "kd": url = f"{C.ARCHIVE}/daily/klines/{sym}/1h/{sym}-1h-{key}.zip"
    elif kind == "f": url = f"{C.ARCHIVE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{key}.zip"
    else: url = f"{C.ARCHIVE}/daily/metrics/{sym}/{sym}-metrics-{key}.zip"
    try:
        b = _get(url)
        if b is None: return coin, kind, key, "missing", None
        return coin, kind, key, "ok", (_met(b) if kind == "m" else _fund(b) if kind == "f" else _kl(b))
    except Exception as e:
        return coin, kind, key, "error", str(e)[:120]

def update(coins=None, today=None, log=print):
    coins = coins or C.COINS; today = today or dt.datetime.utcnow().date()
    os.makedirs(f"{C.DATA_DIR}/raw", exist_ok=True)
    mpath = f"{C.DATA_DIR}/raw/manifest.json"
    man = json.load(open(mpath)) if os.path.exists(mpath) else {}
    start = dt.date.fromisoformat(C.START); jobs = []
    last_full_month = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    for coin in coins:
        M = man.setdefault(coin, {"done": [], "miss": {}})
        done = set(M["done"])
        for m in _months(start, last_full_month):          # complete months as monthly files
            key = m.strftime("%Y-%m")
            if "km" + key not in done: jobs.append((coin, "km", key))
            if "f" + key not in done: jobs.append((coin, "f", key))      # funding rates (monthly files)
        d = today - dt.timedelta(days=45)                   # recent days as daily files (fills months not yet published)
        while d < today:
            k = d.isoformat()
            if "kd" + k not in done and "km" + d.strftime("%Y-%m") not in done: jobs.append((coin, "kd", k))
            d += dt.timedelta(days=1)
        d = start                                           # positioning: daily files
        while d < today:
            k = d.isoformat()
            if "m" + k not in done: jobs.append((coin, "m", k))
            d += dt.timedelta(days=1)
    log(f"fetch: {len(jobs)} files to try")
    got = {}
    with ThreadPoolExecutor(32) as ex:
        for coin, kind, key, st, df in ex.map(lambda j: _job(*j), jobs):
            M = man[coin]
            if st == "ok": got.setdefault((coin, {"m": "m", "f": "f"}.get(kind, "k")), []).append(df); M["done"].append(kind + key)
            elif st == "missing":
                day = dt.date.fromisoformat(key if len(key) == 10 else key + "-01")
                if (today - day).days > (70 if kind in ("km", "f") else 7): M["done"].append(kind + key)   # give up on old gaps (e.g. coin not listed yet)
                else: M["miss"][kind + key] = M["miss"].get(kind + key, 0) + 1
    for (coin, part), dfs in got.items():
        path = f"{C.DATA_DIR}/raw/{coin}_{part}.parquet"
        new = pd.concat(dfs)
        if os.path.exists(path): new = pd.concat([pd.read_parquet(path), new])
        new = new.drop_duplicates("t", keep="last").sort_values("t").reset_index(drop=True)
        new.to_parquet(path, index=False)
    for coin in coins: man[coin]["done"] = sorted(set(man[coin]["done"]))
    json.dump(man, open(mpath, "w"))
    log(f"fetch: {sum(len(v) for v in got.values())} files added")
    return len(jobs)


def update_live(log=print):
    """the newest hours from the live relay, stored separately and OVERWRITTEN every run ("swap"):
    features.py stitches them onto the archive only where the archive has no data yet, so once Binance publishes those days
    the archive takes over automatically and nothing piles up."""
    url = C.RELAY_URL
    if not url: log("live: no relay configured (archive only)"); return {}
    coins = [c for c in C.COINS if c not in C.DEAD]
    r = S.get(url, params={"coins": ",".join(coins)}, timeout=120); r.raise_for_status(); d = r.json()
    os.makedirs(f"{C.DATA_DIR}/live", exist_ok=True); info = {"region": d.get("region"), "errors": d.get("errors", {}), "coins": 0, "check": {}}
    for c, v in d.get("coins", {}).items():
        k = pd.DataFrame(v.get("k", []), columns=["t", "o", "h", "l", "c", "v", "tb"])
        m5 = pd.DataFrame(v.get("m", []), columns=["t5", "oi", "glob", "topAcc", "topPos", "tk"])
        if len(k): k.astype({"t": "int64"}).to_parquet(f"{C.DATA_DIR}/live/{c}_k.parquet", index=False)
        if len(m5):   # exactly the archive's hourly aggregation: last 5-minute reading of each hour (taker ratio: hourly mean)
            m5 = m5.sort_values("t5"); m5["t"] = (m5.t5 // 3600 * 3600).astype("int64"); g = m5.groupby("t")
            m = pd.DataFrame({"oi": g.oi.last(), "glob": g.glob.last(), "topAcc": g.topAcc.last(), "topPos": g.topPos.last(), "tk": g.tk.mean()}).reset_index()
            m.to_parquet(f"{C.DATA_DIR}/live/{c}_m.parquet", index=False)
            # consistency check against the archive on the hours both have (should be ~0% difference)
            ap = f"{C.DATA_DIR}/raw/{c}_m.parquet"
            if os.path.exists(ap):
                A = pd.read_parquet(ap).set_index("t"); B = m.set_index("t"); both = A.index.intersection(B.index)
                if len(both) >= 6:
                    info["check"][c] = {col: round(float((B.loc[both, col] / A.loc[both, col] - 1).abs().median() * 100), 3) for col in ("oi", "glob", "topPos")}
        if len(k):
            ap = f"{C.DATA_DIR}/raw/{c}_k.parquet"
            if os.path.exists(ap):
                A = pd.read_parquet(ap).set_index("t"); B = k.set_index("t"); both = A.index.intersection(B.index)
                if len(both) >= 6: info["check"].setdefault(c, {})["close"] = round(float((B.loc[both, "c"] / A.loc[both, "c"] - 1).abs().median() * 100), 4)
        info["coins"] += 1
    newest = max((int(pd.read_parquet(f"{C.DATA_DIR}/live/{c}_k.parquet").t.max()) for c in d.get("coins", {}) if os.path.exists(f"{C.DATA_DIR}/live/{c}_k.parquet")), default=None)
    info["newest"] = newest
    log(f"live: {info['coins']} coins from relay ({info['region']}), newest hour {dt.datetime.utcfromtimestamp(newest) if newest else '-'}, errors {len(info['errors'])}")
    return info
