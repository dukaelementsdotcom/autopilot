"""READ-ONLY: what the Binance demo holds right now (positions, normal and conditional orders). Changes nothing."""
import json, traceback
from . import demo
out = {}
try:
    acc = demo.req("GET", "/fapi/v2/account"); out["wallet"] = acc["totalWalletBalance"]
    out["positions"] = [{k: p.get(k) for k in ("symbol", "positionAmt", "entryPrice", "leverage", "isolated")} for p in acc["positions"] if float(p["positionAmt"])]
    for sym in [p["symbol"] for p in out["positions"]] or ["ENAUSDT"]:
        try: out[sym + " openOrders"] = [{k: x.get(k) for k in ("type", "side", "stopPrice", "origQty", "closePosition", "reduceOnly")} for x in demo.req("GET", "/fapi/v1/openOrders", symbol=sym)]
        except Exception as e: out[sym + " openOrders"] = str(e)[:200]
        try: out[sym + " algoOpen"] = demo.req("GET", "/fapi/v1/openAlgoOrders", symbol=sym)
        except Exception as e: out[sym + " algoOpen"] = str(e)[:200]
except Exception: out["error"] = traceback.format_exc()[-900:]
print(json.dumps(out, indent=1, default=str)); json.dump(out, open("labtest.json", "w"), default=str)
