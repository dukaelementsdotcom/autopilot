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
    "leverage": ["p_basis", "p_basis24", "p_hlprem", "p_hlprem24", "p_hlfund24"],
    "realmoney": ["p_sshare", "p_sshare7", "p_stimb24", "p_basis"],
    "mood": ["p_btc_r1", "p_btc_r24", "p_fg", "p_fg7", "p_cbp", "p_cbp24", "p_dvol", "p_dvol24", "p_dvole", "p_stab7", "hour", "dow"],
}
KNN_FEATS = ["p_r4", "p_r24", "p_r72", "p_vol24", "p_oi24", "p_glob", "p_topPos", "p_timb4", "p_pos24", "p_btc_r24", "p_basis"]

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

def _X(P, rows, cols):
    """only the needed rows, column by column (never materialise the whole table: memory stays flat)"""
    rows = np.asarray(rows)
    return np.column_stack([P[c].values[rows].astype(np.float32, copy=False) for c in cols]) if len(cols) else np.empty((len(rows), 0), np.float32)

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
def new_acct(J, now_iso, start=START):
    return {"born": now_iso, "start": start, "cash": start, "lastT": int(J.t.max()) - H, "open": [], "closed": [], "curve": [[int(J.t.max()), start]],
            "decisions": [], "paused": None}

def step(mem, J, now_iso, log=print, problems=None):
    from . import challengers as CH
    R = mem.setdefault("referee", {}); B = load(); Hd = CH.load_heads(B) if B is not None else None
    A = R.get("acct")
    if A is None: A = R["acct"] = new_acct(J, now_iso)
    newest = int(J.t.max())
    chal = CH.sync(R, B, Hd=Hd) if B is not None else {}
    champ_cfg = CH.cfg_of(R, "champion")
    for n, c in chal.items():
        if c["kind"] == "policy" and "acct" not in c:
            c["acct"] = new_acct(J, now_iso); c["acct"].update(champAtBirth=A.get("equity", A["cash"]), bornT=newest)
    accts = [("champion", A, champ_cfg)] + [(n, c["acct"], CH.cfg_of(R, n)) for n, c in chal.items() if c.get("acct")]
    try:
        from . import guard; R["guardApply"] = guard.apply(R, log)
    except Exception as e: R["guardApply"] = {"error": str(e)[:120]}
    # 1) manage open trades hour by hour on the new bars (every account)
    for _, AA, _cfg in accts:
        for p in list(AA["open"]): _manage(J, B, p, AA, newest)
        if AA.get("pairs"):
            from . import specialists; specialists.manage_pairs(J, AA, newest)
    # 2) look for trades in the hours that arrived since the last run
    if B is not None and newest > A["lastT"]:
        live = liquid_coins(J)
        rows = np.flatnonzero((J.t > A["lastT"]) & (J.t <= newest) & np.isin(J.coin, list(live)) & np.isfinite(J.dvol) & np.isfinite(J.O))
        if len(rows):
            S = score(J, B, rows); V = votes(mem, J, rows); REG = regimes(J, np.unique(J.t[rows])); R["regimeNow"] = REG.get(int(J.t[rows].max()))
            R["_votes"] = {int(k): v for k, v in V.items() if v}
            look = []; newest_rows = np.flatnonzero(J.t[rows] == J.t[rows].max())
            for pos in newest_rows:
                i = int(rows[pos]); stp = float(stop_pct(J.dvol[i])); be = breakeven(stp)
                for name, sd in B["sides"].items():
                    fp = {f: float(v[pos]) for f, v in S[name]["fams"].items()}; pr = float(S[name]["prob"][pos])
                    sup = [f for f, p in fp.items() if p >= sd["base"] + 0.03]; ag = [f for f, p in fp.items() if p <= sd["base"] - 0.03]
                    row = {"coin": str(J.coin[i]), "side": name.upper(), "prob": round(pr, 3), "for": sup, "against": ag,
                           "unfamiliar": bool(S[name]["unfamiliar"][pos]), "off": not side_live(R, name, sd, REG.get(int(J.t[i]))),
                           "regime": REG.get(int(J.t[i])), "need": round(be + margin_of(R, name, sd), 3), "px": float(J.O[i]), "stopPct": round(stp, 4),
                           "grade": grade(pr, fp, sd["base"], margin_of(R, name, sd), be, S[name]["unfamiliar"][pos]),
                           "fams": {f: round(p, 3) for f, p in fp.items()}, "base": round(float(sd["base"]), 3), "unf": bool(S[name]["unfamiliar"][pos])}
                    bs = CH.best_shape(Hd, name, {f: [v] for f, v in fp.items()}, sd["order"], [stp]) if Hd else None
                    if bs:                                                     # what the shape challenger would do with the same evidence
                        T_, Hz_, pt_, ev_ = bs[0]; h = Hd["sides"][name]
                        row["shape"] = {"target": round(T_, 4), "hours": Hz_, "prob": round(pt_, 3), "need": round((stp + C.FEE) / (T_ + stp) + h["margin"], 3)}
                    look.append(row)
            look.sort(key=lambda x: (x["off"], -(x["prob"] - x["need"])))
            keep = [x for x in look if not x["off"]][:6] + [x for x in look if x["off"]][:3]
            R["look"] = {"t": int(J.t[rows].max()), "at": now_iso, "rows": keep, "scanned": int(len(newest_rows))}
            _shadow_add(J, B, R, rows, S, REG)
            _ledger_add(J, B, R, rows, S, REG, Hd, V)
            ctx = {"corr": _corr(J, newest), "live": None}
            REG2 = None
            try:
                from . import compass
                REG2 = compass.regimes2(J, np.unique(J.t[rows])); R["trend2Now"] = REG2.get(int(J.t[rows].max()))
            except Exception as e: R["trend2Now"] = f"unavailable: {e}"
            for n, AA, cfg in accts:
                if AA.get("paused"): continue
                RG = REG2 if (cfg.get("trend") == "v2" and REG2) else REG             # the trend-reader challenger sees the market through the new reader
                if cfg.get("spec"):
                    from . import specialists; specialists.trade(J, B, R, AA, cfg, rows, S, RG, V, now_iso, newest, ctx)
                else: _trade(J, B, R, Hd, AA, cfg, rows, S, RG, V, now_iso, newest, ctx, log)
    for _, AA, _cfg in accts: AA["lastT"] = max(AA["lastT"], newest)
    newly = _ledger_resolve(J, R, newest, Hd)
    _shadow_resolve(J, R, newest); adapt(R)
    try:
        CH.online_update(R, B, newly); trust_tables(R); CH.evaluate(R, log); CH.promote(R, log)
    except Exception as e:
        if problems is not None: problems.append(f"challengers: {e}")
    for x in A["closed"][-10:]:
        if "pm" not in x:
            try: x["pm"] = postmortem(J, x)
            except Exception as e: x["pm"] = f"(post-mortem unavailable: {e})"
    # 3) live marks + kill switch
    for _, AA, _cfg in accts: _mark(AA)
    done = [x for x in A["closed"] if x.get("grade")]
    if len(done) >= 20 and not A.get("paused"):
        exp = np.mean([x["prob"] for x in done[-20:]]); act = np.mean([1.0 if x["hitTarget"] else 0.0 for x in done[-20:]])
        if act < exp - 0.20: A["paused"] = f"{now_iso}: live hit rate {act:.0%} far below the predicted {exp:.0%} over 20 trades; paused until retrained"
    from . import archive                                     # trimmed history goes to the permanent archive, never lost
    archive.trim(A["closed"], 300, "referee_trades"); archive.trim(A["curve"], 1000, "referee_curve"); archive.trim(A["decisions"], 60, "referee_decisions")
    for n, AA, _cfg in accts[1:]:
        AA["closed"] = AA["closed"][-200:]; AA["curve"] = AA["curve"][-500:]; AA["decisions"] = AA["decisions"][-30:]
    R["model"] = None if B is None else {"trainedAt": B["trainedAt"], "secs": B.get("secs"), "coins": B.get("coins"),
                                          **{n: {k: sd.get(k) for k in ("base", "margin", "weights", "exam", "calibration", "valMonthly", "off", "live", "why")} for n, sd in B["sides"].items()}}
    if Hd: R["shapeExam"] = {n: {"exam": v["exam"], "k": v.get("k"), "margin": v.get("margin"), "fixedExam": v.get("champExam")} for n, v in Hd["sides"].items()}
    R["champCfg"] = champ_cfg; R.pop("_votes", None)
    try:
        from . import compass; R["upcoming"] = compass.upcoming()
    except Exception: pass
    _journal(R, A)
    try:
        from . import guard; R["guardSync"] = guard.sync(R, log)
    except Exception as e: R["guardSync"] = {"error": str(e)[:120]}
    try:
        from . import witnesses; R["witnesses"] = witnesses.status(B)
    except Exception: pass
    log(f"referee: ${A.get('equity', A['cash'])} ({len(A['open'])} open, {len(A['closed'])} closed){' PAUSED' if A.get('paused') else ''}{' (no model yet)' if B is None else ''}; "
        f"{len(chal)} challengers ({sum(1 for c in chal.values() if c.get('acct'))} with shadow accounts)")

