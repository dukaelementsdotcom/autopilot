"""The judge. Turns a hypothesis into trades on the hourly panel and grades it on three separate stretches of time:
  discovery  = everything older than the last ~16 months   (where ideas are allowed to be found)
  validation = the year after that                           (must hold up here, and beat random entries)
  holdout    = the newest 4 months                           (a final exam, used once, only for promotion)
Then champions earn a LIVE paper record on data that did not exist when they were promoted."""
import hashlib, json, numpy as np, pandas as pd
from . import config as C

UNI = {"BTC": ["BTC"], "MAJ": ["BTC", "ETH", "SOL"], "ALL": None}
DIRS = ("long", "short", "with", "against")
HOUR = 3600

def canon(h): return json.dumps(h, sort_keys=True, separators=(",", ":"))
def hid(h): return hashlib.sha1(canon(h).encode()).hexdigest()[:16]
def family(h):
    if h["kind"] == "xsec": return f"xsec|{h['rank']}|{h['order']}"
    return f"event|{h['uni']}|{h['dir']}|{h['exit']}|" + "+".join(sorted(c["f"] for c in h["when"]))

def describe(h, desc=None):
    d = desc or {}
    def cond(c):
        name = d.get(c["f"].removeprefix("p_"), c["f"]); lo, hi = c["lo"], c["hi"]
        if c["f"].startswith(("p_", "x_")):
            if hi >= 1 and lo > 0: return f"{name} in its top {round((1 - lo) * 100)}%"
            if lo <= 0 and hi < 1: return f"{name} in its bottom {round(hi * 100)}%"
            return f"{name} between the {round(lo * 100)}th and {round(hi * 100)}th percentile"
        return f"{c['f']} {lo}–{hi}"
    if h["kind"] == "xsec":
        return f"Every {h['rebal']}h: rank coins by {d.get(h['rank'].removeprefix('x_'), h['rank'])}; BUY the {'lowest' if h['order'] == 'low_long' else 'highest'} {round(h['frac'] * 100)}%, SHORT the opposite end"
    ex = C.EXITS[h["exit"]]; exs = f"hold {ex[1]}h" if ex[0] == "ret" else f"target +{ex[0] * 100:g}% / stop −{ex[1] * 100:g}% / max {ex[2]}h"
    who = {"BTC": "BTC", "MAJ": "BTC, ETH, SOL", "ALL": "all 21 coins"}[h["uni"]]
    side = {"long": "go LONG", "short": "go SHORT", "with": "follow the last hour's move", "against": "bet against the last hour's move"}[h["dir"]]
    return f"{who}: when " + " AND ".join(cond(c) for c in h["when"]) + f" → {side}; {exs}"

def valid(h, cols):
    try:
        if h["kind"] == "xsec":
            return h["rank"] in cols and h["order"] in ("low_long", "high_long") and 0.1 <= h["frac"] <= 0.5 and h["rebal"] in (24, 84, 168)
        if h["kind"] != "event" or h["uni"] not in UNI or h["dir"] not in DIRS or h["exit"] not in C.EXITS: return False
        if not (1 <= len(h["when"]) <= 3): return False
        for c in h["when"]:
            if c["f"] not in cols or not (c["lo"] < c["hi"]): return False
            if c["f"].startswith(("p_", "x_")) and not (0 <= c["lo"] < c["hi"] <= 1 and c["hi"] - c["lo"] >= 0.04): return False
        return True
    except Exception: return False

