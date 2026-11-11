"""CHALLENGERS: every new ability is a clone of the referee that differs by exactly ONE thing.
It is scored on the same live cases as the referee and takes over only when it is proven better; the old champion stays
as a challenger, so a promotion that turns out worse is reversed by the same rule.

Two kinds:
  odds challengers    a different way of turning the evidence into odds (online learning, a witness removed, a new witness).
                      Scored on EVERY calibration-ledger candidate (hundreds a day): honest odds (log-loss), calibration,
                      how many Grade A it finds, and what its picks earned.
  policy challengers  same odds, a different way of trading them (trade shape, pace, risk guard, sizing, shock exits).
                      Each runs its own $500 shadow paper account on the same live hours; the trade shape is also scored on the ledger.

Promotion (all must hold, over at least MIN_BETS independent bets): more accurate (lower log-loss), at least as many Grade A,
calibration not worse, profit not worse. Calls made in the same market move count as one bet (effective number of bets)."""
import os, time, json, math, numpy as np
from . import config as C

H = 3600
MIN_BETS = 40
HEADS_PATH = lambda: f"{C.DATA_DIR}/models/heads.joblib"

CHAMP = {"shape": "fixed", "pace": "daily1", "risk": "plain", "size": "fixed", "odds": "weekly", "shock": False, "doubt": False, "cointrust": False, "rules": False, "alloc": False, "lowbar": False, "lock": False, "curious": False, "spec": None}

def registry(B=None):
    """name -> (the one change, plain-English description, kind)"""
    reg = {
        "shape":       ({"shape": "vol"}, "Trade shape adapts to volatility: the target grows with the stop (max(+2%, k x stop), k learned), so a wild market no longer forces an 80% bar", "policy"),
        "pace":        ({"pace": "fluid"}, "Fluid pace: several trades a day when the evidence is there (learned from its own opportunity rate), Grade S always allowed", "policy"),
        "guard":       ({"risk": "guard"}, "Risk guard: no second bet on the same move (correlation), total open risk capped by tail loss, daily and weekly brakes", "policy"),
        "kelly":       ({"size": "kelly"}, "Edge-proportional sizing: fractional Kelly from the calibrated odds (bigger only when the edge is proven)", "policy"),
        "online-fast": ({"odds": "online:0.97"}, "Learns which evidence is right from every scored candidate, forgets old evidence fast (Kalman-style online learning)", "odds"),
        "online-slow": ({"odds": "online:0.995"}, "Same online learning, forgets slowly (more stable)", "odds"),
    }
    reg["pairs"] = ({"spec": "pairs"}, "Pair trades: long the coin it likes most, short the one it dislikes most, among coins that move together (the market's swings cancel; tight stop even in wild markets)", "policy")
    reg["news"] = ({"spec": "news"}, "News drift: after a sudden market-wide shock, ride the move in its direction on the coin whose evidence agrees most (24h limit)", "policy")
    reg["range"] = ({"spec": "range"}, "Range trades: in wild but sideways markets, short near the top of a coin's 3-day range and buy near the bottom (+1% target, stop just outside)", "policy")
    reg["lock"] = ({"lock": True}, "Profit lock: once a trade is well on its way the stop goes to break-even, and after the first target it always keeps at least half of the best open profit", "policy")
    reg["curious"] = ({"curious": True}, "Curious: unfamiliar situations are grouped by kind; a kind with a proven live record becomes tradable (its own witness)", "policy")
    reg["alloc"] = ({"alloc": True}, "Daily-goal allocator: one budget per opportunity (up to 30% margin, 6% risk), all-in on one strong setup or split across 2-4 coins that are NOT the same bet; judged on account growth", "policy")
    reg["lowbar"] = ({"lowbar": True}, "Less cautious: half the safety margin above break-even (catches over-caution live)", "policy")
    reg["cointrust"] = ({"cointrust": True}, "Coin trust: demands more evidence on groups of coins where its own live odds proved too optimistic", "policy")
    reg["rules"] = ({"rules": True}, "Research rules as fair witnesses: a rule counts only by its LIVE record in the current market state (trusted: odds up; keeps failing: a warning sign, odds down)", "policy")
    reg["doubt"] = ({"doubt": True}, "Split jury: when the witnesses disagree with each other, the bar rises by how much they disagree", "policy")
    reg["shock"] = ({"shock": True}, "Shock exits: the own server watches every minute and exits a trade when a sudden move hits it (threshold learned from past shocks)", "policy")
    if B is not None:
        for f in B["sides"][next(iter(B["sides"]))]["order"]:
            reg[f"without-{f}"] = ({"odds": f"without:{f}"}, f"Subtraction test: the referee without the '{f}' witness (if it is no worse, '{f}' is noise and goes)", "odds")
    if B is not None:
        from . import witnesses
        for w in witnesses.ready(B):
            reg[f"witness-{w}"] = ({"odds": f"witness:{w}"}, witnesses.WITNESSES[w]["what"], "odds")
    return reg

