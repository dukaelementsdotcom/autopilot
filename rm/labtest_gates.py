"""LAB TEST (on demand): across the walk-forward periods (brain retrained for each, tested on the following unseen 120 days):
  shorts gated by the trend reader (off when it says up / strong up), longs gated (only when it says up / strong up), both together,
  and smaller targets for shorts (+0.8 / +1 / +1.5%). Signals = the exam rule, first qualifying signal of each day."""
import json, time, numpy as np, pandas as pd
from . import config as C, features, referee as RF, challengers as CH, compass
from .judge import Judge
from .walkforward import pol

DAY = 86400
def main(folds=7):
    t0 = time.time(); blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    J = Judge(features.build(blocks=blocks)); out = {"folds": []}
    orig = RF.fam_cols; RF.fam_cols = lambda P_: {k: v for k, v in orig(P_).items() if k != "invented"}
    end = int(J.t_end); UP = ("up", "strong up")
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY; v0 = h0 - C.VALID_DAYS * DAY
        if time.time() - t0 > 95 * 60: print("time budget reached"); break
        J.t_end, J.hold0, J.val0 = te, h0, v0
        try: B = RF.train(J, log=lambda *a: None)
        except Exception as e: print("fold skipped:", str(e)[:100]); break
        X = RF._rows(J, h0, te - RF.HORIZON * 3600); S = RF.score(J, B, X); span = (te - h0) / DAY; st = compass.states_for(J, J.t[X]); up = np.isin(st, UP)
        T, O, Hh, Ll, Cc = J._bars("BTC"); s = pd.Series(Cc, index=T)
        f = {"exam": f"{pd.to_datetime(h0, unit='s').date()} to {pd.to_datetime(te, unit='s').date()}", "btc": round(float(s.asof(te) / s.asof(h0) - 1) * 100, 1),
             "shareUp": round(float(up.mean()), 2), "states": {x: round(float((st == x).mean()), 2) for x in compass.STATES}}
        parts = {}
        for name, sd in B["sides"].items():
            side = 1 if name == "long" else -1; stp = RF.stop_pct(J.dvol[X]); p = np.asarray(S[name]["prob"]); be = RF.breakeven(stp)
            y, r = RF.barrier(J, X, side, RF.TARGET, stp, RF.HORIZON); pick = (p >= be + sd["margin"]) & ~np.asarray(S[name]["unfamiliar"])
            gate = pick & (up if name == "long" else ~up)
            parts[name] = (pick, gate, r, p, stp)
            f[name] = {"all": pol(J, X[pick], r[pick], p[pick], stp[pick], span), "gated": pol(J, X[gate], r[gate], p[gate], stp[gate], span)}
            if name == "short":
                for tg in (0.008, 0.01, 0.015):
                    yy, rr = CH._barrier_T(RF, J, X[pick], side, np.full(int(pick.sum()), tg), stp[pick], RF.HORIZON)
                    f[name][f"target +{tg*100:g}%"] = pol(J, X[pick], rr, p[pick], stp[pick], span) if pick.any() else {"trades": 0}
        # one Arbiter: shorts when the trend is not up, longs when it is
        rows = np.r_[X[parts["short"][1]], X[parts["long"][1]]]; rr = np.r_[parts["short"][2][parts["short"][1]], parts["long"][2][parts["long"][1]]]
        pp = np.r_[(parts["short"][3] - RF.breakeven(parts["short"][4]))[parts["short"][1]], (parts["long"][3] - RF.breakeven(parts["long"][4]))[parts["long"][1]]]
        ss = np.r_[parts["short"][4][parts["short"][1]], parts["long"][4][parts["long"][1]]]
        f["both gated"] = pol(J, rows, rr, pp, ss, span) if len(rows) else {"trades": 0}
        out["folds"].append(f); print(json.dumps(f), flush=True); json.dump(out, open("labtest.json", "w"), indent=1)
    json.dump(out, open("labtest.json", "w"), indent=1)

if __name__ == "__main__": main()