class Judge:
    def __init__(self, P):
        self.P = P
        lab = P["L_r24"].notna()
        self.t_end = int(P.loc[lab, "t"].max())
        self.hold0 = self.t_end - C.HOLDOUT_DAYS * 86400
        self.val0 = self.hold0 - C.VALID_DAYS * 86400
        self.cols = set(P.columns)
        self.coin = P.coin.values; self.t = P.t.values; self.r1 = P.r1.values
        self._fwd = {}

    def _mask(self, h):
        P = self.P; m = np.ones(len(P), bool)
        u = UNI[h.get("uni", "ALL")]
        if u: m &= P.coin.isin(u).values
        for c in h.get("when", []):
            x = P[c["f"]].values; m &= (x >= c["lo"]) & (x <= c["hi"])
        return m

    def trades(self, h, t_from=None):
        """event rule → (times, returns) after the per-coin cooldown"""
        P = self.P; m = self._mask(h)
        side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": np.sign(self.r1), "against": -np.sign(self.r1)}[h["dir"]]
        L, S = P["L_" + h["exit"]].values, P["S_" + h["exit"]].values
        r = np.where(side > 0, L, np.where(side < 0, S, np.nan))
        m &= ~np.isnan(r)
        if t_from is not None: m &= self.t >= t_from
        idx = np.flatnonzero(m)
        if len(idx) == 0: return np.array([]), np.array([])
        cool = h.get("cool", 24) * HOUR; keep = []
        order = np.lexsort((self.t[idx], self.coin[idx])); idx = idx[order]
        last_coin, last_t = None, -1e18
        for i in idx:
            c, t = self.coin[i], self.t[i]
            if c != last_coin: last_coin, last_t = c, -1e18
            if t - last_t >= cool: keep.append(i); last_t = t
        keep = np.array(keep); o = np.argsort(self.t[keep]); keep = keep[o]
        return self.t[keep], r[keep]

    def xsec_returns(self, h, t_from=None):
        """cross-sectional rule → one return per rebalance (long basket minus short basket, fees for full turnover)"""
        R = h["rebal"]
        if R not in self._fwd:
            P = self.P; px = P.px.values
            nxt = P.groupby("coin").px.shift(-R).values
            self._fwd[R] = nxt / px - 1
        P = self.P; fwd = self._fwd[R]
        times = np.unique(self.t[self.t % (R * HOUR) == 0])
        if t_from is not None: times = times[times >= t_from]
        rank = P[h["rank"]].values
        df = pd.DataFrame({"t": self.t, "k": rank, "f": fwd})
        df = df[df.t.isin(times)].dropna()
        if h.get("when"):
            ok = self._mask({"when": h["when"], "uni": "ALL"})
            df = df[ok[df.index]]
        out_t, out_r = [], []
        for t, g in df.groupby("t"):
            if len(g) < 8: continue
            g = g.sort_values("k"); k = max(1, int(len(g) * h["frac"]))
            lo, hi = g.f.values[:k].mean(), g.f.values[-k:].mean()
            out_t.append(t); out_r.append((lo - hi if h["order"] == "low_long" else hi - lo) - 2 * C.FEE)
        return np.array(out_t), np.array(out_r)

    def series(self, h, t_from=None):
        return self.xsec_returns(h, t_from) if h["kind"] == "xsec" else self.trades(h, t_from)

    @staticmethod
    def stats(t, r):
        n = len(r)
        if n == 0: return {"n": 0}
        day = (t // 86400).astype(np.int64)
        d = pd.Series(r).groupby(day).mean().values      # one value per day, so many coins on one day don't fake significance
        sd = d.std(ddof=1) if len(d) > 2 else 0
        tt = float(d.mean() / (sd / np.sqrt(len(d)))) if sd > 0 else 0.0
        return {"n": int(n), "days": int(len(d)), "mean": round(float(r.mean()) * 100, 4), "hit": round(float((r > 0).mean()) * 100, 1), "t": round(tt, 2)}

    def null_p(self, h, t, r, lo, hi, reps=200, seed=1):
        """how often do RANDOM entries (same coins, same side rule, same exit, same period) do as well?"""
        if h["kind"] == "xsec":
            rng = np.random.default_rng(seed); obs = r.mean(); beats = 0
            # random baskets: shuffle the ranking feature within each rebalance
            P = self.P; R = h["rebal"]; fwd = self._fwd[R]
            times = np.unique(self.t[(self.t % (R * HOUR) == 0) & (self.t >= lo) & (self.t < hi)])
            df = pd.DataFrame({"t": self.t, "f": fwd}); df = df[df.t.isin(times)].dropna()
            groups = [g.f.values for _, g in df.groupby("t") if len(g) >= 8]
            if not groups: return 1.0
            for _ in range(reps):
                vals = []
                for g in groups:
                    p = rng.permutation(g); k = max(1, int(len(g) * h["frac"])); vals.append(p[:k].mean() - p[-k:].mean() - 2 * C.FEE)
                if np.mean(vals) >= obs: beats += 1
            return round((beats + 1) / (reps + 1), 3)
        P = self.P; m = (self.t >= lo) & (self.t < hi)
        u = UNI[h["uni"]]
        if u: m &= P.coin.isin(u).values
        side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": np.sign(self.r1), "against": -np.sign(self.r1)}[h["dir"]]
        L, S = P["L_" + h["exit"]].values, P["S_" + h["exit"]].values
        pool = np.where(side > 0, L, np.where(side < 0, S, np.nan))[m]; pool = pool[~np.isnan(pool)]
        if len(pool) < len(r) * 2: return 1.0
        rng = np.random.default_rng(seed); obs = r.mean()
        sims = np.array([rng.choice(pool, len(r), replace=False).mean() for _ in range(reps)])
        return round(float(((sims >= obs).sum() + 1) / (reps + 1)), 3)

    def evaluate(self, h):
        """discovery + validation (the holdout stays untouched here)"""
        t, r = self.series(h)
        d = t < self.val0; v = (t >= self.val0) & (t < self.hold0)
        disc, val = self.stats(t[d], r[d]), self.stats(t[v], r[v])
        rec = {"disc": disc, "val": val}
        min_d = C.MIN_DISC_TRADES if h["kind"] == "event" else 60
        min_v = C.MIN_VAL_TRADES if h["kind"] == "event" else 30
        if disc["n"] < min_d or disc["mean"] <= 0 or disc["t"] < C.DISC_T: rec["status"] = "dead_disc"; return rec
        if val["n"] < min_v or val["mean"] <= 0 or val["t"] < C.VAL_T:
            rec["status"] = "near" if val.get("mean", -1) > 0 else "dead_val"; return rec
        p = self.null_p(h, t[v], r[v], self.val0, self.hold0); val["p_random"] = p
        rec["status"] = "validated" if p < 0.05 else "near"
        return rec

    def holdout(self, h):
        t, r = self.series(h); m = t >= self.hold0
        return self.stats(t[m], r[m])

    def live(self, h, since):
        t, r = self.series(h, t_from=since + HOUR)
        s = self.stats(t, r); s["last"] = [[int(a), round(float(b) * 100, 3)] for a, b in zip(t[-12:], r[-12:])]
        return s

    def signals_now(self, h, hours=36):
        """entries that fired in the newest data (their outcome may still be pending)"""
        if h["kind"] == "xsec": return []
        P = self.P; m = self._mask(h) & (self.t > self.P.t.max() - hours * HOUR)
        side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": np.sign(self.r1), "against": -np.sign(self.r1)}[h["dir"]]
        idx = np.flatnonzero(m)
        return [{"coin": str(self.coin[i]), "t": int(self.t[i]), "side": "LONG" if side[i] > 0 else "SHORT", "px": float(P.px.values[i])} for i in idx[-10:] if side[i] != 0]

    # ── the "undeniable" bar: only rules that pass ALL of this ever trigger a message ─────────────
    def certify(self, h, since, n_tested, cols, mutate=None):
        """stress test under every condition we can measure. returns (passed, checks)"""
        t, r = self.series(h); ck = {}
        if len(r) < 50: return False, {"enough history": False}
        L = self.stats(t[t > since], r[t > since]); A = self.stats(t, r)
        ck[f"live: ≥100 paper trades over ≥90 days (has {L.get('n', 0)} / {L.get('days', 0)} days)"] = L.get("n", 0) >= 100 and (t[-1] - since) >= 90 * 86400
        ck[f"live: t ≥ 3 (has {L.get('t', 0)})"] = L.get("t", 0) >= 3
        need = round(float(np.sqrt(2 * np.log(max(n_tested, 10)))), 2)       # luck bar grows with every idea ever tried
        ck[f"whole history: t ≥ {need}, the luck bar for {n_tested:,} ideas tested (has {A['t']})"] = A["t"] >= need
        yrs = pd.Series(r).groupby(pd.to_datetime(t, unit="s").year).agg(["mean", "size"])
        yrs = yrs[yrs["size"] >= 10]
        ck[f"profitable in every calendar year ({int((yrs['mean'] > 0).sum())}/{len(yrs)})"] = len(yrs) >= 4 and bool((yrs["mean"] > 0).all())
        ck["still profitable with double fees"] = float(r.mean()) - C.FEE > 0
        if h["kind"] == "event":
            P = self.P; pos = {int(a): i for i, a in enumerate(t)}
            ix = self._entry_index(h)
            tr = P["tr50"].values[ix]; vo = P["p_vol24"].values[ix]
            for name, m in (("uptrend", tr > 0), ("downtrend", tr <= 0), ("calm markets", vo < 0.5), ("wild markets", vo >= 0.5)):
                ok = m.sum() >= 20 and r[m].mean() > 0
                ck[f"profitable in {name}"] = bool(ok)
            coins = pd.Series(r).groupby(self.coin[ix]).agg(["mean", "size"]); coins = coins[coins["size"] >= 15]
            if len(coins) > 1: ck[f"profitable on ≥70% of coins ({int((coins['mean'] > 0).sum())}/{len(coins)})"] = (coins["mean"] > 0).mean() >= 0.7
            if mutate:
                rng = np.random.default_rng(3); good = tot = 0
                for _ in range(12):
                    v = mutate(h, cols, __import__("random").Random(int(rng.integers(1e9))))
                    if v["kind"] != "event" or v["exit"] != h["exit"]: continue
                    _, rv = self.series(v)
                    if len(rv) >= 30: tot += 1; good += rv.mean() > 0
                ck[f"nearby variants also profitable ({good}/{tot})"] = tot >= 4 and good / tot >= 0.75
        ck = {k: bool(v) for k, v in ck.items()}; return all(ck.values()), ck

    def _entry_index(self, h):
        """row indices of the trades returned by trades(h) (same order)"""
        P = self.P; m = self._mask(h)
        side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": np.sign(self.r1), "against": -np.sign(self.r1)}[h["dir"]]
        L, S = P["L_" + h["exit"]].values, P["S_" + h["exit"]].values
        m &= ~np.isnan(np.where(side > 0, L, np.where(side < 0, S, np.nan)))
        idx = np.flatnonzero(m); cool = h.get("cool", 24) * HOUR; keep = []
        idx = idx[np.lexsort((self.t[idx], self.coin[idx]))]; lc, lt = None, -1e18
        for i in idx:
            if self.coin[i] != lc: lc, lt = self.coin[i], -1e18
            if self.t[i] - lt >= cool: keep.append(i); lt = self.t[i]
        keep = np.array(keep, dtype=int); return keep[np.argsort(self.t[keep], kind="stable")]
