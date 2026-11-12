"""ORDER-BOOK HISTORY: Binance's free public archive of the futures order book (how much money is waiting to buy and to sell
within 0.2% / 1% / 5% of the price, every 30 seconds, since January 2023, for every coin).

It is published once a day (about 08:00 UTC for the day before), so it cannot say what the book looks like this minute; it says
how the book has been leaning and how thick or thin it has become over the last days. Used honestly: a day's numbers count only
from 10:00 UTC the next day. The back-fill (about 70,000 small files) is spread over the hourly runs with a time budget; the
measurements appear in the panel only once the back-fill is complete, so the witness is never trained on half a history."""
import io, os, json, time, zipfile, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd
from . import config as C
from .fetch import _get

DAY = 86400; H = 3600
START = dt.date(2023, 1, 1)
LAG_DAYS = 1 + 10 / 24                      # day D is usable from D+1 10:00 UTC (files appear ~08:00-08:20 UTC)
COLS = ["w_b_imb1", "w_b_imb5", "w_b_imb02", "w_b_imb1w", "w_b_late", "w_b_dep", "w_b_dep7", "w_b_ofi", "w_b_ofi3", "w_b_front", "w_b_rel", "w_b_mkt"]
PATH = lambda coin: f"{C.DATA_DIR}/raw/book_{coin}.parquet"
MAN = lambda: f"{C.DATA_DIR}/raw/book_manifest.json"

