"""LAB TEST 2: the owner's three-step exit on the Arbiter's own picks (same entries, same odds, same day slot; research only).
  TODAY         50% at +2%, stop to break-even, the rest until break-even or 72h (the live evidence trail is not replicated)
  STEPS data    50% at +2% -> stop to break-even; 25% at TP2 = this setup's typical move before stalling (median, >= +2.5%);
                the last 25% at TP3 = its good-case move (75th percentile, >= TP2 + 0.5%); after TP2 the stop steps into profit
                (variant a: to +1%, variant b: to TP1 = +2%). TP2/TP3 learned per setup on the DISCOVERY years only.
  STEPS fixed   the same with fixed TP2 +3% and TP3 +4.5% (do the learned levels add anything?)
  RATCHET       50% at +2%, then the stop keeps at least half of the best open profit (what the live 'lock' contestant does)"""
import json, math, time, numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor as HGBR
from . import config as C, features, referee as RF
from .judge import Judge
from .labtest_exits import picks, mfe, stats
H = 3600; DAY = 86400; FEE = C.FEE

def walk_steps(J, x, tps, fracs, step_stops, hz=72, ratchet=False):
    """tps: ascending take-profit levels; fracs: share closed at each; step_stops[k]: where the stop goes after TP k (as a move)"""
    i, sd, stp = x["i"], x["sd"], x["stp"]; e = J.O[i]; stop = e * (1 - sd * stp); k = 0; got = 0.0; left = 1.0; best = 0.0; px = e
    lvl = lambda mv: e * (1 + sd * mv)
    for j in range(i, min(J.last[i] + 1, i + hz)):
        h, l, c = J.H[j], J.L[j], J.Cl[j]
        if (sd > 0 and l <= stop) or (sd < 0 and h >= stop): px = stop; break
        fav = (h / e - 1) if sd > 0 else (1 - l / e); best = max(best, fav)
        while k < len(tps) and fav >= tps[k]:
            got += fracs[k] * tps[k]; left -= fracs[k]; ns = lvl(step_stops[k]); stop = max(stop, ns) if sd > 0 else min(stop, ns); k += 1
        if ratchet and k >= 1:
            ns = lvl(0.5 * best); stop = max(stop, ns) if sd > 0 else min(stop, ns)
        px = c
        if left <= 1e-9: break
    return got + left * sd * (px / e - 1) - FEE

