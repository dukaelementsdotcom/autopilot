"""MORE COLLECTORS (owner, Nov 24): the free sources from the data wish-list. Nothing here changes a decision; it only builds history.
Every source is real (public endpoints, no keys), time-boxed and failure-contained: if a source refuses us, the error is recorded in the run's collector report
and NOTHING is invented in its place.
  hourly snapshots (cannot be re-downloaded later)    BTC/ETH dominance, Bybit and OKX funding + open interest per coin, Polymarket odds on macro/crypto questions
  daily, history downloaded on the first run           DefiLlama stablecoin supply, chain TVL, DEX volume
  weekly, history downloaded on the first run          CFTC Commitments of Traders (Bitcoin and Ether futures, who holds what), with the date it became public
  daily, best effort                                   Spot ETF flows (Farside page; may refuse automated readers: then it simply records the refusal)
Saved in state/collect (kept by the memory) as parquet files."""
import re, io, json, time, datetime as dt
import numpy as np, pandas as pd, requests
from .collect import KEEP, S, H, _save, _merge

DAY = 86400
def get(url, params=None, timeout=25, headers=None):
    r = S.get(url, params=params, timeout=timeout, headers=headers); r.raise_for_status(); return r

def _num(x): return pd.to_numeric(x, errors="coerce")

def _due(name, secs):
    from .compass import _age
    return _age(name) > secs

def _done(name):
    from .compass import _stamp
    _stamp(name)

# ── hourly ──────────────────────────────────────────────────────────────────
def dominance(log=print):
    t = int(time.time()) // H * H; row = None; errs = []
    try:
        g = get("https://api.coingecko.com/api/v3/global").json()["data"]
        row = {"src": "coingecko", "btc_dom": g["market_cap_percentage"]["btc"], "eth_dom": g["market_cap_percentage"].get("eth"), "total_mcap_usd": g["total_market_cap"]["usd"], "mcap_chg24": g.get("market_cap_change_percentage_24h_usd")}
    except Exception as e: errs.append(f"coingecko: {str(e)[:80]}")
    if row is None:
        try:
            g = get("https://api.coinpaprika.com/v1/global").json()
            row = {"src": "coinpaprika", "btc_dom": g["bitcoin_dominance_percentage"], "eth_dom": None, "total_mcap_usd": g["market_cap_usd"], "mcap_chg24": g.get("market_cap_change_24h")}
        except Exception as e: errs.append(f"coinpaprika: {str(e)[:80]}")
    if row is None: return {"error": "; ".join(errs)}
    path = KEEP("mk_dominance.parquet"); df = pd.DataFrame([{"t": t, **row}]); out = _merge(path, df, ["t"]); _save(out, path)
    log(f"dominance: BTC {row['btc_dom']:.2f}% ({row['src']})"); return {"rows": int(len(out)), "btc_dom": round(float(row["btc_dom"]), 2), "src": row["src"]}