def _day(blob):
    z = zipfile.ZipFile(io.BytesIO(blob)); x = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
    x["t"] = pd.to_datetime(x.timestamp).values.astype("datetime64[s]").astype("int64")
    pv = x.pivot_table(index="t", columns="percentage", values="notional", aggfunc="last").sort_index()
    g = lambda k: pv[k].astype(float) if k in pv else pd.Series(np.nan, index=pv.index)
    d = pd.DataFrame({"b02": g(-0.2), "a02": g(0.2), "b1": g(-1.0), "a1": g(1.0), "b5": g(-5.0), "a5": g(5.0)})
    d["ofi"] = (d.b1 - d.a1).diff()            # order-flow imbalance: bids added minus asks added between snapshots
    hour = (d.index.values // H) * H; gb = d.groupby(hour)
    out = gb[["b02", "a02", "b1", "a1", "b5", "a5"]].mean(); out["ofi"] = gb.ofi.sum(); out["n"] = gb.b1.count()
    out.index.name = "t"; return out.reset_index().astype({"t": "int64"}).astype({c: "float32" for c in ("b02", "a02", "b1", "a1", "b5", "a5", "ofi")})

def _job(coin, day):
    try:
        b = _get(f"{C.ARCHIVE}/daily/bookDepth/{coin}USDT/{coin}USDT-bookDepth-{day}.zip")
        return coin, day, ("missing", None) if b is None else ("ok", _day(b))
    except Exception as e: return coin, day, ("error", str(e)[:100])

def _man():
    try: return json.load(open(MAN()))
    except Exception: return {"done": {}, "miss": {}, "complete": False}

def update(log=print, budget=540, today=None, coins=None):
    """download what is new (newest days first), for at most `budget` seconds"""
    t0 = time.time(); coins = coins or C.COINS; today = today or dt.datetime.utcnow().date(); man = _man()
    os.makedirs(f"{C.DATA_DIR}/raw", exist_ok=True)
    for c in coins:                                           # a lost or damaged file means that coin starts again
        if man["done"].get(c) and not os.path.exists(PATH(c)): man["done"][c] = []; man["miss"][c] = {}
    days = [(today - dt.timedelta(days=i)).isoformat() for i in range(1, (today - START).days + 1)]
    jobs = []
    for d in days:
        recent = (today - dt.date.fromisoformat(d)).days <= 7
        for c in coins:
            if d in man["done"].get(c, ()): continue
            if man["miss"].get(c, {}).get(d, 0) >= (6 if recent else 1): continue     # old gaps are final; new days are retried for a while
            jobs.append((c, d))
    todo = len(jobs); got = {}; nok = nmiss = nerr = 0
    if jobs:
        with ThreadPoolExecutor(24) as ex:
            for i in range(0, len(jobs), 480):
                if time.time() - t0 > budget: break
                for c, d, (st, val) in ex.map(lambda j: _job(*j), jobs[i:i + 480]):
                    if st == "ok": got.setdefault(c, []).append((d, val)); nok += 1
                    elif st == "missing": man["miss"].setdefault(c, {})[d] = man["miss"].get(c, {}).get(d, 0) + 1; nmiss += 1
                    else: nerr += 1
        for c, parts in got.items():
            new = pd.concat([p for _, p in parts])
            if os.path.exists(PATH(c)):
                try: new = pd.concat([pd.read_parquet(PATH(c)), new])
                except Exception: pass
            new = new.drop_duplicates("t", keep="last").sort_values("t")
            new.to_parquet(PATH(c) + ".tmp", index=False); os.replace(PATH(c) + ".tmp", PATH(c))
            man["done"].setdefault(c, []).extend(d for d, _ in parts)
    left = todo - nok - nmiss
    was = man.get("complete", False); man["complete"] = bool(was or (left <= len(coins) * 2 and nerr == 0 and sum(len(v) for v in man["done"].values()) > 1000))
    json.dump(man, open(MAN() + ".tmp", "w")); os.replace(MAN() + ".tmp", MAN())
    ndone = sum(len(v) for v in man["done"].values())
    # the data cache is saved only when a version file changes: while back-filling, mark progress every run
    open(f"{C.DATA_DIR}/raw/version_book.txt", "w").write(f"{ndone}" if nok else open(f"{C.DATA_DIR}/raw/version_book.txt").read() if os.path.exists(f"{C.DATA_DIR}/raw/version_book.txt") else "0")
    log(f"order-book history: {nok} new days ({nmiss} not published, {nerr} errors), {ndone} stored, {max(left, 0)} still to fetch, complete={man['complete']}, {time.time() - t0:.0f}s")
    return {"new": nok, "stored": ndone, "left": max(left, 0), "complete": man["complete"], **({"errors": nerr} if nerr else {})}

def daily(coin):
    """one row per day for a coin: how the book leaned and how thick it was"""
    h = pd.read_parquet(PATH(coin)); h["d"] = (h.t // DAY) * DAY; g = h.groupby("d")
    s = g[["b02", "a02", "b1", "a1", "b5", "a5", "ofi", "n"]].sum()
    imb = lambda b, a: (b - a) / (b + a).replace(0, np.nan)
    o = pd.DataFrame(index=s.index)
    o["imb1"] = imb(s.b1, s.a1); o["imb5"] = imb(s.b5, s.a5); o["imb02"] = imb(s.b02, s.a02).where(s.b02 + s.a02 > 0)
    late = h[(h.t % DAY) >= 18 * H].groupby("d")[["b1", "a1"]].sum(); o["late"] = imb(late.b1, late.a1).reindex(o.index)
    dep = np.log(((s.b1 + s.a1) / s.n.replace(0, np.nan) * 24).replace(0, np.nan))          # average money within 1% of the price
    o["dep"] = dep - dep.rolling(30, min_periods=10).median(); o["dep7"] = dep.diff(7)
    of = s.ofi / (s.b1 + s.a1).replace(0, np.nan) * s.n; o["ofi"] = of; o["ofi3"] = of.rolling(3, min_periods=2).mean()
    o["imb1w"] = o.imb1.rolling(7, min_periods=3).mean()
    o["front"] = np.log(((s.b1 + s.a1) / (s.b5 + s.a5).replace(0, np.nan)).replace(0, np.nan))  # how much of the book sits close to the price
    return o[s.n >= 12]

def features(P, log=print):
    """per-coin columns for the panel (empty until the back-fill is complete)"""
    if not _man().get("complete"): return {}
    from .witnesses import _daily_to_hourly
    ts = np.sort(P.t.unique()); frames = {}
    for c in pd.unique(P.coin):
        try:
            if os.path.exists(PATH(c)): frames[c] = daily(c)
        except Exception as e: log(f"   order book {c} skipped: {e}")
    if len(frames) < 10: return {}
    mk = pd.concat([f.imb1.rename(c) for c, f in frames.items()], axis=1).mean(axis=1)       # the whole market's lean
    names = {"w_b_imb1": "imb1", "w_b_imb5": "imb5", "w_b_imb02": "imb02", "w_b_imb1w": "imb1w", "w_b_late": "late", "w_b_dep": "dep", "w_b_dep7": "dep7",
             "w_b_ofi": "ofi", "w_b_ofi3": "ofi3", "w_b_front": "front"}
    out = {k: np.full(len(P), np.nan, "float32") for k in COLS}; coin = P.coin.values; tP = P.t.values
    mkh = pd.Series(_daily_to_hourly(ts, mk.index.values, mk.values, LAG_DAYS), index=ts)
    for c, f in frames.items():
        m = coin == c
        if not m.any(): continue
        tt = tP[m]
        for k, col in names.items():
            out[k][m] = pd.Series(_daily_to_hourly(ts, f.index.values, f[col].values, LAG_DAYS), index=ts).reindex(tt).values
        out["w_b_mkt"][m] = mkh.reindex(tt).values
        out["w_b_rel"][m] = out["w_b_imb1"][m] - out["w_b_mkt"][m]
    return out
