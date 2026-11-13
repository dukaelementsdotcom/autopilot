"""GENERATOR: genetic programming on strategy genomes + an LLM hook (FunSearch-style) that reads the best elites and the clusters of
their LOSING trades and writes targeted veto filters (and new signal ideas) in the DSL. Everything it writes is parsed and checked
against the grammar; nothing it says is trusted until the judge has scored it.
Genome: {"sig": AST, "side": +1/-1, "tail": "hi"/"lo", "z": entry threshold (rolling z-score of the signal), "flt": None or
         {"node": AST, "dir": "above"/"below", "z": threshold, "veto": bool}}"""
import json, copy, random, numpy as np
from . import dsl

ZS = (1.0, 1.5, 2.0, 2.5)
FZ = (-1.0, 0.0, 1.0)
PREFER = ("p_r", "p_vol", "p_oi", "p_glob", "p_topPos", "p_timb", "p_basis", "p_fg", "p_btc", "x_", "w_", "p_u_", "p_tk", "p_sshare", "p_hl")

def _col(cols, rng):
    pref = [c for c in cols if c.startswith(PREFER)]
    return rng.choice(pref if pref and rng.random() < 0.8 else cols)

def leaf(cols, rng):
    r = rng.random()
    if r < 0.70: return ["col", _col(cols, rng)]
    if r < 0.82: return ["raw", rng.choice("chlo")]
    if r < 0.94: return ["resid", rng.choice((24, 72, 168, 720)), rng.choice((1, 4, 24, 72))]
    return ["const", round(rng.uniform(-2, 2), 2)]

def tree(cols, rng, d=3):
    if d <= 1 or rng.random() < 0.3: return leaf(cols, rng)
    r = rng.random()
    if r < 0.40: return [rng.choice(dsl.WUNARY), rng.choice(dsl.WIN), tree(cols, rng, d - 1)]
    if r < 0.55: return [rng.choice(dsl.UNARY + dsl.XUNARY), tree(cols, rng, d - 1)]
    return [rng.choice(dsl.BINARY), tree(cols, rng, d - 1), tree(cols, rng, d - 1)]

def genome(cols, rng):
    return {"sig": tree(cols, rng, rng.choice((2, 3, 4))), "side": rng.choice((1, -1)), "tail": rng.choice(("hi", "lo")), "z": rng.choice(ZS),
            "flt": None if rng.random() < 0.7 else {"node": tree(cols, rng, 2), "dir": rng.choice(("above", "below")), "z": rng.choice(FZ), "veto": False}}

def _paths(node, p=()):
    yield p
    for i, a in enumerate(node[1:], 1):
        if isinstance(a, list): yield from _paths(a, p + (i,))

def _get(node, p):
    for i in p: node = node[i]
    return node

def _set(node, p, v):
    if not p: return v
    node = copy.deepcopy(node); x = node
    for i in p[:-1]: x = x[i]
    x[p[-1]] = v; return node

def mutate_tree(node, cols, rng):
    p = rng.choice(list(_paths(node))); sub = _get(node, p); r = rng.random()
    if r < 0.45: new = tree(cols, rng, rng.choice((1, 2, 3)))                                       # subtree replacement
    elif r < 0.75:                                                                                    # point mutation
        new = copy.deepcopy(sub)
        if new[0] == "col": new[1] = _col(cols, rng)
        elif new[0] in dsl.WUNARY: new[1] = rng.choice(dsl.WIN)
        elif new[0] in dsl.BINARY: new[0] = rng.choice(dsl.BINARY)
        elif new[0] in dsl.UNARY + dsl.XUNARY: new[0] = rng.choice(dsl.UNARY + dsl.XUNARY)
        elif new[0] == "const": new[1] = round(float(new[1]) + rng.gauss(0, 0.5), 2)
        else: new = leaf(cols, rng)
    elif r < 0.9: new = [rng.choice(dsl.WUNARY), rng.choice(dsl.WIN), sub]                              # wrap
    else: new = next((a for a in sub[1:] if isinstance(a, list)), sub)                                  # hoist
    out = _set(node, p, new)
    return out if dsl.depth(out) <= 6 and dsl.size(out) <= 24 else node

def crossover(a, b, rng):
    pa = rng.choice(list(_paths(a))); pb = rng.choice(list(_paths(b)))
    out = _set(a, pa, copy.deepcopy(_get(b, pb)))
    return out if dsl.depth(out) <= 6 and dsl.size(out) <= 24 else a

def mutate(g, cols, rng, other=None):
    g = copy.deepcopy(g); r = rng.random()
    if other is not None and r < 0.25: g["sig"] = crossover(g["sig"], other["sig"], rng)
    elif r < 0.65: g["sig"] = mutate_tree(g["sig"], cols, rng)
    elif r < 0.75: g["z"] = rng.choice(ZS)
    elif r < 0.80: g["tail"] = "lo" if g["tail"] == "hi" else "hi"
    elif r < 0.85: g["side"] = -g["side"]
    else:
        if g.get("flt") and rng.random() < 0.3: g["flt"] = None
        elif g.get("flt"): g["flt"]["node"] = mutate_tree(g["flt"]["node"], cols, rng); g["flt"]["z"] = rng.choice(FZ)
        else: g["flt"] = {"node": tree(cols, rng, 2), "dir": rng.choice(("above", "below")), "z": rng.choice(FZ), "veto": False}
    return g

