"""THE WIDER-UNIVERSE CONTESTANT: the Arbiter exactly as it is (same brain, same grading, same one-trade-a-day policy, same sizing) but
looking ONLY at extra liquid coins the core system does not trade. Its own $500 paper account races the Arbiter's. The core panel,
the Arbiter, the research engine and every other contestant are untouched: the extra coins live in their own panel, and their cross-coin
ranks are measured against the core coins so nothing in the core changes.
If it clearly beats the Arbiter, adding these coins to the Arbiter is a human decision (it needs the extra panel inside the champion)."""
import numpy as np
from . import config as C, referee as RF, features
from .judge import Judge

def step(mem, J, now_iso, blocks=(), log=print):
    R = mem.setdefault("referee", {}); B = RF.load()
    if B is None: return None
    from . import challengers as CH
    Hd = CH.load_heads(B)
    need = sorted({c for cols in B["fams"].values() for c in cols} | {c for sd in B["sides"].values() for c in (sd.get("knnCols") or []) + (sd.get("exitCols") or [])} | {"p_mtrend"})
    X = features.build_extra(J.P, [c for c in C.WIDE if c not in C.COINS], need, log, blocks)
    if X is None or not len(X): return None
    Jx = Judge(X); W = R.setdefault("wide", {})
    A = W.get("acct")
    if A is None:
        A = W["acct"] = RF.new_acct(Jx, now_iso); A.update(champAtBirth=(R.get("acct") or {}).get("equity", 500.0))
    newest = int(Jx.t.max())
    for p in list(A["open"]): RF._manage(Jx, B, p, A, newest)
    recent = X[X.t >= newest - 30 * 86400].groupby("coin")._qv24.median()
    liq = sorted(recent[recent >= C.WIDE_MIN_USD].index.tolist()); W["coins"] = liq
    W["liqM"] = {c: round(float(v) / 1e6, 1) for c, v in recent.sort_values(ascending=False).items()}
    W["rowsRecent"] = {c: int(n) for c, n in X[X.t >= newest - 30 * 86400].groupby("coin").size().items()}
    if newest > A["lastT"] and liq and not A.get("paused"):
        rows = np.flatnonzero((Jx.t > A["lastT"]) & (Jx.t <= newest) & np.isin(Jx.coin, liq) & np.isfinite(Jx.dvol) & np.isfinite(Jx.O))
        if len(rows):
            S = RF.score(Jx, B, rows); REG = RF.regimes(J, np.unique(Jx.t[rows]))        # the market's state is read on the core coins
            ctx = {"corr": RF._corr(Jx, newest), "live": None}; cfg = CH.cfg_of(R, "champion")
            RF._trade(Jx, B, R, Hd, A, cfg, rows, S, REG, {}, now_iso, newest, ctx, log)
            look = []; nr = np.flatnonzero(Jx.t[rows] == Jx.t[rows].max())
            for pos in nr:
                i = int(rows[pos]); stp = float(RF.stop_pct(Jx.dvol[i])); be = RF.breakeven(stp)
                for name, sd in B["sides"].items():
                    fp = {f: float(v[pos]) for f, v in S[name]["fams"].items()}; pr = float(S[name]["prob"][pos]); mg = RF.margin_of(R, name, sd)
                    g = RF.grade(pr, fp, sd["base"], mg, be, S[name]["unfamiliar"][pos])
                    if g and RF.side_live(R, name, sd, REG.get(int(Jx.t[i]))): look.append({"coin": str(Jx.coin[i]), "side": name.upper(), "grade": g, "prob": round(pr, 3), "need": round(be + mg, 3), "px": float(Jx.O[i])})
            W["look"] = sorted(look, key=lambda x: -(x["prob"] - x["need"]))[:6]
    A["lastT"] = max(A["lastT"], newest); RF._mark(A)
    A["closed"] = A["closed"][-200:]; A["curve"] = A["curve"][-500:]; A["decisions"] = A["decisions"][-30:]
    ar = R.get("acct") or {}; g = lambda a, s: (a.get("equity", a.get("cash", s)) / s - 1) * 100
    W["vs"] = {"wide": round(g(A, A["start"]), 2), "arbiterSinceBirth": round(((ar.get("equity", ar.get("cash", 500)) / A.get("champAtBirth", 500)) - 1) * 100, 2), "trades": len(A["closed"]), "open": len(A["open"])}
    log(f"wider universe: ${A.get('equity', A['cash'])} ({len(A['open'])} open, {len(A['closed'])} closed) on {len(liq)} extra coins; {W['vs']}")
    return W["vs"]