def _journal(R, A):
    """one line a day: how the Arbiter is doing and learning (equity, honest odds, live hit rate, who leads the challengers)"""
    day = time.strftime("%Y-%m-%d", time.gmtime()); Jl = R.setdefault("journal", [])
    sc = R.get("chalScores") or {}; st = (R.get("shadowStats") or {}).get("short") or {}
    lead = sorted([(n, s) for n, s in sc.items() if n != "champion" and s.get("me", {}).get("ll") is not None],
                  key=lambda kv: kv[1]["me"]["ll"] - kv[1]["champion"]["ll"])[:1]
    bands = (R.get("calibration") or {}).get("bands") or []; n = sum(b["n"] for b in bands)
    calerr = round(sum(abs(b["hit"] - b["pred"]) * b["n"] for b in bands) / n, 3) if n else None
    row = {"day": day, "equity": A.get("equity", A["cash"]), "trades": len(A["closed"]), "regime": R.get("regimeNow"),
           "hit": st.get("hit"), "pred": st.get("pred"), "calls": st.get("n"), "calErr": calerr, "scored": (R.get("calibration") or {}).get("scored"),
           "challengers": len(R.get("chal") or {}), "leader": lead[0][0] if lead else None,
           "leaderGap": round(lead[0][1]["champion"]["ll"] - lead[0][1]["me"]["ll"], 4) if lead else None,
           "champion": {k: v for k, v in (R.get("champCfg") or {}).items() if v != CHAMP_DEFAULT.get(k)}}
    if Jl and Jl[-1]["day"] == day: Jl[-1] = row
    else: Jl.append(row)
    del Jl[:-400]

def _corr(J, newest, days=14):
    """hourly-return correlations between coins over the last two weeks, shrunk halfway toward the average (stable with little data)"""
    try:
        m = J.t > newest - days * 86400
        import pandas as pd
        piv = pd.DataFrame({"t": J.t[m], "c": J.coin[m], "r": J.r1[m]}).pivot_table(index="t", columns="c", values="r")
        cm = piv.corr(min_periods=100); avg = float(np.nanmean(cm.values[np.triu_indices(len(cm), 1)]))
        return (0.5 * cm + 0.5 * avg).to_dict()
    except Exception: return {}

