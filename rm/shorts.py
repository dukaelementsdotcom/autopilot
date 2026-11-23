"""SHORT CONTESTANTS + SNIPER (owner, Nov 22). Each races live in its own $500 shadow account, one new trade a UTC day, at most 2 open.
Nothing here touches the Arbiter; a contestant takes over only by out-earning it under the judge. Every input is known at the decision hour.

  exhaust-short       buyer exhaustion on a normal coin that ran up 3-20% in 24h (not the 100% pump coins). Needs 3 of 4 green:
                      ORDER FLOW   price at its 24h high but spot / aggressive buyers not following (executed trades, hard to spoof)
                      VOLUME       ultra-high volume that ends in a long upper wick or a narrow body (buyers absorbed)
                      FUNDING      extreme positive funding (0.05% per 8h or 1.5 sigma above the coin's norm) = crowded longs that pay shorts
                      OPEN INTEREST open interest falling while price rises (a squeeze, no new buyers)
                      Full exit at +2%, 48 hours at most.
  exhaust-short-wide  the same signal aiming for +5% with a 2.5-4% stop (the 2-10% moves), 48 hours at most.
  sniper              the Arbiter's own best call (only a side it trusts now) but entered with a LIMIT order resting at the nearest key level
                      (a recent swing high for shorts, swing low for longs, 0.3-2% away). Fills only if price touches the level within 6 hours;
                      otherwise nothing happens. Tests whether better entries beat market entries. Fill is modelled at the level when touched
                      (optimistic: a real order can sit in a queue and miss), no slippage."""
import time, numpy as np, pandas as pd
from . import config as C
from .longs import _daily, _row, _hist

H = 3600; DAY = 86400
KINDS = ("exhaust-short", "exhaust-short-wide", "sniper", "blitz")

def _resistances(d):
    """key levels above: daily swing highs of the last 60 days, yesterday's high, the 20-day high"""
    lv = [float(d.h.iloc[-1]), float(d.h.iloc[-20:].max())]
    Hh = d.h.values[-60:]
    for k in range(3, len(Hh) - 3):
        if Hh[k] >= Hh[k - 3:k + 4].max(): lv.append(float(Hh[k]))
    return sorted(set(lv))

def trade(kind, J, B, R, A, rows, S, pos_new, now_iso, newest, REG, ctx):
    if kind == "blitz": return _blitz(J, B, R, A, rows, S, pos_new, now_iso, newest, REG, ctx)
    if kind == "sniper": return _sniper(J, B, R, A, rows, S, pos_new, now_iso, newest, REG, ctx)
    return _exhaust(kind, J, B, R, A, rows, S, pos_new, now_iso, newest, ctx)

