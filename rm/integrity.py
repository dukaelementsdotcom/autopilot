"""INTEGRITY: the parts that keep the evidence honest and make the learning visible.
  - the FROZEN JUDGE: the promotion test and its numbers live here and in challengers.evaluate; their fingerprint is shown on the
    dashboard. A learner cannot edit them; changing them is a human decision and changes the fingerprint.
  - sequential promotion test (a pre-set boundary to promote, a pre-set boundary to give up), counted in separate market moves
  - recalibration of the odds from the live ledger (enters as the 'recal' challenger, never directly)
  - multiple-testing haircut for research rules
  - per-account expectancy, drawdown and result per market state
  - the mechanical kill switch (human reset)
  - one audit log line per decision kind, carried into the health report"""
import time, json, math, hashlib, inspect, numpy as np
from . import config as C

H = 3600; DAY = 86400

# ── the promotion test (frozen) ─────────────────────────────────────────────────────────────────────────────────────────
PROMO = {"z": 3.0, "minBets": 40, "minDays": 10,                 # promote: lead of 3.0 standard errors for ONE contestant (raised for the number racing, see z_needed), any hourly look from 40 separate 6-hour windows over 10+ days
         "futility": [[80, -1.5], [240, 0.5]],                   # give up: clearly behind at 80 windows, or still no lead at 240
         "alpha": "about 0.02 one-sided over the whole race, for ALL contestants together (the line rises with the number racing: Bonferroni / first step of Holm)",
         "beta": "about 0.2: a contestant truly better by a quarter of a standard deviation per window is promoted by 240 windows about 4 times in 5"}

def z_needed(m):
    """FAMILY-WISE promotion line (Bonferroni = the first step of Holm): with m contestants racing at once, each is held to the
    one-sided tail of the single-contestant line divided by m, so the chance that ANY of them is promoted by luck stays where one
    contestant's chance was. z_m = Phi^-1(1 - (1 - Phi(z_1)) / m)."""
    from statistics import NormalDist
    N = NormalDist(); m = max(1, int(m))
    return round(N.inv_cdf(1 - (1 - N.cdf(PROMO["z"])) / m), 2)

def seq_decision(n_eff, z, days, m=1):
    """-> (decision, boundary state). decision: 'promote' | 'futility' | None. m = contestants racing at once"""
    zn = z_needed(m)
    if n_eff >= PROMO["minBets"] and days >= PROMO["minDays"] and z >= zn: return "promote", f"promotion boundary crossed ({zn} with {m} racing)"
    for nn, zz in PROMO["futility"]:
        if n_eff >= nn and z <= zz: return "futility", "futility boundary crossed"
    if n_eff >= 20 and z >= zn - 1.0: return None, "near promotion"
    if n_eff >= 40 and z <= -1.0: return None, "near futility"
    return None, "collecting"

def judge_hash():
    from . import challengers as CH
    src = inspect.getsource(seq_decision) + inspect.getsource(z_needed) + json.dumps(PROMO, sort_keys=True) + inspect.getsource(CH.evaluate) + inspect.getsource(CH.icc) + inspect.getsource(CH.promote)
    return hashlib.sha1(src.encode()).hexdigest()[:10]

# ── audit log lines (kept in memory, shown in the health report) ────────────────────────────────────────────────────────
def audit(R, kind, text, log=None):
    A = R.setdefault("auditLog", {}); A[kind] = (A.get(kind) or [])[-7:] + [f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime())} {kind}: {text}"]
    if log: log(f"   {kind}: {text}")

# ── recalibration from the live ledger ──────────────────────────────────────────────────────────────────────────────────
def _scored(R):
    from . import archive
    return [x for x in archive.read("ledger") + (R.get("ledger") or []) if x.get("y") is not None]

