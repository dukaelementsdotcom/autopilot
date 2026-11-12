"""SERVER GUARD + SHOCK MEMORY. GitHub does the thinking, the always-on server does the reacting.
learn():  from history, does exiting after a sudden move against a trade (within one hour, x times the stop distance) beat holding?
          x is chosen on the validation year; if no x helps, shock exits stay off (the challenger is not created).
sync():   every run hands the server all open paper positions (stop, target, and the shock threshold for accounts that use it).
apply():  the server's minute-by-minute shock events close positions in the accounts that run the shock-exit policy."""
import os, time, json, numpy as np, requests
from . import config as C

H = 3600

def _srv():
    url = os.environ.get("RM_OWN_RELAY", ""); key = os.environ.get("RM_RELAY_KEY", "")
    return (url.split("/api/")[0], {"x-relay-key": key}) if url and key else (None, None)

def learn(J, log=print):
    from . import referee as RF
    out = {}
    for name, side in (("long", 1), ("short", -1)):
        rows = RF._rows(J, J.val0, J.hold0, every=6); stp = RF.stop_pct(J.dvol[rows]); y, base_ret = RF.barrier(J, rows, side, RF.TARGET, stp, RF.HORIZON)
        best = (0.0, None)
        for x in (0.5, 0.75):
            ret = base_ret.copy(); e = J.O[rows]; tg = e * (1 + side * RF.TARGET); st = e * (1 - side * stp); alive = np.ones(len(rows), bool); last = J.last[rows]
            for j in range(RF.HORIZON):
                k = rows + j; ok = alive & (k <= last); kk = np.where(ok, k, rows)
                hi, lo, op, cl = J.H[kk], J.L[kk], J.O[kk], J.Cl[kk]
                s_hit = ok & ((lo <= st) if side > 0 else (hi >= st)); t_hit = ok & ~s_hit & ((hi >= tg) if side > 0 else (lo <= tg))
                fast = ((op - lo) / op if side > 0 else (hi - op) / op); shock = ok & ~s_hit & ~t_hit & (fast >= x * stp)
                ret[shock] = side * (cl[shock] / e[shock] - 1) - C.FEE          # exit at that hour's close
                alive &= ~(s_hit | t_hit | shock)
            gain = float(np.nanmean(ret - base_ret)) * 100
            if gain > max(best[0], 0.05): best = (gain, x)              # must be worth at least 0.05% per trade, or it is noise
        out[name] = {"x": best[1], "gainPerTrade": round(best[0], 4)}
    log(f"   shock memory: {out}")
    return {"at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), **out}

def sync(R, log=print):
    base, hd = _srv()
    if not base: return {"skipped": "no own server"}
    rule = R.get("guardRule") or {}; pos = []
    accts = [("champion", R.get("acct") or {}, ((R.get("champ") or {}).get("cfg") or {}))] + [(n, c.get("acct") or {}, {**((R.get("champ") or {}).get("cfg") or {}), **c.get("change", {})}) for n, c in (R.get("chal") or {}).items()]
    for n, A, cfg in accts:
        for p in A.get("open", []):
            x = (rule.get(p["side"].lower()) or {}).get("x") if cfg.get("shock") else None
            pos.append({"id": f"{n}|{p['coin']}|{p['t']}", "coin": p["coin"], "sd": p["sd"], "entry": p["entry"], "stop": p["stop"], "target": p["target"],
                        "shockPct": round(x * p["stopPct"], 5) if x else 0})
    try:
        r = requests.post(base + "/guard/set", json={"positions": pos}, headers=hd, timeout=15); return {"watching": r.json().get("watching"), "sent": len(pos)}
    except Exception as e: return {"error": str(e)[:120]}

def apply(R, log=print):
    """the server's minute-by-minute events are booked at the minute and price they happened:
    target -> half taken, the rest's stop to break-even; stop -> closed; shock -> closed (accounts with the shock-exit policy).
    Safe to repeat: an event older than what a position has already processed is ignored, so the hourly run and the fast lane
    (every few minutes) can both call this."""
    base, hd = _srv()
    if not base: return {}
    G = R.setdefault("guard", {"since": 0, "applied": []})
    try: ev = requests.get(base + "/guard/events", params={"since": G["since"]}, headers=hd, timeout=15).json().get("events", [])
    except Exception as e: return {"error": str(e)[:120]}
    from . import referee as RF
    n_ = 0; booked = []
    for e in sorted(ev, key=lambda e: e["t"]):
        acct_name, coin, t = e["id"].split("|"); te = int(e["t"]); px = float(e["px"])
        A = R.get("acct") if acct_name == "champion" else ((R.get("chal") or {}).get(acct_name) or {}).get("acct")
        if not A: continue
        for p in list(A.get("open", [])):
            if not (p["coin"] == coin and str(p["t"]) == t) or "target" not in p or "stop" not in p: continue
            bar = te // H * H
            if te <= p.get("evT", 0) or (te < p.get("lastBar", p["t"] - H) + H and p.get("evBar") != bar): continue      # the hourly replay already covered that minute
            sd = p["sd"]; did = None
            if e["kind"] == "shock": RF._close(A, p, px, "shock exit (server guard)", te); n_ += 1; did = "shock exit"
            elif e["kind"] == "target" and not p.get("half") and ((sd > 0 and px >= p["target"]) or (sd < 0 and px <= p["target"])):
                RF._close(A, p, p["target"], f"half at +{p.get('tgtPct', RF.TARGET)*100:.0f}% (to the minute)", te, part=0.5); p["half"] = True
                p["stop"] = p["entry"] * (1 + sd * C.FEE * 2); did = "half at target"
            elif e["kind"] in ("stop", "target") and ((sd > 0 and px <= p["stop"]) or (sd < 0 and px >= p["stop"])):
                RF._close(A, p, p["stop"], ("break-even / trailing stop" if p.get("half") else "stop") + " (to the minute)", te); did = "stop"
            if did:
                p["evT"] = te; p["evBar"] = bar; p["lastBar"] = max(p.get("lastBar", 0), bar); p["best"] = max(p["best"], px) if sd > 0 else min(p["best"], px)
                booked.append({"at": time.strftime("%Y-%m-%d %H:%M", time.gmtime(te)), "account": acct_name, "coin": coin, "what": did, "px": px})
    if ev: G["since"] = max(G["since"], max(int(e["t"]) for e in ev) - 180)      # a little overlap; repeats are ignored
    G["applied"] = (G.get("applied") or [])[-50:] + booked
    if booked: log(f"   server guard booked to the minute: {booked}")
    return {"events": len(ev), "shockExits": n_, "booked": len(booked), "last": booked[-3:]}