def run(J, P_, fn):
    used = set(); out = []
    by = {}
    for x in P_: by.setdefault(x["t"], []).append(x)
    for t in sorted(by):
        if t // DAY in used: continue
        used.add(t // DAY); x = max(by[t], key=lambda z: z["edge"]); out.append((x["t"], fn(x), x["stp"], False))
    return out

def main():
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    P = features.build(blocks=blocks); J = Judge(P); B = RF.load(); out = {"brain": B.get("trainedAt")}
    W = {"validation year": (J.val0, J.hold0), "final-exam months": (J.hold0, int(J.t_end) - 72 * H)}
    fams = {k: v for k, v in RF.fam_cols(P).items() if k != "invented"}; cols = sorted({c for v in fams.values() for c in v})
    D = RF._rows(J, int(J.t.min()), J.val0 - 72 * H, every=12); M = {}
    for nm in B["sides"]:
        s = 1 if nm == "long" else -1; stp = RF.stop_pct(J.dvol[D]); ok = np.isfinite(stp); Dr = D[ok]
        y = np.array([mfe(J, int(i), s, float(st), hz=72) for i, st in zip(Dr, stp[ok])]); X = RF._X(P, Dr, cols)
        M[s] = {q: HGBR(loss="quantile", quantile=q, max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=300, random_state=0).fit(X, y) for q in (0.5, 0.75)}
        print(f"move models {nm}: {len(y)} discovery rows; median {np.median(y) * 100:.2f}%, 75th pct {np.percentile(y, 75) * 100:.2f}% ({time.time() - t0:.0f}s)", flush=True)
    for wname, (a, b) in W.items():
        P_ = picks(J, B, RF._rows(J, a, b, every=1)); days = (b - a) / DAY
        for x in P_:
            Xi = RF._X(P, np.array([x["i"]]), cols); q50 = float(M[x["sd"]][0.5].predict(Xi)[0]); q75 = float(M[x["sd"]][0.75].predict(Xi)[0])
            x["tp2"] = float(np.clip(q50, 0.025, 0.06)); x["tp3"] = float(np.clip(max(q75, x["tp2"] + 0.005), x["tp2"] + 0.005, 0.10))
        V = {"TODAY (50% at +2%, rest break-even/72h)": lambda x: walk_steps(J, x, [0.02], [0.5], [2 * FEE]),
             "STEPS data, stop to +1% after TP2": lambda x: walk_steps(J, x, [0.02, x["tp2"], x["tp3"]], [0.5, 0.25, 0.25], [2 * FEE, 0.01, x["tp2"]]),
             "STEPS data, stop to +2% after TP2": lambda x: walk_steps(J, x, [0.02, x["tp2"], x["tp3"]], [0.5, 0.25, 0.25], [2 * FEE, 0.02, x["tp2"]]),
             "STEPS fixed 3% / 4.5%, stop to +1%": lambda x: walk_steps(J, x, [0.02, 0.03, 0.045], [0.5, 0.25, 0.25], [2 * FEE, 0.01, 0.03]),
             "RATCHET (keep half of best, = lock)": lambda x: walk_steps(J, x, [0.02], [0.5], [2 * FEE], ratchet=True),
             "OWNER fixed 3% / 4.5%, SL3 = TP1 (+2%)": lambda x: walk_steps(J, x, [0.02, 0.03, 0.045], [0.5, 0.25, 0.25], [2 * FEE, 0.02, 0.03]),
             "OWNER fixed, SL3 = TP1, 24h max": lambda x: walk_steps(J, x, [0.02, 0.03, 0.045], [0.5, 0.25, 0.25], [2 * FEE, 0.02, 0.03], hz=24),
             "OWNER fixed, SL3 = TP1, 48h max": lambda x: walk_steps(J, x, [0.02, 0.03, 0.045], [0.5, 0.25, 0.25], [2 * FEE, 0.02, 0.03], hz=48),
             "STEPS fixed, SL3 = +1%, 24h max": lambda x: walk_steps(J, x, [0.02, 0.03, 0.045], [0.5, 0.25, 0.25], [2 * FEE, 0.01, 0.03], hz=24),
             "TODAY, 24h max": lambda x: walk_steps(J, x, [0.02], [0.5], [2 * FEE], hz=24)}
        out[wname] = {k: stats(run(J, P_, f), days) for k, f in V.items()}
        for k, v in out[wname].items(): print(f"  {wname} | {k}: {v}", flush=True)
        t2 = [x["tp2"] for x in P_]; t3 = [x["tp3"] for x in P_]
        print(f"  learned TP2 median {np.median(t2) * 100:.2f}% (10-90%: {np.percentile(t2, 10) * 100:.2f}-{np.percentile(t2, 90) * 100:.2f}), TP3 median {np.median(t3) * 100:.2f}% ({np.percentile(t3, 10) * 100:.2f}-{np.percentile(t3, 90) * 100:.2f})", flush=True)
    base = {w: out[w]["TODAY (50% at +2%, rest break-even/72h)"] for w in W}; win = []
    for k in out["validation year"]:
        if k.startswith("TODAY"): continue
        if all((out[w][k].get("monthly") or -99) > (base[w].get("monthly") or -99) + 0.5 and (out[w][k].get("maxDD") or 99) <= (base[w].get("maxDD") or 0) + 2 for w in W):
            win.append((round(sum(out[w][k]["monthly"] for w in W), 2), k))
    out["verdict"] = sorted(win, reverse=True); print("VERDICT (clearly better in both unseen windows):", out["verdict"] or "none", flush=True)
    json.dump(out, open("labtest.json", "w"), indent=1, default=str)

if __name__ == "__main__": main()