def _exhaust(kind, J, B, R, A, rows, S, pos_new, now_iso, newest, ctx):
    from . import referee as RF
    if "short" not in B["sides"]: return
    if any(int(x["t"] // DAY) == newest // DAY for x in A["open"] + A["closed"][-10:]) or len(A["open"]) >= 2: return
    wide = kind == "exhaust-short-wide"; best = None; notes = {}
    for pos in pos_new:
        i = int(rows[pos]); coin = str(J.coin[i])
        if any(x["coin"] == coin for x in A["open"]): continue
        T, O, Hh, Ll, Cc = J._bars(coin); j = int(np.searchsorted(T, newest))
        if j < 24 * 5 or j >= len(T) + 1: continue
        r24 = float(Cc[j - 1] / Cc[j - 25] - 1)
        if not (0.03 <= r24 <= 0.20): continue                               # a normal run-up, not a 100% pump
        hi24 = float(Hh[j - 24:j].max()); near_high = bool(Cc[j - 1] >= 0.99 * hi24)
        tk1, si = _row(J, i, "tk1"), _row(J, i, "stimb24"); fr, frz, oi1, vr = _row(J, i, "w_x_fr"), _row(J, i, "w_x_frz"), _row(J, i, "w_x_oi1"), _row(J, i, "vr")
        rng = Hh[j - 2:j] - Ll[j - 2:j]; ok = rng > 0
        upper = np.where(ok, (Hh[j - 2:j] - np.maximum(O[j - 2:j], Cc[j - 2:j])) / np.where(ok, rng, 1), 0.0)
        body = np.where(ok, np.abs(Cc[j - 2:j] - O[j - 2:j]) / np.where(ok, rng, 1), 1.0)
        g = {"orderFlow": near_high and ((np.isfinite(si) and si < 0) or (np.isfinite(tk1) and tk1 < 0)),
             "volume": bool(np.isfinite(vr) and vr >= 1.5 and (upper.max() >= 0.4 or body.min() <= 0.35)),
             "funding": bool((np.isfinite(fr) and fr >= 0.0005) or (np.isfinite(frz) and frz >= 1.5)),
             "openInterest": bool(np.isfinite(oi1) and oi1 < 0)}
        n = sum(bool(v) for v in g.values()); notes[coin] = {"green": n, "r24": round(r24, 3), **{k: bool(v) for k, v in g.items()}}
        if n < 3: continue
        score = n + (max(0.0, frz) / 10 if np.isfinite(frz) else 0.0)
        if best is None or score > best[0]: best = (score, pos, i, n, r24)
    R.setdefault("shortsNow", {})[kind] = {"at": now_iso, "checked": notes if notes else None}
    if not best: return
    _, pos, i, n, r24 = best; pr = float(S["short"]["prob"][pos]); stp = float(RF.stop_pct(J.dvol[i]))
    if wide: stp = float(min(0.04, max(stp, 0.025))); T_ = 0.05
    else: T_ = RF.TARGET
    be = float((stp + C.FEE) / (T_ + stp)); n0 = len(A["open"])
    RF._open(J, B, A, i, ("short", "A", pr, 0.0), stp, be, S, pos, [], now_iso, R, {"spec": kind}, T=T_, Hz=48, ctx=ctx)
    if len(A["open"]) > n0:
        A["open"][-1].update(why=f"buyer exhaustion {n}/4 green after a {r24:+.1%} run in 24h", fullExit=True, maxHold=48)

def _sniper(J, B, R, A, rows, S, pos_new, now_iso, newest, REG, ctx):
    from . import referee as RF
    pend = A.setdefault("pending", [])
    keep = []                                                                  # 1) orders already resting: fill, expire or keep
    for p in pend:
        if newest >= p["t"] + 6 * H + H: continue                              # expired unfilled
        T, O, Hh, Ll, Cc = J._bars(p["coin"]); filled = None
        for k in np.flatnonzero((T >= p["t"]) & (T < newest)):
            if (p["side"] == "short" and Hh[k] >= p["level"]) or (p["side"] == "long" and Ll[k] <= p["level"]): filled = int(T[k]) + H; break
        if filled is None: keep.append(p); continue
        m = np.flatnonzero((J.coin == p["coin"]) & (J.t == filled))
        pp = [int(q) for q in pos_new if str(J.coin[int(rows[q])]) == p["coin"]]
        if not len(m) or not pp or p["side"] not in B["sides"]: continue
        i = int(m[0]); px = float(J.O[i]); lv = p["level"]; stp = p["stp"]
        if (p["side"] == "short" and px >= lv * (1 + stp)) or (p["side"] == "long" and px <= lv * (1 - stp)): continue    # the price ran through the stop before we could be managed
        n0 = len(A["open"])
        RF._open(J, B, A, i, (p["side"], "A", p["prob"], 0.0), stp, float(RF.breakeven(stp)), S, pp[0], [], now_iso, R, {"spec": "sniper", "_entry": lv}, T=RF.TARGET, Hz=RF.HORIZON, ctx=ctx)
        if len(A["open"]) > n0: A["open"][-1]["why"] = f"limit order filled at the key level {lv:.6g}"
    A["pending"] = keep
    if len(A["open"]) >= 2 or len(keep) >= 2: return                           # 2) place a new order (one a UTC day)
    if any(int(x["t"] // DAY) == newest // DAY for x in A["open"] + A["closed"][-10:] + keep): return
    reg = REG.get(newest) or ""; best = None
    for side in ("long", "short"):
        if side not in B["sides"] or not RF.side_live(R, side, B["sides"][side], reg): continue
        base = B["sides"][side]["base"]
        for pos in pos_new:
            pr = float(S[side]["prob"][pos])
            if pr < base: continue
            i = int(rows[pos]); coin = str(J.coin[i])
            if any(x["coin"] == coin for x in A["open"] + keep): continue
            d = _daily(J, coin, newest)
            if d is None or len(d) < 60: continue
            px = float(J.O[i])
            lv = [x for x in _resistances(d) if px * 1.003 <= x <= px * 1.02] if side == "short" else [x for x in _supports_below(d, px) if px * 0.98 <= x <= px * 0.997]
            if not lv: continue
            level = min(lv) if side == "short" else max(lv)                      # the nearest key level
            if best is None or pr - base > best[0]: best = (pr - base, side, coin, level, pr, float(RF.stop_pct(J.dvol[i])))
    R.setdefault("shortsNow", {})["sniper"] = {"at": now_iso, "resting": len(A["pending"]), "candidate": best[2] if best else None}
    if best:
        _, side, coin, level, pr, stp = best
        A["pending"].append({"coin": coin, "side": side, "level": float(level), "t": int(newest), "prob": round(pr, 3), "stp": stp, "placed": now_iso})

def _supports_below(d, px):
    L = d.l.values[-60:]; lv = [float(d.l.iloc[-1]), float(d.l.iloc[-20:].min())]
    for k in range(3, len(L) - 3):
        if L[k] <= L[k - 3:k + 4].min(): lv.append(float(L[k]))
    return sorted(set(lv))

BLITZ_MARGIN, BLITZ_LEV, BLITZ_ROE = 0.35, 20, 0.225          # owner's design (Nov 22): 35% of the account as margin, 20x, exit fully at +22.5% on the margin (= +1.125% price)

def _blitz(J, B, R, A, rows, S, pos_new, now_iso, newest, REG, ctx):
    """hardcoded: ONE trade at a time with 35% of the account as margin at 20x, the Arbiter's stop distance, a FULL exit at +22.5% on the margin
    (+1.125% price), no trailing, no add-on, no half exit; the moment it closes, look for the next one (no daily limit). Entry = the Arbiter's own
    best call among the sides it trusts right now. Only the 8% per-trade risk ceiling is lifted, and only for this paper shadow account."""
    from . import referee as RF
    if A["open"]: return
    reg = REG.get(newest) or ""; best = None
    for side in ("long", "short"):
        if side not in B["sides"] or not RF.side_live(R, side, B["sides"][side], reg): continue
        base = B["sides"][side]["base"]
        for pos in pos_new:
            pr = float(S[side]["prob"][pos])
            if pr >= base and (best is None or pr - base > best[0]): best = (pr - base, side, pos, pr)
    R.setdefault("shortsNow", {})["blitz"] = {"at": now_iso, "candidate": best[1] if best else None}
    if not best: return
    _, side, pos, pr = best; i = int(rows[pos]); stp = float(RF.stop_pct(J.dvol[i])); T_ = BLITZ_ROE / BLITZ_LEV
    be = float((stp + C.FEE) / (T_ + stp))
    RF._open(J, B, A, i, (side, "A", pr, 0.0), stp, be, S, pos, [], now_iso, R, {"spec": "blitz", "_risk": 10.0, "_nocap": True, "_lev": BLITZ_LEV, "_budget": BLITZ_MARGIN}, T=T_, Hz=48, ctx=ctx)
    if A["open"] and A["open"][-1]["t"] == int(J.t[i]):
        A["open"][-1].update(why=f"blitz: {BLITZ_MARGIN:.0%} margin at {BLITZ_LEV}x, full exit at +{BLITZ_ROE:.1%} on the margin", fullExit=True, noadd=True, maxHold=12)