def venues(log=print):
    t = int(time.time()) // H * H; info = {}
    try:                                                                       # Bybit: one call, every USDT perpetual
        L = get("https://api.bybit.com/v5/market/tickers", {"category": "linear"}).json()["result"]["list"]
        df = pd.DataFrame([{"t": t, "coin": x["symbol"][:-4], "fr": _num(x.get("fundingRate")), "oi_usd": _num(x.get("openInterestValue")), "turn24h": _num(x.get("turnover24h")), "px": _num(x.get("lastPrice"))}
                           for x in L if x["symbol"].endswith("USDT") and "-" not in x["symbol"]]).sort_values("turn24h", ascending=False).head(150)
        path = KEEP("ex_bybit.parquet"); out = _merge(path, df, ["coin", "t"]); _save(out, path); info["bybit"] = {"coins": int(len(df)), "rows": int(len(out))}
    except Exception as e: info["bybit"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    try:                                                                       # OKX: open interest for every swap, funding for every swap
        oi = get("https://www.okx.com/api/v5/public/open-interest", {"instType": "SWAP"}).json()["data"]
        fr = get("https://www.okx.com/api/v5/public/funding-rate", {"instId": "ANY"}).json().get("data") or []
        f = {x["instId"]: _num(x.get("fundingRate")) for x in fr}
        rows = [{"t": t, "coin": x["instId"].split("-")[0], "fr": f.get(x["instId"]), "oi_usd": _num(x.get("oiUsd")), "oi_ccy": _num(x.get("oiCcy"))} for x in oi if x["instId"].endswith("-USDT-SWAP")]
        df = pd.DataFrame(rows).sort_values("oi_usd", ascending=False).head(150)
        path = KEEP("ex_okx.parquet"); out = _merge(path, df, ["coin", "t"]); _save(out, path); info["okx"] = {"coins": int(len(df)), "withFunding": int(df.fr.notna().sum()), "rows": int(len(out))}
    except Exception as e: info["okx"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    log("venues:", json.dumps(info)); return info

POLY_RE = re.compile(r"\b(fed|fomc|rate cut|rate hike|interest rate|inflation|cpi|recession|bitcoin|btc|ethereum|crypto|etf|tariff|trump|treasury|government shutdown|sec )\b", re.I)
def polymarket(log=print):
    t = int(time.time()) // H * H
    L = get("https://gamma-api.polymarket.com/markets", {"active": "true", "closed": "false", "limit": 300, "order": "volume24hr", "ascending": "false"}).json()
    rows = []
    for m in L:
        q = m.get("question") or ""
        if not POLY_RE.search(q): continue
        try: px = json.loads(m["outcomePrices"]) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices")
        except Exception: continue
        if not px: continue
        rows.append({"t": t, "id": str(m.get("id")), "question": q[:200], "yes": float(px[0]), "vol24": float(m.get("volume24hr") or 0), "liq": float(m.get("liquidityNum") or m.get("liquidity") or 0), "end": str(m.get("endDate") or "")[:10]})
    if not rows: return {"error": "no matching markets in the response", "markets_seen": len(L)}
    df = pd.DataFrame(rows).sort_values("vol24", ascending=False).head(60); path = KEEP("poly.parquet"); out = _merge(path, df, ["id", "t"]); _save(out, path)
    log(f"polymarket: {len(df)} markets"); return {"markets": int(len(df)), "rows": int(len(out))}

# ── daily / weekly (full history downloaded on the first run) ─────────────────────
def defillama(log=print):
    rows = []; errs = []
    def add(metric, pairs):
        for d, v in pairs:
            if v is not None and np.isfinite(float(v)): rows.append({"t": int(d) // DAY * DAY, "metric": metric, "value": float(v)})
    try:
        for x in get("https://stablecoins.llama.fi/stablecoincharts/all").json():
            add("stable_total", [(x["date"], (x.get("totalCirculatingUSD") or x.get("totalCirculating") or {}).get("peggedUSD"))])
    except Exception as e: errs.append(f"stablecoins: {str(e)[:80]}")
    try: add("tvl_all", [(x["date"], x["tvl"]) for x in get("https://api.llama.fi/v2/historicalChainTvl").json()])
    except Exception as e: errs.append(f"tvl_all: {str(e)[:80]}")
    for ch in ("Ethereum", "Solana", "Tron", "BSC", "Arbitrum", "Base"):
        try: add(f"tvl_{ch}", [(x["date"], x["tvl"]) for x in get(f"https://api.llama.fi/v2/historicalChainTvl/{ch}").json()])
        except Exception as e: errs.append(f"tvl_{ch}: {str(e)[:60]}")
    for ch in (None, "ethereum", "solana", "bsc", "base", "arbitrum"):
        try:
            u = "https://api.llama.fi/overview/dexs" + (f"/{ch}" if ch else ""); j = get(u, {"excludeTotalDataChart": "false", "excludeTotalDataChartBreakdown": "true"}, timeout=60).json()
            add(f"dex_vol_{ch or 'all'}", [(d, v) for d, v in j.get("totalDataChart", [])])
        except Exception as e: errs.append(f"dex_{ch}: {str(e)[:60]}")
    if not rows: return {"error": "; ".join(errs) or "empty"}
    path = KEEP("defi_daily.parquet"); out = _merge(path, pd.DataFrame(rows), ["t", "metric"]); _save(out, path)
    log(f"defillama: {len(rows)} points, {out.metric.nunique()} series"); return {"rows": int(len(out)), "series": int(out.metric.nunique()), **({"partial": errs[:4]} if errs else {})}

CFTC_COLS = ["open_interest_all", "dealer_positions_long_all", "dealer_positions_short_all", "asset_mgr_positions_long", "asset_mgr_positions_short", "lev_money_positions_long", "lev_money_positions_short",
             "other_rept_positions_long", "other_rept_positions_short", "nonrept_positions_long_all", "nonrept_positions_short_all"]
def cftc(log=print):
    where = "market_and_exchange_names like '%BITCOIN%' OR market_and_exchange_names like '%ETHER%'"
    j = get("https://publicreporting.cftc.gov/resource/gpe5-46if.json", {"$where": where, "$limit": 20000, "$order": "report_date_as_yyyy_mm_dd"}, timeout=90).json()
    if not j: return {"error": "empty answer"}
    d = pd.DataFrame(j); miss = [c for c in CFTC_COLS if c not in d.columns]
    day = pd.to_datetime(d["report_date_as_yyyy_mm_dd"]).dt.tz_localize("UTC")
    out = pd.DataFrame({"t": (day - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s"), "market": d["market_and_exchange_names"].str.slice(0, 60)})
    out["t_public"] = out["t"] + 3 * DAY + 20 * H + 1800             # reports describe Tuesday and come out Friday 3:30pm ET (~20:30 UTC): never use a row before this time
    for c in CFTC_COLS:
        if c in d.columns: out[c] = _num(d[c])
    path = KEEP("cftc.parquet"); res = _merge(path, out, ["t", "market"]); _save(res, path)
    log(f"cftc: {len(out)} rows, markets {sorted(out.market.unique())[:4]}"); return {"rows": int(len(res)), "markets": int(res.market.nunique()), "newest": str(pd.to_datetime(int(res.t.max()), unit="s").date()), **({"missingColumns": miss} if miss else {})}

def etf(log=print):
    hdr = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}; allrows = []
    for asset in ("btc", "eth"):
        r = get(f"https://farside.co.uk/{asset}/", headers=hdr, timeout=40)
        tabs = pd.read_html(io.StringIO(r.text)); tab = max(tabs, key=lambda x: x.shape[0])
        tab.columns = [str(c[-1] if isinstance(c, tuple) else c).strip() for c in tab.columns]; dc = tab.columns[0]
        for _, row in tab.iterrows():
            try: d = pd.to_datetime(str(row[dc]), format="%d %b %Y", utc=True)
            except Exception: continue
            for c in tab.columns[1:]:
                v = str(row[c]).replace(",", "").replace("(", "-").replace(")", "").strip()
                try: allrows.append({"t": int((d - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s")), "asset": asset, "ticker": c, "flow_musd": float(v)})
                except Exception: pass
    if not allrows: return {"error": "page read but no flow numbers found"}
    path = KEEP("etf_flows.parquet"); out = _merge(path, pd.DataFrame(allrows), ["t", "asset", "ticker"]); _save(out, path)
    log(f"etf flows: {len(allrows)} points"); return {"rows": int(len(out)), "newest": str(pd.to_datetime(int(out.t.max()), unit="s").date())}

def update(log=print):
    info = {}
    jobs = [("dominance", dominance, 0), ("venues", venues, 0), ("polymarket", polymarket, 0), ("defillama", defillama, 20 * H), ("cftc", cftc, 20 * H), ("etf", etf, 20 * H)]
    for name, fn, every in jobs:
        if every and not _due("more_" + name, every): continue
        t0 = time.time()
        try:
            r = fn(log=log); info[name] = r
            if every and not (isinstance(r, dict) and r.get("error")): _done("more_" + name)
        except Exception as e: info[name] = {"error": f"{type(e).__name__}: {str(e)[:150]}"}; log(f"   ! more/{name}: {e}")
        info[name] = {**(info[name] or {}), "secs": round(time.time() - t0, 1)}
    return info
