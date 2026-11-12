import json, time, traceback
from . import demo
out = {}
try:
    out["check"] = demo.check(); sym = "DOGEUSDT"; I = demo.info(sym); px = float(demo.req("GET", "/fapi/v1/ticker/price", signed=False, symbol=sym)["price"])
    try: demo.req("POST", "/fapi/v1/leverage", symbol=sym, leverage=5)
    except Exception as e: out["lev"] = str(e)[:150]
    qty = demo._fl(30 / px, I["step"]); qty = max(qty, I["minQty"])
    o = demo.req("POST", "/fapi/v1/order", symbol=sym, side="SELL", type="MARKET", quantity=demo._fmt(qty, I["step"]), newOrderRespType="RESULT"); out["open"] = {"qty": qty, "fill": o.get("avgPrice"), "status": o.get("status")}
    out["stop"] = demo.cond(sym, "BUY", "STOP_MARKET", px * 1.03, I["tick"])
    out["tp"] = demo.cond(sym, "BUY", "TAKE_PROFIT_MARKET", px * 0.98, I["tick"], demo._fl(qty / 2, I["step"]) or qty, I["step"])
    try: out["openOrders"] = [(x["type"], x.get("stopPrice")) for x in demo.req("GET", "/fapi/v1/openOrders", symbol=sym)]
    except Exception as e: out["openOrders"] = str(e)[:150]
    try: out["algoOpen"] = str(demo.req("GET", "/fapi/v1/openAlgoOrders", symbol=sym))[:300]
    except Exception as e: out["algoOpen"] = str(e)[:150]
    demo.uncond(sym, out["tp"]); demo.cancel_all(sym)
    o = demo.req("POST", "/fapi/v1/order", symbol=sym, side="BUY", type="MARKET", quantity=demo._fmt(qty, I["step"]), reduceOnly="true", newOrderRespType="RESULT"); out["close"] = {"fill": o.get("avgPrice"), "status": o.get("status")}
    acc = demo.req("GET", "/fapi/v2/account"); out["after"] = {"wallet": acc["totalWalletBalance"], "open": [p["symbol"] for p in acc["positions"] if float(p["positionAmt"])]}
    try: out["leftOrders"] = len(demo.req("GET", "/fapi/v1/openOrders", symbol=sym))
    except Exception as e: out["leftOrders"] = str(e)[:100]
except Exception: out["error"] = traceback.format_exc()[-900:]
print(json.dumps(out, indent=1, default=str)); json.dump(out, open("labtest.json", "w"), default=str)
