"""STRICTLY CAUSAL DSL. An expression is a JSON list (an AST): ["op", args...]. Leaves read only columns that are already known at the
start of hour t (the panel shifts every measurement by one hour); raw OHLC leaves are shifted by one more hour HERE, in the compiler.
Every operator looks backwards only (rolling windows, lags >= 1, cross-sections at the same hour). `probe()` proves it: the values up
to a cut time must not change when everything after the cut is deleted.

Grammar
  leaf    ["col", name]            a causal panel column (whitelist below)
          ["raw", "c"|"h"|"l"|"o"]  the coin's previous bar (shift(1) enforced here)
          ["resid", n, k]          BTC/ETH-beta residual: coin return minus beta*BTC return (beta over n hours), summed over k hours
          ["const", x]
  unary   neg abs sgn slog | zs n | ema n | diff n | lag n | tsr n (rolling rank) | xsr (cross-sectional rank) | xsd (cross-sectional demean)
  binary  add sub mul div max min
"""
import json, hashlib, numpy as np, pandas as pd

WIN = (4, 12, 24, 72, 168, 720)
UNARY = ("neg", "abs", "sgn", "slog")
WUNARY = ("zs", "ema", "diff", "lag", "tsr")
XUNARY = ("xsr", "xsd")
BINARY = ("add", "sub", "mul", "div", "max", "min")
RAW = {"c": "_cl", "h": "_hi", "l": "_lo", "o": "px"}
FORBID_PREFIX = ("L_", "S_", "_", "e_")
FORBID = {"px", "t", "coin", "_hi", "_lo", "_cl", "_qv24"}

def leaves(P):
    """the causal columns an expression may read"""
    return sorted(c for c in P.columns if c not in FORBID and not c.startswith(FORBID_PREFIX) and P[c].dtype.kind == "f")

def key(node): return hashlib.sha1(json.dumps(node, separators=(",", ":")).encode()).hexdigest()[:12]
def text(node):
    op = node[0]
    if op == "col": return node[1]
    if op == "raw": return f"{node[1]}[t-1]"
    if op == "const": return f"{node[1]:g}"
    if op == "resid": return f"resid_btc({node[1]}h,{node[2]}h)"
    if op in WUNARY: return f"{op}{node[1]}({text(node[2])})"
    if op in UNARY or op in XUNARY: return f"{op}({text(node[1])})"
    return f"{op}({text(node[1])}, {text(node[2])})"
def size(node): return 1 + sum(size(a) for a in node[1:] if isinstance(a, list))
def depth(node): return 1 + max([depth(a) for a in node[1:] if isinstance(a, list)] or [0])

def valid(node, cols):
    """structure check (also used on what the LLM writes): known ops, known columns, windows from the menu, lags >= 1"""
    if not isinstance(node, list) or not node: return False
    op = node[0]
    try:
        if op == "col": return len(node) == 2 and node[1] in cols
        if op == "raw": return len(node) == 2 and node[1] in RAW
        if op == "const": return len(node) == 2 and np.isfinite(float(node[1]))
        if op == "resid": return len(node) == 3 and int(node[1]) in WIN and 1 <= int(node[2]) <= 168
        if op in UNARY or op in XUNARY: return len(node) == 2 and valid(node[1], cols)
        if op in WUNARY: return len(node) == 3 and int(node[1]) >= 1 and (int(node[1]) in WIN or op == "lag" and int(node[1]) <= 24) and valid(node[2], cols)
        if op in BINARY: return len(node) == 3 and valid(node[1], cols) and valid(node[2], cols)
    except Exception: return False
    return False

