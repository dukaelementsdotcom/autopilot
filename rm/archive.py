"""PERMANENT MEMORY: nothing the referee experienced is ever thrown away.
Working lists in memory.json stay short so every run is fast; whatever is trimmed from them is appended here first,
compressed (state/archive/<kind>.jsonl.gz), so "what happened to me a week ago / last year" can always be looked up."""
import os, json, gzip
from . import config as C

def _path(kind): return f"{C.STATE_DIR}/archive/{kind}.jsonl.gz"

def keep(kind, rows):
    if not rows: return
    os.makedirs(f"{C.STATE_DIR}/archive", exist_ok=True)
    with gzip.open(_path(kind), "at") as f:           # appending adds a new gzip member; readers see one continuous file
        for r in rows: f.write(json.dumps(r, separators=(",", ":"), default=str) + "\n")

def trim(lst, n, kind):
    """keep the newest n items in the working list; the older ones go to the permanent archive (in place)"""
    if len(lst) > n:
        keep(kind, lst[:len(lst) - n]); del lst[:len(lst) - n]
    return lst

def read(kind):
    p = _path(kind)
    if not os.path.exists(p): return []
    with gzip.open(p, "rt") as f: return [json.loads(l) for l in f if l.strip()]

def sizes():
    d = f"{C.STATE_DIR}/archive"
    return {f.split(".")[0]: round(os.path.getsize(os.path.join(d, f)) / 1e6, 2) for f in os.listdir(d)} if os.path.isdir(d) else {}