def gkey(g): return dsl.key([g["sig"], g["side"], g["tail"], g["z"], g.get("flt")])
def gtext(g):
    s = f"{'LONG' if g['side'] > 0 else 'SHORT'} when z({dsl.text(g['sig'])}) {'>=' if g['tail'] == 'hi' else '<= -'}{g['z']}"
    f = g.get("flt")
    if f: s += f"; {'skip' if f.get('veto') else 'only'} when z({dsl.text(f['node'])}) {'>' if f['dir'] == 'above' else '<'} {f['z']}"
    return s

# ── LLM hook ────────────────────────────────────────────────────────────────────────────────────────────────────────────
GRAMMAR = """Expressions are JSON lists. Leaves: ["col", NAME] (a column from the list), ["raw","c"|"h"|"l"|"o"] (previous bar),
["resid", N, K] (coin return minus its Bitcoin beta, beta over N hours, summed over K hours), ["const", X].
Unary: ["neg"|"abs"|"sgn"|"slog"|"xsr"|"xsd", E] (xsr = rank across coins at that hour, xsd = minus the average across coins).
Windowed: ["zs"|"ema"|"diff"|"lag"|"tsr", N, E] with N in 4,12,24,72,168,720 (lag also 1..24). Binary: ["add"|"sub"|"mul"|"div"|"max"|"min", E1, E2].
Every value is already known at decision time; you cannot look ahead."""

def failure_clusters(P, rows, rets, feats, k=3, seed=0):
    """cluster the LOSING trades of an elite on a few context measurements; describe each cluster against all its trades"""
    lose = rets < 0
    if lose.sum() < 15: return []
    X = np.column_stack([P[f].values[rows].astype(float) for f in feats]); mu = np.nanmean(X, 0); sd = np.nanstd(X, 0) + 1e-9
    Z = np.nan_to_num((X - mu) / sd); L = Z[lose]; rng = np.random.default_rng(seed); cen = L[rng.choice(len(L), k, replace=False)]
    for _ in range(25):
        lab = np.argmin(((L[:, None, :] - cen[None]) ** 2).sum(-1), 1)
        cen = np.array([L[lab == j].mean(0) if (lab == j).any() else cen[j] for j in range(k)])
    out = []
    for j in range(k):
        n = int((lab == j).sum())
        if n < 5: continue
        top = sorted(zip(feats, cen[j]), key=lambda x: -abs(x[1]))[:3]
        out.append({"losses": n, "share": round(n / len(L), 2), "where": ", ".join(f"{f} {'high' if v > 0 else 'low'} ({v:+.1f} sd)" for f, v in top)})
    return out

def llm_children(elites, clusters, cols, ask, n=4):
    """ask the language model for veto filters aimed at the failure clusters, and for a few new orthogonal signals"""
    if not elites: return []
    show = [{"rule": gtext(e["genome"]), "t": e["fit"], "trades": e["stats"]["n"], "perTradePct": e["stats"]["mean"], "lossClusters": clusters.get(e["key"], [])} for e in elites[:4]]
    colist = ", ".join([c for c in cols if c.startswith(PREFER)][:120])
    msg = [{"role": "system", "content": "You are a quantitative researcher. You write trading-signal expressions in a strict JSON grammar. Answer with one JSON object."},
           {"role": "user", "content": f"{GRAMMAR}\nColumns: {colist}\n\nCurrent best strategies and where their LOSING trades cluster:\n{json.dumps(show)}\n\n"
            f"1) For each strategy write one VETO filter: an expression that is high exactly in its losing clusters (the trade is skipped when it is high).\n"
            f"2) Write {n} NEW signal expressions that use different information from these strategies (orthogonal ideas).\n"
            'Answer: {"vetoes": [{"for": <index 0..3>, "expr": <expression>, "why": "..."}], "signals": [{"expr": <expression>, "side": "long"|"short", "why": "..."}]}'}]
    out = ask(msg)
    txt = out[0] if isinstance(out, tuple) else out
    if not txt: return []
    try: d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except Exception: return []
    kids = []
    for v in d.get("vetoes", [])[:6]:
        try:
            e = elites[int(v["for"])]; node = v["expr"]
            if dsl.valid(node, set(cols)):
                g = copy.deepcopy(e["genome"]); g["flt"] = {"node": node, "dir": "above", "z": 1.0, "veto": True}; kids.append((g, "llm-veto", v.get("why", "")))
        except Exception: continue
    for s in d.get("signals", [])[:n + 2]:
        node = s.get("expr")
        if dsl.valid(node, set(cols)):
            for tail in ("hi", "lo"):
                kids.append(({"sig": node, "side": 1 if s.get("side") == "long" else -1, "tail": tail, "z": 1.5, "flt": None}, "llm-signal", s.get("why", "")))
    return kids
