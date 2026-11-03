"""The judge. Turns a hypothesis into trades on the hourly panel and grades it on three separate stretches of time:
  discovery  = everything older than the last ~16 months   (where ideas are allowed to be found)
  validation = the year after that                           (must hold up here, and beat random entries)
  holdout    = the newest 4 months                           (a final exam, used once, only for promotion)
Then champions earn a LIVE paper record on data that did not exist when they were promoted."""
import os, hashlib, json, numpy as np, pandas as pd
from . import config as C

UNI = {"BTC": ["BTC"], "MAJ": ["BTC", "ETH", "SOL"], "ALL": None}
DIRS = ("long", "short", "with", "against")
HOUR = 3600

def canon(h): return json.dumps(h, sort_keys=True, separators=(",", ":"))

# ── exits: a menu name (precomputed) OR dials the machine can set itself ───────────────────────────
DIALS = {"tp": (0.003, 0.2), "sl": (0.003, 0.2), "trail": (0.003, 0.1), "arm": (0.0, 0.1)}
VOLDIALS = (0.2, 8.0)
def exit_ok(ex):
    if isinstance(ex, str): return ex in C.EXITS
    if isinstance(ex, dict) and ex.get("unit") == "vol":
        if set(ex) - set(DIALS) - {"hold", "unit"} or not (isinstance(ex.get("hold"), int) and 1 <= ex["hold"] <= 168): return False
        if not any(ex.get(k) for k in ("tp", "sl", "trail")): return False
        if any(ex.get(k) is not None and not (isinstance(ex[k], (int, float)) and VOLDIALS[0] <= ex[k] <= VOLDIALS[1]) for k in ("tp", "sl", "trail")): return False
        if ex.get("arm") is not None and (ex.get("trail") is None or not (0 <= ex["arm"] <= VOLDIALS[1])): return False
        return True
    if not isinstance(ex, dict) or set(ex) - set(DIALS) - {"hold"}: return False
    if not (isinstance(ex.get("hold"), int) and 1 <= ex["hold"] <= 168): return False
    for k, (lo, hi) in DIALS.items():
        v = ex.get(k)
        if v is not None and not (isinstance(v, (int, float)) and lo <= v <= hi): return False
    return not (ex.get("arm") is not None and ex.get("trail") is None)
def exit_key(ex): return ex if isinstance(ex, str) else ("v" if ex.get("unit") == "vol" else "d") + "_".join(f"{k}{ex[k]:g}" for k in ("tp", "sl", "trail", "arm", "hold") if ex.get(k) is not None)
def scale_exit(ex, daily_vol):
    """turn volatility-unit dials into % dials for one trade (daily_vol = the coin's normal daily move, e.g. 0.03)"""
    if not (isinstance(ex, dict) and ex.get("unit") == "vol"): return ex
    if not daily_vol or daily_vol != daily_vol or daily_vol <= 0: return None
    out = {"hold": ex["hold"]}
    for k in ("tp", "sl", "trail", "arm"):
        if ex.get(k) is not None: out[k] = float(np.clip(ex[k] * daily_vol, 0.003 if k != "arm" else 0.0, 0.5))
    return out
def exit_text(ex):
    if isinstance(ex, dict) and ex.get("unit") == "vol":
        parts = []
        if ex.get("tp"): parts.append(f"target {ex['tp']:g}× the coin's normal daily move")
        if ex.get("sl"): parts.append(f"stop {ex['sl']:g}× its daily move (outside normal noise)")
        if ex.get("trail"): parts.append(f"trailing stop {ex['trail']:g}× daily move" + (f" once up {ex['arm']:g}×" if ex.get("arm") else ""))
        parts.append(f"max {ex['hold']}h"); return " / ".join(parts)
    if isinstance(ex, str):
        e = C.EXITS[ex]; return f"hold {e[1]}h" if e[0] == "ret" else f"target +{e[0] * 100:g}% / stop −{e[1] * 100:g}% / max {e[2]}h"
    parts = []
    if ex.get("tp"): parts.append(f"target +{ex['tp'] * 100:g}%")
    if ex.get("sl"): parts.append(f"stop −{ex['sl'] * 100:g}%")
    if ex.get("trail"): parts.append(f"trailing stop {ex['trail'] * 100:g}%" + (f" once up {ex['arm'] * 100:g}%" if ex.get("arm") else ""))
    parts.append(f"max {ex['hold']}h"); return " / ".join(parts)

