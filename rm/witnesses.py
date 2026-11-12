"""NEW WITNESSES: new kinds of evidence, each trained as one extra family and entered as a challenger.
A witness reaches the referee only if the referee WITH it beats the referee without it on live candidates.

  macro     the outside world, daily, known only from the next day (no peeking): Nasdaq-100, S&P 500, VIX, US 10-year yield, dollar
  onchain   BTC / ETH exchange flows and exchange balances (Coin Metrics), known only from the next day
  sectors   money moving between groups of coins (AI, memes, layer-1s, DeFi, exchange tokens...), from prices
  shock     how big the market's last move was compared with its normal size (the shock memory's input)
Waiting for history (need ~3 weeks before they can be trained; data has been recording since Nov 9-10):
  recorder  liquidations, order-book and order-flow imbalance, live funding (own server)
  whales    largest Hyperliquid accounts' positions
  news      headlines tagged by topic"""
import os, time, json, numpy as np, pandas as pd
from . import config as C
from . import challengers as CH

H = 3600; DAY = 86400
SECTORS = {
    "major": "BTC ETH", "l1": "SOL ADA AVAX DOT NEAR ATOM APT SUI SEI TIA ICP ALGO HBAR XLM TRX TON KAS ETC BCH LTC XRP",
    "l2": "ARB OP IMX STX", "defi": "UNI AAVE CRV LDO PENDLE RUNE ENA ONDO JUP INJ", "ai": "FET TAO WLD FIL",
    "meme": "DOGE 1000PEPE 1000SHIB WIF ORDI", "exchange": "BNB HYPE", "privacy": "ZEC"}
SECTOR_OF = {c: s for s, cs in SECTORS.items() for c in cs.split()}

WITNESSES = {
    "macro":   {"cols": ["w_ndx1", "w_ndx5", "w_spx5", "w_vix", "w_vix5", "w_y10_5", "w_dxy5"], "what": "Outside-world witness: Nasdaq, S&P 500, VIX, US yields and the dollar (risk-on / risk-off), known only from the next day"},
    "onchain": {"cols": ["w_btcnet", "w_btcsup7", "w_ethnet", "w_ethsup7"], "what": "On-chain witness: Bitcoin and Ethereum flowing into / out of exchanges, and exchange balances"},
    "sectors": {"cols": ["w_sec24", "w_secrank", "w_insec", "w_sec168"], "what": "Sector witness: money rotating between AI, memes, layer-1s, DeFi and exchange tokens"},
    "global":  {"cols": [f"w_g_{x}{n}" for x in ("spx", "ndx", "dax", "nikkei", "hsi", "dxy", "gold", "oil", "nvda", "chips", "coin") for n in (5, 20)] + ["w_g_link"],
                "what": "Compass global-markets witness: US, European and Asian stocks, the dollar, gold, oil, NVIDIA and chips, Coinbase, and how tightly Bitcoin follows stocks right now"},
    "mood":    {"cols": ["w_g_fng", "w_g_fng7", "w_g_dvol", "w_g_dvol7", "w_g_edvol", "w_g_edvol7"], "what": "Compass mood witness: the crypto Fear & Greed index and the options market's fear gauge (implied volatility), and how fast they are changing"},
    "flows":   {"cols": ["w_f_stab7", "w_f_stab30", "w_f_stab90"], "what": "Compass money-flow witness: dollar stablecoin supply growing or shrinking (new money arriving in / leaving crypto)"},
    "book":    {"cols": ["w_b_imb1", "w_b_imb5", "w_b_imb02", "w_b_imb1w", "w_b_late", "w_b_dep", "w_b_dep7", "w_b_ofi", "w_b_ofi3", "w_b_front", "w_b_rel", "w_b_mkt"],
                "what": "Order-book witness: how much money has been waiting to buy versus to sell near the price over the last days, and whether the book is getting thinner (Binance's own archive since 2023, one day behind)"},
    "shock":   {"cols": ["w_shock1", "w_shock4", "w_shockN"], "what": "Shock witness: how unusual the market's last moves were (what usually follows a shock like this)"},
}
WAITING = {"recorder": "Recorder witness: liquidations, order-book and order-flow imbalance, live funding (own server)",
           "whales": "Whale witness: the largest Hyperliquid accounts' positions",
           "news": "News witness: headlines tagged by the AI"}
