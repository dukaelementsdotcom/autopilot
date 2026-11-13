"""LAB TEST: gold (and silver) as a contestant. Daily gold futures history from Yahoo (GC=F, SI=F, ~25 years) plus the drivers
everyone names: the dollar, US 10-year yield, S&P 500, VIX, oil. Walk-forward by calendar year: each exam year is traded unseen,
the model is trained on all years before the validation year, and the entry bar is chosen on the validation year only.

Entry at the NEXT day's open (no look-ahead: every input is known at the previous close). Barriers checked on daily high/low; a day
that touches both counts as a stop (pessimistic). Cost 0.1% per round trip. 4% of the account at risk at the stop, one position at a time.

Variants (target / stop in multiples of 20-day average daily range, horizon in trading days), both sides:
  G5   1.5 / 1.0 / 5d      G10  2.0 / 1.0 / 10d      G20  3.0 / 1.5 / 20d
Baselines it must beat on the same years: buy-and-hold, a 50/200-day trend rule, and the same barriers taken at random."""
import json, math, time, numpy as np, pandas as pd, requests
from sklearn.ensemble import HistGradientBoostingClassifier as HGB
UA = {"User-Agent": "Mozilla/5.0 (research-machine)"}
SYM = {"gold": "GC=F", "silver": "SI=F", "dxy": "DX-Y.NYB", "y10": "^TNX", "spx": "^GSPC", "vix": "^VIX", "oil": "CL=F"}
V = {"G5": (1.5, 1.0, 5), "G10": (2.0, 1.0, 10), "G20": (3.0, 1.5, 20)}
FEE = 0.001; RISK = 0.04

def yahoo(sym):
    j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym)}", params={"range": "max", "interval": "1d"}, headers=UA, timeout=60).json()
    r = j["chart"]["result"][0]; q = r["indicators"]["quote"][0]
    d = pd.DataFrame({"o": q["open"], "h": q["high"], "l": q["low"], "c": q["close"]}, index=pd.to_datetime(r["timestamp"], unit="s").normalize())
    return d[~d.index.duplicated(keep="last")].dropna(subset=["c"])

def build(asset):
    raw = {k: yahoo(s) for k, s in SYM.items()}; time.sleep(0.5)
    g = raw[asset].copy(); g = g[(g.h >= g.l) & (g.c > 0)]
    g["o"] = g.o.fillna(g.c); g["rng"] = ((g.h - g.l) / g.c).rolling(20).mean()
    F = pd.DataFrame(index=g.index); c = g.c
    for n in (1, 5, 20, 60, 120): F[f"r{n}"] = np.log(c / c.shift(n))
    for n in (20, 50, 200): F[f"ma{n}"] = np.log(c / c.rolling(n).mean())
    F["vol20"] = np.log(c).diff().rolling(20).std(); F["volr"] = F.vol20 / np.log(c).diff().rolling(120).std()
    F["rng"] = g.rng; F["hi252"] = np.log(c / c.rolling(252).max()); F["lo252"] = np.log(c / c.rolling(252).min())
    F["up10"] = (np.log(c).diff() > 0).rolling(10).mean()
    for k in ("silver", "dxy", "spx", "oil", "gold"):
        if k == asset: continue
        x = raw[k].c.reindex(g.index).ffill(limit=3)
        for n in (5, 20, 60): F[f"{k}_r{n}"] = np.log(x / x.shift(n))
    y10 = raw["y10"].c.reindex(g.index).ffill(limit=3); vix = raw["vix"].c.reindex(g.index).ffill(limit=3)
    for n in (5, 20, 60): F[f"y10_d{n}"] = y10 - y10.shift(n)
    F["vix"] = vix; F["vix_d5"] = vix - vix.shift(5)
    other = "silver" if asset == "gold" else "gold"; ratio = np.log(c / raw[other].c.reindex(g.index).ffill(limit=3))
    F["ratio_z"] = (ratio - ratio.rolling(252).mean()) / ratio.rolling(252).std()
    F["dow"] = g.index.dayofweek
    return g, F.replace([np.inf, -np.inf], np.nan)

def label(g, side, tk, sk, hz):
    """entry at next open; +1 target first, 0 stop first or neither (exit at close of last day); r = return as share of price"""
    o, h, l, c, rg = g.o.values, g.h.values, g.l.values, g.c.values, g.rng.values; n = len(g)
    y = np.full(n, np.nan); r = np.full(n, np.nan); s = np.full(n, np.nan)
    for i in range(n - hz - 1):
        if not np.isfinite(rg[i]): continue
        e = o[i + 1]; tg, st = tk * rg[i], sk * rg[i]; s[i] = st
        up, dn = (e * (1 + tg), e * (1 - st)) if side > 0 else (e * (1 - tg), e * (1 + st))
        res = None
        for j in range(i + 1, i + 1 + hz):
            hit_t = h[j] >= up if side > 0 else l[j] <= up
            hit_s = l[j] <= dn if side > 0 else h[j] >= dn
            if hit_s: res = (0, -st); break
            if hit_t: res = (1, tg); break
        if res is None: res = (0, side * (c[i + hz] / e - 1))
        y[i], r[i] = res[0], res[1] - FEE
    return y, r, s

