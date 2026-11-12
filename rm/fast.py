"""FAST LANE (every few minutes, takes seconds): books what the own server saw to the minute on open trades (target reached -> half
taken and the stop to break-even; stop reached -> closed), re-marks the accounts, hands the new stops back to the server and refreshes the
dashboard. No research, no training, no new entries: those stay with the hourly run. If nothing happened it changes nothing."""
import os, sys, json, time
from . import config as C

def main():
    mp = f"{C.STATE_DIR}/memory.json"
    if not os.path.exists(mp): print("fast lane: no memory yet"); return 0
    mem = json.load(open(mp)); R = mem.get("referee") or {}
    if not R.get("acct"): print("fast lane: no account yet"); return 0
    from . import guard, referee as RF, integrity as IG
    res = guard.apply(R, print); print("fast lane:", res)
    n_open = len(R["acct"].get("open", [])) + sum(len(c["acct"].get("open", [])) for c in (R.get("chal") or {}).values() if c.get("acct"))
    if not res.get("booked") and not res.get("stopsMoved"):
        if n_open and not res.get("watching") and "error" not in res: print("fast lane: the server was restarted; handing it the open trades again:", guard.sync(R, print))
        return 0
    accts = [R["acct"]] + [c["acct"] for c in (R.get("chal") or {}).values() if c.get("acct")]
    for A in accts:
        try: RF._mark(A)
        except Exception as e: print("mark failed:", e)
    for b in res.get("last", []): IG.audit(R, "fast_lane", f"{b['account']} {b['coin']}: {b['what']} at {b['px']} ({b['at']} UTC)")
    try: R["guardSync"] = guard.sync(R, print)
    except Exception as e: print("guard sync failed:", e)
    try: IG.observe(mem, None, print)
    except Exception as e: print("observe failed:", e)
    json.dump(mem, open(mp + ".tmp", "w"), separators=(",", ":")); os.replace(mp + ".tmp", mp)
    try:                                                      # dashboard: the hourly page's data with the accounts refreshed
        from . import report
        sp = f"{C.STATE_DIR}/docs/summary.json"; d = json.load(open(sp)); A = R["acct"]; rf = d.get("referee") or {}
        rf["acct"] = {**{k: A.get(k) for k in ("born", "start", "cash", "equity", "paused", "curve")}, "open": [{k: v for k, v in p.items() if k not in ("units", "fams", "row")} for p in A["open"]],
                      "closed": A["closed"][-300:], "decisions": A.get("decisions", [])[-30:]}
        rf["obs"] = R.get("obs"); d["referee"] = rf; d["fastAt"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        for n, c in (R.get("chal") or {}).items():
            if c.get("acct") and n in (rf.get("chal") or {}): rf["chal"][n]["acct"] = {k: c["acct"].get(k) for k in ("cash", "equity", "start")}
        import shutil; shutil.copytree(f"{C.STATE_DIR}/docs", C.DOCS_DIR, dirs_exist_ok=True)      # health report and the rest stay on the site
        blob = json.dumps(d, separators=(",", ":")).replace("</", "<\\/")
        for out in (f"{C.STATE_DIR}/docs", C.DOCS_DIR):
            os.makedirs(out, exist_ok=True)
            open(f"{out}/index.html", "w", encoding="utf-8").write(report.PAGE.replace("__DATA__", blob)); json.dump(d, open(f"{out}/summary.json", "w"))
    except Exception as e: print("dashboard refresh failed:", e)
    open("fast_changed", "w").write("1"); return 0

if __name__ == "__main__": sys.exit(main())
