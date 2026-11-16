"""LONG CONTESTANTS (owner, Nov 17): each idea races live in its own $500 shadow account, trading LONGS only, one new trade a UTC day,
at most 2 open. Nothing here touches the Arbiter; a contestant takes over only by out-earning it under the judge.
Every input is known at the decision hour (hourly bars before it, daily data already lagged by its collector).

  confluence-long  the owner's pre-trade checklist: TREND (daily higher highs and higher lows, above the 50-day average) + LOCATION
                   (at a key support, or a swept support just reclaimed) + RISK (stop just under the support / sweep low, reward at
                   least 2x the risk) are required, and at least 3 of 4 more must be green: LIQUIDATIONS (a long flush yesterday),
                   OI & FUNDING (open interest down on the pullback, funding not crowded), ORDER FLOW (aggressive buying flipped positive
                   this hour), VOLUME (quiet pullback, then a pick-up). Full exit at the target, 72 hours at most.
  plumbing-long    the owner's 'plumbing' checklist, BTC and ETH only (options data exists only for them): MACRO (US net liquidity =
                   Fed balance sheet - reverse repo - Treasury account not falling over 4 weeks, the dollar not breaking higher),
                   SUPPLY (BTC leaving exchanges), REGIME (dealer gamma positive, skew turning to calls, implied vol not far below
                   realised), MAP (a support close below, the 7-day high above as the target), TRIGGER (buying flipped positive or spot
                   leading). Layers without data count as NOT green. Needs MACRO, MAP and TRIGGER plus 4+ green in all; size scales
                   with the number of green lights (4% risk at 7 of 7).
  pullback         uptrend coin (above its 20- and 50-day averages, 20 above 50) that dipped 3%+ from its 7-day high back to its
                   20-day average, with this hour turning up: the Arbiter's +2% plan.
  leader           market not falling (BTC above its 50-day average): the coin making a new 20-day high that is strongest against
                   BTC over 7 days, still rising today: the Arbiter's +2% plan.
  squeeze          shorts crowded and paying (funding negative across exchanges) while open interest rises and the price holds above
                   its 50-day average and is up today: the Arbiter's +2% plan.
  spotled          a breakout to a new 20-day high bought with real money (spot buying more than futures over 24h, spot buyers
                   aggressive): the Arbiter's +2% plan."""
import os, time, numpy as np, pandas as pd
from . import config as C

H = 3600; DAY = 86400
KINDS = ("confluence-long", "plumbing-long", "pullback", "leader", "squeeze", "spotled")