class Ctx:
    """the panel prepared for fast block-wise (per coin) evaluation; rows are sorted by coin, then time"""
    def __init__(self, P, cache=256):
        self.P = P; self.n = len(P); c = P.coin.values
        self.starts = np.r_[0, np.flatnonzero(c[1:] != c[:-1]) + 1]; self.ends = np.r_[self.starts[1:], self.n]
        self.t = P.t.values; self.cols = set(leaves(P)); self._memo = {}; self._cap = cache
        self._tg = None; b = P.loc[P.coin.values == "BTC", ["t", "r1"]] if "r1" in P else None
        self.btc_r1 = pd.Series(b.r1.values, index=b.t.values).reindex(self.t).values.astype(float) if b is not None and len(b) else np.full(self.n, np.nan)

    def _blocks(self, x, fn):
        out = np.full(self.n, np.nan)
        for a, b in zip(self.starts, self.ends): out[a:b] = fn(pd.Series(x[a:b])).values
        return out

    def _xs(self, x, how):
        if self._tg is None: self._tg = pd.Series(self.t)
        s = pd.Series(x)
        return (s.groupby(self._tg.values).rank(pct=True) if how == "rank" else s - s.groupby(self._tg.values).transform("mean")).values

    def eval(self, node):
        k = json.dumps(node, separators=(",", ":"))
        if k in self._memo: return self._memo[k]
        v = self._eval(node).astype(np.float32); v[~np.isfinite(v)] = np.nan
        if len(self._memo) >= self._cap: self._memo.pop(next(iter(self._memo)))
        self._memo[k] = v; return v

    def _eval(self, node):
        op = node[0]; P = self.P
        if op == "col": return P[node[1]].values.astype(float)
        if op == "raw": return self._blocks(P[RAW[node[1]]].values.astype(float), lambda s: s.shift(1))     # shift(1) ENFORCED here
        if op == "const": return np.full(self.n, float(node[1]))
        if op == "resid":
            n_, k_ = int(node[1]), int(node[2]); r = P["r1"].values.astype(float); b = self.btc_r1
            out = np.full(self.n, np.nan)
            for a, e in zip(self.starts, self.ends):
                rs, bs = pd.Series(r[a:e]), pd.Series(b[a:e])
                beta = (rs * bs).rolling(n_, min_periods=n_ // 2).mean() - rs.rolling(n_, min_periods=n_ // 2).mean() * bs.rolling(n_, min_periods=n_ // 2).mean()
                beta = beta / (bs.rolling(n_, min_periods=n_ // 2).var(ddof=0) + 1e-12)
                out[a:e] = (rs - beta.shift(1) * bs).rolling(k_, min_periods=1).sum().values      # beta from the hours BEFORE (no peeking into its own hour)
            return out
        if op in UNARY:
            x = self.eval(node[1]).astype(float)
            return {"neg": -x, "abs": np.abs(x), "sgn": np.sign(x), "slog": np.sign(x) * np.log1p(np.abs(x))}[op]
        if op in XUNARY: return self._xs(self.eval(node[1]).astype(float), "rank" if op == "xsr" else "demean")
        if op in WUNARY:
            n_ = int(node[1]); x = self.eval(node[2]).astype(float)
            if op == "zs": return self._blocks(x, lambda s: (s - s.rolling(n_, min_periods=max(2, n_ // 2)).mean()) / (s.rolling(n_, min_periods=max(2, n_ // 2)).std() + 1e-12))
            if op == "ema": return self._blocks(x, lambda s: s.ewm(span=n_, adjust=False, ignore_na=True).mean())
            if op == "diff": return self._blocks(x, lambda s: s - s.shift(n_))
            if op == "lag": return self._blocks(x, lambda s: s.shift(n_))
            if op == "tsr": return self._blocks(x, lambda s: s.rolling(n_, min_periods=max(2, n_ // 2)).rank(pct=True))
        if op in BINARY:
            a, b = self.eval(node[1]).astype(float), self.eval(node[2]).astype(float)
            with np.errstate(all="ignore"):
                return {"add": a + b, "sub": a - b, "mul": a * b, "div": a / np.where(np.abs(b) < 1e-9, np.nan, b), "max": np.fmax(a, b), "min": np.fmin(a, b)}[op]
        raise ValueError(f"unknown op {op}")

def probe(node, P, cut_frac=0.7, rows=200000):
    """CAUSALITY PROOF: compute on the full panel and on the panel cut at time T; every value at t <= T must be identical"""
    T = np.quantile(P.t.values, cut_frac); full = Ctx(P, cache=8).eval(node); m = P.t.values <= T
    cut = Ctx(P.loc[m].reset_index(drop=True), cache=8).eval(node)
    a, b = full[m], cut
    if len(a) > rows: ix = np.random.default_rng(0).choice(len(a), rows, replace=False); a, b = a[ix], b[ix]
    same = (np.isnan(a) & np.isnan(b)) | np.isclose(a, b, rtol=1e-4, atol=1e-6)
    return bool(same.all()), float(1 - same.mean())

def leaf_names(node):
    if node[0] == "col": yield node[1]
    for a in node[1:]:
        if isinstance(a, list): yield from leaf_names(a)

def leak_check(node, P, limit=0.12, rows=400000):
    """FUTURE-DATA AUDIT of the columns an expression reads. A column known at the start of hour t cannot track the move DURING hour t
    or over the next 6 or 24 hours: |corr| of the column (and of its hourly change) with those moves above `limit` means it contains the
    future (real signals sit below ~0.05)."""
    O, Cl, coin = P["px"].values, P["_cl"].values, P.coin.values
    same = np.r_[coin[1:] == coin[:-1], False]; same24 = np.r_[coin[24:] == coin[:-24], np.zeros(24, bool)]
    same6 = np.r_[coin[6:] == coin[:-6], np.zeros(6, bool)]
    y1 = Cl / O - 1; y6 = np.where(same6, np.r_[Cl[5:], np.full(5, np.nan)] / O - 1, np.nan); y24 = np.where(same24, np.r_[Cl[23:], np.full(23, np.nan)] / O - 1, np.nan)
    rng = np.random.default_rng(1); ix = rng.choice(len(P) - 25, min(rows, len(P) - 25), replace=False); bad = {}
    for c in set(leaf_names(node)):
        x = P[c].values.astype(float); dx = np.where(np.r_[False, coin[1:] == coin[:-1]], x - np.r_[np.nan, x[:-1]], np.nan)
        for nm, v in (("level", x), ("change", dx)):
            for hz, y in (("this hour", y1), ("next 6h", y6), ("next 24h", y24)):
                a, b = v[ix], y[ix]; m = np.isfinite(a) & np.isfinite(b)
                if m.sum() > 1000 and np.std(a[m]) > 0:
                    r = float(np.corrcoef(a[m], b[m])[0, 1])
                    if abs(r) > limit: bad[f"{c} ({nm} vs {hz})"] = round(r, 3)
    return not bad, bad
