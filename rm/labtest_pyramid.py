"""LAB TEST (on demand): does holding / pyramiding the Arbiter's signals beat the +2% target? And how often does a 20% move come
before a 6% pullback? Signals = the exam rule (first qualifying signal of each day). Every variant starts with the same risk
(4% of the account if the first stop is hit). Validation year first, then the newest months."""
import json, math, numpy as np, pandas as pd
from . import config as C, features, referee as RF
from .judge import Judge

def main():
    blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    J = Judge(features.build(blocks=blocks)); B = RF.load(); out = {}
    def sim(i, side, s, step, addf, hold=336, cap=6.0, final=None):
        e = J.O[i]; n0 = min(RF.RISK["A"] / s, RF.MAX_MARGIN * 20); units = [(e, n0)]; lvl = 0; stop = e * (1 - side * s); last = J.last[i]
        for k in range(i, min(i + hold, last + 1)):
            hi, lo = J.H[k], J.L[k]
            if (lo <= stop) if side > 0 else (hi >= stop): return sum(n * (side * (stop / p - 1) - C.FEE) for p, n in units), lvl
            fav = side * ((hi if side > 0 else lo) / e - 1)
            while fav >= (lvl + 1) * step:
                lvl += 1; px = e * (1 + side * lvl * step)
                if final and lvl * step >= final: return sum(n * (side * (px / p - 1) - C.FEE) for p, n in units), lvl
                if addf > 0 and sum(n for _, n in units) + addf * n0 <= cap: units.append((px, addf * n0))
                stop = e * (1 + side * (lvl - 1) * step)
        px = J.Cl[min(i + hold - 1, last)]
        return sum(n * (side * (px / p - 1) - C.FEE) for p, n in units), lvl
    cache = {}
    def picks(a, b, name):
        if (a, b) not in cache: X = RF._rows(J, a, b); cache[(a, b)] = (X, RF.score(J, B, X))
        X, S = cache[(a, b)]; sd = B["sides"][name]; stp = RF.stop_pct(J.dvol[X]); p = np.asarray(S[name]["prob"])
        ok = (p >= RF.breakeven(stp) + sd["margin"]) & ~np.asarray(S[name]["unfamiliar"])
        df = pd.DataFrame({"i": X[ok], "t": J.t[X][ok], "p": p[ok], "s": stp[ok]}); df["day"] = df.t // 86400
        return df.sort_values(["t", "p"], ascending=[True, False]).groupby("day").head(1), (b - a) / 86400
    def summary(res, span):
        a = np.maximum(np.array([r[0] for r in res]), -0.5); eq = np.cumprod(1 + a)
        return {"n": int(len(a)), "win": round(100 * float((a > 0).mean())), "avgAcct": round(float(a.mean()) * 100, 2), "best": round(float(a.max()) * 100, 1), "worst": round(float(a.min()) * 100, 1),
                "monthly": round(math.expm1(np.log1p(a).sum() / span * 30.4) * 100, 1), "maxDrop": round(float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max()) * 100, 1),
                "endMultiple": round(float(eq[-1]), 2), "runs3steps": int(sum(r[1] >= 3 for r in res))}
    VAR = {"today (+2% target)": None, "hold, trailing 3% steps, no adds": (0.03, 0.0), "pyramid 3% steps, add 0.5": (0.03, 0.5), "pyramid 3% steps, add 1.0": (0.03, 1.0),
           "pyramid 5% steps, add 1.0": (0.05, 1.0), "pyramid 2% steps, add 1.0": (0.02, 1.0), "pyramid 3% steps, add 1.0, out at +20%": (0.03, 1.0, 0.20)}
    for name in ("short", "long"):
        side = 1 if name == "long" else -1
        for lab, a, b in (("validation year", J.val0, J.hold0), ("exam months", J.hold0, J.t_end - 14 * 86400)):
            df, span = picks(a, b, name); key = f"{name} | {lab} | {len(df)} signals"; out[key] = {}
            for vn, v in VAR.items():
                if v is None:
                    y, r = RF.barrier(J, df.i.values, side, RF.TARGET, df.s.values, RF.HORIZON); res = [(float(rr * min(RF.RISK["A"] / s, RF.MAX_MARGIN * RF.LEV)), 0) for rr, s in zip(r, df.s.values)]
                else: res = [sim(int(i), side, float(s), v[0], v[1], final=(v[2] if len(v) > 2 else None)) for i, s in zip(df.i.values, df.s.values)]
                out[key][vn] = summary(res, span) if len(res) else None
            print(key, json.dumps(out[key]), flush=True)
    for name in ("short", "long"):
        side = 1 if name == "long" else -1; X = RF._rows(J, J.val0, J.t_end - 14 * 86400)[::7]
        def big(rows):
            hit = 0
            for i in rows:
                e = J.O[i]; last = J.last[i]
                for k in range(i, min(i + 336, last + 1)):
                    if (J.L[k] <= e * 0.94) if side > 0 else (J.H[k] >= e * 1.06): break
                    if (J.H[k] >= e * 1.2) if side > 0 else (J.L[k] <= e * 0.8): hit += 1; break
            return [round(100 * hit / max(1, len(rows)), 1), int(len(rows))]
        dfa = pd.concat([picks(J.val0, J.hold0, name)[0], picks(J.hold0, J.t_end - 14 * 86400, name)[0]])
        out[f"20% before a 6% pullback within 14 days, {name}"] = {"anyHour": big(X), "arbiterSignals": big(dfa.i.values)}
        print(name, out[f"20% before a 6% pullback within 14 days, {name}"], flush=True)
    json.dump(out, open("labtest.json", "w"), indent=1)

if __name__ == "__main__": main()