WITNESSES = {}          # filled by witnesses.py (new evidence trained as an extra family)

def cfg_of(R, name):
    base = dict((R.get("champ") or {}).get("cfg") or CHAMP)
    if name == "champion": return base
    if name == "previous": return dict((R.get("champ") or {}).get("prev") or CHAMP)
    ch = (R.get("chal") or {}).get(name, {}).get("change") or (registry().get(name, ({},))[0])
    return {**base, **ch}

def exam_gate(Hd, B):
    """a challenger with a backtest (the trade shape) must first pass its final exam: on the newest months it must do at least as well
    as the champion on every side that trades. Otherwise it waits for the next weekly retrain."""
    out = {}
    if not Hd: return {"shape": "waiting for its models"}
    for n, h in Hd["sides"].items():
        sd = B["sides"][n]
        if not (sd.get("live") and not sd.get("off")): continue
        me, ch = (h.get("exam") or {}).get("monthly"), (sd.get("exam") or {}).get("monthly")
        if me is None or ch is None or me < ch:
            out["shape"] = f"failed its final exam on {n}s: {me}%/month vs the champion's {ch}%/month (retried after the next weekly retrain)"
    return out

def sync(R, B, log=print, Hd=None):
    """keep the list of active challengers in step with the registry and the champion (at most MAX_ACTIVE at a time;
    a challenger identical to the champion has nothing left to prove and retires)"""
    reg = registry(B); chal = R.setdefault("chal", {}); champ = (R.get("champ") or {}).get("cfg") or CHAMP
    rej = exam_gate(Hd, B) if B is not None else {}
    gr = R.get("guardRule") or {}
    if not any((gr.get(n) or {}).get("x") for n in ("long", "short")): rej["shock"] = "shock memory found no exit rule that beats holding" if gr else "waiting for the shock memory"
    R["chalRejected"] = rej
    for n in rej:
        if not ((R.get("champ") or {}).get("cfg") or {}).get(n): reg.pop(n, None)
    if (R.get("champ") or {}).get("prev"): reg["previous"] = ((R["champ"]["prev"]), "The previous champion (a promotion that proves worse is reversed)", "full")
    for n, (change, what, kind) in reg.items():
        full = dict(change) if kind == "full" else {**champ, **change}
        if full == champ: chal.pop(n, None); continue
        if n in chal: chal[n].update(what=what, kind=("policy" if any(k != "odds" for k in change if change.get(k) != champ.get(k)) else "odds"))
        elif sum(1 for c in chal.values() if c.get("kind") == "policy") < 16 or kind == "odds":
            chal[n] = {"change": change if kind != "full" else {k: v for k, v in change.items() if champ.get(k) != v}, "what": what,
                       "kind": "policy" if kind in ("policy", "full") and any(k != "odds" for k in change) else "odds", "born": time.strftime("%Y-%m-%d %H:%M", time.gmtime())}
    for n in [n for n in chal if n not in reg]: chal.pop(n)
    return chal

# ── trade-shape models ("heads"): odds for every rung of the ladder, from the same family opinions ─────────────────────
def _lg(p): p = np.clip(p, 1e-3, 1 - 1e-3); return np.log(p / (1 - p))

def head_X(fp, order, stop):
    return np.column_stack([_lg(np.asarray(fp[f])) for f in order] + [np.log(np.asarray(stop, float))])

KS = (0.5, 0.75, 1.0)          # target = max(+2%, k x stop): the shape grows with the market's volatility