CH.WITNESSES.update({k: v for k, v in WITNESSES.items()})
MODEL = lambda name: f"{C.DATA_DIR}/models/w_{name}.joblib"

# ── features (added to the panel; prefix w_ so the research engine and the existing families never see them) ─────────────
def _daily_to_hourly(ts_hours, d_t, vals, lag_days=1):
    """a daily value for date d becomes usable from d + lag (no peeking); forward-filled across hours"""
    s = pd.Series(np.asarray(vals, float), index=np.asarray(d_t, np.int64) + lag_days * DAY).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(np.union1d(s.index, ts_hours)).ffill().reindex(ts_hours).values

def panel_extras(P, log=print):
    out = {}; ts = np.sort(P.t.unique()); tP = P.t.values
    def put(name, hourly):                        # market-wide (same value for every coin at an hour)
        out[name] = pd.Series(hourly, index=ts).reindex(tP).astype("float32").values
    try:
        m = pd.read_parquet(f"{C.DATA_DIR}/raw/macro.parquet").sort_values("t").drop_duplicates("t")
        def chg(col, n, diff=False):
            x = m[["t", col]].dropna(); v = x[col].values
            r = (v[n:] - v[:-n]) if diff else (v[n:] / v[:-n] - 1); return x.t.values[n:], r
        for nm, (col, n, diff) in {"w_ndx1": ("ndx", 1, False), "w_ndx5": ("ndx", 5, False), "w_spx5": ("spx", 5, False), "w_vix5": ("vix", 5, True),
                                    "w_y10_5": ("us10y", 5, True), "w_dxy5": ("dxy", 5, False)}.items():
            if col in m: t_, r_ = chg(col, n, diff); put(nm, _daily_to_hourly(ts, t_, r_))
        if "vix" in m: x = m[["t", "vix"]].dropna(); put("w_vix", _daily_to_hourly(ts, x.t.values, x.vix.values))
    except Exception as e: log(f"   witness macro features skipped: {e}")
    try:
        from . import compass; compass.features(P, ts, put, log)
    except Exception as e: log(f"   compass features skipped: {e}")
    try:
        o = pd.read_parquet(f"{C.DATA_DIR}/raw/onchain.parquet")
        for a in ("btc", "eth"):
            x = o[o.asset == a].sort_values("t"); net = (x.FlowInExNtv - x.FlowOutExNtv) / x.SplyExNtv
            z = (net - net.rolling(30, min_periods=10).mean()) / net.rolling(30, min_periods=10).std()
            put(f"w_{a}net", _daily_to_hourly(ts, x.t.values, z.values)); put(f"w_{a}sup7", _daily_to_hourly(ts, x.t.values, x.SplyExNtv.pct_change(7).values))
    except Exception as e: log(f"   witness on-chain features skipped: {e}")
    try:
        sec = P.coin.map(SECTOR_OF).fillna("other").values
        df = pd.DataFrame({"t": tP, "s": sec, "r24": P.r24.values, "r168": P.r168.values if "r168" in P else np.nan})
        mk = df.groupby("t").r24.transform("mean"); sm = df.groupby(["t", "s"]).r24.transform("mean")
        out["w_sec24"] = (sm - mk).astype("float32").values; out["w_insec"] = (df.r24 - sm).astype("float32").values
        out["w_sec168"] = (df.groupby(["t", "s"]).r168.transform("mean") - df.groupby("t").r168.transform("mean")).astype("float32").values
        g = df.groupby(["t", "s"]).r24.mean().groupby(level=0).rank(pct=True)
        out["w_secrank"] = pd.Series(g.values, index=g.index).reindex(pd.MultiIndex.from_arrays([tP, sec])).astype("float32").values
    except Exception as e: log(f"   witness sector features skipped: {e}")
    try:
        b = P.loc[P.coin.values == "BTC", ["t", "r1", "vol24"]].drop_duplicates("t").set_index("t").reindex(ts)
        put("w_shock1", (b.r1 / b.vol24.replace(0, np.nan)).values)
        r4 = b.r1.rolling(4, min_periods=4).sum(); put("w_shock4", (r4 / (b.vol24 * 2).replace(0, np.nan)).values)
        big = (np.abs(b.r1 / b.vol24.replace(0, np.nan)) > 3).astype(float); put("w_shockN", big.rolling(72, min_periods=1).sum().values)
    except Exception as e: log(f"   witness shock features skipped: {e}")
    try:
        from . import book; out.update(book.features(P, log))
    except Exception as e: log(f"   order-book features skipped: {e}")
    try:
        from . import compass; out["p_mtrend"] = compass.trend_panel(P, out)      # the market's trend as a measurement (research rules can condition on it)
    except Exception as e: log(f"   trend column skipped: {e}")
    return out

