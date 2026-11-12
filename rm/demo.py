"""BINANCE DEMO ACCOUNT: the Arbiter's trades mirrored as real orders on Binance's free futures demo (fake money, real order handling).
The paper books stay the record; this is the rehearsal of real-money trading (Build C) and a check of fills, fees and slippage.
Keys: BINANCE_DEMO_KEY / BINANCE_DEMO_SECRET (GitHub secrets). Only the demo address is ever used here."""
import os, time, hmac, hashlib, json, math, urllib.parse, requests

BASE = "https://demo-fapi.binance.com"; REAL = "https://fapi.binance.com"
S = requests.Session()

def _keys(): return os.environ.get("BINANCE_DEMO_KEY", "").strip(), os.environ.get("BINANCE_DEMO_SECRET", "").strip()

def req(method, path, signed=True, **params):
    k, s = _keys()
    if signed:
        params["timestamp"] = int(time.time() * 1000); params.setdefault("recvWindow", 10000)
        q = urllib.parse.urlencode(params); q += "&signature=" + hmac.new(s.encode(), q.encode(), hashlib.sha256).hexdigest()
    else: q = urllib.parse.urlencode(params)
    via = os.environ.get("RM_OWN_RELAY", "").strip()
    if via:      # Binance refuses GitHub's US machines: the signed request goes through the own server in Stockholm (it holds no secret)
        base = via.split("/api/")[0].rstrip("/")
        r = S.post(base + "/demo/fwd", json={"method": method, "path": path, "query": q, "key": k}, headers={"x-relay-key": os.environ.get("RM_RELAY_KEY", "")}, timeout=30)
    else: r = S.request(method, f"{BASE}{path}?{q}", headers={"X-MBX-APIKEY": k}, timeout=20)
    try: j = r.json()
    except Exception: j = {"raw": r.text[:200]}
    if r.status_code >= 400: raise IOError(f"HTTP {r.status_code} {path}: {str(j)[:200]}")
    return j

_INFO = {}
def info(sym):
    if not _INFO:
        for x in req("GET", "/fapi/v1/exchangeInfo", signed=False)["symbols"]:
            f = {g["filterType"]: g for g in x["filters"]}
            _INFO[x["symbol"]] = {"step": float(f["LOT_SIZE"]["stepSize"]), "minQty": float(f["LOT_SIZE"]["minQty"]), "tick": float(f["PRICE_FILTER"]["tickSize"]),
                                  "minNotional": float((f.get("MIN_NOTIONAL") or {}).get("notional", 5))}
    return _INFO.get(sym)

def _fl(x, step): return math.floor(x / step + 1e-9) * step
def _fmt(x, step): d = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0; return f"{x:.{d}f}"

def cond(sym, side, typ, trigger, tick, qty=None, step=None):
    """a stop / take-profit order that waits on the exchange. Returns (kind, id)."""
    base = dict(symbol=sym, side=side, type=typ, workingType="MARK_PRICE")
    if qty is None: base["closePosition"] = "true"
    else: base.update(quantity=_fmt(qty, step), reduceOnly="true")
    try:
        o = req("POST", "/fapi/v1/order", stopPrice=_fmt(trigger, tick), **base); return "o", o.get("orderId")
    except IOError as e:
        if "lgo" not in str(e) and "-4120" not in str(e): raise
    o = req("POST", "/fapi/v1/algoOrder", algoType="CONDITIONAL", triggerPrice=_fmt(trigger, tick), **base); return "a", o.get("algoId")

def uncond(sym, ref):
    if not ref or not isinstance(ref, (list, tuple)): return
    try:
        if ref[0] == "a": req("DELETE", "/fapi/v1/algoOrder", algoId=ref[1])
        else: req("DELETE", "/fapi/v1/order", symbol=sym, orderId=ref[1])
    except IOError: pass

def cancel_all(sym):
    for path in ("/fapi/v1/allOpenOrders", "/fapi/v1/algoOpenOrders"):
        try: req("DELETE", path, symbol=sym)
        except IOError: pass

