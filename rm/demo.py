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

def _fill(sym, o):
    px = float(o.get("avgPrice") or 0)
    if not px and o.get("orderId"):
        try: px = float(req("GET", "/fapi/v1/order", symbol=sym, orderId=o["orderId"]).get("avgPrice") or 0)
        except IOError: pass
    return px or None

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

def _mkt(sym, side, qty, reduce=False):
    I = info(sym); kw = {"reduceOnly": "true"} if reduce else {}
    o = req("POST", "/fapi/v1/order", symbol=sym, side=side, type="MARKET", quantity=_fmt(qty, I["step"]), newOrderRespType="RESULT", **kw)
    return _fill(sym, o)

def mirror(R, log=print):
    """THE DEMO IS A 1:1 COPY OF THE ARBITER: the same coin amount, the same leverage, isolated margin (so the same margin and the same
    liquidation price), the same stop, the same half-target, the same break-even and trailing moves, closed when the Arbiter closes.
    The account starts equal to the Arbiter's and from then on moves only with the demo's own real fills and fees, so any gap between
    the two accounts is exactly the cost of real execution. Idempotent: the hourly run and the 5-minute fast lane both call it."""
    if not all(_keys()): return {"skipped": "no demo keys"}
    A = R.get("acct") or {}; D = R.setdefault("demo", {"pos": {}, "log": []}); done = []; now = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    acc = req("GET", "/fapi/v2/account"); real = float(acc["totalWalletBalance"])
    held = {p["symbol"]: float(p["positionAmt"]) for p in acc["positions"] if float(p["positionAmt"])}
    MODE = "mirror-1:1-v1"
    if D.get("mode") != MODE:                                  # start (again) as an exact copy: flat, account = the Arbiter's cash
        for sym, amt in held.items():
            cancel_all(sym); done.append({"close": sym, "px": _mkt(sym, "BUY" if amt < 0 else "SELL", abs(amt), True), "why": "start of the 1:1 copy"})
        acc = req("GET", "/fapi/v2/account"); real = float(acc["totalWalletBalance"]); held = {}
        D.clear(); D.update({"mode": MODE, "pos": {}, "log": [], "base": round(real - A.get("cash", 500.0), 4), "start": round(A.get("cash", 500.0), 2), "since": int(time.time()), "startedAt": now})
    wallet = real - D["base"]                                  # the demo account: started equal to the Arbiter's, then only its own results
    want = {p["coin"] + "USDT": p for p in A.get("open", [])}
    # 1) the Arbiter closed it -> close it here
    for sym, amt in list(held.items()):
        if sym not in want:
            cancel_all(sym); px = _mkt(sym, "BUY" if amt < 0 else "SELL", abs(amt), True); st = D["pos"].pop(sym, {})
            pe = _paper_exit(A, st.get("pid")); done.append({"close": sym, "px": px, "paperExit": pe}); held.pop(sym)
            _record(D, sym, st, f"closed with the Arbiter ({(pe or {}).get('how') or 'Arbiter closed'})", pe)
    for sym in [k for k in D["pos"] if k not in want and k not in held]:
        st = D["pos"].get(sym) or {}; _record(D, sym, st, "Binance filled its stop / target", _paper_exit(A, st.get("pid")))
        cancel_all(sym); D["pos"].pop(sym, None)
    # 2) the Arbiter holds it -> the same here
    for sym, p in want.items():
        I = info(sym); pid = f"{p['coin']}|{p['t']}"
        if not I: done.append({"skip": sym, "why": "not on the demo"}); continue
        side = "SELL" if p["sd"] < 0 else "BUY"; opp = "BUY" if side == "SELL" else "SELL"; st = D["pos"].get(sym) or {}
        from . import referee as RF
        size = float(RF._size(p) / RF._avg(p)) if p.get("units") else float(p.get("size") or 0)      # coins held now (halved after the target)
        if st.get("pid") == pid and sym not in held:                                 # the exchange closed it first (its stop or target): never reopen
            _record(D, sym, st, "Binance filled its stop before the Arbiter closed it", None); continue
        if st.get("pid") != pid and sym in held:                                    # held but not on record (record lost): adopt, never open twice
            st = D["pos"][sym] = {"pid": pid, "qty": abs(held[sym]), "qty0": abs(held[sym]), "fill": p["entry"], "lev": p.get("lev"), "adopted": True, "t0": int(time.time()), "t0ms": int(time.time() * 1000) - 60000}; cancel_all(sym)
        if st.get("pid") != pid:
            try: req("POST", "/fapi/v1/marginType", symbol=sym, marginType="ISOLATED")
            except IOError: pass
            try: req("POST", "/fapi/v1/leverage", symbol=sym, leverage=int(p.get("lev") or 10))
            except IOError as e: done.append({"note": sym, "why": f"leverage {p.get('lev')}x refused: {str(e)[:80]}"})
            qty = _fl(size, I["step"])
            if qty < I["minQty"] or qty * p["entry"] < I["minNotional"]: done.append({"skip": sym, "why": "below Binance's minimum size"}); continue
            t0ms = int(time.time() * 1000) - 2000; fill = _mkt(sym, side, qty) or 0
            st = D["pos"][sym] = {"pid": pid, "qty": qty, "qty0": qty, "fill": fill, "lev": p.get("lev"), "paperEntry": p["entry"], "at": now, "late": p["t"] < D["since"] - 3600,
                                  "t0": int(time.time()), "t0ms": t0ms, "grade": p.get("grade"), "prob": p.get("prob"), "card": p.get("card")}
            done.append({"open": sym, "side": side, "qty": qty, "lev": p.get("lev"), "fill": fill, "paper": p["entry"],
                         "slipPct": round((fill / p["entry"] - 1) * 100 * (1 if side == "BUY" else -1), 3) if fill else None, "late": st["late"]})
            held[sym] = qty if side == "BUY" else -qty
        # the Arbiter took (part of) the profit -> the same size here (only ever reduced to match, never added back)
        if sym in held and abs(held[sym]) > size * 1.02 + I["step"]:
            cut = _fl(abs(held[sym]) - size, I["step"])
            if cut >= I["minQty"]:
                if st.get("tpId") and st["tpId"] != "done": uncond(sym, st["tpId"])
                done.append({"half": sym, "px": _mkt(sym, opp, cut, True)}); st["tpId"] = "done"; held[sym] = held[sym] - (cut if side == "BUY" else -cut)
        # the same stop (moved when the Arbiter moves it) and the same half-target, waiting on the exchange
        stp = float(_fmt(p["stop"], I["tick"]))
        if st.get("stop") != stp:
            uncond(sym, st.get("stopId")); st["stopId"] = cond(sym, opp, "STOP_MARKET", stp, I["tick"]); st["stop"] = stp; done.append({"stop": sym, "at": stp})
        if not p.get("half") and not st.get("tpId"):
            hq = _fl(st["qty"] / 2, I["step"])
            if hq >= I["minQty"]: st["tpId"] = cond(sym, opp, "TAKE_PROFIT_MARKET", p["target"], I["tick"], hq, I["step"]); st["tp"] = p["target"]; done.append({"target": sym, "at": p["target"], "qty": hq})
        st.update(side=("LONG" if side == "BUY" else "SHORT"), size=abs(held.get(sym, 0)), tgt=p["target"], grade=p.get("grade"), coin=p["coin"])
    # 3) the demo account in the SAME shape as the Arbiter's (the dashboard draws both with the same panel), from Binance's own numbers
    if done:
        acc = req("GET", "/fapi/v2/account"); real = float(acc["totalWalletBalance"]); wallet = real - D["base"]
    cv = D.setdefault("curve", [[D.get("since", int(time.time())), D.get("start")]])
    if abs(wallet - cv[-1][1]) > 0.01: cv.append([int(time.time()), round(wallet, 2)]); D["curve"] = cv[-500:]
    opn = []
    try:
        for r in req("GET", "/fapi/v2/positionRisk"):
            amt = float(r.get("positionAmt") or 0)
            if not amt: continue
            sym = r["symbol"]; st = D["pos"].get(sym) or {}; e_ = float(r["entryPrice"]); lev = int(float(r.get("leverage") or st.get("lev") or 10)); n = abs(amt) * e_
            opn.append({"coin": sym.replace("USDT", ""), "side": "LONG" if amt > 0 else "SHORT", "entry": e_, "lev": lev, "notional": round(n, 2),
                        "margin": round(float(r.get("isolatedMargin") or 0) or n / lev, 2), "stop": st.get("stop"), "target": None if st.get("tpId") == "done" else st.get("tp"),
                        "half": st.get("tpId") == "done", "t": st.get("t0") or int(time.time()), "mark": float(r.get("markPrice") or e_), "unreal": round(float(r.get("unRealizedProfit") or 0), 2),
                        "grade": st.get("grade"), "prob": st.get("prob"), "card": st.get("card"), "liqBinance": float(r.get("liquidationPrice") or 0) or None})
    except IOError: pass
    D["acct"] = {"born": D.get("startedAt"), "start": D.get("start"), "cash": round(wallet, 2), "equity": round(wallet + sum(o["unreal"] for o in opn), 2), "open": opn,
                 "closed": D.get("closedTrades", [])[-300:], "curve": D.get("curve") or [[D.get("since", int(time.time())), D.get("start")]], "decisions": []}
    pa = sorted((p["coin"], p["side"]) for p in A.get("open", [])); pd_ = sorted((o["coin"], o["side"]) for o in opn)
    D["sync"] = {"ok": pa == pd_, "arbiter": pa, "demo": pd_, "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    # 4) what the dashboard shows live (prices are added in the browser)
    D["wallet"] = round(wallet, 2); D["realWallet"] = round(real, 2); D["holding"] = len(held); D["at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    D["arbiterCash"] = round(A.get("cash", 0), 2); D["gap"] = round(wallet - A.get("cash", 0), 2)
    D["log"] = (D.get("log") or [])[-60:] + [{"at": now, **d} for d in done]
    if done: log(f"   demo account (1:1 copy): {done}")
    return {"wallet": D["wallet"], "actions": done}

def _income(sym, t0ms):
    """what Binance itself booked for this trade: realised profit, fees, funding (since it opened)"""
    try: rows = req("GET", "/fapi/v1/income", symbol=sym, startTime=int(t0ms), limit=1000)
    except IOError: return None
    return round(sum(float(x["income"]) for x in rows if x.get("incomeType") in ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")), 2)

def _exit_px(sym, t0ms, opp):
    try: tr = [x for x in req("GET", "/fapi/v1/userTrades", symbol=sym, startTime=int(t0ms), limit=500) if x.get("side") == opp]
    except IOError: return None
    q = sum(float(x["qty"]) for x in tr)
    return round(sum(float(x["price"]) * float(x["qty"]) for x in tr) / q, 8) if q else None

def _record(D, sym, st, how, paper=None):
    """a finished trade, as Binance booked it, next to the Arbiter's paper result"""
    if st.get("recorded") or not st.get("t0ms"): return
    side = st.get("side") or "SHORT"; opp = "BUY" if side == "SHORT" else "SELL"
    usd = _income(sym, st["t0ms"]); ex = _exit_px(sym, st["t0ms"], opp)
    n = float(st.get("qty0") or st.get("qty") or 0) * float(st.get("fill") or 0); lev = st.get("lev") or 10; mg = n / max(1, lev)
    D.setdefault("closedTrades", []).append({"coin": st.get("coin") or sym.replace("USDT", ""), "side": side, "entry": st.get("fill"), "exitPx": ex, "usd": usd if usd is not None else 0.0,
        "lev": lev, "notional": round(n, 2), "margin": round(mg, 2), "roe": round((usd or 0) / mg * 100, 1) if mg else None, "how": how, "t": st.get("t0"), "exitT": int(time.time()),
        "grade": st.get("grade"), "paperUsd": (paper or {}).get("usd"), "paperExit": (paper or {}).get("px"), "paperHow": (paper or {}).get("how")})
    D["closedTrades"] = D["closedTrades"][-300:]; st["recorded"] = True

def _paper_exit(A, pid):
    for c in reversed((A.get("closed") or [])[-30:]):
        if pid and f"{c.get('coin')}|{c.get('t')}" == pid: return {"px": c.get("exitPx"), "how": c.get("how"), "usd": c.get("usd")}
    return None
