"""THE ARBITER'S OWN ERRORS, OUT OF SAMPLE. Candidates are scored by how much they lower the Arbiter's odds error, so we need the
Arbiter's probabilities on rows it was NOT trained on: its family models and combiner are refit by time-blocked cross-fitting on the
discovery years (3 folds, 2-day embargo); the validation year and the newest months get the families trained on all discovery years and
a combiner that never saw the rows it scores. (The 'similar moments' voter is left out here: it only matters at the margin.)

Fitness of a candidate voter = the drop in log-loss when its columns are added to the Arbiter's odds, measured on rows the fit never saw,
weighted toward the decision band (odds near the trading bar), averaged per 6-hour market move (calls in one move are one bet), as a
t-statistic. The baseline is the Arbiter's odds RECALIBRATED on the same rows, so a candidate earns nothing for fixing calibration alone."""
import time, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier as HGB
from sklearn.linear_model import LogisticRegression
from .. import referee as RF, config as C

H = 3600; DAY = 86400; EMB = 2 * DAY; BAND = 0.06

def _lg(p): p = np.clip(p, 1e-3, 1 - 1e-3); return np.log(p / (1 - p))
def _auc(p, y):
    """ranking power: the chance a random winner gets higher odds than a random loser (rank formula, ties count half)"""
    y = np.asarray(y) > 0; n1 = int(y.sum()); n0 = len(y) - n1
    if n1 < 20 or n0 < 20: return None
    r = pd.Series(np.asarray(p)).rank().values
    return round(float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)), 4)

def _ll(y, p): p = np.clip(p, 1e-4, 1 - 1e-4); return -(y * np.log(p) + (1 - y) * np.log(1 - p))