def sim(idx, r, s, hz, days):
    """one position at a time: after an entry, skip until it can have closed"""
    taken, nxt = [], -1
    for k, i in enumerate(idx):
        if i >= nxt: taken.append(k); nxt = i + hz + 1
    if not taken: return {"trades": 0}
    rr, ss = r[taken], s[taken]; a = np.maximum(RISK * rr / ss, -0.95); eq = np.cumprod(1 + a)
    return {"trades": len(taken), "win": round(float((rr > 0).mean()) * 100, 1), "avg": round(float(rr.mean()) * 100, 3),
            "yearly": round(math.expm1(np.log1p(a).sum() / days * 365) * 100, 1),
            "maxDrop": round(float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max()) * 100, 1)}

def run(asset):
    g, F = build(asset); cols = list(F.columns); X = F.values; yrs = g.index.year.values; out = {"asset": asset, "from": str(g.index[0].date()), "rows": len(g), "years": {}}
    pool = {(v, sd): [] for v in V for sd in (1, -1)}; base = {"hold": [], "trend": []}
    first = max(int(yrs.min()) + 8, 2010)
    for Y in range(first, int(yrs.max()) + 1):
        D = np.where(yrs < Y - 1)[0]; Vv = np.where(yrs == Y - 1)[0]; T = np.where(yrs == Y)[0]
        if len(T) < 40: continue
        rec = {"price": round(float(g.c.values[T[-1]] / g.c.values[T[0]] - 1) * 100, 1)}
        ma = (g.c / g.c.rolling(50).mean()).values; ma2 = (g.c.rolling(50).mean() / g.c.rolling(200).mean()).values
        tr = np.where(ma2[T] > 1, 1, -1) * np.log(g.c.shift(-1) / g.c).values[T]; rec["trend50_200"] = round(float(np.nansum(tr)) * 100, 1)
        for v, (tk, sk, hz) in V.items():
            for sd in (1, -1):
                y, r, s = label(g, sd, tk, sk, hz); okD = D[np.isfinite(y[D]) & (np.isfinite(X[D]).mean(1) > 0.6)]
                okV = Vv[np.isfinite(y[Vv])]; okT = T[np.isfinite(y[T])]
                if len(okD) < 1000: continue
                m = HGB(max_iter=200, learning_rate=0.04, max_leaf_nodes=8, min_samples_leaf=60, l2_regularization=1.0, random_state=0).fit(X[okD], y[okD])
                pV = m.predict_proba(X[okV])[:, 1]; pT = m.predict_proba(X[okT])[:, 1]
                be = lambda ii: (s[ii] + FEE) / (tk / sk * s[ii] + s[ii])
                best, bm = None, None
                for mg in (0.0, 0.03, 0.06, 0.09, 0.12):
                    pk = okV[pV >= be(okV) + mg]
                    if len(pk) >= 8:
                        st = sim(pk, r[pk], s[pk], hz, 365)
                        if st["trades"] >= 4 and (best is None or st["yearly"] > best): best, bm = st["yearly"], mg
                key = f"{v}{'L' if sd > 0 else 'S'}"
                rnd = sim(okT, r[okT], s[okT], hz, 365)                # every day taken: what the barriers give with no skill
                if bm is None or best <= 0: rec[key] = {"off": True, "val": best, "allDays": rnd.get("yearly")}; continue
                pk = okT[pT >= be(okT) + bm]; st = sim(pk, r[pk], s[pk], hz, 365)
                rec[key] = {**st, "bar": bm, "val": best, "allDays": rnd.get("yearly")}
                if st["trades"]: pool[(v, sd)].append(st)
        out["years"][Y] = rec; print(Y, json.dumps(rec), flush=True)
    summ = {}
    for (v, sd), L in pool.items():
        if L:
            n = sum(x["trades"] for x in L); yl = [x["yearly"] for x in L]
            summ[f"{v}{'L' if sd > 0 else 'S'}"] = {"yearsOn": len(L), "trades": n, "medianYear": float(np.median(yl)), "worstYear": min(yl), "yearsPositive": sum(y > 0 for y in yl),
                                                     "win": round(sum(x["win"] * x["trades"] for x in L) / max(n, 1), 1), "avgPerTrade": round(sum(x["avg"] * x["trades"] for x in L) / max(n, 1), 3)}
    out["summary"] = summ; print("SUMMARY", asset, json.dumps(summ), flush=True); return out

if __name__ == "__main__":
    res = [run("gold"), run("silver")]
    json.dump(res, open("labtest.json", "w"), indent=1, default=str)
