"""LAB TEST: the two new witnesses (cvd, xoi) - backfill, NO-LOOK-AHEAD check (values identical when the later history is cut off),
validation-year exam and final holdout exam (log-loss of the Arbiter WITH the witness vs WITHOUT it), live coverage at the newest hour."""
import json, time, tempfile, os, numpy as np, pandas as pd
from . import config as C, features, xoi, witnesses as W, referee as RF, challengers as CH
from .judge import Judge
out = {}
out["xoiUpdate"] = xoi.update(budget=1800); print("update", out["xoiUpdate"], flush=True)
# 1. no look-ahead
cz = {}
for coin in ("BTC", "SOL", "DOGE"):
    k = pd.read_parquet(f"{C.DATA_DIR}/raw/{coin}_k.parquet").set_index("t"); idx = np.arange(k.index.min(), k.index.max() + 3600, 3600); k = k.reindex(idx)
    k[["v", "tb"]] = k[["v", "tb"]].fillna(0); k["c"] = k.c.ffill()
    sp = features._optional(coin, "s", idx)
    full = pd.DataFrame(features.cvd_cols(k, sp), index=idx); cut = idx[int(len(idx) * 0.7)]
    kt, st = k[k.index < cut], sp[sp.index < cut]
    part = pd.DataFrame(features.cvd_cols(kt, st), index=kt.index)
    a, b = full.loc[part.index].values, part.values; m = np.isfinite(a) | np.isfinite(b)
    cz[coin] = float(np.nanmax(np.abs(np.nan_to_num(a[m]) - np.nan_to_num(b[m])))) if m.any() else None
out["cvdPrefixMaxDiff"] = cz; print("cvd prefix", cz, flush=True)
blocks = []
try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
except Exception: pass
P = features.build(blocks=blocks); J = Judge(P)
d = pd.read_parquet(xoi.P_D()); cut = int(d.t.quantile(0.7)) // 86400 * 86400
F1 = xoi.features(P); tmp = tempfile.mkdtemp(); orig = xoi.P_D
d[d.t < cut].to_parquet(f"{tmp}/x.parquet"); xoi.P_D = lambda: f"{tmp}/x.parquet"; F2 = xoi.features(P); xoi.P_D = orig
rows = P.t.values < cut + 86400           # rows whose inputs are all before the cut
out["xoiPrefixMaxDiff"] = {k: float(np.nanmax(np.abs(np.nan_to_num(F1[k][rows]) - np.nan_to_num(F2[k][rows])))) for k in F1}
print("xoi prefix", out["xoiPrefixMaxDiff"], flush=True)
# coverage: history and live
newest = P.t.max(); nr = P.t.values == newest
for w in ("cvd", "xoi"):
    cols = [c for c in W.WITNESSES[w]["cols"] if c in P]
    out[f"{w}Coverage"] = {"cols": len(cols), "newestHour": round(float(P.loc[nr, cols].notna().mean().mean()), 3),
                           "from": str(pd.to_datetime(P.t[P[cols].notna().mean(axis=1) >= 0.5].min(), unit="s").date())}
    print(w, out[f"{w}Coverage"], flush=True)
# 2 + 3. validation-year and final holdout exams
B = RF.load(); Hd = CH.load_heads(B)
for w in ("cvd", "xoi"):
    m = W.fit(w, J, B, Hd, print); out[f"{w}Exam"] = m and m["exam"]; print(w, "exam", out[f"{w}Exam"], flush=True)
json.dump(out, open("labtest.json", "w"), indent=1, default=str)