def check():
    """can we reach the demo, what is in it, and do its prices follow the real market?"""
    out = {"keys": all(_keys())}
    bal = req("GET", "/fapi/v2/balance"); out["balance"] = {b["asset"]: round(float(b["balance"]), 2) for b in bal if float(b["balance"])}
    acc = req("GET", "/fapi/v2/account"); out["wallet"] = round(float(acc["totalWalletBalance"]), 2); out["openPositions"] = sum(1 for p in acc["positions"] if float(p["positionAmt"]))
    out["hedgeMode"] = req("GET", "/fapi/v1/positionSide/dual").get("dualSidePosition")
    dp = {x["symbol"]: float(x["price"]) for x in req("GET", "/fapi/v1/ticker/price", signed=False)}
    try: rp = {x["symbol"]: float(x["price"]) for x in S.get("https://data-api.binance.vision/api/v3/ticker/price", timeout=20).json()}     # the real market (spot; futures is a hair apart)
    except Exception as e: rp = {}; out["realPricesError"] = str(e)[:120]
    gaps = {s: round((dp[s] / rp[s] - 1) * 100, 3) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT", "HBARUSDT", "DOGEUSDT") if s in dp and s in rp}
    out["priceGapPct"] = gaps; out["symbols"] = len(dp)
    return out

def mirror(R, log=print):
    """keep the demo account holding what the Arbiter holds, scaled to the demo's size. Idempotent: run every few minutes."""
    if not all(_keys()): return {"skipped": "no demo keys"}
    A = R.get("acct") or {}; D = R.setdefault("demo", {"pos": {}, "log": []}); done = []
    acc = req("GET", "/fapi/v2/account"); wallet = float(acc["totalWalletBalance"]); D["wallet"] = round(wallet, 2)
    held = {p["symbol"]: float(p["positionAmt"]) for p in acc["positions"] if float(p["positionAmt"])}
    eqA = A.get("equity", A.get("cash", 500.0)) or 500.0; scale = wallet / eqA
    want = {}
    D.setdefault("since", int(time.time()))
    for p in A.get("open", []):
        sym = p["coin"] + "USDT"; pid = f"{p['coin']}|{p['t']}"
        if p.get("half") and (D["pos"].get(sym) or {}).get("pid") != pid: continue     # already half-closed on paper before the demo saw it: stays paper-only
        want[sym] = (p, pid)
    # 1) close what the Arbiter no longer holds
    for sym, amt in held.items():
        if sym not in want:
            cancel_all(sym)
            o = req("POST", "/fapi/v1/order", symbol=sym, side="BUY" if amt < 0 else "SELL", type="MARKET", quantity=_fmt(abs(amt), info(sym)["step"]), reduceOnly="true", newOrderRespType="RESULT")
            done.append({"close": sym, "px": float(o.get("avgPrice") or 0)}); D["pos"].pop(sym, None)
    # 2) open what it holds and the demo does not; keep stop / target orders in line with the paper position
    for sym, (p, pid) in want.items():
        I = info(sym)
        if not I: done.append({"skip": sym, "why": "not on the demo"}); continue
        side = "SELL" if p["sd"] < 0 else "BUY"; opp = "BUY" if side == "SELL" else "SELL"; st = D["pos"].get(sym) or {}
        if st.get("pid") != pid or sym not in held:
            if st.get("pid") == pid and sym not in held:                       # the exchange closed it (its stop or target filled): do not reopen
                continue
            try: req("POST", "/fapi/v1/leverage", symbol=sym, leverage=int(min(20, p.get("lev") or 5)))
            except Exception: pass
            try: req("POST", "/fapi/v1/marginType", symbol=sym, marginType="ISOLATED")
            except Exception: pass
            qty = _fl(p.get("notional", 0) * scale / (p.get("mark") or p["entry"]), I["step"])
            if qty < I["minQty"] or qty * p["entry"] < I["minNotional"]: done.append({"skip": sym, "why": "too small"}); continue
            o = req("POST", "/fapi/v1/order", symbol=sym, side=side, type="MARKET", quantity=_fmt(qty, I["step"]), newOrderRespType="RESULT")
            fill = float(o.get("avgPrice") or 0); st = {"pid": pid, "qty": qty, "fill": fill, "paperEntry": p["entry"], "at": time.strftime("%Y-%m-%d %H:%M", time.gmtime())}
            D["pos"][sym] = st; done.append({"open": sym, "side": side, "qty": qty, "fill": fill, "paper": p["entry"], "late": p["t"] < D["since"] - 3600, "slipPct": round((fill / p["entry"] - 1) * 100 * (1 if side == "BUY" else -1), 3) if fill else None})
        # stop order (whole position) and the half target (before the half is taken)
        stp = float(_fmt(p["stop"], I["tick"]))
        if st.get("stop") != stp:
            uncond(sym, st.get("stopId"))
            st["stopId"] = cond(sym, opp, "STOP_MARKET", stp, I["tick"]); st["stop"] = stp; done.append({"stop": sym, "at": stp})
        if not p.get("half") and not st.get("tpId"):
            hq = _fl(st["qty"] / 2, I["step"])
            if hq >= I["minQty"]:
                st["tpId"] = cond(sym, opp, "TAKE_PROFIT_MARKET", p["target"], I["tick"], hq, I["step"]); done.append({"target": sym, "at": p["target"], "qty": hq})
        if p.get("half") and sym in held and abs(held[sym]) > st["qty"] * 0.75:      # paper took half, the exchange has not (yet): take it
            hq = _fl(abs(held[sym]) / 2, I["step"])
            if st.get("tpId") and st["tpId"] != "done": uncond(sym, st["tpId"])
            o = req("POST", "/fapi/v1/order", symbol=sym, side=opp, type="MARKET", quantity=_fmt(hq, I["step"]), reduceOnly="true", newOrderRespType="RESULT")
            st["tpId"] = "done"; done.append({"half": sym, "px": float(o.get("avgPrice") or 0)})
    D["log"] = (D.get("log") or [])[-40:] + [{"at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), **d} for d in done]
    D["at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()); D["holding"] = len(held)
    if done: log(f"   demo account: {done}")
    return {"wallet": D["wallet"], "actions": done}
