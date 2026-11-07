"""THE REFEREE: one brain that weighs every kind of evidence, picks at most ONE trade a day, and manages it by evidence.

Question it answers for every coin, long AND short:  "will this reach +2% before its stop (within 48h)?"
  - the stop sits outside the coin's normal noise: 1.25x its usual daily move, kept between 1.5% and 4% (far inside liquidation)
How it decides (all learned from the past only, never from the future):
  1. FAMILIES of independent evidence each give a probability: positioning, flow, trend/structure, volatility, market mood,
     invented measurements, and "similar moments" (the 50 most similar past situations).        -> trained on the DISCOVERY years
  2. A small combiner learns how much to trust each family (and when).                         -> trained on the VALIDATION year
  3. FINAL EXAM: the whole policy is replayed on the newest months it has never seen.            -> its honest record
  4. Live: A-grade = clearly above break-even AND >=2 families agree AND <=1 against; B-grade = smaller edge (after 18:00 UTC only).
     Unfamiliar situations (unlike anything in history) are skipped.
Champion rules' votes are SHOWN on the evidence card but NOT learned from: those rules were chosen on the same years (leakage).
Trade management: hard stop; at +2% half is taken and the stop goes to break-even; the rest is held or closed by an EXIT model
("from here, +1% more before -1%?"); one add-on to a confirmed winner; max 72h. Sizing by risk: a full loss costs ~4% (A) / 2% (B)
of the account, margin capped at 20%, 10x isolated. A kill switch pauses it if live results fall far below what the exam predicted."""
import os, math, time, json, numpy as np, pandas as pd
from . import config as C

H = 3600; TARGET = 0.02; HORIZON = 48; STOP_K, STOP_LO, STOP_HI = 1.25, 0.015, 0.04; LEV = 10; MAX_MARGIN = 0.20
RISK = {"A": 0.04, "B": 0.02}; MAX_HOLD = 72; START = 500.0; TOP_LIQUID = 40
MODEL_PATH = lambda: f"{C.DATA_DIR}/models/referee.joblib"

FAMILIES = {
    "positioning": ["p_glob", "p_glob24", "p_topPos", "p_topPos24", "p_topAcc", "p_oi1", "p_oi4", "p_oi24", "p_oiz", "x_glob", "x_topPos", "x_oi24"],
    "flow": ["p_timb1", "p_timb4", "p_timb24", "p_tk1", "p_tk4", "p_vr", "x_timb24"],
    "trend": ["p_r1", "p_r4", "p_r24", "p_r72", "p_r168", "p_pos24", "don20", "p_tr50", "emat", "emax", "rsi14", "bbz", "adx14", "streak", "x_r24", "x_r168"],
    "volatility": ["p_vol24", "p_volr", "x_vol24", "p_adx14", "p_bbz"],
    "leverage": ["p_fund", "p_basis", "p_basis24", "p_hlprem", "p_hlprem24", "p_hlfund24"],
    "realmoney": ["p_sshare", "p_sshare7", "p_stimb24", "p_basis"],
    "mood": ["p_btc_r1", "p_btc_r24", "p_fg", "p_fg7", "p_cbp", "p_cbp24", "p_dvol", "p_dvol24", "p_dvole", "p_stab7", "hour", "dow"],
}
KNN_FEATS = ["p_r4", "p_r24", "p_r72", "p_vol24", "p_oi24", "p_glob", "p_topPos", "p_timb4", "p_pos24", "p_btc_r24", "p_fund", "p_basis"]

def fam_cols(P):
    out = {f: [c for c in cols if c in P.columns] for f, cols in FAMILIES.items()}
    inv = sorted(c for c in P.columns if c.startswith("p_u_"))
    if inv: out["invented"] = inv
    return {f: c for f, c in out.items() if len(c) >= 2}

def stop_pct(dvol): return np.clip(STOP_K * dvol, STOP_LO, STOP_HI)
def breakeven(stop): return (stop + C.FEE) / (TARGET + stop)        # win rate needed at +2% target vs this stop, after costs

def barrier(J, rows, side, target, stop, horizon):
    """first touch: 1 = target first, 0 = stop first or time-out; also the realised return (after costs). Stops checked first."""
    rows = np.asarray(rows); e = J.O[rows]; n = len(rows)
    y = np.zeros(n, np.int8); ret = np.full(n, np.nan); open_ = np.ones(n, bool); last = J.last[rows]
    tg = e * (1 + side * target); st = e * (1 - side * stop)
    for j in range(horizon):
        k = rows + j; ok = open_ & (k <= last)
        if not ok.any(): break
        kk = np.where(ok, k, rows)
        hi, lo = J.H[kk], J.L[kk]
        s_hit = ok & ((lo <= st) if side > 0 else (hi >= st)); t_hit = ok & ~s_hit & ((hi >= tg) if side > 0 else (lo <= tg))
        ret[s_hit] = -stop[s_hit] - C.FEE; y[t_hit] = 1; ret[t_hit] = target - C.FEE
        open_ &= ~(s_hit | t_hit)
        ended = open_ & (k == last)                    # the coin's data ends (delisting / newest hour): close at that bar
        if ended.any(): ret[ended] = side * (J.Cl[k[ended]] / e[ended] - 1) - C.FEE; open_ &= ~ended
    k = np.minimum(rows + horizon - 1, last); rem = open_
    ret[rem] = side * (J.Cl[k[rem]] / e[rem] - 1) - C.FEE
    return y, ret