def simulate(entry, H, L, Cl, j0, j_last, side, ex):
    """replay one trade bar by bar from bar j0 (entry at the open of j0). Stops are checked before targets inside a bar (pessimistic);
    a trailing stop only uses highs/lows of bars that are already finished. returns (exit bar, exit price, how, return after fees) or None"""
    if isinstance(ex, str):
        e = C.EXITS[ex]
        if e[0] == "ret": tp = sl = tr = arm = None; hold = e[1]
        else: tp, sl, hold = e; tr = arm = None
    else: tp, sl, tr, arm, hold = ex.get("tp"), ex.get("sl"), ex.get("trail"), ex.get("arm"), ex["hold"]
    end = j0 + hold - 1
    if end > j_last: return None
    best = entry; armed = arm is None
    for j in range(j0, end + 1):
        hi, lo = H[j], L[j]
        stops = []
        if sl: stops.append(entry * (1 - side * sl))
        if tr and armed: stops.append(best * (1 - side * tr))
        if stops:
            st = max(stops) if side > 0 else min(stops)
            if (side > 0 and lo <= st) or (side < 0 and hi >= st):
                return j, st, ("stop-loss" if sl and st == entry * (1 - side * sl) else "trailing stop"), side * (st / entry - 1) - C.FEE
        if tp:
            tg = entry * (1 + side * tp)
            if (side > 0 and hi >= tg) or (side < 0 and lo <= tg): return j, tg, "take-profit", tp - C.FEE
        best = max(best, hi) if side > 0 else min(best, lo)
        if not armed and (side * (best / entry - 1) >= arm): armed = True
    return end, Cl[end], f"time ({hold}h)", side * (Cl[end] / entry - 1) - C.FEE

def lev_returns(r, lev=10):
    """each trade as a share of its margin with `lev`x isolated leverage: liquidated (-100% of the margin) about 1% before the
    theoretical -1/lev move, never worse than -100%"""
    r = np.asarray(r, float)
    if lev <= 1: return r
    liq = 1.0 / lev - 0.01
    return np.where(r <= -liq, -1.0, np.maximum(-1.0, lev * r))

def trade_lev(h): return 1 if h.get("kind") == "rotate" else 10

FORBIDDEN = ("L_", "S_", "_")
def feature_ok(f, cols):
    return f in cols and not f.startswith(FORBIDDEN) and f not in ("px", "t", "coin")
def hid(h): return hashlib.sha1(canon(h).encode()).hexdigest()[:16]
def family(h):
    if h["kind"] == "xsec": return f"xsec|{h['rank']}|{h['order']}"
    if h["kind"] == "pair": return f"pair|{h['a']}-{h['b']}|{h['mode']}"
    if h["kind"] == "carry": return f"carry|{h['uni']}"
    if h["kind"] == "union": return "union|" + "|".join(sorted(family(q) for q in h["parts"]))
    if h["kind"] == "rotate": return f"rotate|{h['rank']}|{h['order']}|{h['hold']}"
    return f"event|{h['uni']}|{h['dir']}|{exit_key(h['exit'])}|" + "+".join(sorted(c["f"] for c in h["when"]))

def describe(h, desc=None):
    d = desc or {}
    def cond(c):
        name = d.get(c["f"].removeprefix("p_"), c["f"]); lo, hi = c["lo"], c["hi"]
        if c["f"].startswith(("p_", "x_")):
            if hi >= 1 and lo > 0: return f"{name} in its top {round((1 - lo) * 100)}%"
            if lo <= 0 and hi < 1: return f"{name} in its bottom {round(hi * 100)}%"
            return f"{name} between the {round(lo * 100)}th and {round(hi * 100)}th percentile"
        return f"{name} between {lo:g} and {hi:g}"
    if h["kind"] == "xsec":
        return f"Every {h['rebal']}h: rank coins by {d.get(h['rank'].removeprefix('x_'), h['rank'])}; BUY the {'lowest' if h['order'] == 'low_long' else 'highest'} {round(h['frac'] * 100)}%, SHORT the opposite end"
    if h["kind"] == "rotate":
        f = d.get(h["rank"].removeprefix("p_"), h["rank"])
        filt = (" among coins where " + " AND ".join(cond(c) for c in h["when"])) if h.get("when") else ""
        stop = f", stop −{h['stop'] * 100:g}%" if h.get("stop") else ", no stop"
        return f"ALL-IN, NO LEVERAGE: every {h['hold']}h put the whole account into the one coin with the {'highest' if h['order'] == 'high' else 'lowest'} {f}{filt}; sell after {h['hold']}h{stop}; then pick again"
    if h["kind"] == "union":
        return "TEAM of " + str(len(h["parts"])) + " rules (trade when ANY fires): " + "  ‖  ".join(f"({describe(q, desc)})" for q in h["parts"])
    if h["kind"] == "pair":
        act = "bet the gap CLOSES (short the stretched coin, buy the other)" if h["mode"] == "revert" else "bet the gap keeps WIDENING"
        return f"Pair {h['a']}/{h['b']}: when their price ratio is {h['z']:g}+ standard deviations from its {h['look'] // 24}-day normal → {act}; hold {h['hold']}h (market-neutral)"
    if h["kind"] == "carry":
        who = {"BTC": "BTC", "MAJ": "BTC, ETH, SOL", "ALL": "all coins"}[h["uni"]]
        return f"Funding carry on {who}: when the last 7 days' funding pays over {h['enter'] * 100:g}%/yr, hold spot LONG + perp SHORT (price risk cancels) and collect funding; leave when it drops below {h['leave'] * 100:g}%/yr"
    exs = exit_text(h["exit"])
    who = {"BTC": "BTC", "MAJ": "BTC, ETH, SOL", "ALL": "all coins"}[h["uni"]]
    side = {"long": "go LONG", "short": "go SHORT", "with": "follow the last hour's move", "against": "bet against the last hour's move"}[h["dir"]]
    return f"{who}: when " + " AND ".join(cond(c) for c in h["when"]) + f" → {side}; {exs}"

