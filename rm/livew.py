"""LIVE-HISTORY WITNESSES: evidence we record ourselves (it cannot be downloaded for past years), so it can never take the usual
exam on a past validation year. Instead, once enough days are recorded (the warm-up), the witness learns from the Arbiter's own
scored candidates, in time order:

  1. measurements, hourly, per coin, each known BEFORE the decision that uses it (panel row t is decided at the start of hour t;
     a recording stamped hour h only counts from row h+1h)
  2. at the end of the warm-up: a small model that corrects the Arbiter's recorded odds with the new measurements, trained on the
     earlier 70% of the scored candidates and examined on the later 30% (2-day gap so no outcome overlaps); if it is not worse
     there, it is trained on everything and joins the race as 'witness-<name>' under the same frozen judge (live, out-of-sample)
  3. it retrains automatically every time the Arbiter's brain retrains; a failed exam is retried every 3 days with more data

  recorder  our own server's minute recorder: live order-book lean and thickness, Binance live funding (who pays to hold), mark
            premium, live liquidations per coin and market-wide
  whales    the 50 largest Hyperliquid accounts: net long/short per coin, its change, the whales' market-wide lean
  news      headline flow by topic (crypto, macro, geopolitics, regulation) vs its normal level, bursts, the AI brief's risk mood"""
import os, json, glob, gzip, time, numpy as np, pandas as pd
from . import config as C

H = 3600; DAY = 86400
KEEP = lambda n: f"{C.STATE_DIR}/collect/{n}"
LIVE = {
    "recorder": {"cols": ["w_r_imb", "w_r_imb6", "w_r_dep", "w_r_fr", "w_r_fr24", "w_r_frx", "w_r_prem", "w_r_lnet24", "w_r_lsz24", "w_r_mlnet24"],
                 "what": "Recorder witness (own server, live): order-book lean and thickness near the price, Binance live funding and who is paying it, mark premium, and liquidations per coin and market-wide",
                 "warm": 21},
    "whales":   {"cols": ["w_h_net", "w_h_net24", "w_h_cnt", "w_h_mkt", "w_h_mkt24"],
                 "what": "Whale witness: how the 50 largest Hyperliquid accounts are positioned per coin (net long/short), how fast it is changing, and their market-wide lean",
                 "warm": 21},
    "news":     {"cols": ["w_n_crypto", "w_n_macro", "w_n_geo", "w_n_reg", "w_n_burst", "w_n_risk"],
                 "what": "News witness: headline flow by topic (crypto, macro, geopolitics, regulation) against its normal level, sudden bursts, and the AI brief's risk mood",
                 "warm": 21},
}
MIN_ROWS = 300            # scored candidates per side needed to train
RETRY_DAYS = 3

# ── when did each one start recording ────────────────────────────────────────────────────────────────────────────────────────
def first_t(name):
    try:
        if name == "recorder": return int(pd.read_parquet(KEEP("rec_depth.parquet"), columns=["t"]).t.min())
        if name == "whales": return int(pd.read_parquet(KEEP("hl_whales.parquet"), columns=["t"]).t.min())
        fs = sorted(glob.glob(KEEP("news/*.jsonl.gz")))
        return int(pd.Timestamp(os.path.basename(fs[0])[:10], tz="UTC").timestamp()) if fs else None
    except Exception: return None

def last_t(name):
    try:
        if name == "recorder": return int(pd.read_parquet(KEEP("rec_depth.parquet"), columns=["t"]).t.max())
        if name == "whales": return int(pd.read_parquet(KEEP("hl_whales.parquet"), columns=["t"]).t.max())
        return int(max(_news().t)) if len(_news()) else None
    except Exception: return None

def days(name):
    f = first_t(name); return (time.time() - f) / DAY if f else 0.0

def warm_left(name): return max(0.0, LIVE[name]["warm"] - days(name))