def shape_of(k, stop):
    T = np.maximum(0.02, k * np.asarray(stop, float)); return T, np.where(T <= 0.03, 48, 72)

def fit_heads(J, B, log=print):
    """on the VALIDATION year, for each k: odds of reaching max(+2%, k x stop) before the stop; the best k and safety margin are
    chosen there; then the FINAL EXAM on the newest months (frozen), next to the fixed +2% policy. Also the subtraction combiners."""
    from sklearn.linear_model import LogisticRegression
    from . import referee as RF
    t0 = time.time()
    V = RF._rows(J, J.val0, J.hold0); X = RF._rows(J, J.hold0, J.t_end - 72 * H)
    SV, SX = RF.score(J, B, V), RF.score(J, B, X)
    out = {"bundle": B["trainedAt"], "fitAt": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "sides": {}}
    for name, sd in B["sides"].items():
        side = 1 if name == "long" else -1; order = sd["order"]
        stV, stX = RF.stop_pct(J.dvol[V]), RF.stop_pct(J.dvol[X])
        XV, XX = head_X(SV[name]["fams"], order, stV), head_X(SX[name]["fams"], order, stX)
        supV, agV = _support(SV[name]["fams"], sd["base"]); supX, agX = _support(SX[name]["fams"], sd["base"])
        # subtraction combiners (the referee without one witness), refit on the same validation year
        lgV = {f: _lg(np.asarray(SV[name]["fams"][f])) for f in order}; yV = RF.barrier(J, V, side, RF.TARGET, stV, RF.HORIZON)[0]
        sub = {}
        for f in order:
            o2 = [q for q in order if q != f]
            sub[f] = {"order": o2, "comb": LogisticRegression(C=0.5, max_iter=500).fit(np.column_stack([lgV[q] for q in o2]), yV)}
        Vc = {"coin": np.asarray(J.coin[V]).astype(str), "t": np.asarray(J.t[V], np.int64), "y": yV.astype(np.int8),
              "lg": np.column_stack([lgV[f] for f in order]).astype(np.float32)}                    # cached for training new witnesses
        best = None
        for k in KS:
            T, Hz = shape_of(k, stV); y = np.zeros(len(V), np.int8); r = np.zeros(len(V))
            for hz in np.unique(Hz):
                m = Hz == hz; yy, rr = _barrier_T(RF, J, V[m], side, T[m], stV[m], int(hz)); y[m], r[m] = yy, rr
            mt = LogisticRegression(C=0.5, max_iter=500).fit(XV, y); pv = mt.predict_proba(XV)[:, 1]; be = (stV + C.FEE) / (T + stV)
            for mg in (0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15):
                pick = (pv >= be + mg) & (supV >= 2) & (agV <= 1) & ~SV[name]["unfamiliar"]
                if pick.sum() < 30: continue
                g = RF._policy(J, V[pick], r[pick], pv[pick], stV[pick])["monthly"]
                if g is not None and (best is None or g > best[0]): best = (g, k, mg, mt)
        if best is None: out["sides"][name] = {"k": None, "exam": {"trades": 0, "monthly": None}, "champExam": sd.get("exam"), "sub": sub, "V": Vc}; continue
        g, k, mg, mt = best
        T, Hz = shape_of(k, stX); yx = np.zeros(len(X), np.int8); rx = np.zeros(len(X))
        for hz in np.unique(Hz):
            m = Hz == hz; yy, rr = _barrier_T(RF, J, X[m], side, T[m], stX[m], int(hz)); yx[m], rx[m] = yy, rr
        px = mt.predict_proba(XX)[:, 1]; bex = (stX + C.FEE) / (T + stX)
        pick = (px >= bex + mg) & (supX >= 2) & (agX <= 1) & ~SX[name]["unfamiliar"]
        exam = RF._policy(J, X[pick], rx[pick], px[pick], stX[pick])
        exam["avgTarget"] = round(float(T[pick].mean()) * 100, 2) if pick.any() else None
        out["sides"][name] = {"k": k, "margin": mg, "pt": mt, "valMonthly": round(g, 2), "exam": exam, "champExam": sd.get("exam"), "sub": sub, "V": Vc}
        log(f"   shape model {name}: target = max(2%, {k} x stop), margin {mg}, exam {exam} (fixed +2%: {sd.get('exam', {}).get('monthly')}%/month)")
    out["secs"] = round(time.time() - t0)
    return out