def _rows(J, t_lo, t_hi, every=3):
    P = J.P; m = (J.t >= t_lo) & (J.t < t_hi) & np.isfinite(J.dvol) & np.isfinite(J.O) & (J.t % (every * H) == 0)
    m &= ~P.coin.isin(C.DEAD).values
    return np.flatnonzero(m)

def _X(P, rows, cols): return P[cols].values[rows].astype(np.float32)

def train(J, log=print):
    """fit families (discovery) -> combiner (validation) -> final exam (newest months). Returns a model bundle."""
    from sklearn.ensemble import HistGradientBoostingClassifier as HGB
    from sklearn.linear_model import LogisticRegression
    from sklearn.neighbors import NearestNeighbors
    t0 = time.time(); P = J.P; fams = fam_cols(P); tmin = int(J.t.min())
    D, V, X_ = _rows(J, tmin, J.val0), _rows(J, J.val0, J.hold0), _rows(J, J.hold0, J.t_end - HORIZON * H)
    bundle = {"fams": fams, "sides": {}, "trainedAt": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "split": [int(J.val0), int(J.hold0), int(J.t_end)]}
    kcols = [c for c in KNN_FEATS if c in P.columns]
    for side, name in ((1, "long"), (-1, "short")):
        sd = {}
        lab = {}
        for nm, rows in (("D", D), ("V", V), ("X", X_)):
            stp = stop_pct(J.dvol[rows]); y, r = barrier(J, rows, side, TARGET, stp, HORIZON); lab[nm] = (rows, y, r, stp)
        Dr, Dy = lab["D"][0], lab["D"][1]; base = float(Dy.mean())
        models = {}
        for f, cols in fams.items():
            m = HGB(max_iter=150, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=300, l2_regularization=1.0, early_stopping=False, random_state=0)
            models[f] = m.fit(_X(P, Dr, cols), Dy)
        # similar moments: 50 nearest past situations (discovery years)
        KX = np.nan_to_num(_X(P, Dr, kcols), nan=0.5); nn = NearestNeighbors(n_neighbors=50).fit(KX)
        dist, _ = nn.kneighbors(KX[np.random.default_rng(0).choice(len(KX), min(20000, len(KX)), replace=False)])
        sd.update({"models": models, "knn": nn, "knnY": Dy.astype(np.float32), "knnCols": kcols, "unfamiliar": float(np.percentile(dist.mean(1), 97)), "base": base})
        def fam_probs(rows):
            out = {f: models[f].predict_proba(_X(P, rows, cols))[:, 1] for f, cols in fams.items()}
            Q = np.nan_to_num(_X(P, rows, kcols), nan=0.5); d, ix = nn.kneighbors(Q); out["similar"] = Dy[ix].mean(1); return out, d.mean(1)
        lg = lambda p: np.log(np.clip(p, 1e-3, 1 - 1e-3) / (1 - np.clip(p, 1e-3, 1 - 1e-3)))
        Vr, Vy = lab["V"][0], lab["V"][1]; Vp, _ = fam_probs(Vr); order = list(Vp)
        comb = LogisticRegression(C=0.5, max_iter=500).fit(np.column_stack([lg(Vp[f]) for f in order]), Vy)
        sd.update({"comb": comb, "order": order, "weights": {f: round(float(w), 3) for f, w in zip(order, comb.coef_[0])}})
        # choose the safety margin above break-even on the VALIDATION year (learned, not hand-set)
        Vprob = comb.predict_proba(np.column_stack([lg(Vp[f]) for f in order]))[:, 1]; Vbe = breakeven(lab["V"][3])
        best_m, best_g = 0.1, -1e9
        for mg in (0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15):
            pick = Vprob >= Vbe + mg
            if pick.sum() >= 30:
                g = _policy(J, Vr[pick], lab["V"][2][pick], Vprob[pick], lab["V"][3][pick])["monthly"]
                if g is not None and g > best_g: best_g, best_m = g, mg
        sd["margin"] = best_m; sd["valMonthly"] = round(best_g, 2) if best_g > -1e9 else None
        sd["off"] = not (best_g > 0)                  # no edge on the validation year -> this side abstains entirely
        # FINAL EXAM on the newest months: everything frozen
        Xr, Xy, Xret, Xstp = lab["X"]; Xp, Xd = fam_probs(Xr); Xprob = comb.predict_proba(np.column_stack([lg(Xp[f]) for f in order]))[:, 1]
        cal = pd.DataFrame({"p": Xprob, "y": Xy}); cal["bin"] = pd.cut(cal.p, [0, .4, .5, .55, .6, .65, .7, 1])
        sd["calibration"] = [[str(b), int(len(g)), round(float(g.p.mean()), 3) if len(g) else None, round(float(g.y.mean()), 3) if len(g) else None] for b, g in cal.groupby("bin", observed=False)]
        pick = (Xprob >= breakeven(Xstp) + best_m) & (Xd <= sd["unfamiliar"])
        sd["exam"] = _policy(J, Xr[pick], Xret[pick], Xprob[pick], Xstp[pick])
        sd["exam"]["base"] = round(float(Xy.mean()), 3); sd["exam"]["n_candidates"] = int(len(Xr))
        # go / no-go: a side trades with paper money only if it ALSO made money on the final exam with enough trades.
        # Otherwise it only "shadows" (decisions are logged, not traded) until a weekly retrain passes.
        ex = sd["exam"]; sd["live"] = (not sd["off"]) and (ex.get("trades") or 0) >= 15 and (ex.get("monthly") or -1) > 0
        sd["why"] = ("trades" if sd["live"] else "no edge on the validation year" if sd["off"] else
                     f"failed its final exam ({ex.get('trades')} trades, {ex.get('monthly')}%/month)" if (ex.get("trades") or 0) >= 15 else f"too few exam trades ({ex.get('trades')})")
        # EXIT model: from any hour, +1% more before -1% within 24h
        ey, _ = barrier(J, Dr, side, 0.01, np.full(len(Dr), 0.01), 24)
        allc = sorted({c for f, cols in fams.items() if f != "invented" for c in cols})      # invented measurements can retire: not in the exit model
        sd["exit"] = HGB(max_iter=120, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=300, early_stopping=False, random_state=0).fit(_X(P, Dr, allc), ey)
        sd["exitCols"] = allc
        bundle["sides"][name] = sd
        log(f"   referee {name}: base rate {base:.3f}, margin {best_m}, exam {sd['exam']}, weights {sd['weights']}")
    bundle["secs"] = round(time.time() - t0); bundle["coins"] = int(P.coin.nunique())
    return bundle

