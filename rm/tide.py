"""MARKET TYPE READER 'tide' (owner, Nov 17): which way is the market going, from a few grouped readings, so shorts trade only in a
falling market and longs only in a rising one. It RACES against the current readers; nothing changes the Arbiter unless it wins.

  MACRO (weeks): the Compass trend score (BTC vs its 20/50/200-day averages, slope, 7/30-day moves, breadth, S&P/Nasdaq, dollar):
                 down if <= -0.15, up if >= +0.15
  DAILY (today): three grouped witnesses, each up / down / neutral, from data known at that hour
                 D1 price today - BTC 24h move, where BTC sits in its 24h range, EMA12/26 gap   (must agree)
                 D2 breadth     - share of coins up over 24h (>= 60% up, <= 40% down)
                 D3 flow        - market-wide aggressive buying vs selling (24h), Coinbase premium (24h)
                 daily = up / down when D1 and at least one other agree; a new reading must hold 12 hours before it counts
  TIDE = +1 (rising: longs allowed) when macro up AND daily up; -1 (falling: shorts allowed) when macro down AND daily down; else 0.

READER CONTEST: every hour each reader's call is recorded (the current regime reader, the Compass reader, tide's macro, daily and
combined), and graded 48 hours later against what the market actually did (average coin's 48h move: up > +1%, down < -1%)."""
import numpy as np, pandas as pd

H = 3600

def daily_state(J):
    """per BTC hour: +1 up / -1 down / 0 neutral, with a 12-hour hold. Built from real price changes (closes), and from the
    percentile columns centred on 0.5 (the p_ columns are ranks in [0, 1] against each coin's own recent history, NOT signed moves)"""
    P = J.P; b = P.coin.values == "BTC"; T = P.t.values[b]; o = np.argsort(T); T = T[o]
    g = lambda c: np.asarray(P[c].values[b][o], float) if c in P.columns else np.full(len(T), np.nan)
    sg = lambda x: np.sign(np.nan_to_num(x))
    cl = pd.Series(g("_cl"), index=T); r24 = (cl / cl.reindex(T - 24 * H).values - 1).values      # BTC's real 24h move
    d1v = (sg(r24) + sg(g("p_pos24") - 0.5) + sg(g("emax"))) / 3; d1 = np.where(d1v >= 0.34, 1, np.where(d1v <= -0.34, -1, 0))
    Q = P[["coin", "t", "_cl"]].copy(); Q = Q.sort_values(["coin", "t"])
    prev = Q.set_index(["coin", "t"])["_cl"]; key = pd.MultiIndex.from_arrays([Q.coin.values, Q.t.values - 24 * H])
    Q["r24"] = Q["_cl"].values / prev.reindex(key).values - 1
    ok = np.isfinite(Q["r24"].values)
    br = pd.Series((Q["r24"].values[ok] > 0).astype(float)).groupby(Q.t.values[ok]).mean().reindex(T).values
    d2 = np.where(br >= 0.6, 1, np.where(br <= 0.4, -1, 0))
    tb = (pd.Series(P["p_timb24"].values).groupby(P.t.values).mean().reindex(T).values - 0.5) if "p_timb24" in P.columns else np.zeros(len(T))
    d3v = (sg(tb) + sg(g("p_cbp24") - 0.5)) / 2; d3 = np.where(d3v > 0, 1, np.where(d3v < 0, -1, 0))
    raw = np.where((d1 == 1) & ((d2 == 1) | (d3 == 1)), 1, np.where((d1 == -1) & ((d2 == -1) | (d3 == -1)), -1, 0))
    st = np.zeros(len(T), int); cur = 0; cand = 0; run = 0
    for i, v in enumerate(raw):
        if v == cand: run += 1
        else: cand, run = v, 1
        if cand != cur and run >= 12: cur = cand
        st[i] = cur
    return pd.Series(st, index=T)

def readings(J):
    """per BTC hour: macro, daily and tide in {-1, 0, +1} (cached on J)"""
    if getattr(J, "_tide", None) is not None: return J._tide
    from . import compass
    ms = compass.trend_scores(J).sort_index(); ds = daily_state(J)
    m = ms.reindex(ds.index, method="ffill").fillna(0).values
    mac = np.where(m >= 0.15, 1, np.where(m <= -0.15, -1, 0)); day = ds.values
    df = pd.DataFrame({"macro": mac, "daily": day, "tide": np.where((mac == 1) & (day == 1), 1, np.where((mac == -1) & (day == -1), -1, 0))}, index=ds.index)
    try: J._tide = df
    except Exception: pass
    return df

def at(J, t):
    df = readings(J); i = df.index.searchsorted(int(t), side="right") - 1
    return df.iloc[i].to_dict() if i >= 0 else {"macro": 0, "daily": 0, "tide": 0}

NAMES = {"regime (current: BTC vs 50-day)": "reg", "compass (trend2)": "c2", "tide macro": "macro", "tide daily": "daily", "tide (macro + daily)": "tide"}

def contest(J, R, REG, REG2, newest):
    """record every reader's call for the newest hour; grade calls 48h old; summarise"""
    from . import compass
    L = R.setdefault("readers", [])
    if not L or L[-1]["t"] < newest:
        x = at(J, newest)
        reg = REG.get(int(newest)) if REG else None; c2 = REG2.get(int(newest)) if REG2 else None
        rec = {"t": int(newest), "reg": 1 if (reg or "").startswith("up") else -1 if (reg or "").startswith("down") else 0,
               "c2": 1 if (c2 or "").split(" wild")[0] in ("up", "strong up") else -1 if (c2 or "").split(" wild")[0] in ("down", "strong down") else 0,
               "macro": int(x["macro"]), "daily": int(x["daily"]), "tide": int(x["tide"])}
        L.append(rec)
    # grade: the average liquid coin's move over the 48 hours after the call
    P = J.P
    for r in L:
        if "mkt" in r or r["t"] + 48 * H > newest: continue
        a = P[P.t == r["t"]][["coin", "_cl"]].set_index("coin")["_cl"] if "_cl" in P.columns else None
        b = P[P.t == r["t"] + 48 * H][["coin", "_cl"]].set_index("coin")["_cl"] if "_cl" in P.columns else None
        if a is None or b is None or not len(a) or not len(b): continue
        mv = float(np.nanmedian((b / a.reindex(b.index) - 1).values))
        if np.isfinite(mv): r["mkt"] = round(mv * 100, 2)
    R["readers"] = L[-3000:]
    g = [r for r in L if "mkt" in r]; out = {}
    for nm, k in NAMES.items():
        calls = [r for r in g if r[k] != 0]
        if not calls: out[nm] = {"calls": 0}; continue
        act = lambda r: 1 if r["mkt"] > 1 else -1 if r["mkt"] < -1 else 0
        right = sum(1 for r in calls if r[k] == act(r)); wrong = sum(1 for r in calls if r[k] == -act(r))
        out[nm] = {"hours": len(g), "calls": len(calls), "right": round(right / len(calls) * 100, 1), "wrongWay": round(wrong / len(calls) * 100, 1),
                   "avgMoveWithCall": round(float(np.mean([r[k] * r["mkt"] for r in calls])), 2), "now": L[-1][k]}
    out["note"] = "each reader's up/down call graded 48h later against the average coin's move (up > +1%, down < -1%); 'avgMoveWithCall' = the market's 48h move in the direction called"
    R["readerContest"] = out
    return out