# ── measurements ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _roll(df, col, n, fn="mean", minp=None):
    return df.groupby("coin")[col].transform(lambda s: getattr(s.rolling(n, min_periods=minp or max(1, n // 3)), fn)())

# Panel row t is decided at the START of hour t (features use hours <= t-1). A recording stamped hour h covers [h, h+1h) (or was
# taken during it), so it may only appear from row h+1h: every live measurement is moved one hour later. (No look-ahead.)
LAG = H

def _per_coin(P, df, cols):
    """per-coin hourly table -> panel columns (rows without a recording stay NaN)"""
    idx = pd.MultiIndex.from_arrays([P.coin.values, P.t.values]); df = df.drop_duplicates(["coin", "t"]).assign(t=lambda x: x.t + LAG).set_index(["coin", "t"])
    return {c: df[c].reindex(idx).astype("float32").values for c in cols}

def _market(P, s, name):
    s = pd.Series(s); s.index = s.index + LAG
    return {name: s.reindex(P.t.values).astype("float32").values}

def recorder_features(P):
    out = {}
    d = pd.read_parquet(KEEP("rec_depth.parquet")).sort_values(["coin", "t"])
    d["dep"] = np.log((d.b1 + d.a1).replace(0, np.nan))
    d["w_r_imb"] = d.imb1; d["w_r_imb6"] = _roll(d, "imb1", 6)
    d["w_r_dep"] = d.dep - _roll(d, "dep", 72, "median", 12)
    out.update(_per_coin(P, d, ["w_r_imb", "w_r_imb6", "w_r_dep"]))
    f = pd.read_parquet(KEEP("rec_funding.parquet")).sort_values(["coin", "t"])
    f["w_r_fr"] = f.fr * 1e4; f["w_r_fr24"] = _roll(f, "w_r_fr", 24)
    f["w_r_frx"] = f.w_r_fr - f.groupby("t").w_r_fr.transform("median")               # this coin's funding vs the market's: crowding
    f["w_r_prem"] = _roll(f, "prem", 6)
    out.update(_per_coin(P, f, ["w_r_fr", "w_r_fr24", "w_r_frx", "w_r_prem"]))
    l = pd.read_parquet(KEEP("rec_liq.parquet"))
    grid_t = np.arange(int(l.t.min()), int(l.t.max()) + H, H)
    mk = l.groupby("t")[["liqL", "liqS"]].sum().reindex(grid_t, fill_value=0).rolling(24, min_periods=6).sum()
    out.update(_market(P, (mk.liqS - mk.liqL) / (mk.liqS + mk.liqL).replace(0, np.nan), "w_r_mlnet24"))
    coins = set(P.coin.unique()); l = l[l.coin.isin(coins)]
    g = (l.set_index(["coin", "t"])[["liqL", "liqS"]].unstack("coin").reindex(grid_t).fillna(0))      # missing hour = no liquidations
    L24, S24 = g["liqL"].rolling(24, min_periods=6).sum(), g["liqS"].rolling(24, min_periods=6).sum()
    net = ((S24 - L24) / (S24 + L24).replace(0, np.nan)).stack().rename("w_r_lnet24")
    sz = np.log1p(L24 + S24); szr = (sz - sz.rolling(168, min_periods=48).median()).stack().rename("w_r_lsz24")
    lt = pd.concat([net, szr], axis=1).reset_index().rename(columns={"level_0": "t"})
    lt.columns = ["t", "coin", "w_r_lnet24", "w_r_lsz24"]
    out.update(_per_coin(P, lt, ["w_r_lnet24", "w_r_lsz24"]))
    return out

def whale_features(P):
    w = pd.read_parquet(KEEP("hl_whales.parquet")).sort_values(["coin", "t"])
    w["w_h_net"] = (w.wLong - w.wShort) / (w.wLong + w.wShort).replace(0, np.nan)
    w["w_h_cnt"] = (w.wNl - w.wNs).astype(float)
    w["w_h_net24"] = w.w_h_net - w.groupby("coin").w_h_net.shift(24)
    out = _per_coin(P, w, ["w_h_net", "w_h_net24", "w_h_cnt"])
    m = w.groupby("t")[["wLong", "wShort"]].sum(); mn = (m.wLong - m.wShort) / (m.wLong + m.wShort)
    out.update(_market(P, mn, "w_h_mkt")); out.update(_market(P, mn - mn.shift(24), "w_h_mkt24"))
    return out

_NC = {}
def _news():
    fs = sorted(glob.glob(KEEP("news/*.jsonl.gz"))); key = tuple((f, os.path.getsize(f)) for f in fs)
    if _NC.get("k") != key:
        rows = []
        for f in fs:
            try:
                with gzip.open(f, "rt") as fh:
                    for line in fh:
                        try: x = json.loads(line); rows.append((int(x["t"]), x.get("topic") or "other"))
                        except Exception: pass
            except Exception: pass
        _NC.update(k=key, df=pd.DataFrame(rows, columns=["t", "topic"]))
    return _NC["df"]

def news_features(P):
    n = _news(); out = {}
    if n.empty: return out
    n = n.assign(h=(n.t // H) * H); grid = np.arange(int(n.h.min()), int(n.h.max()) + H, H)
    cnt = n.groupby(["h", "topic"]).size().unstack(fill_value=0).reindex(grid, fill_value=0)
    for topic, col in (("crypto", "w_n_crypto"), ("macro", "w_n_macro"), ("geopolitics", "w_n_geo"), ("regulation", "w_n_reg")):
        s = cnt[topic] if topic in cnt else pd.Series(0, index=grid)
        c24 = s.rolling(24, min_periods=12).sum(); norm = c24.rolling(168, min_periods=72).mean()
        out.update(_market(P, np.log((c24 + 1) / (norm + 1)), col))                      # today's flow vs the last week's normal
    tot = cnt.sum(axis=1); out.update(_market(P, (tot.rolling(6, min_periods=3).sum() * 4 + 1) / (tot.rolling(24, min_periods=12).sum() + 1), "w_n_burst"))
    try:
        dg = [json.loads(l) for l in open(KEEP("digest.jsonl")) if l.strip()]
        r = pd.Series({(int(x["t"]) // H) * H: float(x["risk"]) for x in dg if x.get("risk") is not None}).sort_index()
        r = r.reindex(np.union1d(r.index, grid)).ffill(limit=12)                         # a brief counts for 12 hours at most
        out.update(_market(P, r, "w_n_risk"))
    except Exception: pass
    return out

def features(P, log=print):
    out = {}
    for name, fn in (("recorder", recorder_features), ("whales", whale_features), ("news", news_features)):
        try: out.update(fn(P))
        except Exception as e: log(f"   live witness {name} features skipped: {e}")
    return out

# ── training on our own scored candidates ──────────────────────────────────────────────────────────────────────────────────
def _lg(p): p = np.clip(np.asarray(p, float), 1e-3, 1 - 1e-3); return np.log(p / (1 - p))
def _ll(y, p): p = np.clip(p, 1e-4, 1 - 1e-4); return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

def fit(name, J, B, R, log=print):
    from sklearn.linear_model import LogisticRegression
    from . import archive
    cols = [c for c in LIVE[name]["cols"] if c in J.P.columns]
    out = {"bundle": B["trainedAt"], "cols": cols, "sides": {}, "fitAt": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "live": True}
    if len(cols) < 2: out.update(failed=True, why="its measurements are not in the panel"); return out
    L = [x for x in archive.read("ledger") + (R.get("ledger") or []) if x.get("y") is not None]
    t0 = (first_t(name) or 0) + 2 * DAY                    # the rolling measurements need a little history first
    L = [x for x in L if x["t"] >= t0]
    idx = pd.MultiIndex.from_arrays([J.coin, J.t])
    why = []
    for side in B["sides"]:
        d = sorted([x for x in L if x["side"] == side], key=lambda x: x["t"])
        if not d: why.append(f"{side}: no scored candidates"); continue
        r = idx.get_indexer(pd.MultiIndex.from_arrays([[x["coin"] for x in d], [x["t"] for x in d]])); ok = r >= 0
        d = [x for x, o in zip(d, ok) if o]; r = r[ok]
        X = np.column_stack([J.P[c].values[r] for c in cols]).astype(float)
        keep = np.isfinite(X).mean(1) >= 0.5; d = [x for x, k in zip(d, keep) if k]; X = X[keep]
        if len(d) < MIN_ROWS: why.append(f"{side}: {len(d)} scored candidates with these measurements (needs {MIN_ROWS})"); continue
        y = np.array([x["y"] for x in d], float); base = np.array([x["prob"] for x in d], float); t = np.array([x["t"] for x in d])
        mu, sdv = np.nanmean(X, 0), np.nanstd(X, 0) + 1e-9
        Z = np.nan_to_num((X - mu) / sdv); Z = np.clip(Z, -4, 4)
        Xc = np.column_stack([_lg(base), Z])
        cut = t[int(len(t) * 0.7)]; tr = t < cut - 2 * DAY; te = t >= cut                 # 2-day gap: no outcome overlaps the exam
        if tr.sum() < 200 or te.sum() < 80: why.append(f"{side}: not enough for an honest exam ({int(tr.sum())}/{int(te.sum())})"); continue
        m = LogisticRegression(C=0.3, max_iter=500).fit(Xc[tr], y[tr])
        new, old = _ll(y[te], m.predict_proba(Xc[te])[:, 1]), _ll(y[te], base[te])
        if new > old: why.append(f"{side}: worse than the Arbiter on the later 30% ({new:.4f} vs {old:.4f})"); continue
        m = LogisticRegression(C=0.3, max_iter=500).fit(Xc, y)
        out["sides"][side] = {"comb": m, "mu": mu.tolist(), "sd": sdv.tolist(), "valLL": round(new, 4), "champValLL": round(old, 4), "n": len(d)}
    out["exam"] = {s: {"valLL": v["valLL"], "champValLL": v["champValLL"], "n": v["n"]} for s, v in out["sides"].items()}
    if not out["sides"]: out.update(failed=True, why="; ".join(why) or "nothing to learn from")
    elif why: out["note"] = "; ".join(why)
    log(f"   live witness {name}: " + (f"trained, exam {out['exam']}" if not out.get("failed") else f"not admitted: {out['why']}"))
    return out

def prob_rows(m, side, J, rows, base):
    s = m["sides"].get(side)
    if not s: return None
    X = np.column_stack([J.P[c].values[rows] for c in m["cols"]]).astype(float)
    Z = np.clip(np.nan_to_num((X - np.array(s["mu"])) / np.array(s["sd"])), -4, 4)
    return s["comb"].predict_proba(np.column_stack([_lg(base), Z]))[:, 1]

# ── AUTO-DISCOVERY: any NEW data table becomes evidence without hand wiring ─────────────────────────────────────────────────
# A future collector only has to save a table with a time column 't' (unix seconds), optionally 'coin', and numeric columns, into
# the saved collect folder (state/collect/) or data/raw/ext/. It is found on the next run, turned into measurements (the value and
# its z-score against its own recent past, shifted one hour later - or one day + one hour for daily tables - so nothing is known
# early), recorded until its warm-up is over, then trained and entered exactly like the recorder / whales / news witnesses.
import re
KNOWN_COLLECT = {"rec_depth.parquet", "rec_funding.parquet", "rec_liq.parquet", "hl_whales.parquet",
                 "deribit_opt.parquet"}         # Deribit options: recorded for the January bull-market build, deliberately NOT auto-wired
EXT = lambda: [os.path.join(KEEP(""), f) for f in os.listdir(KEEP(""))] if os.path.isdir(KEEP("")) else []

def _ext_files():
    fs = [f for f in EXT() if f.endswith(".parquet") and os.path.basename(f) not in KNOWN_COLLECT]
    d = f"{C.DATA_DIR}/raw/ext"
    if os.path.isdir(d): fs += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".parquet")]
    return sorted(fs)

def _slug(path): return re.sub(r"[^a-z0-9]+", "", os.path.basename(path)[:-8].lower())[:12]

def discover():
    """new tables -> witness specs {name: {...}} (schema checked; anything unusable is skipped, never a crash)"""
    out = {}
    for p in _ext_files():
        try:
            import pyarrow.parquet as pq
            sch = pq.read_schema(p); names = sch.names
            if "t" not in names: continue
            num = [n for n in names if n not in ("t", "coin") and str(sch.field(n).type) in ("double", "float", "int64", "int32", "float32", "float64")][:8]
            if not num: continue
            nm = "auto-" + _slug(p)
            cols = [f"w_a_{_slug(p)}_{re.sub(r'[^a-z0-9]', '', c.lower())[:10]}{suf}" for c in num for suf in ("", "_z")]
            out[nm] = {"cols": cols, "what": f"Auto-discovered data '{os.path.basename(p)}' ({', '.join(num)}){' per coin' if 'coin' in names else ', market-wide'}: found by the overseer, measured automatically",
                       "warm": 21, "path": p, "num": num, "perCoin": "coin" in names}
        except Exception: continue
    return out

AUTO = discover()
LIVE.update(AUTO)

def auto_features(P, name, spec):
    d = pd.read_parquet(spec["path"]); d = d[d.t.notna()].copy(); d["t"] = d.t.astype("int64")
    if d.t.max() > 1e11: d["t"] = d.t // 1000
    step = float(np.median(np.diff(np.sort(d.t.unique())))) if d.t.nunique() > 3 else H
    daily = step >= 20 * H; lag = DAY + H if daily else H; win = 30 if daily else 168
    hold = 24 if step <= 1.5 * DAY else int(min(step, 14 * DAY) // H) + 24      # weekly tables (CFTC) are held for the whole following week, not one day
    d["t"] = (d.t // (DAY if daily else H)) * (DAY if daily else H)
    out = {}; cols = iter(spec["cols"])
    if spec["perCoin"]:
        d = d.groupby(["coin", "t"], as_index=False)[spec["num"]].mean().sort_values(["coin", "t"])
        for c in spec["num"]:
            v, z = next(cols), next(cols)
            d[v] = d[c].astype(float); g = d.groupby("coin")[v]
            d[z] = (d[v] - g.transform(lambda s: s.rolling(win, min_periods=win // 3).mean())) / (g.transform(lambda s: s.rolling(win, min_periods=win // 3).std()) + 1e-9)
            for col in (v, z):
                s = d[["coin", "t", col]].copy(); s["t"] = s.t + lag
                if daily:                                      # a daily value is held for the whole next day
                    s = pd.concat([s.assign(t=s.t + k * H) for k in range(hold)])
                idx = pd.MultiIndex.from_arrays([P.coin.values, P.t.values])
                out[col] = s.drop_duplicates(["coin", "t"]).set_index(["coin", "t"])[col].reindex(idx).astype("float32").values
    else:
        d = d.groupby("t")[spec["num"]].mean().sort_index()
        for c in spec["num"]:
            v, z = next(cols), next(cols)
            s = d[c].astype(float); zz = (s - s.rolling(win, min_periods=win // 3).mean()) / (s.rolling(win, min_periods=win // 3).std() + 1e-9)
            for col, ser in ((v, s), (z, zz)):
                ser = ser.copy(); ser.index = ser.index + lag
                grid = np.sort(P.t.unique()); ser = ser.reindex(np.union1d(ser.index, grid)).ffill(limit=hold if daily else 2).reindex(grid)
                out[col] = pd.Series(ser.values, index=grid).reindex(P.t.values).astype("float32").values
    return out

def stale_after(name):
    """seconds after which a table has 'stopped': 4h for hourly tables, 2.5x the update step for daily / weekly ones (so weekly CFTC is fine for 17 days)"""
    try:
        t = np.sort(pd.read_parquet(AUTO[name]["path"], columns=["t"]).t.unique()); t = t // (1000 if t.max() > 1e11 else 1)
        step = float(np.median(np.diff(t))) if len(t) > 3 else H
    except Exception: step = H
    return max(4 * H, 2.5 * step) if step >= 20 * H else 4 * H

_first_t, _last_t = first_t, last_t
def first_t(name):
    if name in AUTO:
        try: return int(pd.read_parquet(AUTO[name]["path"], columns=["t"]).t.min() // (1000 if pd.read_parquet(AUTO[name]["path"], columns=["t"]).t.max() > 1e11 else 1))
        except Exception: return None
    return _first_t(name)
def last_t(name):
    if name in AUTO:
        try:
            t = pd.read_parquet(AUTO[name]["path"], columns=["t"]).t; return int(t.max() // (1000 if t.max() > 1e11 else 1))
        except Exception: return None
    return _last_t(name)

_features = features
def features(P, log=print):
    out = _features(P, log)
    for n, spec in AUTO.items():
        try: out.update(auto_features(P, n, spec))
        except Exception as e: log(f"   auto-discovered {n} skipped: {e}")
    return out

# ── orphan check: every saved data file must belong to something that uses it ────────────────────────────────────────────
RAW_PATTERNS = [r"^[0-9A-Z]+_(k|m|s|f|hl)\.parquet$", r"^book_[0-9A-Z]+\.parquet$", r"^(book_|spot_)?manifest\.json$", r"^version(_\w+)?\.txt$",
                r"^cz_(liq_d|liq_h|oifr_d|markets)\.(parquet|json)$", r"^(macro|onchain)\.parquet$", r"^compass_\w+\.parquet$", r"^ext$"]
COLLECT_KNOWN = KNOWN_COLLECT | {"digest.jsonl", "calendar.json", "ipos.json", "hl_top.json", "stamps.json", "news", "deribit"}

def orphans(recent_days=14):
    """files that nothing reads, written in the last two weeks (a collector whose data is never used). Old leftovers are listed by
    old_orphans() for the record but do not count. Auto-discovered tables are not orphans."""
    cut = time.time() - recent_days * DAY
    return [f for f in _all_orphans() if os.path.getmtime(f.replace("state/", f"{C.STATE_DIR}/", 1).replace("data/", f"{C.DATA_DIR}/", 1)) >= cut]

def old_orphans(recent_days=14):
    new = set(orphans(recent_days)); return [f for f in _all_orphans() if f not in new]

def _all_orphans():
    out = []
    for f in (os.listdir(KEEP("")) if os.path.isdir(KEEP("")) else []):
        if f in COLLECT_KNOWN or (f.endswith(".parquet") and "auto-" + _slug(f) in AUTO) or f.endswith(".tmp"): continue
        out.append(f"state/collect/{f}")
    rd = f"{C.DATA_DIR}/raw"
    for f in (os.listdir(rd) if os.path.isdir(rd) else []):
        if f.endswith(".tmp") or any(re.match(p, f) for p in RAW_PATTERNS): continue
        out.append(f"data/raw/{f}")
    return out