def _daily(J, coin, newest):
    T, O, Hh, Ll, Cc = J._bars(coin); m = T < newest
    if m.sum() < 24 * 60: return None
    d = pd.DataFrame({"d": T[m] // DAY, "h": Hh[m], "l": Ll[m], "c": Cc[m]}).groupby("d").agg(h=("h", "max"), l=("l", "min"), c=("c", "last"))
    return d.iloc[:-1] if len(d) > 1 else d              # completed days only

def _uptrend(d):
    if d is None or len(d) < 60: return False
    sma50 = d.c.iloc[-50:].mean(); h1, h2 = d.h.iloc[-10:].max(), d.h.iloc[-20:-10].max(); l1, l2 = d.l.iloc[-10:].min(), d.l.iloc[-20:-10].min()
    return bool(d.c.iloc[-1] > sma50 and h1 > h2 and l1 > l2)

def _supports(d):
    """key levels below: daily swing lows of the last 60 days (lower than 3 days either side), yesterday's low, the 20-day low"""
    lv = [float(d.l.iloc[-1]), float(d.l.iloc[-20:].min())]
    L = d.l.values[-60:]
    for k in range(3, len(L) - 3):
        if L[k] <= L[k - 3:k + 4].min(): lv.append(float(L[k]))
    return sorted(set(lv))

def _row(J, i, col):
    P = J.P
    return float(P[col].values[i]) if col in P.columns and np.isfinite(P[col].values[i]) else np.nan

def _hist(J, coin, col, t0, t1):
    P = J.P
    if col not in P.columns: return np.array([])
    m = (P.coin.values == coin) & (P.t.values >= t0) & (P.t.values < t1)
    return np.asarray(P[col].values[m], float)

def _minor(a):
    a = np.asarray(a, float); a = a[np.isfinite(a)]
    return float(a.min()) if len(a) else np.inf

def _netliq():
    """US net liquidity 4-week change (Fed balance sheet - reverse repo - Treasury account), from the macro collector; None if missing"""
    try:
        m = pd.read_parquet(f"{C.DATA_DIR}/raw/macro.parquet")
        if not {"walcl", "rrp", "tga"} <= set(m.columns): return None, None
        m = m.sort_values("t").ffill(); m = m[m.t <= time.time() - 2 * DAY]       # published with a delay: use data at least 2 days old
        nl = m.walcl - m.rrp * 1000 - m.tga; s = pd.Series(nl.values, index=m.t.values).dropna()
        dx = pd.Series(m.dxy.values, index=m.t.values).dropna() if "dxy" in m else None
        ch = float(s.iloc[-1] - s[s.index <= s.index[-1] - 28 * DAY].iloc[-1]) if len(s) > 40 else None
        dch = float(dx.iloc[-1] / dx[dx.index <= dx.index[-1] - 20 * DAY].iloc[-1] - 1) if dx is not None and len(dx) > 30 else None
        return ch, dch
    except Exception: return None, None

def _options(coin, newest):
    """latest Deribit reading for BTC/ETH (recorded hourly since Nov 15): gamma exposure, 30-day skew now and a day ago"""
    try:
        o = pd.read_parquet(f"{C.STATE_DIR}/collect/deribit_opt.parquet"); o = o[(o.coin == coin) & (o.t < newest)].sort_values("t")
        if not len(o) or o.t.iloc[-1] < newest - 3 * H: return None
        prev = o[o.t <= o.t.iloc[-1] - 24 * H]
        return {"gex": float(o.gex.iloc[-1]), "rr": float(o.rr25_30.iloc[-1]), "rrPrev": float(prev.rr25_30.iloc[-1]) if len(prev) else np.nan, "atm30": float(o.atm30.iloc[-1])}
    except Exception: return None

def _realised(J, coin, newest, days=30):
    T, O, Hh, Ll, Cc = J._bars(coin); m = (T < newest) & (T >= newest - days * DAY)
    r = np.diff(np.log(Cc[m])); return float(np.std(r) * np.sqrt(24 * 365) * 100) if len(r) > 100 else np.nan

def trade(kind, J, B, R, A, rows, S, pos_new, now_iso, newest, ctx):
    from . import referee as RF
    if "long" not in B["sides"]: return
    if any(int(x["t"] // DAY) == newest // DAY for x in A["open"] + A["closed"][-10:]) or len(A["open"]) >= 2: return
    btc = _daily(J, "BTC", newest); notes = {}; best = None
    btc_up = btc is not None and len(btc) >= 50 and btc.c.iloc[-1] > btc.c.iloc[-50:].mean()
    nl = _netliq() if kind == "plumbing-long" else (None, None)
    for pos in pos_new:
        i = int(rows[pos]); coin = str(J.coin[i])
        if any(x["coin"] == coin for x in A["open"]): continue
        if kind == "plumbing-long" and coin not in ("BTC", "ETH"): continue
        T, O, Hh, Ll, Cc = J._bars(coin); j = int(np.searchsorted(T, newest))
        if j < 24 * 60: continue
        px = float(J.O[i]); d = _daily(J, coin, newest)
        if d is None or len(d) < 60: continue
        tk1 = _row(J, i, "tk1"); tkp = _hist(J, coin, "tk1", newest - 6 * H, newest); r24 = _row(J, i, "r24")
        flip = bool(np.isfinite(tk1) and tk1 > 0 and len(tkp) and np.nanmean(tkp) < 0)
        sup = [s for s in _supports(d) if s < px * 1.002]; S0 = max(sup) if sup else None
        low24 = float(Ll[j - 24:j].min())
        swept = bool(S0 is not None and low24 < S0 * 0.998 and px > S0)
        near = bool(S0 is not None and px <= S0 * 1.015)
        score = None; stp = float(RF.stop_pct(J.dvol[i])); T_ = RF.TARGET; risk = None; full = False; why = ""
        if kind == "confluence-long":
            if not (_uptrend(d) and (near or swept) and S0): continue
            stop_px = min(S0, low24) * 0.995; stp = 1 - stop_px / px
            if not (0.005 <= stp <= 0.04): continue
            T_ = max(RF.TARGET, 2 * stp)
            if T_ > 0.08: continue                                   # a 2:1 target further than 8% is not realistic for one trade
            g = {"liquidations": _row(J, i, "w_l_long1") >= 0.5,
                 "oiFunding": (_row(J, i, "w_x_oi1") <= 0 or not np.isfinite(_row(J, i, "w_x_oi1"))) and _row(J, i, "w_x_frz") <= 0.5,
                 "orderFlow": flip,
                 "volume": _row(J, i, "vr") >= 1.1 and _minor(_hist(J, coin, "vr", newest - 12 * H, newest - 2 * H)) <= 0.9}
            n = sum(bool(v) for v in g.values()); notes[coin] = {"green": n, **{k: bool(v) for k, v in g.items()}, "swept": swept}
            if n < 3: continue
            score = n + (1 if swept else 0); full = True; why = f"checklist {n + 3}/7 green ({'swept and reclaimed' if swept else 'at support'} {S0:.6g})"
        elif kind == "plumbing-long":
            ch, dch = nl; op = _options(coin, newest); rv = _realised(J, coin, newest)
            g = {"macro": ch is not None and ch >= 0 and (dch is None or dch < 0.01),
                 "supply": _row(J, i, "w_btcnet") < 0 if "w_btcnet" in J.P.columns else False,
                 "gamma": bool(op and op["gex"] > 0),
                 "skew": bool(op and np.isfinite(op["rrPrev"]) and op["rr"] > op["rrPrev"]),
                 "vrp": bool(op and np.isfinite(rv) and op["atm30"] - rv > -5),
                 "map": bool(S0 and px <= S0 * 1.03 and float(Hh[j - 168:j].max()) >= px * 1.02),
                 "trigger": flip or _row(J, i, "w_c_div24") > 0}
            n = sum(bool(v) for v in g.values()); notes[coin] = {"green": n, **{k: bool(v) for k, v in g.items()}}
            if not (g["macro"] and g["map"] and g["trigger"] and n >= 4): continue
            stop_px = min(S0, low24) * 0.995; stp = 1 - stop_px / px
            if not (0.005 <= stp <= 0.05): continue
            T_ = min(max(RF.TARGET, float(Hh[j - 168:j].max()) / px - 1), 0.10); risk = 0.04 * n / 7; full = True
            score = n; why = f"plumbing {n}/7 green; target the 7-day high"
        elif kind == "pullback":
            if len(d) < 50: continue
            s20, s50 = d.c.iloc[-20:].mean(), d.c.iloc[-50:].mean(); h7 = float(Hh[j - 168:j].max())
            if not (s20 > s50 and px > s50 and px / h7 - 1 <= -0.03 and abs(px / s20 - 1) <= 0.01 and Cc[j - 1] > O[j - 1] and (np.isfinite(tk1) and tk1 > 0)): continue
            score = -abs(px / s20 - 1); why = f"pullback {px / h7 - 1:+.1%} from the 7-day high to the 20-day average, turning up"
        elif kind == "leader":
            if not btc_up: continue
            don = _row(J, i, "don20")
            bT, bO, bH, bL, bC = J._bars("BTC"); jb = int(np.searchsorted(bT, newest))
            if jb < 170 or not (don >= 0.95 and r24 > 0): continue
            rel = float(Cc[j - 1] / Cc[j - 169] - 1) - float(bC[jb - 1] / bC[jb - 169] - 1)
            if rel <= 0: continue
            score = rel; why = f"leader: {rel:+.1%} vs BTC over 7 days, new 20-day high"
        elif kind == "squeeze":
            fr, frz, oi1 = _row(J, i, "w_x_fr"), _row(J, i, "w_x_frz"), _row(J, i, "w_x_oi1")
            if not ((fr < 0 or frz <= -1) and oi1 > 0 and r24 > 0 and px > d.c.iloc[-50:].mean()): continue
            score = -frz if np.isfinite(frz) else 0; why = f"squeeze: funding {fr:.4%}, open interest rising, price holding"
        elif kind == "spotled":
            don, dv, si = _row(J, i, "don20"), _row(J, i, "w_c_div24"), _row(J, i, "stimb24")
            if not (don >= 0.98 and dv > 0 and si > 0): continue
            score = dv; why = "breakout to a 20-day high bought with real money (spot leading)"
        if score is not None and (best is None or score > best[0]): best = (score, pos, i, stp, T_, risk, full, why)
    R.setdefault("longsNow", {})[kind] = {"at": now_iso, "checked": notes if notes else None}
    if not best: return
    _, pos, i, stp, T_, risk, full, why = best; pr = float(S["long"]["prob"][pos])
    cfg = {"spec": kind}
    if risk is not None: cfg["_risk"] = risk
    n0 = len(A["open"])
    RF._open(J, B, A, i, ("long", "A", pr, 0.0), stp, float(RF.breakeven(stp)), S, pos, [], now_iso, R, cfg, T=T_, Hz=72, ctx=ctx)
    if len(A["open"]) > n0:
        A["open"][-1].update(why=why, **({"fullExit": True, "maxHold": 72} if full else {}))
