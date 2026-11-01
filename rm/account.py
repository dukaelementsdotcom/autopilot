"""The $500 account: trades ONLY the machine's current best rule ("champion of champions"), compounding, forward in time.
Honest by construction:
  - it starts at the moment it is created; it never trades on data from before its own start
  - the best rule is chosen at the END of a run and only used for hours that arrive LATER (no hindsight)
  - every entry is at the real open price of the hour the signal fired; exits replay real hourly highs/lows (TP / stop / time)
  - Binance's archive arrives ~1 day late, so trades are booked a day after they happen; open positions are marked at LIVE prices
Sizing: each trade uses 10% of the account as margin at 10x leverage; at most 3 trades open at once; a trade can lose at most its margin."""
import numpy as np, requests
from . import config as C

START = 500.0; MARGIN_FRAC = 0.10; LEV = 10; SLOTS = 3; MIN_TRADES = 30
HOUR = 3600

def _score(J, t):
    """how convincing a champion is, using only trades AFTER its discovery+validation windows (final exam + live)"""
    tt, rr = J.series(t["h"], t_from=J.hold0)
    s = J.stats(tt, rr)
    return (s.get("t", 0) if s.get("n", 0) >= MIN_TRADES and s.get("mean", 0) > 0 else -99), s

def pick_leader(mem, J):
    best = None
    for k, t in mem["tests"].items():
        if t["status"] != "champion" or t["h"]["kind"] != "event": continue
        sc, s = _score(J, t)
        if best is None or sc > best[0]: best = (sc, k, s)
    return best if best and best[0] > -99 else None

def live_prices(coins):
    try:
        r = requests.get("https://data-api.binance.vision/api/v3/ticker/price", timeout=20)
        px = {d["symbol"]: float(d["price"]) for d in r.json()}
        return {c: px.get(f"{c}USDT") for c in coins}
    except Exception: return {}

def step(mem, J, now_iso, log=print):
    A = mem.get("account")
    if A is None:                                                   # birth: starts at the newest hour of data, trades only what comes after
        A = mem["account"] = {"born": now_iso, "t0": int(J.t.max()), "lastT": int(J.t.max()), "cash": START, "open": [], "closed": [],
                              "leader": None, "leaders": [], "curve": [[int(J.t.max()), START]]}
    tests = mem["tests"]; lead = A.get("leader")
    newest = int(J.t.max())
    # 1) replay the hours that arrived since the last run, in time order, with the leader chosen BEFORE they arrived
    def walk(p):
        w = J._walk(p["coin"], p["t"], p["sd"], p["exit"])
        if w: p["entry"] = float(w["entry"]); p["tp"] = w.get("tp"); p["sl"] = w.get("sl"); p["_w"] = w
        return w
    def close_until(t_now):
        keep = []
        for p in sorted(A["open"], key=lambda p: (p.get("_w") or {}).get("exitT") or 1e18):
            w = p.get("_w") or walk(p)
            if w and w["how"] != "open" and w["exitT"] <= t_now:
                pnl = max(-p["margin"], p["margin"] * LEV * w["ret"]); A["cash"] = round(A["cash"] + pnl, 2)
                A["closed"].append({**{k: v for k, v in p.items() if k not in ("sd", "_w")}, "exitT": w["exitT"], "exitPx": w["exitPx"], "how": w["how"],
                                    "pct": round(w["ret"] * 100, 2), "usd": round(pnl, 2), "after": A["cash"]})
                A["curve"].append([w["exitT"], A["cash"]])
            else: keep.append(p)
        A["open"] = keep
    for p in A["open"]: walk(p)
    if lead and lead in tests and tests[lead]["status"] == "champion" and newest > A["lastT"]:
        h = tests[lead]["h"]; P = J.P
        m = J._mask(h) & (J.t > A["lastT"]) & (J.t <= newest)
        side = {"long": np.ones(len(P)), "short": -np.ones(len(P)), "with": np.sign(J.r1), "against": -np.sign(J.r1)}[h["dir"]]
        for i in sorted(np.flatnonzero(m), key=lambda i: J.t[i]):
            c, t0, sd = str(J.coin[i]), int(J.t[i]), int(side[i])
            close_until(t0)
            if sd == 0 or len(A["open"]) >= SLOTS or any(p["coin"] == c for p in A["open"]): continue
            last_same = max([p["t"] for p in A["closed"] if p["coin"] == c] or [-1e18])
            if t0 - last_same < h.get("cool", 24) * HOUR: continue
            p = {"coin": c, "side": "LONG" if sd > 0 else "SHORT", "sd": sd, "t": t0, "exit": h["exit"], "rule": tests[lead].get("no"),
                 "margin": round(A["cash"] * MARGIN_FRAC, 2)}
            walk(p); A["open"].append(p)
    close_until(newest + HOUR)
    for p in A["open"]:
        w = p.pop("_w", None)
        if w: p["markPx"] = w["exitPx"]; p["ret"] = w["ret"]
    A["lastT"] = max(A["lastT"], newest)
    # 3) open trades valued at LIVE prices
    lp = live_prices({p["coin"] for p in A["open"]}); unreal = 0.0
    for p in A["open"]:
        if lp.get(p["coin"]) and p.get("entry"):
            r = p["sd"] * (lp[p["coin"]] / p["entry"] - 1) - C.FEE; p["livePx"] = lp[p["coin"]]
        else: r = p.get("ret", 0)
        p["unreal"] = round(max(-p["margin"], p["margin"] * LEV * r), 2); unreal += p["unreal"]
    A["equity"] = round(A["cash"] + unreal, 2)
    # 4) re-pick the champion of champions for the NEXT hours (re-tested every run on the newest data)
    best = pick_leader(mem, J)
    if best:
        sc, k, s = best
        if k != lead:
            A["leaders"].append({"at": now_iso, "rule": tests[k].get("no"), "text": tests[k].get("plain") or tests[k]["text"], "score": round(sc, 2),
                                 "why": f"best record after its exams: {s['n']} trades, avg {s['mean']:+}% per trade, {s['hit']}% won (t {s['t']})"})
            A["leader"] = k
    elif lead and (lead not in tests or tests[lead]["status"] != "champion"): A["leader"] = None
    A["closed"] = A["closed"][-400:]; A["curve"] = A["curve"][-1000:]; A["leaders"] = A["leaders"][-50:]
    log(f"account: ${A['equity']} (cash ${A['cash']}, {len(A['open'])} open, {len(A['closed'])} closed), leader rule #{tests[A['leader']].get('no') if A.get('leader') else '-'}")
    return A
