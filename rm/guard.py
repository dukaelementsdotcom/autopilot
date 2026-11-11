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
            if gain > best[0]: best = (gain, x)
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
    base, hd = _srv()
    if not base: return {}
    G = R.setdefault("guard", {"since": 0, "applied": []})
    try: ev = requests.get(base + "/guard/events", params={"since": G["since"]}, headers=hd, timeout=15).json().get("events", [])
    except Exception as e: return {"error": str(e)[:120]}
    from . import referee as RF
    n_ = 0
    for e in ev:
        G["since"] = max(G["since"], e["t"]); acct_name, coin, t = e["id"].split("|"); 
        if e["kind"] != "shock": continue
        A = R.get("acct") if acct_name == "champion" else ((R.get("chal") or {}).get(acct_name) or {}).get("acct")
        if not A: continue
        for p in list(A.get("open", [])):
            if p["coin"] == coin and str(p["t"]) == t:
                RF._close(A, p, float(e["px"]), "shock exit (server guard)", int(e["t"])); n_ += 1
    G["applied"] = (G.get("applied") or [])[-50:] + [e for e in ev if e["kind"] == "shock"]
    return {"events": len(ev), "shockExits": n_}
