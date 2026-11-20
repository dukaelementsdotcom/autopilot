"""AGENT TEAM, the safe tester. The AI never writes code: it writes ONE true/false rule over a fixed list of past-only measurements
(numbers, + - * /, abs(), < > <= >=, & | ~). This file checks the rule is nothing else, then tests it the same honest way the lab tests do:
7 separate 120-day windows, one trade a day picked at random among the hours the rule fires (no brain), the same +2% target / volatility stop /
48h / costs as the Arbiter, compared with the same random one-a-day pick over ALL hours (the baseline)."""
import ast, numpy as np
from . import config as C, referee as RF
from .walkforward import pol

DAY = 86400; H = 3600
OKCOLS = sorted({c for v in RF.FAMILIES.values() for c in v} | set(RF.KNN_FEATS))      # the same past-only columns the live brain uses; never labels (L_*, S_*) or prices of the future

def compile_rule(expr, allowed):
    """returns a function(env) -> boolean array, or raises ValueError saying what is not allowed"""
    expr = (expr or "").strip()
    if not expr or len(expr) > 500: raise ValueError("the rule must be one expression of at most 500 characters")
    try: tree = ast.parse(expr, mode="eval")
    except SyntaxError as e: raise ValueError(f"not a valid expression: {e}")
    allowed = set(allowed); n_nodes = [0]
    def chk(n):
        n_nodes[0] += 1
        if n_nodes[0] > 80: raise ValueError("rule too long (more than 80 parts)")
        if isinstance(n, ast.Expression): chk(n.body)
        elif isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.BitAnd, ast.BitOr)): chk(n.left); chk(n.right)
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.Not, ast.USub, ast.Invert)): chk(n.operand)
        elif isinstance(n, ast.Compare):
            if len(n.ops) != 1 or not isinstance(n.ops[0], (ast.Lt, ast.Gt, ast.LtE, ast.GtE)): raise ValueError("use one comparison (< > <= >=) at a time and wrap each comparison in parentheses, joined with & or |")
            chk(n.left); chk(n.comparators[0])
        elif isinstance(n, ast.BoolOp): raise ValueError("use & and | (not 'and' / 'or'), with each comparison in parentheses")
        elif isinstance(n, ast.Name):
            if n.id not in allowed: raise ValueError(f"unknown measurement '{n.id}' (allowed: {', '.join(sorted(allowed))})")
        elif isinstance(n, ast.Constant):
            if isinstance(n.value, bool) or not isinstance(n.value, (int, float)): raise ValueError("only plain numbers are allowed as constants")
        elif isinstance(n, ast.Call):
            if not (isinstance(n.func, ast.Name) and n.func.id == "abs" and len(n.args) == 1 and not n.keywords): raise ValueError("the only function allowed is abs(x)")
            chk(n.args[0])
        else: raise ValueError(f"'{type(n).__name__}' is not allowed in a rule")
    chk(tree)
    code = compile(tree, "<rule>", "eval")
    def run(env):
        e = dict(env); e["abs"] = np.abs
        with np.errstate(all="ignore"): out = eval(code, {"__builtins__": {}}, e)
        out = np.asarray(out)
        if out.dtype != bool: raise ValueError("the rule must give true/false (use a comparison such as (p_r24 < 0.2))")
        return out
    return run

def evaluate(J, expr, side=1, folds=7):
    allowed = [c for c in OKCOLS if c in J.cols]; run = compile_rule(expr, allowed)
    rng = np.random.default_rng(0); end = int(J.t_end); rows = []
    for k in range(folds):
        te = end - k * C.HOLDOUT_DAYS * DAY; h0 = te - C.HOLDOUT_DAYS * DAY
        X = RF._rows(J, h0, te - RF.HORIZON * H)
        if len(X) < 100: break
        env = {c: np.asarray(J.P[c].values[X], float) for c in allowed}; m = run(env)
        if m.shape != (len(X),): raise ValueError("the rule did not give one true/false per row")
        stp = RF.stop_pct(J.dvol[X]); y, r = RF.barrier(J, X, side, RF.TARGET, stp, RF.HORIZON); span = (te - h0) / DAY; tie = rng.random(len(X))
        rule = pol(J, X[m], r[m], tie[m], stp[m], span) if m.any() else {"trades": 0}
        base = pol(J, X, r, tie, stp, span)
        rows.append({"window": f"{int(h0 // DAY)}-{int(te // DAY)} (days since 1970)", "firesShare": round(float(m.mean()), 3), "rule": rule, "baseline": base,
                     "ruleHit": round(float(y[m].mean()), 3) if m.any() else None, "baseHit": round(float(y.mean()), 3)})
    mr = [x["rule"].get("monthly", 0.0) for x in rows]; mb = [x["baseline"].get("monthly", 0.0) for x in rows]; tr = sum(x["rule"].get("trades", 0) for x in rows)
    pos = sum(1 for v in mr if v > 0); beat = sum(1 for a, b in zip(mr, mb) if a > b)
    summ = {"windows": len(rows), "trades": int(tr), "avgMonthly": round(float(np.mean(mr)), 2) if mr else None, "avgBaselineMonthly": round(float(np.mean(mb)), 2) if mb else None,
            "positiveWindows": f"{pos}/{len(rows)}", "beatBaselineWindows": f"{beat}/{len(rows)}"}
    summ["verdict"] = "PASS" if (len(rows) >= 5 and pos >= 5 and beat >= 5 and tr >= 30 and np.mean(mr) > 0) else "FAIL"
    return {"side": "long" if side > 0 else "short", "summary": summ, "windows": rows}
