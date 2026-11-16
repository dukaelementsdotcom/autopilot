"""LAB TEST: exits and trade slots on the Arbiter's OWN picks (entries and odds identical; research only, nothing live changes).

Picks: the current brain's Grade A candidates (prob >= break-even + margin, >= 2 families for, <= 1 against, familiar), live sides
only, every hour, on two windows the exit rules never saw: the validation year and the final-exam months.
Variants (one change at a time, then combined):
  BASE   today's day slot (first qualifying hour of each UTC day, best candidate of that hour), half at +2%, stop to break-even,
         the rest held until the break-even stop or 72h  (the live evidence-based trail is not replicated; it is the same in all variants)
  DYN    per-setup target: half at the setup's expected move (median favourable move before the stop, learned on the DISCOVERY years
         only), clipped to 1-5%
  BE10 / BE12   stop to break-even once the trade has been +1.0% / +1.2% in favour (BE12 is what the live 'lock' contestant does)
  ROLL   a rolling slot: the next trade 24h after the last entry (not at midnight); a much stronger setup (edge >= 5 points more)
         replaces an open trade that is still near break-even (within 0.3%) and has not taken profit
  ALL    DYN + the better early break-even + ROLL
Account: 4% of the account at risk at the stop, trades compounded in time order."""
import json, math, time, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor as HGBR
from . import config as C, features, referee as RF
from .judge import Judge

H = 3600; DAY = 86400; FEE = C.FEE

def picks(J, B, rows):
    S = RF.score(J, B, rows); stp = RF.stop_pct(J.dvol[rows]); be = RF.breakeven(stp); out = []
    for nm, sd in B["sides"].items():
        if not (sd.get("live") and not sd.get("off")): continue
        F = np.column_stack([np.asarray(v) for v in S[nm]["fams"].values()])
        sup, ag = (F >= sd["base"] + 0.03).sum(1), (F <= sd["base"] - 0.03).sum(1); pr = np.asarray(S[nm]["prob"])
        ok = (pr >= be + sd["margin"]) & (sup >= 2) & (ag <= 1) & ~np.asarray(S[nm]["unfamiliar"])
        for k in np.flatnonzero(ok): out.append({"i": int(rows[k]), "t": int(J.t[rows[k]]), "sd": 1 if nm == "long" else -1, "p": float(pr[k]), "edge": float(pr[k] - be[k]), "stp": float(stp[k])})
    return sorted(out, key=lambda x: (x["t"], -x["edge"]))

def mfe(J, i, sd, stp, hz=48):
    """largest favourable move before the stop is touched (or the horizon ends)"""
    e = J.O[i]; best = 0.0
    for j in range(i, min(J.last[i] + 1, i + hz)):
        fav = (J.H[j] / e - 1) if sd > 0 else (1 - J.L[j] / e); adv = (1 - J.L[j] / e) if sd > 0 else (J.H[j] / e - 1)
        if adv >= stp: break
        best = max(best, fav)
    return best

def walk(J, x, tgt, be_at, hz=72, cut_t=None):
    """one trade bar by bar; returns (return, exit time, half taken). cut_t: closed at market at that hour (replaced)"""
    i, sd, stp = x["i"], x["sd"], x["stp"]; e = J.O[i]; stop = e * (1 - sd * stp); half = False; rh = 0.0; px = e; tx = J.t[i]
    for j in range(i, min(J.last[i] + 1, i + hz)):
        if cut_t is not None and J.t[j] >= cut_t: px = J.O[j]; tx = J.t[j]; break
        h, l, c = J.H[j], J.L[j], J.Cl[j]; tx = J.t[j] + H
        if (sd > 0 and l <= stop) or (sd < 0 and h >= stop): px = stop; break
        fav = (h / e - 1) if sd > 0 else (1 - l / e)
        if be_at and not half and fav >= be_at:
            b_ = e * (1 + sd * 2 * FEE); stop = max(stop, b_) if sd > 0 else min(stop, b_)
        if not half and fav >= tgt:
            half = True; rh = 0.5 * tgt; b_ = e * (1 + sd * 2 * FEE); stop = max(stop, b_) if sd > 0 else min(stop, b_)
        px = c
    rest = sd * (px / e - 1)
    return (rh + 0.5 * rest if half else rest) - FEE, int(tx), half

