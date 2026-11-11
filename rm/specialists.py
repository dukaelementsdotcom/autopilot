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
    if kind == "runner": return _runner(J, B, R, A, rows, S, pos_new, newest, ctx)
    if any(int(x["t"] // 86400) == newest // 86400 for x in A["open"] + A["closed"][-10:]) or len(A["open"]) >= 2: return
    reg = REG.get(newest) or ""
    if kind == "news":
        b = np.flatnonzero((J.coin == "BTC") & (J.t == newest))
        if not len(b) or "w_shock1" not in J.P.columns: return
        sh = float(J.P["w_shock1"].values[b[0]])
        if not np.isfinite(sh) or abs(sh) < 3: return
        side = "long" if sh > 0 else "short"
        if side not in B["sides"] or not RF.side_live(R, side, B["sides"][side], reg): return     # only a side the Arbiter itself trusts right now
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
                if not ok or side not in B["sides"] or not RF.side_live(R, side, B["sides"][side], reg): continue
                if (reg.startswith("down") and side == "long") or (reg.startswith("up") and side == "short"): continue   # with the trend only
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


# ── runner: small first risk, then pyramid into a long that keeps going ──────────────────────────────────────────────────
STEP, HOLD_H, CAP = 0.02, 336, 6.0
def _runner(J, B, R, A, rows, S, pos_new, newest, ctx):
    """LONG signals as the exam scores them (odds above break-even + the learned margin, not unfamiliar), one new run a day, two at most.
    First unit risks 4% of the account at its stop. Every further +2% adds one more unit and lifts the stop to one step behind,
    so a run that keeps going is held with growing size and a failed one costs the first risk. Lab test Nov 11: beat the +2% exit
    on the validation year and the newest months, but through a few very large winners (about 1 in 9 trades)."""
    from . import referee as RF
    runs = A.setdefault("runs", [])
    if "long" not in B["sides"] or len(runs) >= 2 or any(int(x["t"] // 86400) == newest // 86400 for x in runs + [c for c in A["closed"][-10:] if c.get("run")]): return
    sd = B["sides"]["long"]; p = np.asarray(S["long"]["prob"])[pos_new]; unf = np.asarray(S["long"]["unfamiliar"])[pos_new]
    stp = RF.stop_pct(J.dvol[rows[pos_new]]); edge = np.where(unf, -1, p - (RF.breakeven(stp) + sd["margin"]))
    k = int(np.argmax(edge))
    if edge[k] < 0: return
    i = int(rows[pos_new[k]]); coin = str(J.coin[i])
    if any(r["coin"] == coin for r in runs): return
    e = float((ctx.get("live") or {}).get(coin) or J.O[i]); s_ = float(stp[k]); eq = A["cash"]; n0 = min(RF.RISK["A"] / s_, RF.MAX_MARGIN * 20) * eq
    runs.append({"run": True, "coin": coin, "side": "LONG", "t": int(newest), "entry": e, "stopPct": round(s_, 4), "stop": e * (1 - s_), "n0": round(n0, 2), "units": [[e, round(n0, 2)]],
                 "lvl": 0, "cap": round(CAP * eq, 2), "lastBar": int(newest), "prob": round(float(p[k]), 3), "grade": "A", "margin": round(n0 / 20, 2),
                 "why": "runner: pyramids into a long that keeps going (+2% steps)"})

def manage_runs(J, A, newest):
    for r in list(A.get("runs", [])):
        T, O, Hh, Ll, Cc = J._bars(r["coin"]); j = int(np.searchsorted(T, r["lastBar"] + H)); e = r["entry"]; done = None
        while j < len(T) and T[j] <= newest and done is None:
            t = int(T[j]); r["lastBar"] = t
            if Ll[j] <= r["stop"]: done = (r["stop"], "stop" if r["lvl"] == 0 else f"trailing stop after {r['lvl']} steps", t)
            else:
                while Hh[j] / e - 1 >= (r["lvl"] + 1) * STEP:
                    r["lvl"] += 1; px = e * (1 + r["lvl"] * STEP)
                    if sum(n for _, n in r["units"]) + r["n0"] <= r["cap"]: r["units"].append([px, r["n0"]])
                    r["stop"] = e * (1 + (r["lvl"] - 1) * STEP)
                if t + H - r["t"] >= HOLD_H * H: done = (float(Cc[j]), "time (14 days)", t)
            j += 1
        if done:
            px, how, t = done; pnl = sum(n * (px / p - 1 - C.FEE) for p, n in r["units"]); A["cash"] = round(A["cash"] + pnl, 2); A["runs"].remove(r)
            A["closed"].append({**{k: v for k, v in r.items() if k != "units"}, "exitT": t + H, "how": how, "usd": round(pnl, 2), "after": A["cash"], "steps": r["lvl"],
                                "pct": round((px / e - 1) * 100, 2), "hitTarget": r["lvl"] >= 1, "roe": round(pnl / max(1e-9, r["margin"]) * 100, 1)})
            A["curve"].append([t + H, A["cash"]])