def _cands(J, B, R, Hd, cfg, rows, S, REG):
    """for every candidate row and side, under this configuration: probability, grade (S/A/B), trade shape and expected value"""
    from . import challengers as CH
    out = {}
    for name, sd in B["sides"].items():
        fams = S[name]["fams"]; n = len(rows); stp = stop_pct(J.dvol[rows]); be = breakeven(stp); mg = margin_of(R, name, sd)
        prob = np.array(S[name]["prob"], float)
        if cfg.get("odds", "weekly").startswith("witness:"):
            from . import witnesses
            pw = witnesses.prob_rows(cfg["odds"][8:], name, J, rows, fams, B)
            if pw is not None: prob = np.asarray(pw, float)
        elif cfg.get("odds", "weekly") != "weekly":
            prob = np.array([CH.odds(R, B, None if not Hd else Hd, cfg, name, {f: float(v[k]) for f, v in fams.items()}, REG.get(int(J.t[rows[k]])), prob[k]) for k in range(n)])
        sup, ag = CH._support(fams, sd["base"]); unf = np.asarray(S[name]["unfamiliar"])
        if cfg.get("curious"):                                     # an unfamiliar situation is allowed once its GROUP has a good live record
            UT = R.get("unfTrust") or {}
            ok = np.array([(UT.get(unf_key(name, {f: float(v[k]) for f, v in fams.items()}, sd["base"], REG.get(int(J.t[rows[k]])))) or {}).get("state") == "proven" for k in range(len(rows))])
            unf = unf & ~ok
        if cfg.get("cointrust"):                                   # demand more where its own live record on this group of coins is weak
            from .witnesses import SECTOR_OF
            ct = R.get("coinTrust") or {}; mg = mg + np.array([ct.get(SECTOR_OF.get(str(c), "other"), 0.0) for c in J.coin[rows]])
        if cfg.get("rules") and R.get("ruleTrust"):                # research rules as fair witnesses: only their LIVE record, per market state
            V_ = ctx_votes(R, J, rows, name)
            if V_ is not None: prob = 1 / (1 + np.exp(-(np.log(np.clip(prob, 1e-3, 1 - 1e-3) / (1 - np.clip(prob, 1e-3, 1 - 1e-3))) + V_)))
        if cfg.get("lowbar"): mg = mg * 0.5                        # the "less cautious" challenger: half the safety margin
        if cfg.get("doubt"):                                       # split jury: when the witnesses disagree, demand more
            F = np.column_stack([np.asarray(v) for v in fams.values()]); mg = mg + np.std(F, 1)
        T = np.full(n, TARGET); Hz = np.full(n, HORIZON); ev = prob * TARGET - (1 - prob) * stp - C.FEE; ptarget = prob
        if cfg.get("shape") == "vol" and Hd and Hd["sides"].get(name, {}).get("k"):
            bs = CH.best_shape(Hd, name, fams, sd["order"], stp); h = Hd["sides"][name]
            T = np.array([b[0] for b in bs]); Hz = np.array([b[1] for b in bs]); ptarget = np.array([b[2] for b in bs]); ev = np.array([b[3] for b in bs])
            beT = (stp + C.FEE) / (T + stp); mgT = h["margin"] + ((R.get("adapt") or {}).get(name, {}).get("marginAdj", 0.0))
            g = np.where(unf, None, np.where((ptarget >= beT + 2 * mgT) & (sup >= 3) & (ag == 0), "S", np.where((ptarget >= beT + mgT) & (sup >= 2) & (ag <= 1), "A",
                         np.where((ptarget >= beT + mgT / 2) & (sup >= 2) & (ag <= 2), "B", None))))
            be = beT
        else:
            g = np.where(unf, None, np.where((prob >= be + 2 * mg) & (sup >= 3) & (ag == 0), "S", np.where((prob >= be + mg) & (sup >= 2) & (ag <= 1), "A",
                         np.where((prob >= be + mg / 2) & (sup >= 2) & (ag <= 2), "B", None))))
        out[name] = {"prob": prob, "pt": ptarget, "T": T, "Hz": Hz, "ev": ev, "grade": g, "stop": stp, "be": be}
    return out