def _policy(J, rows, rets, probs, stops):
    """the daily policy, exactly as live: the FIRST qualifying trade of each UTC day, sized by risk (full loss ~4% of the account),
    position at most 2x the account (20% margin x 10x). Simple version: no partial exits (those only add on top)."""
    if len(rows) == 0: return {"trades": 0, "win": None, "monthly": None}
    df = pd.DataFrame({"t": J.t[rows], "day": J.t[rows] // 86400, "r": rets, "p": probs, "s": stops}).sort_values(["t", "p"], ascending=[True, False]).groupby("day").head(1)
    r = df.r.values; expo = np.minimum(RISK["A"] / df.s.values, MAX_MARGIN * LEV)
    acct = np.maximum(r * expo, -MAX_MARGIN)                    # isolated margin: never more than the margin is lost
    days = max(1, (df.day.max() - df.day.min() + 1)); per_month = len(df) / days * 30.4
    g = float(np.mean(np.log1p(acct))); monthly = math.expm1(g * per_month)
    return {"trades": int(len(df)), "win": round(float((r > 0).mean()) * 100, 1), "avg": round(float(r.mean()) * 100, 3), "perMonth": round(float(per_month), 1), "monthly": round(float(monthly) * 100, 2)}

def save(bundle):
    import joblib
    os.makedirs(os.path.dirname(MODEL_PATH()), exist_ok=True); joblib.dump(bundle, MODEL_PATH() + ".tmp", compress=3); os.replace(MODEL_PATH() + ".tmp", MODEL_PATH())

def load():
    import joblib
    try: return joblib.load(MODEL_PATH()) if os.path.exists(MODEL_PATH()) else None
    except Exception: return None

def score(J, B, rows):
    """probabilities, family opinions and familiarity for candidate rows, both sides"""
    P = J.P; out = {}
    lg = lambda p: np.log(np.clip(p, 1e-3, 1 - 1e-3) / (1 - np.clip(p, 1e-3, 1 - 1e-3)))
    for name, sd in B["sides"].items():
        fp = {}
        for f, cols in B["fams"].items():
            if all(c in P.columns for c in cols): fp[f] = sd["models"][f].predict_proba(_X(P, rows, cols))[:, 1]
            else: fp[f] = np.full(len(rows), sd["base"])
        Q = np.nan_to_num(_X(P, rows, sd["knnCols"]), nan=0.5); d, ix = sd["knn"].kneighbors(Q); fp["similar"] = sd["knnY"][ix].mean(1)
        prob = sd["comb"].predict_proba(np.column_stack([lg(fp[f]) for f in sd["order"]]))[:, 1]
        out[name] = {"prob": prob, "fams": fp, "dist": d.mean(1), "unfamiliar": d.mean(1) > sd["unfamiliar"]}
    return out

def grade(prob, fams, base, margin, be, unfamiliar):
    if unfamiliar: return None
    sup = sum(1 for f, p in fams.items() if p >= base + 0.03); against = sum(1 for f, p in fams.items() if p <= base - 0.03)
    if prob >= be + margin and sup >= 2 and against <= 1: return "A"
    if prob >= be + margin / 2 and sup >= 2 and against <= 2: return "B"
    return None

def liquid_coins(J, n=TOP_LIQUID):
    P = J.P
    if "_qv24" not in P.columns: return set(P.coin.unique()) - set(C.DEAD)
    recent = P[P.t >= J.t.max() - 30 * 86400]
    s = recent.groupby("coin")["_qv24"].median().drop(labels=list(C.DEAD), errors="ignore").sort_values(ascending=False)
    return set(s.index[:n])

def votes(mem, J, rows):
    """which champion rules fire on these rows (shown, not learned from)"""
    out = {int(i): [] for i in rows}; rs = set(int(i) for i in rows)
    for k, t in mem["tests"].items():
        if t["status"] != "champion" or t["h"]["kind"] not in ("event", "union"): continue
        for i, sd, _ in J.raw_entries(t["h"], t_lo=int(J.t[rows].min()) - 1 if len(rows) else None):
            if i in rs: out[i].append(("L" if sd > 0 else "S") + str(t.get("no")))
    return out

# ── the account ─────────────────────────────────────────────────────────────────────────────────
def step(mem, J, now_iso, log=print, problems=None):
    R = mem.setdefault("referee", {}); B = load()
    A = R.get("acct")
    if A is None:
        A = R["acct"] = {"born": now_iso, "start": START, "cash": START, "lastT": int(J.t.max()) - H, "open": [], "closed": [], "curve": [[int(J.t.max()), START]],
                         "decisions": [], "paused": None}
    newest = int(J.t.max())
    # 1) manage open trades hour by hour on the new bars
    for p in list(A["open"]): _manage(J, B, p, A, newest)
    # 2) look for today's trade in the hours that arrived since the last run
    if B is not None and not A.get("paused") and newest > A["lastT"]:
        live = liquid_coins(J)
        rows = np.flatnonzero((J.t > A["lastT"]) & (J.t <= newest) & np.isin(J.coin, list(live)) & np.isfinite(J.dvol) & np.isfinite(J.O))
        if len(rows):
            S = score(J, B, rows); V = votes(mem, J, rows); REG = regimes(J, np.unique(J.t[rows])); R["regimeNow"] = REG.get(int(J.t[rows].max()))
            # what it saw at the newest hour (shown even when it skips): the closest candidates and what they lacked
            look = []; newest_rows = np.flatnonzero(J.t[rows] == J.t[rows].max())
            for pos in newest_rows:
                i = int(rows[pos]); stp = float(stop_pct(J.dvol[i])); be = breakeven(stp)
                for name, sd in B["sides"].items():
                    fp = {f: float(v[pos]) for f, v in S[name]["fams"].items()}; pr = float(S[name]["prob"][pos])
                    sup = [f for f, p in fp.items() if p >= sd["base"] + 0.03]; ag = [f for f, p in fp.items() if p <= sd["base"] - 0.03]
                    look.append({"coin": str(J.coin[i]), "side": name.upper(), "prob": round(pr, 3), "need": round(be + sd["margin"], 3), "for": sup, "against": ag,
                                 "unfamiliar": bool(S[name]["unfamiliar"][pos]), "off": not side_live(R, name, sd, REG.get(int(J.t[i]))),
                                 "regime": REG.get(int(J.t[i])), "need": round(be + margin_of(R, name, sd), 3),
                                 "grade": grade(pr, fp, sd["base"], margin_of(R, name, sd), be, S[name]["unfamiliar"][pos])})
            look.sort(key=lambda x: (x["off"], -(x["prob"] - x["need"])))          # sides that may trade first, closest to a trade on top
            keep = [x for x in look if not x["off"]][:6] + [x for x in look if x["off"]][:3]
            R["look"] = {"t": int(J.t[rows].max()), "at": now_iso, "rows": keep, "scanned": int(len(newest_rows))}
            _shadow_add(J, B, R, rows, S, REG)
            for pos in np.argsort(J.t[rows], kind="stable"):
                i = int(rows[pos]); day = int(J.t[i] // 86400)
                if any(int(x["t"] // 86400) == day for x in A["open"] + A["closed"][-5:]) : continue      # max one new trade per UTC day
                if any(x["coin"] == str(J.coin[i]) for x in A["open"]): continue
                stp = float(stop_pct(J.dvol[i])); be = breakeven(stp); best = None
                for name, sd in B["sides"].items():
                    if not side_live(R, name, sd, REG.get(int(J.t[i]))): continue
                    g = grade(S[name]["prob"][pos], {f: v[pos] for f, v in S[name]["fams"].items()}, sd["base"], margin_of(R, name, sd), be, S[name]["unfamiliar"][pos])
                    hour = int(J.t[i] % 86400 // H)
                    if g == "B" and hour < 18: g = None
                    if g and (best is None or S[name]["prob"][pos] > best[2]): best = (name, g, float(S[name]["prob"][pos]))
                if best:
                    _open(J, B, A, i, best, stp, be, S, pos, V.get(i, []), now_iso, R); A["open"][-1]["regime"] = REG.get(int(J.t[i])); _manage(J, B, A["open"][-1], A, newest)
    A["lastT"] = max(A["lastT"], newest)
    _shadow_resolve(J, R, newest); adapt(R)
    for x in A["closed"][-10:]:
        if "pm" not in x:
            try: x["pm"] = postmortem(J, x)
            except Exception as e: x["pm"] = f"(post-mortem unavailable: {e})"
    # 3) live marks + kill switch
    _mark(A)
    done = [x for x in A["closed"] if x.get("grade")]
    if len(done) >= 20 and not A.get("paused"):
        exp = np.mean([x["prob"] for x in done[-20:]]); act = np.mean([1.0 if x["hitTarget"] else 0.0 for x in done[-20:]])
        if act < exp - 0.20: A["paused"] = f"{now_iso}: live hit rate {act:.0%} far below the predicted {exp:.0%} over 20 trades; paused until retrained"
    A["closed"] = A["closed"][-300:]; A["curve"] = A["curve"][-1000:]; A["decisions"] = A["decisions"][-60:]
    R["model"] = None if B is None else {"trainedAt": B["trainedAt"], "secs": B.get("secs"), "coins": B.get("coins"),
                                          **{n: {k: sd.get(k) for k in ("base", "margin", "weights", "exam", "calibration", "valMonthly", "off", "live", "why")} for n, sd in B["sides"].items()}}
    log(f"referee: ${A.get('equity', A['cash'])} ({len(A['open'])} open, {len(A['closed'])} closed){' PAUSED' if A.get('paused') else ''}{' (no model yet)' if B is None else ''}")

def margin_cap(A):
    """your plan: 20% of the account as margin while it is small, easing to 10% as it grows (2x start -> 4x start)"""
    g = A["cash"] / A["start"]
    return MAX_MARGIN if g <= 2 else max(0.10, MAX_MARGIN - 0.10 * (g - 2) / 2)

def _open(J, B, A, i, best, stp, be, S, pos, votes_, now_iso, R=None):
    name, g, prob = best; sd = 1 if name == "long" else -1; e = float(J.O[i]); eq = A["cash"]
    mult = ((R or {}).get("adapt") or {}).get(name, {}).get("riskMult", 1.0)
    notional = min(RISK[g] * mult * eq / stp, margin_cap(A) * eq * LEV); margin = notional / LEV
    fams = {f: round(float(v[pos]), 3) for f, v in S[name]["fams"].items()}; base = B["sides"][name]["base"]
    card = {"for": sorted([f for f, p in fams.items() if p >= base + 0.03], key=lambda f: -fams[f]), "against": [f for f, p in fams.items() if p <= base - 0.03],
            "similar": round(fams.get("similar", 0) * 100), "rules": votes_}
    p = {"coin": str(J.coin[i]), "side": "LONG" if sd > 0 else "SHORT", "sd": sd, "t": int(J.t[i]), "entry": e, "grade": g, "prob": round(prob, 3), "be": round(be, 3),
         "stop": e * (1 - sd * stp), "target": e * (1 + sd * TARGET), "stopPct": round(stp, 4), "lev": LEV, "units": [[e, notional]], "margin": round(margin, 2),
         "realized": 0.0, "best": e, "half": False, "added": False, "notional": round(notional, 2), "avgEntry": e, "mark": e, "card": card, "fams": fams, "row": int(i)}
    A["open"].append(p); A["decisions"].append({"at": now_iso, "t": p["t"], "coin": p["coin"], "side": p["side"], "grade": g, "prob": p["prob"], "card": card})

def _size(p): return sum(n for _, n in p["units"])
def _avg(p): return sum(px * n for px, n in p["units"]) / max(1e-9, _size(p))

def _close(A, p, px, how, t, part=1.0):
    n = _size(p) * part; r = p["sd"] * (px / _avg(p) - 1)
    pnl = n * r - n * C.FEE; A["cash"] = round(A["cash"] + pnl, 2); p["realized"] = round(p["realized"] + pnl, 2)
    if part < 1: p["units"] = [[a, b * (1 - part)] for a, b in p["units"]]; return
    A["open"] = [x for x in A["open"] if x is not p]
    A["closed"].append({k: v for k, v in p.items() if k not in ("units", "sd", "row", "fams")} | {"exit": px, "exitT": t, "how": how, "usd": p["realized"], "after": A["cash"],
                        "hitTarget": bool(p["half"]), "exitPx": px, "roe": round(p["realized"] / max(1e-9, p["margin"]) * 100, 1), "pct": round(p["sd"] * (px / p["entry"] - 1) * 100, 2)})
    A["curve"].append([t, A["cash"]])

def _manage(J, B, p, A, newest):
    """replay the new hours of an open trade: stop, half at target + break-even, evidence-based hold/close, one add, max hold"""
    T, O, Hh, Ll, Cc = J._bars(p["coin"]); sd = p["sd"]
    j = int(np.searchsorted(T, p.get("lastBar", p["t"] - H) + H))
    while j < len(T) and T[j] <= newest:
        t = int(T[j]); hi, lo = Hh[j], Ll[j]
        if (sd > 0 and lo <= p["stop"]) or (sd < 0 and hi >= p["stop"]):
            _close(A, p, p["stop"], "stop" if not p["half"] else "break-even / trailing stop", t + H); return
        if not p["half"] and ((sd > 0 and hi >= p["target"]) or (sd < 0 and lo <= p["target"])):
            _close(A, p, p["target"], "half at +2%", t + H, part=0.5); p["half"] = True
            p["stop"] = p["entry"] * (1 + sd * C.FEE * 2)                          # rest is now risk-free (break-even)
        p["best"] = max(p["best"], hi) if sd > 0 else min(p["best"], lo); p["lastBar"] = t
        if (t + H - p["t"]) / H >= MAX_HOLD: _close(A, p, float(Cc[j]), f"time ({MAX_HOLD}h)", t + H); return
        # evidence-based decisions at the close of this hour
        if B is not None:
            rowmask = np.flatnonzero((J.coin == p["coin"]) & (J.t == t + H))
            if len(rowmask):
                r = int(rowmask[0]); side = "long" if sd > 0 else "short"; sdm = B["sides"][side]
                cont = float(sdm["exit"].predict_proba(_X(J.P, [r], sdm["exitCols"]))[0, 1]); p["cont"] = round(cont, 3)
                px = float(Cc[j]); gain = sd * (px / p["entry"] - 1)
                dv = float(J.dvol[r]) if np.isfinite(J.dvol[r]) else p["stopPct"]
                if p["half"]:
                    if cont < 0.40: _close(A, p, px, "evidence: move fading, profit secured", t + H); return
                    trail = p["best"] * (1 - sd * (1.5 if cont >= 0.6 else 1.0) * dv)
                    p["stop"] = max(p["stop"], trail) if sd > 0 else min(p["stop"], trail)
                elif not p["added"] and gain >= 0.01 and cont >= 0.6:
                    p["units"].append([px, _size(p) * 0.5]); p["added"] = True
                    p["stop"] = p["entry"] if sd > 0 else p["entry"]                    # whole position now stops at the first entry
                    p["margin"] = round(_size(p) / LEV, 2)
        j += 1

def _mark(A):
    from .account import live_prices
    lp = live_prices({p["coin"] for p in A["open"]}); unreal = 0.0
    for p in A["open"]:
        px = lp.get(p["coin"]) or p.get("mark") or p["entry"]; p["mark"] = px
        p["unreal"] = round(_size(p) * (p["sd"] * (px / _avg(p) - 1)) - _size(p) * C.FEE, 2); unreal += p["unreal"]
        p["size"] = round(_size(p) / _avg(p), 6); p["avgEntry"] = _avg(p); p["notional"] = round(_size(p), 2)
        p["liq"] = _avg(p) * (1 - p["sd"] * (1 / LEV - 0.005))       # isolated 10x: ~9.5% against the average entry (0.5% maintenance)
        p["roe"] = round(p["unreal"] / max(1e-9, p["margin"]) * 100, 2); p["hours"] = round((time.time() - p["t"]) / H, 1)
    A["equity"] = round(A["cash"] + unreal, 2)


def maybe_train(J, mem, log=print, time_left=1e9):
    """retrain once a week (or when there is no model yet), only if there is enough time left in this run.
    A failed training keeps the previous model; with no model at all the referee simply waits."""
    R = mem.setdefault("referee", {}); B = load(); age = None
    if B is not None:
        try: age = (time.time() - time.mktime(time.strptime(B["trainedAt"], "%Y-%m-%d %H:%M"))) / 86400
        except Exception: age = 99
    fresh_fams = set(fam_cols(J.P)) - {"invented"}
    if B is not None and age < 7 and fresh_fams <= set(B["fams"]): return False      # also retrain when new kinds of evidence appear
    if time_left < 20 * 60: log(f"referee: training postponed ({time_left/60:.0f} min left in this run)"); R["trainNote"] = "postponed: not enough time this run"; return False
    t0 = time.time(); B = train(J, log); save(B)
    R["trainNote"] = f"trained {B['trainedAt']} UTC in {time.time()-t0:.0f}s on {B['coins']} coins"
    R["trainings"] = (R.get("trainings") or [])[-20:] + [{"at": B["trainedAt"], **{n: {"exam": sd["exam"], "margin": sd["margin"], "off": sd["off"], "live": sd["live"]} for n, sd in B["sides"].items()}}]
    if R.get("acct", {}).get("paused"): R["acct"]["paused"] = None          # a fresh model gets a fresh chance
    return True


# ── shadow book: every A/B call on BOTH sides is scored against what really happened (live, out-of-sample) ────────────
def side_live(R, name, sd, reg=None):
    """a side trades if its exams passed, unless its own live shadow record has since proven otherwise (either way).
    The most specific evidence wins: its live record in THIS market regime, then its overall live record, then its exams."""
    rv = ((R.get("regimeOverride") or {}).get(name) or {}).get(reg) if reg else None
    if rv is not None: return rv["live"]
    ov = (R.get("override") or {}).get(name)
    if ov is not None: return ov["live"]
    return (not sd.get("off")) and sd.get("live", True)

def _shadow_add(J, B, R, rows, S, REG=None):
    sh = R.setdefault("shadow", []); recent = {(x["coin"], x["side"]): x["t"] for x in sh[-400:]}
    for pos in range(len(rows)):
        i = int(rows[pos]); stp = float(stop_pct(J.dvol[i])); be = breakeven(stp)
        for name, sd in B["sides"].items():
            g = grade(S[name]["prob"][pos], {f: v[pos] for f, v in S[name]["fams"].items()}, sd["base"], sd["margin"], be, S[name]["unfamiliar"][pos])
            if not g: continue
            key = (str(J.coin[i]), name)
            if int(J.t[i]) - recent.get(key, -10 ** 12) < 24 * H: continue          # one call per coin and side per day
            sh.append({"t": int(J.t[i]), "coin": key[0], "side": name, "grade": g, "prob": round(float(S[name]["prob"][pos]), 3), "be": round(be, 3), "stop": round(stp, 4),
                       "reg": (REG or {}).get(int(J.t[i]))})
            recent[key] = int(J.t[i])
    del sh[:-3000]

def _shadow_resolve(J, R, newest):
    sh = R.get("shadow") or []
    for x in sh:
        if "y" in x or x["t"] + HORIZON * H > newest + H: continue
        r = np.flatnonzero((J.coin == x["coin"]) & (J.t == x["t"]))
        if not len(r): x["y"] = None; continue
        y, ret = barrier(J, r[:1], 1 if x["side"] == "long" else -1, TARGET, np.array([x["stop"]]), HORIZON)
        x["y"] = int(y[0]); x["ret"] = round(float(ret[0]), 4)
    stats = {}
    for name in ("long", "short"):
        d = [x for x in sh if x["side"] == name and x.get("y") is not None]
        if not d: continue
        last = d[-60:]
        stats[name] = {"n": len(d), "recentN": len(last), "hit": round(float(np.mean([x["y"] for x in last])), 3), "pred": round(float(np.mean([x["prob"] for x in last])), 3),
                       "be": round(float(np.mean([x["be"] for x in last])), 3), "avg": round(float(np.mean([x["ret"] for x in last])) * 100, 3),
                       "open": sum(1 for x in sh if x["side"] == name and "y" not in x)}
    R["shadowStats"] = stats
    # promotion / demotion on live evidence (at least 25 resolved calls)
    ov = R.setdefault("override", {})
    for name, st in stats.items():
        if st["recentN"] < 25: continue
        if st["avg"] > 0.15 and st["hit"] >= st["be"] + 0.03: ov[name] = {"live": True, "why": f"live shadow record: {st['recentN']} calls, hit {st['hit']:.0%} vs break-even {st['be']:.0%}, avg {st['avg']:+.2f}%"}
        elif st["avg"] < 0 and st["hit"] < st["be"]: ov[name] = {"live": False, "why": f"live shadow record below break-even: {st['recentN']} calls, hit {st['hit']:.0%} vs {st['be']:.0%}, avg {st['avg']:+.2f}%"}

def postmortem(J, x):
    """plain-English account of one finished trade: what the evidence said, what the price did, and the verdict"""
    T, O, Hh, Ll, Cc = J._bars(x["coin"]); sd = 1 if x["side"] == "LONG" else -1
    a = int(np.searchsorted(T, x["t"])); b = int(np.searchsorted(T, x["exitT"] - H, side="right"))
    if b <= a: return "too short to analyse"
    e = x["entry"]; fav = (Hh[a:b].max() / e - 1) if sd > 0 else (1 - Ll[a:b].min() / e); adv = (1 - Ll[a:b].min() / e) if sd > 0 else (Hh[a:b].max() / e - 1)
    k_f = int(np.argmax(Hh[a:b] if sd > 0 else -Ll[a:b])); hrs = (x["exitT"] - x["t"]) / H
    card = x.get("card") or {}
    said = f"It went {x['side'].lower()} on {x['coin']} with {round(x['prob']*100)}% odds (grade {x['grade']})" + (f"; for: {', '.join(card.get('for', []))}" if card.get("for") else "") + "."
    did = f" Best point {fav*100:+.1f}% after {k_f+1}h, worst {-adv*100:.1f}%; closed after {hrs:.0f}h ({x['how']}), {x['usd']:+.2f} USD."
    if x["usd"] > 0 and fav > 0.04: v = " Verdict: right call; the move kept going, exits could have held longer."
    elif x["usd"] > 0: v = " Verdict: right call, profit taken."
    elif fav >= 0.015: v = " Verdict: the idea worked for a while (nearly reached the target) but reversed; timing/exit issue more than a wrong read."
    elif adv > x.get("stopPct", 0.02) and k_f < 2: v = " Verdict: wrong read; price went against it almost immediately."
    else: v = " Verdict: no follow-through; the evidence did not play out."
    return said + did + v


# ── adapting: the referee reshapes its own behaviour from live evidence, every hour ──────────────────────────
def regimes(J, ts):
    """market regime per hour, from BTC: trend (above/below its 50-day average) x temperature (volatility in its top 30% = wild)"""
    P = J.P; b = P[(P.coin == "BTC") & P.t.isin(ts)][["t", "tr50", "p_vol24"]]
    out = {}
    for t, tr, pv in b.itertuples(index=False):
        if not np.isfinite(tr): continue
        out[int(t)] = ("up" if tr > 0 else "down") + "-" + ("wild" if (np.isfinite(pv) and pv >= 0.7) else "calm")
    return out

def margin_of(R, name, sd):
    return sd["margin"] + ((R.get("adapt") or {}).get(name, {}).get("marginAdj", 0.0))

def adapt(R):
    """three self-adjustments, each bounded and each explained on the dashboard:
    1) CALIBRATION: if live calls hit less often than predicted, demand more (raise the bar); if more often, relax a little
    2) SIZE: risk per trade scales 0.5x-1.25x with the live edge (never above plan)
    3) REGIME: a side is switched on/off separately in each market regime once it has 15 live calls there"""
    sh = [x for x in (R.get("shadow") or []) if x.get("y") is not None]; ad = {}; rov = {}
    for name in ("long", "short"):
        d = [x for x in sh if x["side"] == name][-80:]
        if len(d) >= 20:
            hit = np.mean([x["y"] for x in d]); pred = np.mean([x["prob"] for x in d]); avg = np.mean([x["ret"] for x in d])
            adj = float(np.clip(pred - hit, -0.04, 0.10)); mult = float(np.clip(1.0 + avg * 25, 0.5, 1.25))   # +1% avg -> 1.25x, -2% -> 0.5x
            ad[name] = {"marginAdj": round(adj, 3), "riskMult": round(mult, 2), "n": len(d), "hit": round(float(hit), 3), "pred": round(float(pred), 3), "avg": round(float(avg) * 100, 3),
                        "why": (f"live calls hit {hit:.0%} vs predicted {pred:.0%}: " + ("bar raised" if adj > 0.005 else "bar relaxed" if adj < -0.005 else "well calibrated")
                                + f" ({adj*100:+.1f} pts); size x{mult:.2f}")}
        byreg = {}
        for x in sh:
            if x["side"] == name and x.get("reg"): byreg.setdefault(x["reg"], []).append(x)
        for reg, d in byreg.items():
            d = d[-60:]
            if len(d) < 15: continue
            hit = np.mean([x["y"] for x in d]); be = np.mean([x["be"] for x in d]); avg = np.mean([x["ret"] for x in d])
            rov.setdefault(name, {})[reg] = {"live": bool(avg > 0 and hit >= be), "n": len(d), "hit": round(float(hit), 3), "be": round(float(be), 3), "avg": round(float(avg) * 100, 3)}
    R["adapt"] = ad; R["regimeOverride"] = rov