class Oracle:
    def __init__(self, P, lab, log=print, every=6):
        t0 = time.time(); self.P = P; self.lab = lab; t = P.t.values; fams = RF.fam_cols(P); self.fams = fams
        base = lab.ok & (t % (every * H) == 0) & ~P.coin.isin(C.DEAD).values
        rows = {"disc": np.flatnonzero(base & lab.split["disc"]), "val": np.flatnonzero(base & lab.split["val"]), "exam": np.flatnonzero(base & lab.split["exam"])}
        self.S = {}
        for side in (1, -1):
            y_all = lab.Y[side]; out = {}
            D = rows["disc"]; Dy = y_all[D]; tD = t[D]; edges = np.r_[np.quantile(tD, [0, 1 / 3, 2 / 3]), tD.max() + 1]
            oof = {f: np.full(len(D), np.nan) for f in fams}
            for k in range(3):
                te = (tD >= edges[k]) & (tD < edges[k + 1]); tr = ~te & ((tD < edges[k] - EMB) | (tD >= edges[k + 1] + EMB))
                for f, cols in fams.items():
                    m = self._fit(RF._X(P, D[tr], cols), Dy[tr]); oof[f][te] = m.predict_proba(RF._X(P, D[te], cols))[:, 1]
            Z = np.column_stack([_lg(oof[f]) for f in fams]); p_d = np.full(len(D), np.nan)
            for k in range(3):                                        # the combiner is cross-fitted too
                te = (tD >= edges[k]) & (tD < edges[k + 1]); tr = ~te & ((tD < edges[k] - EMB) | (tD >= edges[k + 1] + EMB))
                p_d[te] = LogisticRegression(C=0.5, max_iter=500).fit(Z[tr], Dy[tr]).predict_proba(Z[te])[:, 1]
            out["disc"] = (D, p_d, Dy)
            full = {f: self._fit(RF._X(P, D, cols), Dy) for f, cols in fams.items()}
            V, Xr = rows["val"], rows["exam"]
            ZV = np.column_stack([_lg(full[f].predict_proba(RF._X(P, V, cols))[:, 1]) for f, cols in fams.items()])
            ZX = np.column_stack([_lg(full[f].predict_proba(RF._X(P, Xr, cols))[:, 1]) for f, cols in fams.items()])
            tV = t[V]; half = np.median(tV); a = tV < half; p_v = np.full(len(V), np.nan)
            p_v[~a] = LogisticRegression(C=0.5, max_iter=500).fit(ZV[a], y_all[V][a]).predict_proba(ZV[~a])[:, 1]
            p_v[a] = LogisticRegression(C=0.5, max_iter=500).fit(ZV[~a], y_all[V][~a]).predict_proba(ZV[a])[:, 1]
            out["val"] = (V, p_v, y_all[V])
            out["exam"] = (Xr, LogisticRegression(C=0.5, max_iter=500).fit(ZV, y_all[V]).predict_proba(ZX)[:, 1], y_all[Xr])
            for k_, (r, p, y) in out.items():
                ok = np.isfinite(p); r, p, y = r[ok], p[ok], y[ok]; be = RF.breakeven(RF.stop_pct(lab.dvol[r]))
                w = 1.0 + 2.0 * np.exp(-((p - (be + BAND)) / BAND) ** 2)            # the decision band counts three times
                out[k_] = {"rows": r, "p": p, "y": y.astype(float), "lp": _lg(p), "be": be, "w": w, "win": t[r] // (6 * H)}
            self.S[side] = out
        d = self.S[-1]["disc"]; self.n = {k: len(v["rows"]) for k, v in self.S[-1].items()}
        self.base_ll = {s: {k: float(np.average(_ll(v["y"], v["p"]), weights=v["w"])) for k, v in self.S[s].items()} for s in self.S}
        log(f"evo oracle: the Arbiter's out-of-sample odds on {self.n} rows per side in {time.time() - t0:.0f}s; weighted log-loss {self.base_ll}")

    @staticmethod
    def _fit(X, y):
        return HGB(max_iter=150, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=300, l2_regularization=1.0, early_stopping=False, random_state=0).fit(X, y)

    def voter_cols(self, ctx, g):
        """exactly the columns a staged voter gets (rm/evo/stage.py): z-score of the signal per coin, its rank across coins, its filter"""
        return {"z": ctx.eval(["zs", 720, g["sig"]]), "s": ctx.eval(g["sig"]), "f": ctx.eval(["zs", 720, g["flt"]["node"]]) if g.get("flt") else None}

    def _F(self, vc, d):
        r = d["rows"]; z = np.clip(np.nan_to_num(vc["z"][r].astype(float)), -5, 5)
        s = pd.Series(vc["s"][r].astype(float)); x = s.groupby(self.P.t.values[r]).rank(pct=True).fillna(0.5).values - 0.5
        cols = [z, x, z * z / 5, z * d["lp"], np.isfinite(vc["z"][r]).astype(float)]
        if vc["f"] is not None: f = np.clip(np.nan_to_num(vc["f"][r].astype(float)), -5, 5); cols += [f, f * d["lp"]]
        return np.column_stack(cols)

    def delta(self, vc, split="disc"):
        """odds-error drop per 6-hour move, both sides pooled. On the discovery years: fit on the first 60%, score the last 40%;
        on the validation year / newest months: fit on all discovery years, score those rows (one look)."""
        per = []; out = {}
        for side, S in self.S.items():
            d = S["disc"]
            if split == "disc":
                cut = np.quantile(self.P.t.values[d["rows"]], 0.6); fit = self.P.t.values[d["rows"]] < cut - EMB; ev_d = d; ev = self.P.t.values[d["rows"]] >= cut
            else: fit = np.ones(len(d["rows"]), bool); ev_d = S[split]; ev = np.ones(len(ev_d["rows"]), bool)
            Fd = self._F(vc, d); Fe = Fd if ev_d is d else self._F(vc, ev_d)
            if np.isfinite(vc["z"][d["rows"]]).mean() < 0.3: return None
            b = LogisticRegression(C=1.0, max_iter=300).fit(d["lp"][fit, None], d["y"][fit])
            n = LogisticRegression(C=1.0, max_iter=300).fit(np.column_stack([d["lp"][fit], Fd[fit]]), d["y"][fit])
            pb = b.predict_proba(ev_d["lp"][ev, None])[:, 1]; pn = n.predict_proba(np.column_stack([ev_d["lp"][ev], Fe[ev]]))[:, 1]
            y = ev_d["y"][ev]; w = ev_d["w"][ev]; dl = (_ll(y, pb) - _ll(y, pn)) * w
            # ANTI-SHYNESS: what the new odds do to the TRADES at the bar, against what the Arbiter does today (its raw odds)
            p0 = ev_d["p"][ev]; bar = ev_d["be"][ev] + BAND; rr = self.lab.R[side][ev_d["rows"][ev]]; wins = ev_d["win"][ev]
            g0, g1 = p0 >= bar, pn >= bar
            per.append(pd.DataFrame({"win": wins, "d": dl, "w": w, "r0": np.where(g0, rr, np.nan), "r1": np.where(g1, rr, np.nan)}))
            near = (p0 >= ev_d["be"][ev]) & (p0 < bar + BAND)                          # the decision band: where ranking decides trades
            auc0, auc1 = _auc(p0[near], y[near]), _auc(pn[near], y[near])
            band = np.abs(ev_d["p"][ev] - (ev_d["be"][ev] + BAND)) < BAND
            out["long" if side > 0 else "short"] = {"milli": round(float(dl.sum() / w.sum()) * 1000, 3), "bandMilli": round(float(dl[band].sum() / max(1e-9, w[band].sum())) * 1000, 3) if band.any() else None,
                                                    "aucBand": [auc0, auc1]}
        D = pd.concat(per); g = D.groupby("win")[["d", "w"]].sum(); v = (g.d / g.w).values
        tt = float(v.mean() / (v.std(ddof=1) + 1e-12) * np.sqrt(len(v))) if len(v) > 2 else 0.0
        out.update({"t": round(tt, 2), "milli": round(float(D.d.sum() / D.w.sum()) * 1000, 3), "windows": int(len(v))})
        # graded calls counted per separate 6-hour move (one bet each), profit per month = the edge actually harvested
        months = max(1.0, (D.win.max() - D.win.min()) * 6 / (24 * 30.4))
        m0 = D.groupby("win").r0.mean().dropna(); m1 = D.groupby("win").r1.mean().dropna()
        out["grades"] = {"before": int(len(m0)), "after": int(len(m1)), "profitBefore": round(float(m0.sum()) * 100 / months, 2), "profitAfter": round(float(m1.sum()) * 100 / months, 2)}
        a = [v["aucBand"] for k, v in out.items() if isinstance(v, dict) and v.get("aucBand") and None not in v["aucBand"]]
        out["aucGain"] = round(float(np.mean([x[1] - x[0] for x in a])), 4) if a else None
        G = out["grades"]
        out["gates"] = {"ranking": bool(out["aucGain"] is not None and out["aucGain"] >= 0),
                        "profit": bool(G["profitAfter"] >= G["profitBefore"]),
                        "volume": bool(G["after"] >= 0.8 * G["before"])}
        out["passesGates"] = all(out["gates"].values())
        return out

    def near_misses(self, side, feats, k=3):
        """real +2% winners the Arbiter just missed: odds below the trading bar but above break-even (where a booster creates Grade A)"""
        from .generator import failure_clusters
        d = self.S[side]["disc"]; m = (d["p"] >= d["be"]) & (d["p"] < d["be"] + BAND)
        if m.sum() < 50: return []
        cl = failure_clusters(self.P, d["rows"][m], np.where(d["y"][m] > 0, -1.0, 1.0), feats, k)      # the WINNERS are clustered
        return [{"winners": c.pop("losses"), **c} for c in cl]

    def confident_misses(self, side, feats, k=3):
        """where the Arbiter's confident calls (odds above the trading bar) LOST, on the discovery years"""
        from .generator import failure_clusters
        d = self.S[side]["disc"]; m = d["p"] >= d["be"] + BAND
        if m.sum() < 50: return []
        return failure_clusters(self.P, d["rows"][m], np.where(d["y"][m] > 0, 1.0, -1.0), feats, k)