def _allocate(J, B, R, Hd, A, cfg, rows, S, REG, V, now_iso, newest, ctx, K):
    """DAILY-GOAL ALLOCATOR: one budget per opportunity window. One very strong setup takes the whole budget (and runs);
    several good setups share it, but only if they are NOT the same bet (same side and moving together = one trade).
    Budget: up to 30% margin and 6% risk of the account per window, split by edge; nothing qualifies -> nothing spent."""
    newest_pos = np.flatnonzero(J.t[rows] == newest)
    if not len(newest_pos) or newest < time.time() - 4 * H or len(A["open"]) >= 4: return
    c = []
    for pos in newest_pos:
        i = int(rows[pos])
        if any(x["coin"] == str(J.coin[i]) for x in A["open"]): continue
        for name, sd in B["sides"].items():
            if not side_live(R, name, sd, REG.get(int(J.t[i]))): continue
            g = K[name]["grade"][pos]
            if not g or (g == "B" and int(J.t[i] % 86400 // H) < 18): continue
            edge = float(K[name]["pt"][pos] - K[name]["be"][pos])
            if edge > 0: c.append((edge * (2 if g == "S" else 1), name, g, pos, i))
    if not c: return
    c.sort(reverse=True); pick = []
    corr = ctx.get("corr") or {}
    for e_, name, g, pos, i in c:
        coin = str(J.coin[i])
        same = [q for q in pick if q[1] == name and corr.get(coin, {}).get(str(J.coin[q[4]]), 0) >= 0.6]
        same += [p for p in A["open"] if p["side"] == name.upper() and corr.get(coin, {}).get(p["coin"], 0) >= 0.6]
        if same: continue
        pick.append((e_, name, g, pos, i))
        if len(pick) + len(A["open"]) >= 4 or g == "S": break
    tot = sum(p[0] for p in pick); risk_budget, margin_budget = 0.06, 0.30
    for e_, name, g, pos, i in pick:
        share = e_ / tot
        c2 = {**cfg, "_risk": max(0.01, risk_budget * share), "_budget": margin_budget * share}
        _open(J, B, A, i, (name, g, float(K[name]["pt"][pos]), float(K[name]["ev"][pos])), float(K[name]["stop"][pos]), float(K[name]["be"][pos]), S, pos,
              V.get(i, []), now_iso, R, c2, T=float(K[name]["T"][pos]), Hz=int(K[name]["Hz"][pos]), ctx=ctx)
        A["open"][-1]["regime"] = REG.get(int(J.t[i])); A["open"][-1]["alloc"] = round(share, 2)

def _trade(J, B, R, Hd, A, cfg, rows, S, REG, V, now_iso, newest, ctx, log=print):
    K = _cands(J, B, R, Hd, cfg, rows, S, REG)
    if cfg.get("alloc"): return _allocate(J, B, R, Hd, A, cfg, rows, S, REG, V, now_iso, newest, ctx, K)
    fluid = cfg.get("pace") == "fluid"; guard = cfg.get("risk") == "guard"
    rate = sum(((R.get("callRateBySide") or {}).get(n) or {}).get("A", 0) for n in B["sides"] if side_live(R, n, B["sides"][n], R.get("regimeNow")))
    cap_day = max(1, int(round(2 * rate))) if fluid else 1
    for pos in np.argsort(J.t[rows], kind="stable"):
        i = int(rows[pos]); day = int(J.t[i] // 86400)
        if int(J.t[i]) < newest - H or newest < time.time() - 4 * H: continue   # never enter at a stale price (after a data outage, only the newest hour counts)
        if any(x["coin"] == str(J.coin[i]) for x in A["open"]): continue
        today = [x for x in A["open"] + A["closed"][-20:] if int(x["t"] // 86400) == day]
        best = None
        for name, sd in B["sides"].items():
            if not side_live(R, name, sd, REG.get(int(J.t[i]))): continue
            g = K[name]["grade"][pos]
            if g == "B" and int(J.t[i] % 86400 // H) < 18: g = None
            if not g: continue
            if len(today) >= cap_day and g != "S": continue                   # pace: Grade S is always allowed
            if fluid and len(A["open"]) >= 3 and g != "S": continue
            score_ = K[name]["ev"][pos]
            if best is None or score_ > best[3]: best = (name, g, float(K[name]["pt"][pos]), float(score_))
        if not best: continue
        if guard and not _guard_ok(A, best[0], str(J.coin[i]), K[best[0]]["stop"][pos], ctx): continue
        _open(J, B, A, i, best, float(K[best[0]]["stop"][pos]), float(K[best[0]]["be"][pos]), S, pos, V.get(i, []), now_iso, R, cfg,
              T=float(K[best[0]]["T"][pos]), Hz=int(K[best[0]]["Hz"][pos]), ctx=ctx)
        A["open"][-1]["regime"] = REG.get(int(J.t[i]))

def _guard_ok(A, side, coin, stp, ctx):
    """risk guard: no second bet on the same move, total tail risk capped, daily and weekly brakes"""
    eq = A.get("equity", A["cash"]); S_ = side.upper()
    for p in A["open"]:
        if p["side"] == S_ and (ctx.get("corr") or {}).get(coin, {}).get(p["coin"], 0) >= 0.6: return False
    tail = sum(p.get("notional", 0) * p.get("stopPct", 0.02) * 1.5 for p in A["open"])           # stops can gap: 1.5x as tail allowance
    if tail / max(1e-9, eq) > 0.10: return False
    now = time.time(); dayP = sum(x.get("usd", 0) for x in A["closed"] if x.get("exitT", 0) > now - 86400); wkP = sum(x.get("usd", 0) for x in A["closed"] if x.get("exitT", 0) > now - 7 * 86400)
    return dayP > -0.05 * eq and wkP > -0.10 * eq

MAX_LEV = 20
def lev_for(stp):
    """leverage per trade: as high as 20x, but the liquidation price always stays at least 2.5x the stop distance away"""
    return int(max(2, min(MAX_LEV, math.floor(1 / (2.5 * stp + 0.005)))))

def margin_cap(A):
    """your plan: 20% of the account as margin while it is small, easing to 10% as it grows (2x start -> 4x start)"""
    g = A["cash"] / A["start"]
    return MAX_MARGIN if g <= 2 else max(0.10, MAX_MARGIN - 0.10 * (g - 2) / 2)

def _open(J, B, A, i, best, stp, be, S, pos, votes_, now_iso, R=None, cfg=None, T=TARGET, Hz=HORIZON, ctx=None):
    name, g, prob, _ev = best; sd = 1 if name == "long" else -1; e = float(J.O[i]); eq = A["cash"]; cfg = cfg or {}
    via = "hour open"
    if ctx is not None:                                       # honest timing: fill at the live price at decision time, not the hour's open
        if ctx.get("live") is None:
            from .account import live_prices
            ctx["live"] = live_prices(set(J.coin[np.flatnonzero(J.t == J.t[i])].tolist())) or {}
        lp = ctx["live"].get(str(J.coin[i]))
        if lp and abs(lp / e - 1) < 0.05: e, via = float(lp), "live price"
    mult = ((R or {}).get("adapt") or {}).get(name, {}).get("riskMult", 1.0)
    risk = RISK["A" if g in ("A", "S") else "B"] * mult
    if cfg.get("size") == "kelly":                            # fractional (quarter) Kelly from the calibrated odds, kept within 0.5x-1.5x of plan
        b = (T - C.FEE) / (stp + C.FEE); f = (prob * b - (1 - prob)) / b
        risk = float(np.clip(f / 4, 0.5 * risk, 1.5 * risk)) if f > 0 else 0.5 * risk
    lev = lev_for(stp); cap = (cfg.get("_budget") or margin_cap(A)) * eq * lev
    if cfg.get("_risk") is not None: risk = cfg["_risk"]
    notional = min(risk * eq / stp, cap); margin = notional / lev
    fams = {f: round(float(v[pos]), 3) for f, v in S[name]["fams"].items()}; base = B["sides"][name]["base"]
    card = {"for": sorted([f for f, p in fams.items() if p >= base + 0.03], key=lambda f: -fams[f]), "against": [f for f, p in fams.items() if p <= base - 0.03],
            "similar": round(fams.get("similar", 0) * 100), "rules": votes_}
    p = {"coin": str(J.coin[i]), "side": "LONG" if sd > 0 else "SHORT", "sd": sd, "t": int(J.t[i]), "entry": e, "grade": g, "prob": round(prob, 3), "be": round(be, 3),
         "stop": e * (1 - sd * stp), "target": e * (1 + sd * T), "tgtPct": T, "maxHold": int(Hz) + 24, "stopPct": round(stp, 4), "lev": lev, "units": [[e, notional]],
         "margin": round(margin, 2), "realized": 0.0, "best": e, "half": False, "added": False, "notional": round(notional, 2), "avgEntry": e, "mark": e, "card": card,
         "fams": fams, "row": int(i), "lastBar": int(J.t[i]), "fill": via, "cfg": {k: v for k, v in cfg.items() if v != CHAMP_DEFAULT.get(k)}, "lock": bool(cfg.get("lock"))}
    A["open"].append(p); A["decisions"].append({"at": now_iso, "t": p["t"], "coin": p["coin"], "side": p["side"], "grade": g, "prob": p["prob"], "card": card,
                                                "target": T, "fill": via})

CHAMP_DEFAULT = {"shape": "fixed", "pace": "daily1", "risk": "plain", "size": "fixed", "odds": "weekly", "shock": False, "doubt": False, "cointrust": False, "rules": False, "alloc": False, "lowbar": False, "lock": False, "curious": False, "spec": None, "trend": "v1"}

def unf_key(side, fams, base, reg):
    """the 'kind' of an unfamiliar situation: side, market state and which two witnesses lean hardest each way"""
    ranked_ = sorted(fams.items(), key=lambda kv: kv[1] - base)
    return f"{side}|{reg}|+{ranked_[-1][0]}|-{ranked_[0][0]}"

def unf_tables(R, L):
    """every unfamiliar candidate is recorded and scored like any other; groups of similar ones that keep ending the same way
    become a category with a track record (proven: tradable; failing: avoided; mixed: it is what it is)"""
    g = {}
    for x in L:
        if x.get("unf") and x.get("fams"): g.setdefault(unf_key(x["side"], x["fams"], x.get("base", 0.5), x.get("reg")), []).append(x)
    out = {}
    for k, xs in g.items():
        n = len(xs); hit = float(np.mean([x["y"] for x in xs])); be = float(np.mean([x["be"] for x in xs])); avg = float(np.mean([x["ret"] for x in xs]))
        out[k] = {"n": n, "hit": round(hit, 3), "be": round(be, 3), "avg": round(avg * 100, 2),
                  "state": "proven" if n >= 15 and hit >= be + 0.05 and avg > 0 else "failing" if n >= 15 and hit < be else "learning"}
    R["unfTrust"] = out

def ctx_votes(R, J, rows, side):
    """logit adjustment from the rules firing on these rows: +0.3 per rule live-trusted in this market state, -0.3 per rule that keeps failing there"""
    T = R.get("ruleTrust") or {}; vt = R.get("_votes") or {}; reg = R.get("regimeNow")
    out = np.zeros(len(rows))
    for k, i in enumerate(rows):
        for no in vt.get(int(i), []):
            if no[0] != ("L" if side == "long" else "S"): continue
            st = (T.get(no[1:]) or {}).get(reg) or {}
            out[k] += 0.3 if st.get("state") == "trusted" else -0.3 if st.get("state") == "warning" else 0.0
    return out

def trust_tables(R):
    """from every scored candidate: each rule's live record per market state, each coin group's calibration, and which witness misled"""
    from . import archive
    from .witnesses import SECTOR_OF
    L = [x for x in archive.read("ledger") + (R.get("ledger") or []) if x.get("y") is not None]
    base = {}
    for x in L: base.setdefault((x["side"], x.get("reg")), []).append(x["y"])
    rt = {}
    for x in L:
        for no in x.get("rules") or []: rt.setdefault(no, {}).setdefault(x.get("reg"), []).append((x["y"], x["side"], x.get("reg")))
    trust = {}
    for no, regs in rt.items():
        for reg, ys in regs.items():
            n = len(ys); hit = float(np.mean([y for y, _, _ in ys])); b = float(np.mean(base.get((ys[0][1], reg), [0.5])))
            state = "trusted" if n >= 20 and hit >= b + 0.08 else "warning" if n >= 20 and hit <= b - 0.08 else "learning"
            trust.setdefault(no, {})[str(reg)] = {"n": n, "hit": round(hit, 3), "base": round(b, 3), "state": state}
    R["ruleTrust"] = trust
    ct = {}
    for g in set(SECTOR_OF.values()) | {"other"}:
        d = [x for x in L if SECTOR_OF.get(x["coin"], "other") == g]
        if len(d) >= 30: ct[g] = round(float(np.clip(np.mean([x["prob"] for x in d]) - np.mean([x["y"] for x in d]), 0, 0.10)), 3)
    R["coinTrust"] = ct
    mis = {}
    for x in L:
        for f, p in (x.get("fams") or {}).items():
            mis.setdefault(f, []).append((p - x.get("base", 0.5)) * (1 if x["y"] == 0 else -1))
    R["attrib"] = {f: round(float(np.mean(v)) * 100, 2) for f, v in mis.items() if len(v) >= 30}
    unf_tables(R, L)     # > 0: this witness pushed the odds the wrong way on average


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
            _close(A, p, p["target"], f"half at +{p.get('tgtPct', TARGET)*100:.0f}%", t + H, part=0.5); p["half"] = True
            p["stop"] = p["entry"] * (1 + sd * C.FEE * 2)                          # rest is now risk-free (break-even)
        p["best"] = max(p["best"], hi) if sd > 0 else min(p["best"], lo); p["lastBar"] = t
        mh = p.get("maxHold", MAX_HOLD)
        if (t + H - p["t"]) / H >= mh: _close(A, p, float(Cc[j]), f"time ({mh}h)", t + H); return
        # evidence-based decisions at the close of this hour
        if B is not None:
            rowmask = np.flatnonzero((J.coin == p["coin"]) & (J.t == t + H))
            if len(rowmask):
                r = int(rowmask[0]); side = "long" if sd > 0 else "short"; sdm = B["sides"][side]
                cont = float(sdm["exit"].predict_proba(_X(J.P, [r], sdm["exitCols"]))[0, 1]); p["cont"] = round(cont, 3)
                px = float(Cc[j]); gain = sd * (px / p["entry"] - 1)
                dv = float(J.dvol[r]) if np.isfinite(J.dvol[r]) else p["stopPct"]
                peak = sd * (p["best"] / p["entry"] - 1)
                if p.get("lock") and not p["half"] and peak >= 0.6 * p.get("tgtPct", TARGET):          # a good start: never let it turn into a loss
                    be_ = p["entry"] * (1 + sd * C.FEE * 2); p["stop"] = max(p["stop"], be_) if sd > 0 else min(p["stop"], be_)
                if p["half"]:
                    if cont < 0.40: _close(A, p, px, "evidence: move fading, profit secured", t + H); return
                    trail = p["best"] * (1 - sd * (1.5 if cont >= 0.6 else 1.0) * dv)
                    if p.get("lock"): trail = p["entry"] * (1 + sd * 0.5 * peak) if sd * (trail / p["entry"] - 1) < 0.5 * peak else trail   # keep at least half of the best open profit
                    p["stop"] = max(p["stop"], trail) if sd > 0 else min(p["stop"], trail)
                elif not p["added"] and gain >= 0.01 and cont >= 0.6:
                    p["units"].append([px, _size(p) * 0.5]); p["added"] = True
                    p["stop"] = p["entry"] if sd > 0 else p["entry"]                    # whole position now stops at the first entry
                    p["margin"] = round(_size(p) / p.get("lev", LEV), 2)
        j += 1

def _mark(A):
    from .account import live_prices
    lp = live_prices({p["coin"] for p in A["open"]}); unreal = 0.0
    for p in A["open"]:
        px = lp.get(p["coin"]) or p.get("mark") or p["entry"]; p["mark"] = px
        p["unreal"] = round(_size(p) * (p["sd"] * (px / _avg(p) - 1)) - _size(p) * C.FEE, 2); unreal += p["unreal"]
        p["size"] = round(_size(p) / _avg(p), 6); p["avgEntry"] = _avg(p); p["notional"] = round(_size(p), 2)
        p["liq"] = _avg(p) * (1 - p["sd"] * (1 / p.get("lev", LEV) - 0.005))       # isolated margin: liquidation ~(1/leverage - 0.5%) against the average entry
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


CRASHES = {"May 2021 crash": ("2021-05-10", "2021-05-25"), "LUNA collapse": ("2022-05-05", "2022-05-20"), "FTX collapse": ("2022-11-05", "2022-11-20"),
           "Aug 2024 carry unwind": ("2024-08-01", "2024-08-12"), "Nov 2026 sell-off": ("2026-11-01", "2026-11-09")}

def crash_rehearsal(J, B, log=print):
    """replay the champion's policy through past shocks: trades, wins, worst single trade and worst drop of the account.
    Shocks before the final-exam months were inside its training years (marked: optimistic)."""
    import datetime as dt
    out = {}
    for name, (a, b) in CRASHES.items():
        t0 = int(dt.datetime.fromisoformat(a).replace(tzinfo=dt.timezone.utc).timestamp()); t1 = int(dt.datetime.fromisoformat(b).replace(tzinfo=dt.timezone.utc).timestamp())
        rows = _rows(J, t0, t1, every=1)
        if not len(rows): continue
        S = score(J, B, rows); stp = stop_pct(J.dvol[rows]); be = breakeven(stp); picks = []
        for nm, sd in B["sides"].items():
            if not (sd.get("live") and not sd.get("off")): continue
            fams = S[nm]["fams"]; sup, ag = (lambda F: ((F >= sd["base"] + 0.03).sum(1), (F <= sd["base"] - 0.03).sum(1)))(np.column_stack([np.asarray(v) for v in fams.values()]))
            ok = (S[nm]["prob"] >= be + sd["margin"]) & (sup >= 2) & (ag <= 1) & ~np.asarray(S[nm]["unfamiliar"])
            y, r = barrier(J, rows[ok], 1 if nm == "long" else -1, TARGET, stp[ok], HORIZON)
            picks += [(int(J.t[i]), float(rr), float(s_)) for i, rr, s_ in zip(rows[ok], r, stp[ok])]
        picks.sort(); seen = set(); eq = 1.0; peak = 1.0; dd = 0.0; worst = 0.0; n = w = 0
        for t, rr, s_ in picks:
            if t // 86400 in seen: continue
            seen.add(t // 86400); ch = max(rr * min(RISK["A"] / s_, MAX_MARGIN * LEV), -MAX_MARGIN)
            eq *= 1 + ch; peak = max(peak, eq); dd = max(dd, 1 - eq / peak); worst = min(worst, ch); n += 1; w += rr > 0
        out[name] = {"trades": n, "won": w, "result": round((eq - 1) * 100, 1), "worstTrade": round(worst * 100, 1), "maxDrop": round(dd * 100, 1),
                     "inTraining": t1 < J.hold0}
    log(f"   crash rehearsal: {out}")
    return out

def maybe_fit_heads(J, mem, log=print, time_left=1e9):
    """the trade-shape models and subtraction combiners for the CURRENT brain (refit whenever the brain is retrained)"""
    from . import challengers as CH
    B = load()
    if B is not None and CH.load_heads(B) is not None:
        R_ = mem.setdefault("referee", {})
        if (R_.get("crash") is None or R_.get("crashFor") != B["trainedAt"]) and time_left > 6 * 60:     # once per brain
            try: R_["crash"] = crash_rehearsal(J, B, log); R_["crashFor"] = B["trainedAt"]
            except Exception as e: log(f"crash rehearsal failed: {e}")
            try:
                from . import compass; R_["trendAcc"] = compass.trend_accuracy(J)
            except Exception as e: log(f"trend accuracy failed: {e}")
        return False
    if B is None: return False
    if time_left < 10 * 60: log("shape models: postponed (not enough time this run)"); return False
    Hd = CH.fit_heads(J, B, log); CH.save_heads(Hd); mem.setdefault("referee", {})["headsNote"] = f"fitted {Hd['fitAt']} UTC in {Hd['secs']}s"
    try:
        from . import guard; mem["referee"]["guardRule"] = guard.learn(J, log)
    except Exception as e: log(f"shock memory failed: {e}")
    try: mem["referee"]["crash"] = crash_rehearsal(J, B, log); mem["referee"]["crashFor"] = B["trainedAt"]
    except Exception as e: log(f"crash rehearsal failed: {e}")
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
                       "reg": (REG or {}).get(int(J.t[i])), "px": float(J.O[i])})
            recent[key] = int(J.t[i])
    from . import archive; archive.trim(sh, 3000, "scorecard")

def _shadow_resolve(J, R, newest):
    sh = R.get("shadow") or []
    for x in sh:
        if "y" in x or x["t"] + HORIZON * H > newest + H: continue
        r = np.flatnonzero((J.coin == x["coin"]) & (J.t == x["t"]))
        if not len(r): x["y"] = None; continue
        y, ret = barrier(J, r[:1], 1 if x["side"] == "long" else -1, TARGET, np.array([x["stop"]]), HORIZON)
        x["y"] = int(y[0]); x["ret"] = round(float(ret[0]), 4)
        try:                                     # the whole path, so any trade shape can be judged afterwards (not only +2%)
            sd_ = 1 if x["side"] == "long" else -1; m = (J.coin == x["coin"]) & (J.t > x["t"]) & (J.t <= x["t"] + HORIZON * H)
            idx = np.flatnonzero(m); idx = idx[np.argsort(J.t[idx])]; e = x["px"]
            if len(idx):
                fav = (J.H[idx] / e - 1) if sd_ > 0 else (1 - J.L[idx] / e); adv = (1 - J.L[idx] / e) if sd_ > 0 else (J.H[idx] / e - 1)
                x["mfe"] = round(float(fav.max()), 4); x["mae"] = round(float(adv.max()), 4)
                x["mfeH"] = int(np.argmax(fav)) + 1; x["maeH"] = int(np.argmax(adv)) + 1
                x["end"] = round(float(sd_ * (J.Cl[idx[-1]] / e - 1)), 4)
        except Exception: pass
    stats = {}
    for name in ("long", "short"):
        d = [x for x in sh if x["side"] == name and x.get("y") is not None]
        if not d: continue
        last = d[-60:]
        stats[name] = {"n": len(d), "recentN": len(last), "hit": round(float(np.mean([x["y"] for x in last])), 3), "pred": round(float(np.mean([x["prob"] for x in last])), 3),
                       "be": round(float(np.mean([x["be"] for x in last])), 3), "avg": round(float(np.mean([x["ret"] for x in last])) * 100, 3),
                       "open": sum(1 for x in sh if x["side"] == name and "y" not in x)}
    wk = [x for x in sh if x["t"] > newest - 7 * 86400]
    R["callRate"] = {g: round(sum(1 for x in wk if x["grade"] == g) / 7, 2) for g in ("A", "B")}          # calls per day, last 7 days (both sides)
    R["callRateBySide"] = {n: {g: round(sum(1 for x in wk if x["grade"] == g and x["side"] == n) / 7, 2) for g in ("A", "B")} for n in ("long", "short")}
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


# ── calibration ledger: EVERY scanned candidate (both sides, all grades), scored 48h later. Recording only. ──────
def _ledger_add(J, B, R, rows, S, REG=None, Hd=None, V=None):
    """every scanned candidate (newest hour, once per coin, side and 6 hours), with every witness's opinion and what EACH challenger
    would have said about it; scored 48h later. This is what challengers are judged on."""
    from . import challengers as CH
    L = R.setdefault("ledger", []); seen = {(x["coin"], x["side"], x["t"] // (6 * H)) for x in L[-3000:]}
    newest = int(J.t[rows].max()); pos_new = np.flatnonzero(J.t[rows] == newest)
    if not len(pos_new): return
    sub = rows[pos_new]; Ssub = {n: {"prob": np.asarray(v["prob"])[pos_new], "fams": {f: np.asarray(x)[pos_new] for f, x in v["fams"].items()},
                                     "unfamiliar": np.asarray(v["unfamiliar"])[pos_new]} for n, v in S.items()}
    cfgs = {"champion": CH.cfg_of(R, "champion")}; cfgs.update({n: CH.cfg_of(R, n) for n, c in (R.get("chal") or {}).items() if c.get("kind") == "odds" or any(k in c.get("change", {}) for k in ("odds", "shape", "doubt", "cointrust", "rules", "lowbar", "curious"))})
    K = {n: _cands(J, B, R, Hd, c, sub, Ssub, REG or {}) for n, c in cfgs.items()}
    for k in range(len(sub)):
        i = int(sub[k]); stp = float(stop_pct(J.dvol[i])); be = breakeven(stp)
        for name, sd in B["sides"].items():
            key = (str(J.coin[i]), name, int(J.t[i]) // (6 * H))
            if key in seen: continue
            fp = {f: float(v[k]) for f, v in Ssub[name]["fams"].items()}; pr = float(Ssub[name]["prob"][k])
            cp = {}
            for n, KK in K.items():
                q = KK[name]; e = {"p": round(float(q["pt"][k]), 4), "g": q["grade"][k]}
                if abs(float(q["T"][k]) - TARGET) > 1e-9 or int(q["Hz"][k]) != HORIZON: e.update(T=float(q["T"][k]), Hz=int(q["Hz"][k]))
                cp[n] = e
            L.append({"t": int(J.t[i]), "coin": key[0], "side": name, "prob": round(pr, 3), "be": round(be, 3), "stop": round(stp, 4), "px": float(J.O[i]),
                      "need": round(be + margin_of(R, name, sd), 3), "nfor": sum(p >= sd["base"] + 0.03 for p in fp.values()),
                      "nag": sum(p <= sd["base"] - 0.03 for p in fp.values()), "reg": (REG or {}).get(int(J.t[i])),
                      "grade": grade(pr, fp, sd["base"], margin_of(R, name, sd), be, Ssub[name]["unfamiliar"][k]),
                      "fams": {f: round(p, 3) for f, p in fp.items()}, "base": round(float(sd["base"]), 3), "unf": bool(Ssub[name]["unfamiliar"][k]), "cp": cp,
                      "rules": [v[1:] for v in (V or {}).get(i, []) if v[0] == ("L" if name == "long" else "S")]})
            seen.add(key)
    from . import archive; archive.trim(L, 4000, "ledger")

def _ledger_resolve(J, R, newest, Hd=None):
    """score candidates whose 48h (or their own shape's horizon) has passed; returns the newly scored ones (for online learning)"""
    L = R.get("ledger") or []; newly = []
    for x in L:
        if "y" in x or x["t"] + HORIZON * H > newest + H: continue
        r = np.flatnonzero((J.coin == x["coin"]) & (J.t == x["t"]))
        if not len(r): x["y"] = None; continue
        sdn = 1 if x["side"] == "long" else -1
        y, ret = barrier(J, r[:1], sdn, TARGET, np.array([x["stop"]]), HORIZON)
        x["y"] = int(y[0]); x["ret"] = round(float(ret[0]), 4); newly.append(x)
        try:                                     # the whole path, so any trade shape can be judged afterwards (not only +2%)
            m = (J.coin == x["coin"]) & (J.t > x["t"]) & (J.t <= x["t"] + HORIZON * H)
            idx = np.flatnonzero(m); idx = idx[np.argsort(J.t[idx])]; e = x["px"]
            if len(idx):
                fav = (J.H[idx] / e - 1) if sdn > 0 else (1 - J.L[idx] / e); adv = (1 - J.L[idx] / e) if sdn > 0 else (J.H[idx] / e - 1)
                x["mfe"] = round(float(fav.max()), 4); x["mae"] = round(float(adv.max()), 4)
                x["mfeH"] = int(np.argmax(fav)) + 1; x["maeH"] = int(np.argmax(adv)) + 1
                x["end"] = round(float(sdn * (J.Cl[idx[-1]] / e - 1)), 4)
        except Exception: pass
    for x in L:                                  # challengers that asked a different question (another target / horizon) get their own answer
        for n, e in (x.get("cp") or {}).items():
            if "T" in e and "y" not in e and x.get("y", 0) is not None and x["t"] + e["Hz"] * H <= newest + H:
                r = np.flatnonzero((J.coin == x["coin"]) & (J.t == x["t"]))
                if len(r):
                    yy, rr = barrier(J, r[:1], 1 if x["side"] == "long" else -1, e["T"], np.array([x["stop"]]), e["Hz"]); e["y"] = int(yy[0]); e["r"] = round(float(rr[0]), 4)
            elif "T" in e and "y" not in e: e["y"] = None if x["t"] + e["Hz"] * H <= newest + H and x.get("y") is None else e.get("y")
            if "T" not in e and "r" not in e and x.get("ret") is not None: e["r"] = x["ret"]
    done = [x for x in L if x.get("y") is not None]; bands = []
    for lo, hi in ((0, .5), (.5, .55), (.55, .6), (.6, .65), (.65, .7), (.7, .75), (.75, 1.01)):
        d = [x for x in done if lo <= x["prob"] < hi]
        if d: bands.append({"band": f"{int(lo*100)}-{min(100, int(hi*100))}%", "n": len(d), "pred": round(float(np.mean([x["prob"] for x in d])), 3),
                            "hit": round(float(np.mean([x["y"] for x in d])), 3), "avg": round(float(np.mean([x["ret"] for x in d])) * 100, 3)})
    R["calibration"] = {"recorded": len(L), "scored": len(done), "bands": bands, "bySide": _bands_by(done, "side"), "byRegime": _bands_by(done, "reg")}
    R["review"] = review(done)
    return newly

def _bands_by(done, key):
    out = {}
    for v in sorted({str(x.get(key)) for x in done}):
        d = [x for x in done if str(x.get(key)) == v]
        if len(d) >= 10: out[v] = {"n": len(d), "pred": round(float(np.mean([x["prob"] for x in d])), 3), "hit": round(float(np.mean([x["y"] for x in d])), 3)}
    return out

def review(done):
    """the skipped-trade review: what kind of error each miss was, and whether caution is costing money"""
    types = {"wrong read": 0, "right direction, stopped first": 0, "no follow-through": 0, "worked": 0}
    for x in done:
        if x["y"] == 1: types["worked"] += 1
        elif x.get("mfe", 0) >= 0.75 * TARGET: types["right direction, stopped first"] += 1
        elif x.get("mae", 0) >= x["stop"] and x.get("mfe", 0) < 0.005: types["wrong read"] += 1
        else: types["no follow-through"] += 1
    near = [x for x in done if x.get("grade") is None and not x.get("unf") and x["need"] - 0.10 <= x["prob"] < x["need"]]       # just below the bar: the skips that matter
    taken = [x for x in done if x.get("grade") == "A"]
    return {"types": types, "nearMiss": {"n": len(near), "hit": round(float(np.mean([x["y"] for x in near])), 3) if near else None,
                                          "avg": round(float(np.mean([x["ret"] for x in near])) * 100, 3) if near else None},
            "gradeA": {"n": len(taken), "hit": round(float(np.mean([x["y"] for x in taken])), 3) if taken else None,
                       "avg": round(float(np.mean([x["ret"] for x in taken])) * 100, 3) if taken else None},
            "cautionCosts": bool(near and len(near) >= 30 and np.mean([x["ret"] for x in near]) > 0.002)}