def valid(h, cols):
    try:
        if h["kind"] == "xsec":
            return feature_ok(h["rank"], cols) and h["order"] in ("low_long", "high_long") and 0.1 <= h["frac"] <= 0.5 and h["rebal"] in (24, 84, 168)
        if h["kind"] == "pair":
            return h["a"] != h["b"] and h["a"] in C.COINS and h["b"] in C.COINS and 1.0 <= h["z"] <= 4.0 and h["look"] in (168, 336, 720, 1440) and h["hold"] in (24, 72, 168) and h["mode"] in ("revert", "trend")
        if h["kind"] == "rotate":
            if not (feature_ok(h["rank"], cols) and h["order"] in ("high", "low") and h["hold"] in (24, 48, 72)): return False
            if h.get("stop") is not None and not (0.02 <= h["stop"] <= 0.2): return False
            for c in h.get("when", []) or []:
                if not feature_ok(c["f"], cols) or not (c["lo"] < c["hi"]): return False
            return len(h.get("when", []) or []) <= 2
        if h["kind"] == "union":
            return isinstance(h.get("parts"), list) and 2 <= len(h["parts"]) <= 3 and all(isinstance(q, dict) and q.get("kind") == "event" and valid(q, cols) for q in h["parts"])
        if h["kind"] == "carry":
            return h["uni"] in UNI and 0 <= h["leave"] < h["enter"] <= 2.0
        if h["kind"] != "event" or h["uni"] not in UNI or h["dir"] not in DIRS or not exit_ok(h["exit"]): return False
        if not (1 <= len(h["when"]) <= 3): return False
        for c in h["when"]:
            if not feature_ok(c["f"], cols) or not (c["lo"] < c["hi"]): return False
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
        self.sgn = np.nan_to_num(np.sign(self.r1))
        self.dvol = (P["vol24"].values.astype(float) * np.sqrt(24)) if "vol24" in P else np.full(len(P), np.nan)   # normal daily move
        self._blk = {}
        cs = self.coin
        starts = np.r_[0, np.flatnonzero(cs[1:] != cs[:-1]) + 1]
        for a_ in starts: self._blk[str(cs[a_])] = (int(a_), int(self.t[a_]))      # direction of the last hour's move; 0 when it is missing (such hours are skipped)
        self._fwd = {}
        self.O = P.px.values.astype(float)
        self.H = P["_hi"].values.astype(float) if "_hi" in P else self.O; self.L = P["_lo"].values.astype(float) if "_lo" in P else self.O
        self.Cl = P["_cl"].values.astype(float) if "_cl" in P else self.O
        blk = np.r_[np.flatnonzero(self.coin[1:] != self.coin[:-1]), len(P) - 1]       # last row of each coin's block
        self.last = np.repeat(blk, np.diff(np.r_[-1, blk]))

    def ret1(self, i, sd, ex):
        """return of one entry (row i, side sd) with exit ex"""
        if isinstance(ex, str):
            return self.P["L_" + ex].values[i] if sd > 0 else self.P["S_" + ex].values[i]
        ex = scale_exit(ex, self.dvol[i])
        if ex is None: return np.nan
        s_ = simulate(self.O[i], self.H, self.L, self.Cl, i, self.last[i], sd, ex)
        return np.nan if s_ is None else s_[3]

    def _mask(self, h):
        P = self.P; m = np.ones(len(P), bool)
        u = UNI[h.get("uni", "ALL")]
        if u: m &= P.coin.isin(u).values
        for c in h.get("when", []):
            x = P[c["f"]].values; m &= (x >= c["lo"]) & (x <= c["hi"])
        return m

    def trades(self, h, t_from=None):
        """event rule → (times, returns) after the per-coin cooldown"""
        P = self.P; m = self._mask(h); ex = h["exit"]
        side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": self.sgn, "against": -self.sgn}[h["dir"]]
        if isinstance(ex, str):
            L, S = P["L_" + ex].values, P["S_" + ex].values
            r = np.where(side > 0, L, np.where(side < 0, S, np.nan))
        else:
            m &= (side != 0) & (np.arange(len(P)) + ex["hold"] - 1 <= self.last); r = None
            if ex.get("unit") == "vol": m &= ~np.isnan(self.dvol)
        if r is not None: m &= ~np.isnan(r)
        if t_from is not None: m &= self.t >= t_from
        idx = np.flatnonzero(m)
        if len(idx) == 0: self._last_keep = np.array([], dtype=int); return np.array([]), np.array([])
        cool = h.get("cool", 24) * HOUR; keep = []
        order = np.lexsort((self.t[idx], self.coin[idx])); idx = idx[order]
        last_coin, last_t = None, -1e18
        for i in idx:
            c, t = self.coin[i], self.t[i]
            if c != last_coin: last_coin, last_t = c, -1e18
            if t - last_t >= cool: keep.append(i); last_t = t
        keep = np.array(keep); o = np.argsort(self.t[keep]); keep = keep[o]
        if r is None:
            rr = np.array([self.ret1(i, int(side[i]), ex) for i in keep]); ok = ~np.isnan(rr) if len(rr) else np.array([], bool)
            self._last_keep = keep[ok] if len(rr) else keep; return self.t[keep][ok], rr[ok]
        self._last_keep = keep
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
        k = h["kind"]
        if k == "xsec": return self.xsec_returns(h, t_from)
        if k == "pair": return self.pair_returns(h, t_from)
        if k == "carry": return self.carry_returns(h, t_from)
        if k == "union": return self.union_trades(h, t_from)
        if k == "rotate": return self.rotate_trades(h, t_from)
        return self.trades(h, t_from)

    def _pxwide(self):
        if not hasattr(self, "_W"): self._W = self.P.pivot_table(index="t", columns="coin", values="px").sort_index()
        return self._W

    def pair_returns(self, h, t_from=None, z_override=None, rand=None):
        """market-neutral pair: spread = log(A) - log(B); enter when |z| >= k; one position at a time; hold fixed hours"""
        W = self._pxwide()
        if h["a"] not in W or h["b"] not in W: return np.array([]), np.array([])
        a, b = W[h["a"]].values, W[h["b"]].values; T = W.index.values
        sp = pd.Series(np.log(a) - np.log(b))
        mu, sd = sp.rolling(h["look"], min_periods=h["look"] // 2).mean().shift(1), sp.rolling(h["look"], min_periods=h["look"] // 2).std().shift(1)
        z = ((sp - mu) / sd).values; H = h["hold"]; k = h["z"] if z_override is None else z_override
        ts, rs = [], []; i = 0; n = len(T)
        while i < n - H:
            if (t_from is None or T[i] >= t_from) and not np.isnan(z[i]) and abs(z[i]) >= k and not np.isnan(a[i + H]) and not np.isnan(b[i + H]):
                side = -np.sign(z[i]) if h["mode"] == "revert" else np.sign(z[i])        # +1 = long A / short B
                if rand is not None: side = rand.choice([-1, 1])
                ts.append(T[i]); rs.append(side * ((a[i + H] / a[i] - 1) - (b[i + H] / b[i] - 1)) - 4 * C.FEE); i += H
            else: i += 1
        return np.array(ts, dtype=np.int64), np.array(rs)

    def _funding(self, coin):
        if not hasattr(self, "_F"): self._F = {}
        if coin not in self._F:
            p = f"{C.DATA_DIR}/raw/{coin}_f.parquet"
            self._F[coin] = pd.read_parquet(p).sort_values("t") if os.path.exists(p) else None
        return self._F[coin]

    def carry_returns(self, h, t_from=None, always=False):
        """spot long + perp short: earns each funding payment while in the trade; 4 fills (0.1% each) per round trip.
           decision uses only the trailing 7 days of funding (21 payments) known BEFORE each payment."""
        coins = UNI[h["uni"]] or C.COINS; ts, rs = [], []
        for c in coins:
            F = self._funding(c)
            if F is None or len(F) < 60: continue
            fr = F.fr.values; T = F.t.values; per_year = 3 * 365
            trail = pd.Series(fr).rolling(21).mean().shift(1).values * per_year
            inpos = False
            for i in range(len(fr)):
                if np.isnan(trail[i]): continue
                r = 0.0
                if not inpos and (always or trail[i] > h["enter"]): inpos = True; r -= 2 * C.FEE
                elif inpos and not always and trail[i] < h["leave"]:
                    inpos = False
                    if t_from is None or T[i] >= t_from: ts.append(T[i]); rs.append(r - 2 * C.FEE)
                    continue
                if inpos: r += fr[i]
                if inpos and (t_from is None or T[i] >= t_from): ts.append(T[i]); rs.append(r)
        if not ts: return np.array([], dtype=np.int64), np.array([])
        o = np.argsort(ts, kind="stable"); return np.array(ts, dtype=np.int64)[o], np.array(rs)[o]

    @staticmethod
    def stats(t, r, lev=10):
        n = len(r)
        if n == 0: return {"n": 0}
        day = (t // 86400).astype(np.int64)
        d = pd.Series(r).groupby(day).mean().values      # one value per day, so many coins on one day don't fake significance
        sd = d.std(ddof=1) if len(d) > 2 else 0
        tt = float(d.mean() / (sd / np.sqrt(len(d)))) if sd > 0 else 0.0
        w, l = r[r > 0], r[r <= 0]; L = lev_returns(r, lev)
        return {"n": int(n), "days": int(len(d)), "mean": round(float(r.mean()) * 100, 4), "hit": round(float((r > 0).mean()) * 100, 1), "t": round(tt, 2),
                "avgWin": round(float(w.mean()) * 100, 3) if len(w) else 0.0, "avgLoss": round(float(l.mean()) * 100, 3) if len(l) else 0.0,
                "payoff": round(float(w.mean() / -l.mean()), 2) if len(w) and len(l) and l.mean() < 0 else 99.0,
                "lev": lev, "levMean": round(float(L.mean()) * 100, 2), "liq": int((L <= -1).sum()), "stress": round(float((r - C.FEE).mean()) * 100, 4)}

    def _pool(self, h, lo, hi, n):
        """returns of RANDOM entries: same coins, same side rule, same exit, same period (dial exits: a random sample)"""
        P = self.P; m = (self.t >= lo) & (self.t < hi)
        u = UNI[h["uni"]]
        if u: m &= P.coin.isin(u).values
        side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": self.sgn, "against": -self.sgn}[h["dir"]]
        ex = h["exit"]
        if isinstance(ex, str):
            pool = np.where(side > 0, P["L_" + ex].values, np.where(side < 0, P["S_" + ex].values, np.nan))[m]; return pool[~np.isnan(pool)]
        idx = np.flatnonzero(m & (side != 0)); rng = np.random.default_rng(5)
        idx = rng.choice(idx, min(len(idx), max(600, 4 * n)), replace=False) if len(idx) else idx
        pool = np.array([self.ret1(i, int(side[i]), ex) for i in idx]); return pool[~np.isnan(pool)]

    def null_p(self, h, t, r, lo, hi, reps=200, seed=1):
        """how often do RANDOM entries (same coins, same side rule, same exit, same period) do as well?"""
        if h["kind"] == "pair":                      # same pair, random side, entries whenever |z| >= 0.5
            rng = np.random.default_rng(seed); beats = 0; R = __import__("random").Random(seed)
            for _ in range(min(reps, 60)):
                tt, rr = self.pair_returns(h, z_override=0.5, rand=R); m = (tt >= lo) & (tt < hi)
                if m.sum() and rr[m].mean() >= r.mean(): beats += 1
            return round((beats + 1) / (min(reps, 60) + 1), 3)
        if h["kind"] == "rotate":
            ex = self._rot_exit(h); rows = [i for i in self.rotate_rows({**h, "when": h.get("when")}, lo - 1, hi, pick="random")]
            P = self.P; H = h["hold"]
            m = (self.t % (H * HOUR) == 0) & (self.t >= lo) & (self.t < hi); idx = np.flatnonzero(m)
            rng = np.random.default_rng(seed); idx = rng.choice(idx, min(len(idx), 1500), replace=False) if len(idx) else idx
            pool = np.array([self.ret1(int(i), 1, ex) for i in idx]); pool = pool[~np.isnan(pool)]
            if len(pool) < 20: return 1.0
            sims = np.array([rng.choice(pool, len(r)).mean() for _ in range(reps)])
            return round(float(((sims >= r.mean()).sum() + 1) / (reps + 1)), 3)
        if h["kind"] == "union":
            pools = [self._pool(q, lo, hi, len(r)) for q in h["parts"]]
            pool = np.concatenate(pools) if pools else np.array([])
            if len(pool) < len(r) * 2: return 1.0
            rng = np.random.default_rng(seed); sims = np.array([rng.choice(pool, len(r), replace=False).mean() for _ in range(reps)])
            return round(float(((sims >= r.mean()).sum() + 1) / (reps + 1)), 3)
        if h["kind"] == "carry":                     # vs. simply always holding the carry on the same coins
            tt, rr = self.carry_returns(h, always=True); m = (tt >= lo) & (tt < hi)
            if not m.sum(): return 1.0
            pool = rr[m]; rng = np.random.default_rng(seed)
            sims = np.array([rng.choice(pool, len(r)).mean() for _ in range(reps)])
            return round(float(((sims >= r.mean()).sum() + 1) / (reps + 1)), 3)
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
        pool = self._pool(h, lo, hi, len(r))
        if len(pool) < len(r) * 2: return 1.0
        rng = np.random.default_rng(seed); obs = r.mean()
        sims = np.array([rng.choice(pool, len(r), replace=False).mean() for _ in range(reps)])
        return round(float(((sims >= obs).sum() + 1) / (reps + 1)), 3)

    def evaluate(self, h):
        """discovery + validation (the holdout stays untouched here)"""
        t, r = self.series(h)
        d = t < self.val0; v = (t >= self.val0) & (t < self.hold0)
        lv = trade_lev(h)
        disc, val = self.stats(t[d], r[d], lv), self.stats(t[v], r[v], lv)
        rec = {"disc": disc, "val": val}
        min_d, min_v = {"event": (C.MIN_DISC_TRADES, C.MIN_VAL_TRADES), "union": (C.MIN_DISC_TRADES, C.MIN_VAL_TRADES), "rotate": (60, 25), "pair": (30, 12), "carry": (90, 45)}.get(h["kind"], (60, 30))
        if disc["n"] < min_d or disc["mean"] <= 0 or disc["t"] < C.DISC_T: rec["status"] = "dead_disc"; return rec
        # THE PREMISE: it must make money the way it would be traded (10x isolated, liquidations counted as -100% of the margin)
        # and survive double costs. Tiny wins with rare huge losses fail here.
        if h["kind"] in ("event", "union", "rotate"):
            if disc["levMean"] <= 0: rec["status"] = "dead_disc"; rec["why_dead"] = f"loses money at {lv}x"; return rec
            if disc["stress"] <= 0: rec["status"] = "dead_disc"; rec["why_dead"] = "not profitable with double costs"; return rec
        if val["n"] < min_v or val["mean"] <= 0 or val["t"] < C.VAL_T:
            rec["status"] = "near" if val.get("mean", -1) > 0 else "dead_val"; return rec
        if h["kind"] in ("event", "union", "rotate") and (val["levMean"] <= 0 or val["stress"] <= 0):
            rec["status"] = "dead_val"; rec["why_dead"] = f"validation year: loses at {lv}x or with double costs"; return rec
        p = self.null_p(h, t[v], r[v], self.val0, self.hold0); val["p_random"] = p
        rec["status"] = "validated" if p < 0.05 else "near"
        return rec

    def holdout(self, h):
        t, r = self.series(h); m = t >= self.hold0
        return self.stats(t[m], r[m], trade_lev(h))

    def live(self, h, since):
        t, r = self.series(h, t_from=since + HOUR)
        s = self.stats(t, r, trade_lev(h)); s["last"] = [[int(a), round(float(b) * 100, 3)] for a, b in zip(t[-12:], r[-12:])]
        return s

    def signals_now(self, h, hours=36):
        """entries that fired in the newest data (their outcome may still be pending)"""
        if h["kind"] in ("xsec", "pair", "carry"): return []
        if h["kind"] in ("union", "rotate"):
            return [{"coin": str(self.coin[i]), "t": int(self.t[i]), "side": "LONG" if sd > 0 else "SHORT", "px": float(self.P.px.values[i])}
                    for i, sd, _ in self.raw_entries(h, t_lo=self.t.max() - hours * HOUR)][-10:]
        P = self.P; m = self._mask(h) & (self.t > self.P.t.max() - hours * HOUR)
        side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": self.sgn, "against": -self.sgn}[h["dir"]]
        idx = np.flatnonzero(m)
        return [{"coin": str(self.coin[i]), "t": int(self.t[i]), "side": "LONG" if side[i] > 0 else "SHORT", "px": float(P.px.values[i])} for i in idx[-10:] if side[i] != 0]

    # ── individual paper trades (for the dashboard) ─────────────────────────────────────────────
    def _bars(self, coin):
        if not hasattr(self, "_K"): self._K = {}
        if coin not in self._K:
            k = pd.read_parquet(f"{C.DATA_DIR}/raw/{coin}_k.parquet").sort_values("t").drop_duplicates("t")
            self._K[coin] = (k.t.values, k.o.values, k.h.values, k.l.values, k.c.values)
        return self._K[coin]

    def _walk(self, coin, t0, side, exit_):
        """replay one trade on the raw hourly bars: entry, TP/SL levels, exit time/price, how it ended, return ('open' if not finished)"""
        T, O, Hh, Ll, Cc = self._bars(coin); i = int(np.searchsorted(T, t0))
        if i >= len(T) or T[i] != t0: return None
        e = float(O[i]); ex = exit_
        if isinstance(ex, dict) and ex.get("unit") == "vol":
            b = self._blk.get(coin); row = (b[0] + (t0 - b[1]) // HOUR) if b else None
            ex = scale_exit(ex, self.dvol[row] if row is not None and 0 <= row < len(self.dvol) and self.coin[row] == coin and self.t[row] == t0 else np.nan)
            if ex is None: return None
        d = ({"tp": None, "sl": None, "hold": C.EXITS[ex][1]} if C.EXITS[ex][0] == "ret" else {"tp": C.EXITS[ex][0], "sl": C.EXITS[ex][1], "hold": C.EXITS[ex][2]}) if isinstance(ex, str) else ex
        lv = {"entry": e, "tp": e * (1 + side * d["tp"]) if d.get("tp") else None, "sl": e * (1 - side * d["sl"]) if d.get("sl") else None}
        if d.get("trail"): lv["trail"] = d["trail"]
        s_ = simulate(e, Hh, Ll, Cc, i, len(T) - 1, side, ex)
        if s_ is None:
            # not finished yet: has it already hit a stop/target in the bars we have?
            s2 = simulate(e, Hh, Ll, Cc, i, len(T) - 1, side, {**({k: v for k, v in d.items() if k in ("tp", "sl", "trail", "arm") and v}), "hold": max(1, len(T) - i)})
            if s2 and s2[2] not in ("time (%dh)" % max(1, len(T) - i),):
                return {**lv, "exitT": int(T[s2[0]]) + HOUR, "exitPx": float(s2[1]), "how": s2[2], "ret": float(s2[3])}
            return {**lv, "exitT": None, "exitPx": float(Cc[-1]), "how": "open", "ret": side * (Cc[-1] / e - 1) - C.FEE}
        j, px, how, r = s_
        return {**lv, "exitT": int(T[j]) + HOUR, "exitPx": float(px), "how": how, "ret": float(r)}

    def trade_log(self, h, t_from, limit=60):
        """the actual trades a rule took since t_from, with entry, TP/SL, exit and $ result on the paper account"""
        if h["kind"] in ("union", "rotate"):
            cool = h.get("cool", 24) * HOUR if h["kind"] == "union" else 0; last = {}; out = []
            for i, sd, ex in self.raw_entries(h, t_lo=t_from - 1):
                c = str(self.coin[i])
                if self.t[i] - last.get(c, -1e18) < cool: continue
                last[c] = self.t[i]; w = self._walk(c, int(self.t[i]), sd, ex)
                if w: out.append({"coin": c, "side": "LONG" if sd > 0 else "SHORT", "t": int(self.t[i]), **w})
            return out[-limit:]
        if h["kind"] == "event":
            P = self.P; idx = self._entry_index(h); idx = idx[self.t[idx] >= t_from]
            # newest entries whose outcome is not in the data yet (still open)
            m = self._mask(h) & (self.t >= max(t_from, self.t.max() - 96 * HOUR)); extra = [i for i in np.flatnonzero(m) if i not in set(idx)]
            last = {}
            for i in idx: last[self.coin[i]] = max(last.get(self.coin[i], 0), self.t[i])
            for i in extra:
                if self.t[i] - last.get(self.coin[i], -1e18) >= h.get("cool", 24) * HOUR: idx = np.append(idx, i); last[self.coin[i]] = self.t[i]
            side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": self.sgn, "against": -self.sgn}[h["dir"]]
            out = []
            for i in sorted(idx, key=lambda i: self.t[i])[-limit:]:
                if side[i] == 0: continue
                w = self._walk(self.coin[i], int(self.t[i]), int(side[i]), h["exit"])
                if w: out.append({"coin": str(self.coin[i]), "side": "LONG" if side[i] > 0 else "SHORT", "t": int(self.t[i]), **{k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in w.items()}})
            return out
        if h["kind"] == "pair":
            t, r = self.pair_returns(h, t_from)
            return [{"coin": f"{h['a']}/{h['b']}", "side": "PAIR", "t": int(a), "exitT": int(a) + h["hold"] * HOUR, "how": f"time ({h['hold']}h)", "ret": float(b)} for a, b in zip(t[-limit:], r[-limit:])]
        return []

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
        ck[f"survives crashes: worst single trade better than −15% (worst {r.min() * 100:.1f}%)"] = float(r.min()) > -0.15
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
        self.trades(h); return np.array(self._last_keep, dtype=int)

    def _rot_exit(self, h): return {"hold": int(h["hold"]), **({"sl": float(h["stop"])} if h.get("stop") else {})}

    def rotate_rows(self, h, t_lo=None, t_hi=None, pick=None):
        """the coin picked at each rebalance (one at a time, whole account, 1x)"""
        P = self.P; H = h["hold"]
        m = (self.t % (H * HOUR) == 0) & np.isfinite(P[h["rank"]].values)
        if t_lo is not None: m &= self.t > t_lo
        if t_hi is not None: m &= self.t <= t_hi
        if h.get("when"): m &= self._mask({"when": h["when"], "uni": "ALL"})
        idx = np.flatnonzero(m)
        if not len(idx): return []
        df = pd.DataFrame({"i": idx, "t": self.t[idx], "k": P[h["rank"]].values[idx]})
        if pick == "random": return list(df.groupby("t").i.apply(lambda g: g.sample(1, random_state=int(g.iloc[0]) % 997).iloc[0]).values)
        g = df.sort_values(["t", "k"], ascending=[True, h["order"] == "low"]).groupby("t").head(1)
        return list(g.i.values)

    def rotate_trades(self, h, t_from=None):
        ex = self._rot_exit(h); ts, rs = [], []
        for i in self.rotate_rows(h, t_lo=None if t_from is None else t_from - 1):
            r = self.ret1(int(i), 1, ex)
            if not np.isnan(r): ts.append(self.t[i]); rs.append(r)
        return np.array(ts, dtype=np.int64), np.array(rs)

    def raw_entries(self, h, t_lo=None, t_hi=None):
        """every row where an event / union rule fires (no cooldown): (row index, side, exit name)"""
        if h["kind"] == "rotate":
            return [(int(i), 1, self._rot_exit(h)) for i in self.rotate_rows(h, t_lo, t_hi)]
        parts = h["parts"] if h["kind"] == "union" else [h]
        P = self.P; out = []
        for q in parts:
            m = self._mask(q)
            if t_lo is not None: m &= self.t > t_lo
            if t_hi is not None: m &= self.t <= t_hi
            side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": self.sgn, "against": -self.sgn}[q["dir"]]
            for i in np.flatnonzero(m):
                if side[i] != 0: out.append((int(i), int(side[i]), q["exit"]))
        out.sort(key=lambda x: (self.t[x[0]], x[0])); return out

    def union_trades(self, h, t_from=None):
        cool = h.get("cool", 24) * HOUR; last = {}; ts, rs = [], []
        for i, sd, ex in self.raw_entries(h):
            if t_from is not None and self.t[i] < t_from: continue
            r = self.ret1(i, sd, ex)
            if np.isnan(r): continue
            c = self.coin[i]
            if self.t[i] - last.get(c, -1e18) < cool: continue
            last[c] = self.t[i]; ts.append(self.t[i]); rs.append(r)
        return np.array(ts, dtype=np.int64), np.array(rs)

    def fingerprint(self, h):
        """the set of (coin, day) a rule trades — used to spot near-duplicate champions"""
        if h["kind"] in ("event", "union", "rotate"): return {(str(self.coin[i]), int(self.t[i] // 86400)) for i, _, _ in self.raw_entries(h, t_lo=self.val0)}
        t, _ = self.series(h, t_from=self.val0); return {(h.get("a", h.get("uni", "")), int(x // 86400)) for x in t}
