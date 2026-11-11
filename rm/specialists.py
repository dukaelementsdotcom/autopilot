"""WILD-MARKET SPECIALISTS: different tools for the days the main policy sits out. Each runs as a challenger with its own $500
shadow account and takes over only by out-earning the champion (at least 15 trades). Used only when the Arbiter's evidence agrees.

  pairs   long the coin the Arbiter likes most for a long, short the one it likes most for a short, same dollar amount, chosen
          among coins that normally move together: the market's swings cancel, the gap between them is the profit
  news    after a sudden market-wide move (a shock: BTC moving 3x its normal hourly size), ride the drift in the shock's direction
          on the coin whose evidence agrees most; strict 24h limit
  range   in a wild but sideways market: near the top of a coin's 3-day range go short, near the bottom go long, small target,
          stop just outside the range"""
import time, numpy as np
from . import config as C

H = 3600

def trade(J, B, R, A, cfg, rows, S, REG, V, now_iso, newest, ctx):
    from . import referee as RF
    if newest < time.time() - 4 * H: return
    pos_new = np.flatnonzero(J.t[rows] == newest)
    if not len(pos_new): return
    kind = cfg.get("spec")
    if kind == "pairs": return _pairs(J, B, R, A, rows, S, pos_new, now_iso, newest, ctx)
    if any(int(x["t"] // 86400) == newest // 86400 for x in A["open"] + A["closed"][-10:]) or len(A["open"]) >= 2: return
    reg = REG.get(newest) or ""
    if kind == "news":
        b = np.flatnonzero((J.coin == "BTC") & (J.t == newest))
        if not len(b) or "w_shock1" not in J.P.columns: return
        sh = float(J.P["w_shock1"].values[b[0]])
        if not np.isfinite(sh) or abs(sh) < 3: return
        side = "long" if sh > 0 else "short"
        if side not in B["sides"]: return
        k = int(np.argmax(np.asarray(S[side]["prob"])[pos_new])); pos = int(pos_new[k]); i = int(rows[pos])
        pr = float(S[side]["prob"][pos])
        if pr < B["sides"][side]["base"]: return                     # the evidence must at least lean the same way
        stp = float(RF.stop_pct(J.dvol[i]))
        RF._open(J, B, A, i, (side, "A", pr, 0.0), stp, float(RF.breakeven(stp)), S, pos, [], now_iso, R, {"spec": "news"}, T=RF.TARGET, Hz=24, ctx=ctx)
        A["open"][-1]["why"] = f"news drift after a {sh:+.1f}x shock"
    elif kind == "range":
        if not reg.endswith("wild"): return
        best = None
        for pos in pos_new:
            i = int(rows[pos]); T_, O_, Hh, Ll, Cc = J._bars(str(J.coin[i])); j = int(np.searchsorted(T_, int(J.t[i])))
            if j < 72: continue
            hi, lo, px = float(Hh[j - 72:j].max()), float(Ll[j - 72:j].min()), float(J.O[i]); width = hi / lo - 1
            if not (0.06 <= width <= 0.15): continue
            r24 = abs(float(Cc[j - 1] / Cc[j - 25] - 1))
            if r24 > width * 0.6: continue                               # trending inside the window, not a range
            where = (px - lo) / (hi - lo)
            for side, ok in (("short", where >= 0.85), ("long", where <= 0.15)):
                if not ok or side not in B["sides"]: continue
                pr = float(S[side]["prob"][pos])
                if pr < B["sides"][side]["base"]: continue
                stp = (hi * 1.005 / px - 1) if side == "short" else (1 - lo * 0.995 / px)
                if not (0.005 <= stp <= 0.03): continue
                score = pr - B["sides"][side]["base"]
                if best is None or score > best[0]: best = (score, side, pos, i, stp, pr)
        if best:
            _, side, pos, i, stp, pr = best
            RF._open(J, B, A, i, (side, "A", pr, 0.0), stp, float((stp + C.FEE) / (0.01 + stp)), S, pos, [], now_iso, R, {"spec": "range"}, T=0.01, Hz=24, ctx=ctx)
            A["open"][-1]["why"] = "range trade at the edge of its 3-day range"

# ── pairs: a position with two legs, managed on the spread between them ──────────────────────────────────────────────────
def _pairs(J, B, R, A, rows, S, pos_new, now_iso, newest, ctx):
    P_ = A.setdefault("pairs", [])
    if len(P_) >= 2 or any(int(x["t"] // 86400) == newest // 86400 for x in P_ + [c for c in A["closed"][-10:] if c.get("pair")]): return
    if "long" not in B["sides"] or "short" not in B["sides"]: return
    pl, ps = np.asarray(S["long"]["prob"])[pos_new], np.asarray(S["short"]["prob"])[pos_new]
    unf = np.asarray(S["long"]["unfamiliar"])[pos_new] | np.asarray(S["short"]["unfamiliar"])[pos_new]
    bl, bs = B["sides"]["long"]["base"], B["sides"]["short"]["base"]; corr = ctx.get("corr") or {}
    rel = (pl - bl) - (ps - bs)                                        # how much more the evidence favours a long than a short, per coin
    rel = np.where(unf, np.nan, rel); order = np.argsort(np.nan_to_num(rel, nan=0.0))
    best = None
    for a in order[::-1][:6]:
        for b in order[:6]:
            if a == b or not (np.isfinite(rel[a]) and np.isfinite(rel[b])): continue
            ca, cb = str(J.coin[rows[pos_new[a]]]), str(J.coin[rows[pos_new[b]]])
            if corr.get(ca, {}).get(cb, 0) < 0.6 or rel[a] - rel[b] < 0.12: continue      # coins that move together, clearly different evidence
            sc = rel[a] - rel[b]
            if best is None or sc > best[0]: best = (sc, a, b, ca, cb)
    if not best: return
    _, a, b, ca, cb = best; ia, ib = int(rows[pos_new[a]]), int(rows[pos_new[b]])
    lp = (ctx.get("live") or {}); ea, eb = float(lp.get(ca) or J.O[ia]), float(lp.get(cb) or J.O[ib])
    eq = A["cash"]; stop, target = 0.015, 0.02
    notional = min(0.03 * eq / stop, 0.20 * eq * 10)                   # a full stop-out costs ~3% of the account
    P_.append({"pair": True, "coin": f"{ca}/{cb}", "side": "PAIR", "long": ca, "short": cb, "ea": ea, "eb": eb, "t": int(newest), "notional": round(notional, 2),
               "stopPct": stop, "tgtPct": target, "best": 0.0, "lastBar": int(newest), "prob": round(float(pl[a] + ps[b]) / 2, 3), "grade": "A",
               "entry": 0.0, "margin": round(notional / 10, 2), "why": f"long {ca} ({pl[a]:.0%}) vs short {cb} ({ps[b]:.0%}), coins that move together"})

def manage_pairs(J, A, newest):
    for p in list(A.get("pairs", [])):
        Ta, _, _, _, Ca = J._bars(p["long"]); Tb, _, _, _, Cb = J._bars(p["short"])
        for t in range(p["lastBar"] + H, newest + 1, H):
            ja, jb = int(np.searchsorted(Ta, t)), int(np.searchsorted(Tb, t))
            if ja >= len(Ta) or jb >= len(Tb) or Ta[ja] != t or Tb[jb] != t: continue
            spread = float(0.5 * (Ca[ja] / p["ea"] - 1) - 0.5 * (Cb[jb] / p["eb"] - 1))
            p["best"] = max(p["best"], spread); p["lastBar"] = t
            how = ("target" if spread >= p["tgtPct"] else "stop" if spread <= -p["stopPct"] else
                   "profit lock" if p["best"] >= 0.6 * p["tgtPct"] and spread <= 0.5 * p["best"] else "time (48h)" if t + H - p["t"] >= 48 * H else None)
            if how:
                pnl = p["notional"] * spread - p["notional"] * C.FEE * 2; A["cash"] = round(A["cash"] + pnl, 2)
                A["pairs"].remove(p)
                A["closed"].append({k: v for k, v in p.items() if k not in ("best",)} | {"exitT": t + H, "how": how, "usd": round(pnl, 2), "after": A["cash"],
                                    "pct": round(spread * 100, 2), "hitTarget": how == "target", "roe": round(pnl / max(1e-9, p["margin"]) * 100, 1)})
                A["curve"].append([t + H, A["cash"]]); break
    A["equity"] = A["cash"] + sum(0 for _ in A.get("pairs", []))