def _weights(rows):
    from .challengers import icc
    rho = icc(rows) if rows else 0.3; cl = {}
    for x in rows: cl.setdefault((x["side"], x["t"] // (6 * H)), []).append(x)
    return {id(x): 1.0 / (1 + (len(v) - 1) * rho) for v in cl.values() for x in v}, len(cl)

def fit_recal(R, log=print):
    """isotonic map raw odds -> what really happened, per side, shrunk toward 'no change' while evidence is thin. Version-stamped."""
    from sklearn.isotonic import IsotonicRegression
    L = _scored(R); out = {}
    for side in ("long", "short"):
        d = [x for x in L if x["side"] == side]
        if not d: continue
        wt, _ = _weights(d); w = np.array([wt[id(x)] for x in d]); p = np.array([x["prob"] for x in d], float); y = np.array([x["y"] for x in d], float)
        moves = len({x["t"] // (6 * H) for x in d}); table = []
        for lo in np.arange(0.30, 0.90, 0.05):
            m = (p >= lo) & (p < lo + 0.05)
            if w[m].sum() >= 1: table.append({"bucket": f"{int(round(lo*100))}-{int(round(lo*100))+5}%", "predicted": round(float((w[m] * p[m]).sum() / w[m].sum()), 3),
                                              "actual": round(float((w[m] * y[m]).sum() / w[m].sum()), 3), "n_eff": round(float(w[m].sum()), 1), "n": int(m.sum())})
        e = {"moves": moves, "nEff": round(float(w.sum()), 1), "table": table, "version": f"{time.strftime('%Y%m%d', time.gmtime())}-{side[0]}{moves}"}
        if moves >= 30:
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p, y, sample_weight=w)
            xs = np.linspace(0.30, 0.90, 25); s = moves / (moves + 150.0)
            e.update(xs=[round(float(v), 3) for v in xs], ys=[round(float(s * a + (1 - s) * b), 4) for a, b in zip(iso.predict(xs), xs)], trust=round(s, 2))
        else: e["waiting"] = f"needs 30 separate market moves ({moves} so far): until then the calibrated odds are the raw odds"
        out[side] = e
        for b in table: audit(R, "calibration", f"version {e['version']}, bucket {b['bucket']}, predicted {b['predicted']}, actual {b['actual']}, n_eff {b['n_eff']}")
    R["recal"] = out; return out

def recal_prob(R, side, p):
    e = (R.get("recal") or {}).get(side) or {}
    return float(np.interp(p, e["xs"], e["ys"])) if e.get("xs") else float(p)

# ── multiple-testing haircut for research rules ─────────────────────────────────────────────────────────────────────────
def _pz(z): return 0.5 * math.erfc(z / math.sqrt(2))

def haircut(mem):
    """a rule found after N tries must clear a higher bar: its chance-of-luck is multiplied by the number of ideas tried.
    Raw statistics are left untouched; the haircut is shown beside them."""
    T = mem.get("tests") or {}; N = max(1, int(mem.get("total_tests") or len(T))); champs = [t for t in T.values() if t.get("status") == "champion"]
    out = {}
    for t in champs:
        zd = float((t.get("disc") or {}).get("t") or 0); p_d = min(1.0, _pz(zd) * N)                    # discovery: chosen among everything ever tried
        fresh = [(s.get("t") or 0, s.get("n") or 0) for s in (t.get("val"), t.get("hold"), t.get("live")) if s and s.get("n")]
        nn = sum(n for _, n in fresh); zo = sum(z * math.sqrt(n) for z, n in fresh) / math.sqrt(nn) if nn else 0.0   # unseen data, pooled
        p_o = min(1.0, _pz(zo) * max(1, len(champs)))                                                      # unseen data: chosen among the champions
        out[str(t.get("no"))] = {"rawT": round(zd, 2), "ideasTried": N, "pAfterHaircut": round(p_d, 4), "unseenT": round(zo, 2), "unseenN": nn,
                                 "pUnseenAfterHaircut": round(p_o, 4), "admissible": bool(p_o < 0.05)}
        t["haircut"] = out[str(t.get("no"))]
    mem["haircut"] = {"ideasTried": N, "champions": len(champs), "admissible": sum(1 for v in out.values() if v["admissible"]), "rules": out}
    return mem["haircut"]

# ── per-account statistics ──────────────────────────────────────────────────────────────────────────────────────────────
GROUP = lambda r: "unknown" if not r or r == "None" else ("wild" if "wild" in str(r) else "falling" if str(r).startswith("down") else "rising" if str(r).startswith("up") else "calm / sideways")

def acct_stats(A, reg_of=None):
    cl = A.get("closed") or []; start = A.get("start", 500.0); usd = [float(x.get("usd") or 0) for x in cl]
    eqs = [start] + [c[1] for c in (A.get("curve") or [])] + [A.get("equity", A.get("cash", start))]
    v = np.array(eqs, float); pk = np.maximum.accumulate(v); mdd = float(((pk - v) / pk).max() * 100) if len(v) > 1 else 0.0
    by = {}
    for x in cl:
        g = GROUP(x.get("regime") or (reg_of(x.get("t")) if reg_of else None)); b = by.setdefault(g, {"trades": 0, "usd": 0.0, "won": 0})
        b["trades"] += 1; b["usd"] += float(x.get("usd") or 0); b["won"] += (x.get("usd") or 0) > 0
    for b in by.values(): b["returnPct"] = round(b["usd"] / start * 100, 2); b["usd"] = round(b["usd"], 2); b["perTrade"] = round(b["usd"] / max(1, b["trades"]), 2)
    mg = [float(x.get("margin") or 0) for x in cl]
    return {"trades": len(cl), "expectancyUsd": round(float(np.mean(usd)), 2) if usd else None,
            "expectancyPct": round(float(np.mean([u / max(1e-9, start) * 100 for u in usd])), 3) if usd else None,
            "onMarginPct": round(float(np.mean([u / m * 100 for u, m in zip(usd, mg) if m > 0])), 1) if any(m > 0 for m in mg) else None,
            "won": int(sum(u > 0 for u in usd)), "avgWin": round(float(np.mean([u for u in usd if u > 0])), 2) if any(u > 0 for u in usd) else None,
            "avgLoss": round(float(np.mean([u for u in usd if u <= 0])), 2) if any(u <= 0 for u in usd) else None,
            "maxDrawdownPct": round(mdd, 1), "peak": round(float(pk[-1]), 2), "byRegime": by, "returnPct": round((eqs[-1] / start - 1) * 100, 2)}

# ── the mechanical kill switch (Arbiter account) ────────────────────────────────────────────────────────────────────────
def kill_check(R, A, newest, now_iso, close_fn, moves_fn, log=print):
    """a circuit breaker for when something is broken: closes everything, stops, waits for a human reset (config.KILL_RESET)"""
    k = A.get("killed")
    if k:
        if str(getattr(C, "KILL_RESET", "")) > str(k.get("at", "")):
            audit(R, "kill_switch", f"reset by human ({C.KILL_RESET}); was: {k.get('reason')}", log); A.pop("killed"); A["paused"] = None; A["peakReset"] = A.get("equity", A["cash"])
        return k
    eq = A.get("equity", A["cash"]); peak = max([c[1] for c in (A.get("curve") or []) if c[0] >= A.get("peakResetT", 0)] + [A.get("peakReset", A["start"]), eq])
    dd = 1 - eq / peak; reason = None; vals = {"equity": eq, "peak": round(peak, 2), "drawdown": round(dd, 3), "limit": C.KILL_DD}
    if dd > C.KILL_DD: reason = f"account fell {dd:.0%} from its peak ${peak:.0f} (limit {C.KILL_DD:.0%})"
    drift = None
    try:
        sh = [x for x in (R.get("shadow") or []) if x.get("y") is not None and x.get("grade")]
        d = moves_fn(sh[-300:])
        if len(d) >= 30:
            hit = float(np.mean([x["y"] for x in d])); pred = float(np.mean([x["prob"] for x in d])); se = math.sqrt(max(1e-9, pred * (1 - pred) / len(d)))
            drift = {"predicted": round(pred, 3), "actual": round(hit, 3), "moves": len(d), "gap": round(pred - hit, 3), "needs": round(max(C.CAL_DRIFT_TOL, 2 * se), 3)}
            if pred - hit > max(C.CAL_DRIFT_TOL, 2 * se) and not reason:
                reason = f"graded calls won {hit:.0%} against {pred:.0%} predicted over {len(d)} separate market moves (worse than chance allows)"; vals.update(drift)
    except Exception: pass
    R["killState"] = {"drawdown": round(dd, 3), "limit": C.KILL_DD, "peak": round(peak, 2), "tripsAt": round(peak * (1 - C.KILL_DD), 2), "drift": drift, "driftTol": C.CAL_DRIFT_TOL}
    if reason:
        for p in list(A["open"]): close_fn(A, p, p.get("mark") or p["entry"], "kill switch", newest)
        A["killed"] = {"at": now_iso, "reason": reason, "values": vals}; A["paused"] = f"{now_iso}: KILL SWITCH: {reason}. All trades closed; waits for a human reset."
        audit(R, "kill_switch", f"{reason}; values {json.dumps(vals)}", log)
    return A.get("killed")

# ── everything the dashboard needs to show "is it learning, and is the learning real?" ──────────────────────────────────
def observe(mem, J=None, log=print):
    R = mem.get("referee") or {}; A = R.get("acct")
    if not A: return None
    from . import referee as RF
    racing = len([n for n in (R.get("chal") or {}) if n not in (R.get("chalRejected") or {})])
    o = {"judge": {"hash": judge_hash(), "rule": {**PROMO, "racing": racing, "zNow": z_needed(racing)}, "note": "The judge (promotion test, its numbers, how separate bets are counted) is fixed code. The learner cannot edit it; a change is a human decision and changes this fingerprint."}}
    o["arbiter"] = acct_stats(A)
    try:
        B = mem.get("account")
        if B:
            regs = RF.regimes(J, np.unique(np.array([x["t"] for x in B.get("closed", [])], dtype=np.int64))) if J is not None and B.get("closed") else {}
            o["bestRule"] = acct_stats(B, lambda t: regs.get(int(t)) if t is not None else None)
    except Exception as e: o["bestRuleError"] = str(e)[:100]
    # learning
    L = _scored(R); since = (R.get("champ") or {}).get("since"); t_since = __import__("calendar").timegm(time.strptime(since, "%Y-%m-%d %H:%M")) if since else 0
    Ls = [x for x in L if x["t"] >= t_since]; sc = R.get("chalScores") or {}
    top = sorted([(n, s) for n, s in sc.items() if s.get("lead") is not None], key=lambda kv: -kv[1]["lead"])[:3]
    days = {}
    for x in [x for x in L if x.get("grade")]: days.setdefault(time.strftime("%m-%d", time.gmtime(x["t"])), {}).setdefault(x["t"] // (6 * H), []).append((x["y"], x["be"], recal_prob(R, x["side"], x["prob"])))
    edge = [{"day": d, "calibratedEdge": round(float(np.mean([np.mean([c - b for _, b, c in v]) for v in mv.values()])), 3),
             "realisedEdge": round(float(np.mean([np.mean([y - b for y, b, _ in v]) for v in mv.values()])), 3), "moves": len(mv)} for d, mv in sorted(days.items())][-30:]
    o["learning"] = {"nEffSincePromotion": len({(x["side"], x["t"] // (6 * H)) for x in Ls}), "scoredCandidates": len(Ls), "lastPromotion": since or "none yet",
                     "calibration": {s: {k: v for k, v in e.items() if k in ("version", "moves", "table", "trust", "waiting")} for s, e in (R.get("recal") or {}).items()},
                     "top": [{"name": n, "nEff": s.get("bets"), "z": s.get("lead"), "state": s.get("state"), "decision": s.get("decision")} for n, s in top], "edge": edge}
    # risk
    eq = A.get("equity", A["cash"]); pos = [{"coin": p["coin"], "side": p["side"], "riskPct": round(p.get("notional", 0) * p.get("stopPct", 0) / max(1e-9, eq) * 100, 2),
                                              "lev": p.get("lev"), "tier": p.get("tier")} for p in A.get("open", [])]
    ks = R.get("killState") or {}
    o["risk"] = {"openRiskPct": round(sum(p["riskPct"] for p in pos), 2), "positions": pos, "perTradeMaxPct": C.RISK_TRADE_MAX * 100, "kill": ks, "killed": A.get("killed"),
                 "wouldTrip": (f"The account falling to ${ks.get('tripsAt')} (it is ${eq:.0f}; {max(0.0, (C.KILL_DD - ks.get('drawdown', 0)) * 100):.0f} points of room)"
                               + (f", or graded calls winning more than {round(ks['drift']['needs'] * 100)} points below predicted (now {round(ks['drift']['gap'] * 100):+d})" if ks.get("drift") else ", or graded calls winning far below predicted once 30 separate moves are scored")) if ks else None}
    # regime strip
    Jl = R.get("journal") or []; runs = []
    for r in Jl:
        if runs and runs[-1]["regime"] == r.get("regime"): runs[-1].update(to=r["day"], eq1=r.get("equity"))
        else: runs.append({"regime": r.get("regime"), "from": r["day"], "to": r["day"], "eq0": (runs[-1]["eq1"] if runs else A.get("start")), "eq1": r.get("equity")})
    sides = {}
    for name, sd in ((R.get("model") or {}).items()):
        if name not in ("long", "short") or not isinstance(sd, dict): continue
        reg = R.get("regimeNow"); rv = ((R.get("regimeOverride") or {}).get(name) or {}).get(reg); ov = (R.get("override") or {}).get(name)
        allowed = RF.side_live(R, name, sd, reg)
        why = (f"its live record in this market state ({rv['n']} separate moves: won {rv['hit']:.0%}, needs {rv['be']:.0%})" if rv is not None else
               f"its overall live record" if ov is not None else f"its final exam ({(sd.get('exam') or {}).get('monthly')}% a month on the newest unseen months)")
        sides[name] = {"allowed": bool(allowed), "why": why}
        audit(R, "side_gate", f"{name}, allowed {bool(allowed)}, reason: {why}, condition_met {bool(allowed)}")
    o["regime"] = {"now": R.get("regimeNow"), "group": GROUP(R.get("regimeNow")), "trend": R.get("trend2Now"), "sides": sides,
                   "last": [{"regime": r["regime"], "from": r["from"], "to": r["to"], "returnPct": round((r["eq1"] / r["eq0"] - 1) * 100, 2) if r.get("eq0") and r.get("eq1") else None} for r in runs[-3:]]}
    o["haircut"] = {k: v for k, v in (mem.get("haircut") or {}).items() if k != "rules"}
    o["audit"] = R.get("auditLog")
    R["obs"] = o; return o