def _barrier_T(RF, J, rows, side, T, stop, hz):
    """first touch with a per-row target"""
    rows = np.asarray(rows); e = J.O[rows]; n = len(rows); y = np.zeros(n, np.int8); ret = np.full(n, np.nan); open_ = np.ones(n, bool); last = J.last[rows]
    tg = e * (1 + side * T); st = e * (1 - side * stop)
    for j in range(hz):
        k = rows + j; ok = open_ & (k <= last)
        if not ok.any(): break
        kk = np.where(ok, k, rows); hi, lo = J.H[kk], J.L[kk]
        s_hit = ok & ((lo <= st) if side > 0 else (hi >= st)); t_hit = ok & ~s_hit & ((hi >= tg) if side > 0 else (lo <= tg))
        ret[s_hit] = -stop[s_hit] - C.FEE; y[t_hit] = 1; ret[t_hit] = T[t_hit] - C.FEE; open_ &= ~(s_hit | t_hit)
        ended = open_ & (k == last)
        if ended.any(): ret[ended] = side * (J.Cl[k[ended]] / e[ended] - 1) - C.FEE; open_ &= ~ended
    k = np.minimum(rows + hz - 1, last); rem = open_; ret[rem] = side * (J.Cl[k[rem]] / e[rem] - 1) - C.FEE
    return y, ret

def _support(fams, base):
    F = np.column_stack([np.asarray(v) for v in fams.values()])
    return (F >= base + 0.03).sum(1), (F <= base - 0.03).sum(1)

def save_heads(h):
    import joblib
    os.makedirs(os.path.dirname(HEADS_PATH()), exist_ok=True); joblib.dump(h, HEADS_PATH() + ".tmp", compress=3); os.replace(HEADS_PATH() + ".tmp", HEADS_PATH())

_HC = {}
def load_heads(B):
    import joblib
    try:
        p = HEADS_PATH(); m = os.path.getmtime(p)
        if _HC.get("m") != m: _HC.update(m=m, h=joblib.load(p))
        h = _HC["h"]; return h if h.get("bundle") == B["trainedAt"] else None
    except Exception: return None

def best_shape(Hd, name, fp, order, stop):
    """for every candidate row: the volatility-scaled target, its horizon, the odds of reaching it first and the expected value"""
    h = Hd["sides"][name]
    if not h.get("k"): return None
    stop = np.asarray(stop, float); T, Hz = shape_of(h["k"], stop); pt = h["pt"].predict_proba(head_X(fp, order, stop))[:, 1]
    ev = pt * T - (1 - pt) * stop - C.FEE
    return [(float(T[i]), int(Hz[i]), float(pt[i]), float(ev[i])) for i in range(len(stop))]

# ── online learning: Kalman-style logistic update of the combiner, per side and market regime ───────────────────────────
def online_prob(R, B, name, lam, reg, fp):
    st = _ostate(R, B, name, lam, reg); x = np.r_[[float(_lg(fp[f])) for f in B["sides"][name]["order"]], 1.0]
    return float(1 / (1 + np.exp(-x @ np.array(st["w"]))))

def _ostate(R, B, name, lam, reg):
    O = R.setdefault("online", {}); key = f"{name}|{lam}|{reg}"; sd = B["sides"][name]
    if O.get("_bundle") != B["trainedAt"]: O.clear(); O["_bundle"] = B["trainedAt"]          # a retrained brain restarts from its new combiner
    if key not in O:
        w0 = list(map(float, sd["comb"].coef_[0])) + [float(sd["comb"].intercept_[0])]
        O[key] = {"w": w0, "P": (np.eye(len(w0)) * 0.02).tolist(), "n": 0, "lastDay": None}
    return O[key]

