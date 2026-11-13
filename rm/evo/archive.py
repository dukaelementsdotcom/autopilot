"""MAP-ELITES ARCHIVE. Instead of one ranking (which breeds twins of the same winner), the archive keeps the best strategy in every
niche of behaviour: side x trade frequency x average hold x correlation with Bitcoin. A strategy enters only if its niche is empty or
it beats the niche's elite, and only if it is ORTHOGONAL: its signal correlates <= 0.35 with every input the Arbiter already uses, and
its firing pattern overlaps <= 0.35 (Jaccard) with every stronger elite."""
import json, gzip, os, numpy as np

BINS = {"freq": [3.0, 10.0, 30.0], "hold": [8.0, 20.0, 36.0], "btc": [-0.1, 0.2, 0.45]}     # trades/month, hours, Pearson
LABEL = {"freq": ["rare", "weekly", "daily", "busy"], "hold": ["<8h", "8-20h", "20-36h", ">36h"], "btc": ["anti-BTC", "own", "BTC-ish", "BTC-beta"]}
ORTHO = 0.35

def cell(side, d):
    return (int(side),) + tuple(int(np.searchsorted(BINS[k], d[k])) for k in ("freq", "hold", "btc"))

def cell_name(c): return f"{'long' if c[0] > 0 else 'short'} · {LABEL['freq'][c[1]]} · {LABEL['hold'][c[2]]} · {LABEL['btc'][c[3]]}"

class Archive:
    def __init__(self, path):
        self.path = path; self.cells = {}; self.N = 0; self.log = []; self.fires = {}
        if os.path.exists(path):
            d = json.load(gzip.open(path, "rt")); self.N = d.get("N", 0); self.log = d.get("log", [])[-200:]
            for k, e in d.get("cells", {}).items():
                c = tuple(json.loads(k)); self.cells[c] = e
                if e.get("fire"): self.fires[c] = np.unpackbits(np.frombuffer(bytes.fromhex(e["fire"]), np.uint8)).astype(bool)

    def save(self):
        out = {"N": self.N, "log": self.log[-200:], "cells": {}}
        for c, e in self.cells.items():
            e = dict(e)
            if c in self.fires: e["fire"] = np.packbits(self.fires[c]).tobytes().hex()
            out["cells"][json.dumps(list(c))] = e
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with gzip.open(self.path + ".tmp", "wt") as f: json.dump(out, f, separators=(",", ":"))
        os.replace(self.path + ".tmp", self.path)

    def elites(self): return list(self.cells.values())

    def twin_of(self, fire, fit):
        """the strongest existing elite this firing pattern overlaps with by more than ORTHO (Jaccard), if any"""
        for c, f in self.fires.items():
            if len(f) != len(fire): continue
            inter = np.count_nonzero(f & fire); uni = np.count_nonzero(f | fire)
            if uni and inter / uni > ORTHO and self.cells[c]["fit"] >= fit: return c
        return None

    def insert(self, c, elite, fire):
        old = self.cells.get(c)
        if old is not None and old["fit"] >= elite["fit"]: return False
        self.cells[c] = elite; self.fires[c] = fire; return True

def voter_matrix(P, rows, cols):
    """the Arbiter's own inputs on a fixed sample of rows, standardised (for the orthogonality filter)"""
    X = np.column_stack([P[c].values[rows].astype(float) for c in cols])
    mu = np.nanmean(X, 0); sd = np.nanstd(X, 0) + 1e-12; X = (X - mu) / sd; X[~np.isfinite(X)] = 0.0
    return X

def max_corr(sig, X):
    s = sig.astype(float); m = np.isfinite(s)
    if m.sum() < 1000: return 1.0
    s = np.where(m, (s - np.nanmean(s)) / (np.nanstd(s) + 1e-12), 0.0)
    c = np.abs(X[m].T @ s[m]) / m.sum()
    return float(np.nanmax(c)) if len(c) else 0.0