# ── training (one witness per run, in the staggered training slot) ───────────────────────────────────────────────────────
def _load(name):
    import joblib
    try: return joblib.load(MODEL(name)) if os.path.exists(MODEL(name)) else None
    except Exception: return None

_MC = {}
def _cached(name):
    p = MODEL(name); mt = os.path.getmtime(p) if os.path.exists(p) else None
    if name not in _MC or _MC[name][0] != mt: _MC[name] = (mt, _load(name) if mt else None)
    return _MC[name][1]

def model(name, B):
    m = _cached(name)
    return m if m and not m.get("failed") and B is not None and m.get("bundle") == B["trainedAt"] and m.get("want", m.get("cols")) == WITNESSES[name]["cols"] else None

def _tried(name, B):
    m = _cached(name); return bool(m) and B is not None and m.get("bundle") == B["trainedAt"] and (m.get("want", m.get("cols")) == WITNESSES[name]["cols"] or (m.get("failed") and "want" not in m and name not in ("mood", "flows")))

def ready(B):
    return [n for n in WITNESSES if model(n, B) is not None]

def status(B, J=None):
    """what every witness is doing right now (for the dashboard)"""
    st = {}
    for n, w in WITNESSES.items():
        m = model(n, B)
        st[n] = {"what": w["what"], "state": "challenger (being scored live)" if m else "waiting to be trained", **({"exam": m.get("exam")} if m else {})}
        if n == "book" and not m:
            try:
                from . import book; bm = book._man(); nd = sum(len(v) for v in bm["done"].values())
                if not bm.get("complete"): st[n]["state"] = f"back-filling its history from Binance's archive ({nd:,} coin-days so far)"
            except Exception: pass
    for n, what in WAITING.items():
        st[n] = {"what": what, "state": f"recording; needs ~21 days of history ({_days(n):.0f} so far)"}
    return st

def _days(n):
    try:
        if n == "recorder": t = pd.read_parquet(f"{C.STATE_DIR}/collect/rec_depth.parquet", columns=["t"]).t
        elif n == "whales": t = pd.read_parquet(f"{C.STATE_DIR}/collect/hl_whales.parquet", columns=["t"]).t
        else:
            fs = [f for f in os.listdir(f"{C.STATE_DIR}/collect/news") if f.endswith(".jsonl.gz")]; return float(len(fs))
        return float((t.max() - t.min()) / DAY)
    except Exception: return 0.0

