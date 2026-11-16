"""PER-COIN COST TABLE (audit Nov 16; report only, nothing trades on it yet): round-trip cost = Binance taker fees on both sides +
the slippage the demo actually measured (fill vs the paper price at entry). Until a coin has 10 measured fills, the default is used."""
import numpy as np
from . import config as C

TAKER = 0.0005 * 2            # Binance futures taker, both sides

def table(R):
    D = R.get("demo") or {}; fills = {}
    for x in (D.get("log") or []) + (D.get("slipLog") or []):
        if x.get("open") and x.get("slipPct") is not None: fills.setdefault(x["open"].replace("USDT", ""), []).append(float(x["slipPct"]) / 100)
    D["slipLog"] = [x for x in (D.get("log") or []) if x.get("open") and x.get("slipPct") is not None][-300:] or D.get("slipLog", [])
    allv = [v for vs in fills.values() for v in vs]
    out = {"default": C.COST_DEFAULT, "measuredFills": len(allv), "avgEntrySlip": round(float(np.mean(allv)) * 100, 3) if allv else None, "coins": {}}
    for c, vs in fills.items():
        est = TAKER + 2 * max(0.0, float(np.mean(vs)))        # entry slippage measured; the exit is assumed to cost the same
        out["coins"][c] = {"fills": len(vs), "slipPct": round(float(np.mean(vs)) * 100, 3), "roundTrip": round(est if len(vs) >= 10 else C.COST_DEFAULT, 4), "measured": len(vs) >= 10}
    R["costs"] = out
    return out