def unreal(J, x, t):
    """the open trade's move at hour t (from its entry), for the replacement rule"""
    j = x["i"] + int((t - x["t"]) // H)
    if j > J.last[x["i"]]: return None
    return x["sd"] * (J.O[j] / J.O[x["i"]] - 1)

def run(J, P_, tgt_of, be_at, roll):
    taken = []; last = None; last_t = -10 ** 12; day_used = set(); by_hour = {}
    for x in P_: by_hour.setdefault(x["t"], []).append(x)
    for t in sorted(by_hour):
        x = max(by_hour[t], key=lambda z: z["edge"])
        if not roll:
            if t // DAY in day_used: continue
            day_used.add(t // DAY); taken.append(x); continue
        if t >= last_t + DAY: taken.append(x); last = x; last_t = t; continue
        if last is not None and x["edge"] >= last["edge"] + 0.05 and x["i"] != last["i"]:
            u = unreal(J, last, t)
            r_, tx, half = walk(J, last, tgt_of(last), be_at)
            if u is not None and abs(u) <= 0.003 and tx > t and not half:            # still open, near break-even, no profit taken: replace it
                last["cut"] = t; taken.append(x); last = x; last_t = t
    res = []
    for x in taken:
        r_, tx, half = walk(J, x, tgt_of(x), be_at, cut_t=x.get("cut")); res.append((x["t"], r_, x["stp"], half))
        x.pop("cut", None)
    return res

def stats(res, days):
    if not res: return {"trades": 0}
    r = np.array([x[1] for x in res]); s = np.array([x[2] for x in res]); a = np.maximum(RF.RISK["A"] * r / s, -RF.MAX_MARGIN)
    eq = np.cumprod(1 + a); dd = float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max())
    return {"trades": len(r), "win": round(float((r > 0).mean()) * 100, 1), "avg": round(float(r.mean()) * 100, 3),
            "monthly": round(math.expm1(np.log1p(a).sum() / max(days, 1) * 30.4) * 100, 2), "maxDD": round(dd * 100, 1), "halfTaken": round(float(np.mean([x[3] for x in res])) * 100, 1)}

def main():
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    P = features.build(blocks=blocks); J = Judge(P); B = RF.load()
    if not B: print("no brain"); return
    out = {"brain": B.get("trainedAt")}
    W = {"validation year": (J.val0, J.hold0), "final-exam months": (J.hold0, int(J.t_end) - 72 * H)}
    # expected-move model per side, DISCOVERY years only (before the validation year)
    fams = {k: v for k, v in RF.fam_cols(P).items() if k != "invented"}; cols = sorted({c for v in fams.values() for c in v})
    D = RF._rows(J, int(J.t.min()), J.val0 - 72 * H, every=6); mdl = {}
    for nm, sd_ in B["sides"].items():
        s = 1 if nm == "long" else -1; stp = RF.stop_pct(J.dvol[D]); ok = np.isfinite(stp)
        y = np.array([mfe(J, int(i), s, float(st)) for i, st in zip(D[ok][::2], stp[ok][::2])])       # half of them: enough, and fast
        mdl[s] = HGBR(loss="quantile", quantile=0.5, max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=300, random_state=0).fit(RF._X(P, D[ok][::2], cols), y)
        print(f"expected-move model {nm}: trained on {len(y)} discovery rows, median move {np.median(y) * 100:.2f}% ({time.time() - t0:.0f}s)", flush=True)
    for wname, (a, b) in W.items():
        rows = RF._rows(J, a, b, every=1); P_ = picks(J, B, rows); days = (b - a) / DAY
        for x in P_: x["dyn"] = float(np.clip(mdl[x["sd"]].predict(RF._X(P, np.array([x["i"]]), cols))[0], 0.01, 0.05))
        print(f"{wname}: {len(P_)} Grade A candidates ({time.time() - t0:.0f}s)", flush=True)
        fixed = lambda x: RF.TARGET; dyn = lambda x: x["dyn"]
        V = {"BASE (today)": (fixed, None, False), "DYN target": (dyn, None, False), "BE at +1.0%": (fixed, 0.010, False), "BE at +1.2% (= lock)": (fixed, 0.012, False),
             "ROLL 24h slot": (fixed, None, True)}
        rs = {k: stats(run(J, P_, *v), days) for k, v in V.items()}
        be_best = 0.010 if (rs["BE at +1.0%"].get("monthly") or -99) >= (rs["BE at +1.2% (= lock)"].get("monthly") or -99) else 0.012
        rs[f"ALL (DYN + BE {be_best * 100:.1f}% + ROLL)"] = stats(run(J, P_, dyn, be_best, True), days)
        out[wname] = rs
        for k, v in rs.items(): print(f"  {wname} | {k}: {v}", flush=True)
        dv = [x["dyn"] for x in P_]; print(f"  dynamic targets: median {np.median(dv) * 100:.2f}%, 10-90% {np.percentile(dv, 10) * 100:.2f}-{np.percentile(dv, 90) * 100:.2f}%", flush=True)
    # verdict: clearly better = higher monthly return in BOTH unseen windows, and drawdown no more than 2 points deeper in either
    base = {w: out[w]["BASE (today)"] for w in W}; win = []
    for k in out["validation year"]:
        if k.startswith("BASE"): continue
        if all((out[w][k].get("monthly") or -99) > (base[w].get("monthly") or -99) + 0.5 and (out[w][k].get("maxDD") or 99) <= (base[w].get("maxDD") or 0) + 2 for w in W):
            win.append((sum(out[w][k]["monthly"] for w in W), k))
    out["verdict"] = sorted(win, reverse=True)
    print("VERDICT (clearly better in both unseen windows):", out["verdict"] or "none", flush=True)
    json.dump(out, open("labtest.json", "w"), indent=1, default=str)

main()