def fit(name, J, B, Hd, log=print):
    """the witness as one extra family (trained on the discovery years like the others), then the combiner refit on the validation
    year with it included. Its final exam is recorded next to the champion's."""
    from sklearn.ensemble import HistGradientBoostingClassifier as HGB
    from sklearn.linear_model import LogisticRegression
    from . import referee as RF
    cols = [c for c in WITNESSES[name]["cols"] if c in J.P.columns]
    if len(cols) < 2: return None
    t0 = time.time(); P = J.P; D = RF._rows(J, int(J.t.min()), J.val0); out = {"bundle": B["trainedAt"], "cols": cols, "sides": {}, "fitAt": time.strftime("%Y-%m-%d %H:%M", time.gmtime())}
    D = D[np.isfinite(RF._X(P, D, cols)).mean(1) >= 0.5]                    # only the years where this evidence existed
    if len(D) < 20000: log(f"   witness {name}: too little history ({len(D)} rows)"); return None
    for name_s, sd in B["sides"].items():
        side = 1 if name_s == "long" else -1
        y, _ = RF.barrier(J, D, side, RF.TARGET, RF.stop_pct(J.dvol[D]), RF.HORIZON)
        m = HGB(max_iter=150, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=300, l2_regularization=1.0, early_stopping=False, random_state=0).fit(RF._X(P, D, cols), y)
        hv = Hd["sides"][name_s]["V"]; Vr = _rows_of(J, hv["coin"], hv["t"]); ok = Vr >= 0
        wv = m.predict_proba(RF._X(P, Vr[ok], cols))[:, 1]
        Xc = np.column_stack([hv["lg"][ok], CH._lg(wv)])
        comb = LogisticRegression(C=0.5, max_iter=500).fit(Xc, hv["y"][ok])
        base_ll = _ll(hv["y"][ok], sd["comb"].predict_proba(hv["lg"][ok])[:, 1]); new_ll = _ll(hv["y"][ok], comb.predict_proba(Xc)[:, 1])
        out["sides"][name_s] = {"model": m, "comb": comb, "order": list(sd["order"]) + [f"w:{name}"], "valLL": round(new_ll, 4), "champValLL": round(base_ll, 4)}
    out["secs"] = round(time.time() - t0); out["exam"] = {n: {"valLL": v["valLL"], "champValLL": v["champValLL"]} for n, v in out["sides"].items()}
    log(f"   witness {name}: trained in {out['secs']}s, validation log-loss {out['exam']}")
    return out

def _ll(y, p): p = np.clip(p, 1e-4, 1 - 1e-4); return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

def _rows_of(J, coins, ts):
    idx = pd.MultiIndex.from_arrays([J.coin, J.t]); return idx.get_indexer(pd.MultiIndex.from_arrays([np.asarray(coins), np.asarray(ts)]))

def maybe_fit(J, mem, log=print, time_left=1e9):
    """one witness per run (staggered): the first one without a model for the current brain"""
    from . import referee as RF
    B = RF.load(); Hd = CH.load_heads(B) if B else None
    if B is None or Hd is None or "V" not in next(iter(Hd["sides"].values())): return False
    for n in WITNESSES:
        if _tried(n, B): continue
        if not any(c in J.P.columns for c in WITNESSES[n]["cols"]): continue        # its measurements are not in the panel yet (history still arriving)
        if time_left < 8 * 60: log("witnesses: postponed (not enough time this run)"); return False
        m = fit(n, J, B, Hd, log)
        import joblib
        os.makedirs(os.path.dirname(MODEL(n)), exist_ok=True)
        m = m or {"bundle": B["trainedAt"], "failed": True, "sides": {}}; m["want"] = list(WITNESSES[n]["cols"])
        joblib.dump(m, MODEL(n) + ".tmp", compress=3); os.replace(MODEL(n) + ".tmp", MODEL(n)); _MC.pop(n, None)
        return True
    return False

def prob_rows(name, side, J, rows, fams, B):
    """the referee's odds WITH this witness, for candidate rows"""
    m = model(name, B)
    if not m or side not in m.get("sides", {}): return None
    s = m["sides"][side]; from . import referee as RF
    wv = s["model"].predict_proba(RF._X(J.P, rows, m["cols"]))[:, 1]
    Xc = np.column_stack([CH._lg(np.asarray(fams[f])) for f in s["order"][:-1]] + [CH._lg(wv)])
    return s["comb"].predict_proba(Xc)[:, 1]
