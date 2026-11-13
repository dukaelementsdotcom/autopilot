"""THE NIGHTLY EVOLUTION LOOP (python -m rm.evo.run). Time-budgeted; the archive, the trial counter and the staged voters persist on the
'evo' branch. Every candidate is scored ONLY on the discovery years; the validation year and the newest months are looked at once per
elite, at staging time, after the deflated bar."""
import os, sys, json, time, random, math, numpy as np, pandas as pd
from .. import config as C, features, referee as RF
from . import dsl, archive as AR, generator as GEN, guard as GD, stage as ST

H = 3600; DAY = 86400; COOL = 24 * H; MIN_TRADES = 40; MIN_WIN = 30
CTX_FEATS = ["p_vol24", "p_r24", "p_btc_r24", "p_oi24", "p_glob", "p_fg", "p_basis", "hour"]

class Lab:
    """the judge for evolved strategies: the Arbiter's own question (+2% before the volatility stop within 48h), after fees"""
    def __init__(self, P, log=print):
        self.P = P; self.ctx = dsl.Ctx(P, cache=64); t = P.t.values; self.t = t; n = len(P)
        lab = P["L_r24"].notna().values; self.t_end = int(t[lab].max())
        self.hold0 = self.t_end - C.HOLDOUT_DAYS * DAY; self.val0 = self.hold0 - C.VALID_DAYS * DAY
        c = P.coin.values; ends = np.r_[np.flatnonzero(c[1:] != c[:-1]), n - 1]; self.last = np.repeat(ends, np.diff(np.r_[-1, ends]))
        O, Hh, Ll, Cl = (P[k].values.astype(float) for k in ("px", "_hi", "_lo", "_cl"))
        dvol = P["vol24"].values.astype(float) * np.sqrt(24); stp = RF.stop_pct(dvol); ok = np.isfinite(dvol) & (np.arange(n) + RF.HORIZON <= self.last) & (t <= self.t_end)
        self.ok = ok; self.R = {}; self.HRS = {}; self.Y = {}; self.dvol = dvol
        for side in (1, -1):
            r = np.full(n, np.nan); hrs = np.full(n, np.nan); yy = np.zeros(n, np.int8); open_ = ok.copy(); tg = O * (1 + side * RF.TARGET); st = O * (1 - side * stp)
            for j in range(RF.HORIZON):
                k = np.minimum(np.arange(n) + j, n - 1); hi, lo = Hh[k], Ll[k]
                sh = open_ & ((lo <= st) if side > 0 else (hi >= st)); th = open_ & ((hi >= tg) if side > 0 else (lo <= tg)) & ~sh
                r[sh] = -stp[sh]; r[th] = RF.TARGET; yy[th] = 1; hrs[sh | th] = j + 1; open_ &= ~(sh | th)
            k = np.minimum(np.arange(n) + RF.HORIZON - 1, n - 1); r[open_] = side * (Cl[k][open_] / O[open_] - 1); hrs[open_] = RF.HORIZON
            self.R[side] = r - C.FEE; self.HRS[side] = hrs; self.Y[side] = yy                       # C.FEE is the round trip
        b = P.loc[c == "BTC", ["t", "px", "_cl"]]; bo = pd.Series(b.px.values, index=b.t.values); bc = pd.Series(b._cl.values, index=b.t.values)
        self.btc48 = (bc.reindex(t + (RF.HORIZON - 1) * H).values / bo.reindex(t).values - 1)
        self.split = {"disc": t < self.val0, "val": (t >= self.val0) & (t < self.hold0), "exam": t >= self.hold0}
        self.months = {k: max(1.0, (min(self.t_end, int(t[m].max())) - int(t[m].min())) / (30.4 * DAY)) for k, m in self.split.items()}
        rng = np.random.default_rng(7); cand = np.flatnonzero(self.split["disc"] & ok)
        self.sample = np.sort(rng.choice(cand, min(200000, len(cand)), replace=False))
        fam = sorted({x for cols in RF.fam_cols(P).values() for x in cols})
        self.X = AR.voter_matrix(P, self.sample, fam); self.cols = sorted(self.ctx.cols)
        log(f"evo lab: {n:,} rows, discovery to {pd.to_datetime(self.val0, unit='s').date()}, {len(fam)} Arbiter inputs for the orthogonality filter")

    def fire(self, g):
        s = self.ctx.eval(g["sig"]); z = self.ctx.eval(["zs", 720, g["sig"]])
        m = (z >= g["z"]) if g["tail"] == "hi" else (z <= -g["z"])
        f = g.get("flt")
        if f:
            zf = self.ctx.eval(["zs", 720, f["node"]]); cond = (zf > f["z"]) if f["dir"] == "above" else (zf < f["z"])
            m = m & ~cond if f.get("veto") else m & cond
        return s, m & self.ok

    def trades(self, m, split):
        idx = np.flatnonzero(m & self.split[split])
        if len(idx) == 0: return idx
        keep = []; lc, lt = None, -1e18; coin = self.P.coin.values
        for i in idx:                                          # one trade per coin per 24 hours (rows are sorted by coin, then time)
            if coin[i] != lc: lc, lt = coin[i], -1e18
            if self.t[i] - lt >= COOL: keep.append(i); lt = self.t[i]
        return np.array(keep, int)

    def stats(self, idx, side, split):
        r = self.R[side][idx]; ok = np.isfinite(r); idx, r = idx[ok], r[ok]
        if len(idx) < 2: return None
        w = pd.Series(r).groupby(self.t[idx] // (6 * H)).mean().values          # calls in the same 6-hour move count as ONE bet
        tt = float(w.mean() / (w.std(ddof=1) + 1e-12) * math.sqrt(len(w))) if len(w) > 2 else 0.0
        bm = self.btc48[idx] * side; okb = np.isfinite(bm)
        bc = float(np.corrcoef(r[okb], bm[okb])[0, 1]) if okb.sum() > 10 and np.std(bm[okb]) > 0 else 0.0
        return {"n": int(len(idx)), "windows": int(len(w)), "t": round(tt, 2), "mean": round(float(r.mean()) * 100, 3), "win": round(float((r > 0).mean()) * 100, 1),
                "freq": round(len(idx) / self.months[split], 2), "hold": round(float(np.nanmean(self.HRS[side][idx])), 1), "btc": round(bc, 3)}

def main(budget_min=100.0, seed=None, log=print):
    """fitness = how much a candidate lowers the ARBITER's odds error (out of sample, decision band weighted, per market move);
    standalone trading profit is kept as a side statistic and places the candidate in its niche."""
    t0 = time.time(); EV = getattr(C, "EVO_DIR", "evo_state"); os.makedirs(EV, exist_ok=True)
    from . import oracle as OR
    P = features.build(blocks=_blocks(log), log=log); lab = Lab(P, log); orc = OR.Oracle(P, lab, log); A = AR.Archive(os.path.join(EV, "archive.json.gz"))
    if A.cells and any(e.get("fitKind") != "oddsError" for e in A.elites()):        # the archive was scored by profit before: start it again under the new fitness
        A.log.append({"at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "note": f"archive reset: fitness changed to the Arbiter's odds error ({len(A.cells)} profit-scored niches retired)"})
        A.cells, A.fires = {}, {}
    S0 = ST.staged(); kept = []                       # voters staged under the old (profit) fitness must pass the odds-error gate too
    for v in S0["active"]:
        if ((v.get("checks") or {}).get("valGain") or {}).get("passesGates"): kept.append(v); continue      # checked under today's rules already
        try: vg = orc.delta(orc.voter_cols(lab.ctx, v["genome"]), "val")
        except Exception: vg = None
        if vg and vg["t"] >= 1.5 and vg["milli"] > 0 and vg["passesGates"]: v.setdefault("checks", {})["valGain"] = vg; kept.append(v)
        else: S0["retired"].append({**v, "retiredWhy": f"failed the validation-year odds-error gate ({vg})"}); log(f"   staged voter {v['id']} retired: no odds-error gain on the validation year")
    if len(kept) != len(S0["active"]):
        S0["active"] = kept; json.dump(S0, open(ST._path(), "w"), indent=1)
    rng = random.Random(seed if seed is not None else int(time.time())); cols = lab.cols
    seen = set(); rej = {"error": 0, "too little data": 0, "no gain": 0, "shy (fails ranking / profit / volume)": 0, "correlated with the Arbiter": 0, "twin": 0, "worse than its niche": 0}; new = 0; evals = 0
    try: from ..ideas import _groq_call as ask
    except Exception: ask = None
    if not os.environ.get("GROQ_API_KEY"): ask = None
    llm_calls = 0; origins = {}
    feats_ctx = [f for f in CTX_FEATS if f in P.columns]
    misses = {"confidentLosers": {("long" if s > 0 else "short"): orc.confident_misses(s, feats_ctx) for s in (1, -1)},
              "nearMissWinners": {("long" if s > 0 else "short"): orc.near_misses(s, feats_ctx) for s in (1, -1)}}

    def evaluate(g, origin):
        nonlocal new, evals
        k = GEN.gkey(g)
        if k in seen: return
        seen.add(k); A.N += 1; evals += 1
        try:
            vc = orc.voter_cols(lab.ctx, g)
            corr = AR.max_corr(vc["s"][lab.sample], lab.X)
            if corr > AR.ORTHO: rej["correlated with the Arbiter"] += 1; return
            dl = orc.delta(vc, "disc")
        except Exception: rej["error"] += 1; return
        if not dl or dl["windows"] < MIN_WIN: rej["too little data"] += 1; return
        if dl["t"] < 1.0 or dl["milli"] <= 0: rej["no gain"] += 1; return
        if not dl["passesGates"]: rej["shy (fails ranking / profit / volume)"] += 1; return       # may not win by making the Arbiter quiet
        _, m = lab.fire(g); idx = lab.trades(m, "disc")                                 # standalone trading: a side statistic and its niche
        st = lab.stats(idx, g["side"], "disc") if len(idx) >= MIN_TRADES else None
        desc = st or {"freq": 0.0, "hold": 24.0, "btc": 0.0}
        fire = m[lab.sample]; tw = A.twin_of(fire, dl["t"]); c = AR.cell(g["side"], desc)
        if tw is not None and tw != c: rej["twin"] += 1; return
        e = {"key": k, "genome": g, "text": GEN.gtext(g), "fit": dl["t"], "fitKind": "oddsError", "gain": dl, "stats": st, "corr": round(corr, 3), "origin": origin,
             "born": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "N_at": A.N, "cell": AR.cell_name(c)}
        if A.insert(c, e, fire): new += 1; origins[origin] = origins.get(origin, 0) + 1
        else: rej["worse than its niche"] += 1

    while time.time() - t0 < budget_min * 60:
        el = A.elites()
        if len(el) < 24 or rng.random() < 0.1: evaluate(GEN.genome(cols, rng), "random")
        else:
            a, b = rng.choice(el), rng.choice(el); evaluate(GEN.mutate(a["genome"], cols, rng, b["genome"]), "mutation")
        if ask and llm_calls < 8 and evals and evals % 200 == 0:
            llm_calls += 1
            try: kids = GEN.llm_voters(sorted(el, key=lambda e: -e["fit"])[:4], misses, cols, ask)
            except Exception as e: kids = []; log(f"   evo: AI answer unusable ({str(e)[:80]})")
            for g, origin, why in kids: evaluate(g, origin)
        if evals % 500 == 0 and evals: log(f"   evo: {evals} tried this night, {len(A.cells)} niches filled, {new} new elites, rejected {rej}")

    # STAGING: the deflated bar on the discovery gain, then ONE look at the validation year (the gate) and the newest months, then the proofs
    bar = GD.t_min(A.N); staged_now = []
    for c, e in sorted(A.cells.items(), key=lambda kv: -kv[1]["fit"]):
        if e.get("checks") or e["fit"] < bar: continue
        g = e["genome"]; vc = orc.voter_cols(lab.ctx, g); chk = {"bar": round(bar, 2), "N": A.N, "pLuck": round(GD.p_luck(e["fit"], A.N), 4)}
        v = orc.delta(vc, "val"); x = orc.delta(vc, "exam"); chk["valGain"], chk["examGain"] = v, x
        passed = bool(v and v["t"] >= 1.5 and v["milli"] > 0 and v["passesGates"] and x and x["milli"] >= 0 and x["grades"]["profitAfter"] >= x["grades"]["profitBefore"])   # validation-year odds-error gate + anti-shyness
        if passed:
            ok = dsl.probe(g["sig"], P)[0] and (not g.get("flt") or dsl.probe(g["flt"]["node"], P)[0])
            lk, bad = dsl.leak_check(g["sig"], P)
            if g.get("flt"): lk2, bad2 = dsl.leak_check(g["flt"]["node"], P); lk = lk and lk2; bad.update(bad2)
            chk["causal"] = ok; chk["noLeak"] = lk; chk["leaky"] = bad or None; passed = ok and lk
        chk["passed"] = passed; e["checks"] = chk
        if passed and ST.stage_arbiter_upgrade(e, log): staged_now.append(e["key"])
    A.log.append({"at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "evals": evals, "new": new, "filled": len(A.cells), "N": A.N, "bar": round(bar, 2),
                  "rejected": rej, "origins": origins, "llmCalls": llm_calls, "staged": staged_now, "mins": round((time.time() - t0) / 60, 1), "fitness": "Arbiter odds-error drop"})
    A.save()
    summ = {"at": A.log[-1]["at"], "N": A.N, "bar": round(bar, 2), "filled": len(A.cells), "cells": 2 * 4 * 4 * 4, "lastNight": A.log[-1], "fitness": "Arbiter odds-error drop (t per market move)",
            "arbiterLL": orc.base_ll, "misses": misses,
            "top": [{k: e.get(k) for k in ("text", "fit", "gain", "stats", "cell", "origin", "born", "checks", "corr")} for e in sorted(A.elites(), key=lambda e: -e["fit"])[:12]],
            "map": {AR.cell_name(c): e["fit"] for c, e in A.cells.items()}, "staged": ST.staged()}
    json.dump(summ, open(os.path.join(EV, "summary.json"), "w"), indent=1, default=str)
    log(f"evo night: {evals} voters tried (all-time {A.N:,}), {len(A.cells)} of 128 niches filled, {new} new elites, bar t>={bar:.2f}, staged {staged_now}")

def _blocks(log=print):
    """the AI-invented measurements the live Arbiter uses (read from its saved memory, read-only), so the evolution sees the same inputs"""
    try:
        m = json.load(open(os.path.join(getattr(C, "STATE_DIR", "state"), "memory.json"))); return m.get("blocks", [])
    except Exception: return []

if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 100.0)