def online_update(R, B, rows):
    """rows: newly scored ledger candidates. Each one nudges the weights (more if the weight is uncertain), with the forgetting
    factor applied once per day of data; calls from the same market move share one bet's worth of weight."""
    if B is None or not rows: return
    rho = (R.get("chalStats") or {}).get("icc", 0.3)
    clusters = {}
    for x in rows: clusters.setdefault((x["side"], x["t"] // (6 * H)), []).append(x)
    for lam in ("0.97", "0.995"):
        for (side, _), xs in sorted(clusters.items(), key=lambda kv: kv[0][1]):
            if side not in B["sides"]: continue
            wgt = 1.0 / (1 + (len(xs) - 1) * rho)
            for x in xs:
                if not x.get("fams") or x.get("y") is None: continue
                for reg in (x.get("reg") or "?", "all"):
                    st = _ostate(R, B, side, lam, reg); day = x["t"] // 86400
                    P = np.array(st["P"]); w = np.array(st["w"])
                    if st["lastDay"] is not None and day > st["lastDay"]: P = P / (float(lam) ** (day - st["lastDay"]))
                    st["lastDay"] = max(st["lastDay"] or day, day)
                    xx = np.r_[[float(_lg(x["fams"].get(f, 0.5))) for f in B["sides"][side]["order"]], 1.0]
                    p = 1 / (1 + np.exp(-xx @ w)); v = p * (1 - p) * wgt
                    Px = P @ xx; P = P - np.outer(Px, Px) * v / (1 + v * xx @ Px)
                    w = w + P @ xx * (x["y"] - p) * wgt
                    P = np.clip(P, -1, 1)                                              # never explodes
                    st.update(w=w.tolist(), P=P.tolist(), n=st["n"] + 1)

# ── odds for any configuration ─────────────────────────────────────────────────────────────────────────────────────────
def odds(R, B, Hd, cfg, name, fp, reg, base_prob):
    """the probability this configuration assigns (for the +2% question), given the family opinions of one candidate"""
    o = cfg.get("odds", "weekly")
    try:
        if o.startswith("online:"):
            st = (R.get("online") or {}).get(f"{name}|{o[7:]}|{reg}") or (R.get("online") or {}).get(f"{name}|{o[7:]}|all")
            return online_prob(R, B, name, o[7:], reg if st and st.get("n", 0) >= 30 else "all", fp)
        if o.startswith("without:") and Hd:
            s = Hd["sides"][name]["sub"][o[8:]]; return float(s["comb"].predict_proba(np.array([[float(_lg(fp[g])) for g in s["order"]]]))[0, 1])
    except Exception: pass
    return base_prob

# ── scoring and promotion ──────────────────────────────────────────────────────────────────────────────────────────────
def icc(rows):
    """how strongly outcomes of calls made at the same moment move together (0 = independent, 1 = one bet)"""
    g = {}
    for x in rows: g.setdefault((x["side"], x["t"] // (6 * H)), []).append(x["y"])
    gs = [v for v in g.values() if len(v) >= 2]
    if len(gs) < 5: return 0.3
    k = np.mean([len(v) for v in gs]); allv = np.concatenate(gs); m = allv.mean()
    msb = sum(len(v) * (np.mean(v) - m) ** 2 for v in gs) / (len(gs) - 1); msw = sum(((np.array(v) - np.mean(v)) ** 2).sum() for v in gs) / max(1, len(allv) - len(gs))
    return float(np.clip((msb - msw) / (msb + (k - 1) * msw + 1e-9), 0.0, 1.0))

def evaluate(R, log=print):
    """compare every challenger with the champion on the same resolved candidates; promote / roll back by the rule"""
    from . import archive
    L = [x for x in archive.read("ledger") + (R.get("ledger") or []) if x.get("y") is not None and x.get("cp")]
    if not L: R["chalStats"] = {"icc": 0.3, "rows": 0}; return
    rho = icc(L); R.setdefault("chalStats", {}).update(icc=round(rho, 3), rows=len(L))
    cl = {}
    for x in L: cl.setdefault((x["side"], x["t"] // (6 * H)), []).append(x)
    wt = {id(x): 1.0 / (1 + (len(v) - 1) * rho) for v in cl.values() for x in v}
    names = sorted({k for x in L for k in x["cp"]})
    out = {}
    for n in names:
        rows = [x for x in L if n in x["cp"] and "champion" in x["cp"] and x["cp"][n].get("y", x["y"]) is not None]
        if not rows: continue
        w = np.array([wt[id(x)] for x in rows])
        days = max(1.0, (max(x["t"] for x in rows) - min(x["t"] for x in rows)) / 86400)
        def stats(key):
            y = np.array([x["cp"][key].get("y", x["y"]) for x in rows], float)          # a shape challenger answers its own question
            p = np.clip(np.array([x["cp"][key]["p"] for x in rows]), 1e-3, 1 - 1e-3)
            ll = float(-(w * (y * np.log(p) + (1 - y) * np.log(1 - p))).sum() / w.sum())
            cal = float(abs((w * (p - y)).sum() / w.sum()))
            A = np.array([x["cp"][key].get("g") == "A" for x in rows]); r = np.array([x["cp"][key].get("r", x.get("ret", 0.0)) for x in rows], float)
            aret = float((w[A] * r[A]).sum() / max(1e-9, w[A].sum())) if A.any() else 0.0
            return {"ll": round(ll, 4), "cal": round(cal, 4), "aPerDay": round(float(A.sum()) / days, 2), "aRet": round(aret * 100, 3), "aN": int(A.sum()),
                    "profitPerDay": round(aret * float(A.sum()) / days * 100, 3)}
        s, c = stats(n), stats("champion"); bets = float(w.sum())
        if (R.get("chal") or {}).get(n, {}).get("kind") == "policy":      # different question: judged on profit, with honest odds for its own question
            better = s["profitPerDay"] > c["profitPerDay"] and s["aPerDay"] >= c["aPerDay"] and s["cal"] <= max(c["cal"], 0.03) + 0.02
        else:
            better = s["ll"] < c["ll"] and s["aPerDay"] >= c["aPerDay"] and s["cal"] <= c["cal"] + 0.01 and s["profitPerDay"] >= c["profitPerDay"]
        out[n] = {"bets": round(bets, 1), "me": s, "champion": c, "ready": bets >= MIN_BETS, "better": bool(better)}
    # policy challengers that only differ in HOW they trade (pace, guard, sizing, shock exits): judged by their shadow accounts
    ch = R.get("acct") or {}
    for n, c in (R.get("chal") or {}).items():
        a = c.get("acct")
        if c.get("kind") != "policy" or not a or n in out: continue
        g_me = a.get("equity", a["cash"]) / a["start"]; g_ch = ch.get("equity", ch.get("cash", 1)) / max(1e-9, a.get("champAtBirth") or ch.get("start", 1))
        dd_me, dd_ch = _maxdd(a.get("curve")), _maxdd([x for x in ch.get("curve", []) if x[0] >= a.get("bornT", 0)])
        n_tr = len(a.get("closed", []))
        out[n] = {"bets": n_tr, "me": {"growth": round((g_me - 1) * 100, 2), "maxDD": dd_me, "trades": n_tr},
                  "champion": {"growth": round((g_ch - 1) * 100, 2), "maxDD": dd_ch}, "ready": n_tr >= 15,
                  "better": bool(g_me > g_ch and dd_me <= dd_ch + 2.0)}
    R["chalScores"] = out
    return out

def _maxdd(curve):
    v = [c[1] for c in (curve or [])]
    if len(v) < 2: return 0.0
    v = np.array(v, float); return round(float(((np.maximum.accumulate(v) - v) / np.maximum.accumulate(v)).max() * 100), 2)

def promote(R, log=print):
    """the rule, applied every hour: a challenger with enough independent bets that beats the champion on every count takes over.
    The old champion stays on as the 'previous' challenger, so a promotion that turns out worse is reversed by the same rule."""
    sc = R.get("chalScores") or {}
    for n, s in sorted(sc.items(), key=lambda kv: -kv[1]["bets"]):
        if n == "champion" or not (s["ready"] and s["better"]): continue
        champ = R.setdefault("champ", {"cfg": dict(CHAMP)}); prev = dict(champ["cfg"]); new = cfg_of(R, n)
        if new == prev: continue
        champ.update(cfg=new, prev=prev, since=time.strftime("%Y-%m-%d %H:%M", time.gmtime()))
        what = (R.get("chal") or {}).get(n, {}).get("what", n)
        champ.setdefault("history", []).append({"at": champ["since"], "promoted": n, "what": what, "evidence": s})
        log(f"challengers: PROMOTED {n} ({what})")
        for k in list((R.get("chal") or {})): R["chal"][k].pop("acct", None)          # every shadow account restarts against the new champion
        return n
    return None
