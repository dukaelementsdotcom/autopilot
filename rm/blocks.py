"""Invented building blocks. The AI may propose NEW measurements as small formulas. They are never run as code:
each formula is parsed and only a short whitelist of ingredients and operations is allowed, then computed with pandas.
Ingredients (per coin, hourly): o h l c v tb  oi glob topPos topAcc tk  and the machine's own measurements (r1, vol24, rsi14, ...)
Operations: + - * /, lag diff pct mean std z max min rank ema sum (x, hours), abs log sign sqrt, clip(x, lo, hi), corr(x, y, hours)
Everything is shifted one hour at the end, so a block only ever uses information known BEFORE the decision."""
import ast, re, numpy as np, pandas as pd

BASE = ("o", "h", "l", "c", "v", "tb", "oi", "glob", "topPos", "topAcc", "tk")
WIN = {"lag", "diff", "pct", "mean", "std", "z", "max", "min", "rank", "ema", "sum"}
ONE = {"abs", "log", "sign", "sqrt"}
MAXLEN, MAXNODES, MAXWIN = 220, 45, 2160

def check(expr, names):
    """returns an error message, or None if the formula is allowed"""
    if not isinstance(expr, str) or not expr.strip() or len(expr) > MAXLEN: return "empty or too long"
    try: tree = ast.parse(expr, mode="eval")
    except SyntaxError: return "not a formula"
    nodes = list(ast.walk(tree))
    if len(nodes) > MAXNODES: return "too complex"
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Constant, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)
    for n in nodes:
        if not isinstance(n, allowed): return f"'{type(n).__name__}' is not allowed"
        if isinstance(n, ast.Constant) and not isinstance(n.value, (int, float)): return "only numbers allowed"
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.keywords: return "bad call"
            f = n.func.id; a = n.args
            if f in WIN:
                if len(a) != 2 or not isinstance(a[1], ast.Constant) or not isinstance(a[1].value, int) or not 1 <= a[1].value <= MAXWIN: return f"{f}(x, hours) needs whole hours 1-{MAXWIN}"
            elif f in ONE:
                if len(a) != 1: return f"{f}(x) takes one input"
            elif f == "clip":
                if len(a) != 3 or not all(isinstance(x, ast.Constant) for x in a[1:]): return "clip(x, lo, hi)"
            elif f == "corr":
                if len(a) != 3 or not isinstance(a[2], ast.Constant) or not isinstance(a[2].value, int) or not 2 <= a[2].value <= MAXWIN: return "corr(x, y, hours)"
            else: return f"unknown operation '{f}'"
        if isinstance(n, ast.Name) and n.id not in names and n.id not in WIN | ONE | {"clip", "corr"}: return f"unknown ingredient '{n.id}'"
    return None

def compute(expr, env):
    """env: dict name -> pandas Series (one coin, hourly). Returns a Series (NOT yet shifted)."""
    def ev(n):
        if isinstance(n, ast.Expression): return ev(n.body)
        if isinstance(n, ast.Constant): return float(n.value)
        if isinstance(n, ast.Name): return env[n.id].astype(float)
        if isinstance(n, ast.UnaryOp): v = ev(n.operand); return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add): return a + b
            if isinstance(n.op, ast.Sub): return a - b
            if isinstance(n.op, ast.Mult): return a * b
            if isinstance(b, pd.Series): b = b.replace(0, np.nan)
            elif b == 0: return a * np.nan
            return a / b
        f = n.func.id; x = ev(n.args[0])
        if not isinstance(x, pd.Series): x = pd.Series(x, index=next(iter(env.values())).index)
        if f in WIN:
            w = n.args[1].value; mp = max(2, w // 2)
            return {"lag": lambda: x.shift(w), "diff": lambda: x - x.shift(w), "pct": lambda: x / x.shift(w).replace(0, np.nan) - 1,
                    "mean": lambda: x.rolling(w, min_periods=mp).mean(), "std": lambda: x.rolling(w, min_periods=mp).std(),
                    "z": lambda: (x - x.rolling(w, min_periods=mp).mean()) / x.rolling(w, min_periods=mp).std().replace(0, np.nan),
                    "max": lambda: x.rolling(w, min_periods=mp).max(), "min": lambda: x.rolling(w, min_periods=mp).min(),
                    "rank": lambda: x.rolling(w, min_periods=mp).rank(pct=True), "ema": lambda: x.ewm(span=w, adjust=False).mean(),
                    "sum": lambda: x.rolling(w, min_periods=mp).sum()}[f]()
        if f == "abs": return x.abs()
        if f == "log": return np.log(x.where(x > 0))
        if f == "sign": return np.sign(x)
        if f == "sqrt": return np.sqrt(x.where(x >= 0))
        if f == "clip": return x.clip(float(n.args[1].value), float(n.args[2].value))
        if f == "corr": y = ev(n.args[1]); return x.rolling(n.args[2].value, min_periods=n.args[2].value // 2).corr(y)
    out = ev(ast.parse(expr, mode="eval"))
    if not isinstance(out, pd.Series): out = pd.Series(out, index=next(iter(env.values())).index)
    return out.replace([np.inf, -np.inf], np.nan)

def slug(name, expr):
    import hashlib
    base = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:24] or "block"
    return f"u_{base}_{hashlib.sha1(expr.encode()).hexdigest()[:4]}"
