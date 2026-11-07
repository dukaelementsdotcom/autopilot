"""HEALTH REPORT: one downloadable file (docs/health.json) that says whether the machine is working properly, overnight included.
Every run adds a compact snapshot to a rolling history (last 96 runs = ~4 days), so a single download after a night away
shows: did every hourly run happen, how long each took, how much memory, how fresh the data was (archive and live, per coin),
whether the relay answered, whether the AI answered, what broke (if anything), and the state of every account.
Automatic checks turn all of that into OK / WARN / FAIL lines, so problems are obvious without reading logs."""
import os, json, time, resource, datetime as dt, numpy as np, pandas as pd
from . import config as C

H = 3600
def _utc(t): return dt.datetime.utcfromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M") if t else None

def _freshness(J):
    """newest archive hour and newest usable hour per coin, and how far behind 'now' each is"""
    import pyarrow.parquet as pq
    now = time.time(); out = {}
    newest = J.P.groupby("coin").t.max() if J is not None else {}
    for c in C.COINS:
        if c in C.DEAD: continue
        a = None; p = f"{C.DATA_DIR}/raw/{c}_k.parquet"
        try:
            if os.path.exists(p): a = int(pd.read_parquet(p, columns=["t"]).t.max())
        except Exception: a = None
        n = int(newest.get(c)) if c in getattr(newest, "index", []) else None
        out[c] = {"archive": _utc(a), "archiveLagH": round((now - a) / 3600, 1) if a else None,
                  "usable": _utc(n), "usableLagH": round((now - n) / 3600, 1) if n else None}
    return out

def write(mem, J, timings, problems, live, t0, log=print):
    now = time.time(); rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6      # GB (Linux reports kB)
    fresh = {}
    try: fresh = _freshness(J)
    except Exception as e: problems = problems + [f"health: freshness failed {e}"]
    lags = [v["usableLagH"] for v in fresh.values() if v.get("usableLagH") is not None]
    alags = [v["archiveLagH"] for v in fresh.values() if v.get("archiveLagH") is not None]
    R = mem.get("referee") or {}; RA = R.get("acct") or {}; RM = R.get("model") or {}
    def acct(A):
        if not A: return None
        return {"cash": A.get("cash"), "equity": A.get("equity", A.get("cash")), "start": A.get("start"), "open": len(A.get("open", [])), "closed": len(A.get("closed", [])),
                "won": sum(1 for x in A.get("closed", []) if (x.get("usd") or 0) > 0), "openList": [{k: p.get(k) for k in ("coin", "side", "entry", "mark", "stop", "sl", "target", "tp", "unreal", "t")} for p in A.get("open", [])]}
    snap = {"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "secs": round(now - t0), "rssGB": round(rss, 2), "maxLagH": max(lags) if lags else None,
            "archLagH": max(alags) if alags else None, "relayCoins": (live or {}).get("coins"), "relayErr": len((live or {}).get("errors", {}) or {}),
            "problems": len(problems), "tested": (mem.get("runs") or [{}])[0].get("tested"), "referee": RA.get("equity", RA.get("cash")),
            "sha": (os.environ.get("GITHUB_SHA") or "")[:7], "runId": os.environ.get("GITHUB_RUN_ID")}
    hist = mem.setdefault("healthHist", []); hist.insert(0, snap); del hist[96:]
    # ── automatic checks ──
    checks = []
    def chk(name, ok, warn=None, detail=""):
        checks.append({"check": name, "status": "OK" if ok else ("WARN" if warn else "FAIL"), "detail": detail})
    gaps = []
    for a, b in zip(hist, hist[1:]):
        try:
            g = (dt.datetime.strptime(a["at"], "%Y-%m-%d %H:%M") - dt.datetime.strptime(b["at"], "%Y-%m-%d %H:%M")).total_seconds() / 3600
            if g > 1.6: gaps.append(f"{b['at']} → {a['at']} ({g:.1f}h)")
        except Exception: pass
    chk("hourly runs happen", not gaps, warn=len(gaps) <= 2, detail=("missed hours: " + "; ".join(gaps[:8])) if gaps else f"{len(hist)} runs on record, no gaps")
    chk("run finishes in time", snap["secs"] < 50 * 60, warn=snap["secs"] < 70 * 60, detail=f"this run {snap['secs']/60:.1f} min (limit 100)")
    chk("memory", rss < 10, warn=rss < 13, detail=f"peak {rss:.1f} GB of ~16 GB")
    chk("live data fresh", lags and max(lags) <= 2.5, warn=lags and max(lags) <= 6,
        detail=f"newest usable hour is {min(lags) if lags else '?'}–{max(lags) if lags else '?'} h old; stale coins: " + (", ".join(c for c, v in fresh.items() if (v.get('usableLagH') or 99) > 2.5) or "none"))
    chk("archive keeps arriving", alags and np.median(alags) <= 60, warn=alags and np.median(alags) <= 96, detail=f"median archive lag {np.median(alags) if alags else '?'} h (Binance publishes daily, ~1 day late is normal)")
    chk("live relay answers (Render, Vercel backup)", (live or {}).get("coins", 0) >= 40, warn=(live or {}).get("coins", 0) > 0,
        detail=f"{(live or {}).get('coins', 0)} coins, {snap['relayErr']} errors, via {(live or {}).get('via')}")
    # every measurement must exist NOW as often as it did in the past week (a silent gap would blind the referee)
    gaps_live = []
    if J is not None:
        try:
            P = J.P; tn = int(P.t.max()); wk = P[(P.t > tn - 7 * 86400) & (P.t < tn - 6 * H)]; nw = P[P.t == tn]
            for col in [c for c in P.columns if c.startswith("p_") or c.startswith("x_")]:
                past = float(wk[col].notna().mean()); now_ = float(nw[col].notna().mean())
                if past > 0.5 and now_ < past - 0.4: gaps_live.append(f"{col} {now_:.0%} now vs {past:.0%} last week")
        except Exception as e: gaps_live.append(f"check failed: {e}")
    chk("all measurements available live", not gaps_live, warn=len(gaps_live) <= 3, detail="; ".join(gaps_live[:10]) or "every measurement present at the newest hour")
    md = mem.get("moreData") or {}
    sl = (md.get("spotLive") or {}); hl = (md.get("hl") or {})
    chk("spot data (real money vs leverage)", sl.get("ok", 0) >= 35 and "error" not in (md.get("spot") or {}), warn=sl.get("ok", 0) > 0,
        detail=f"live spot {sl.get('ok', 0)} coins (not on spot: {', '.join(sl.get('missing', [])[:8]) or 'none'}); archive {json.dumps(md.get('spot'))[:120]}")
    chk("Hyperliquid data", "error" not in hl and hl.get("coins", 0) > 20, warn=True, detail=json.dumps(hl)[:160])
    chk("no broken parts", not problems, warn=len(problems) <= 2, detail="; ".join(problems[:6]) or "none")
    age = None
    try: age = (now - time.mktime(time.strptime(RM.get("trainedAt"), "%Y-%m-%d %H:%M"))) / 86400 if RM.get("trainedAt") else None
    except Exception: pass
    chk("referee brain trained and recent", age is not None and age < 9, warn=age is not None and age < 15, detail=f"trained {RM.get('trainedAt')} ({R.get('trainNote')})")
    chk("referee kill switch", not RA.get("paused"), detail=RA.get("paused") or "not triggered")
    ai = mem.get("aiStatus") or {}
    chk("AI (Groq) answers", not ai or ai.get("ok", True), warn=True, detail=json.dumps(ai)[:200] if ai else "no AI calls recorded this run")
    worst = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "WARN" if any(c["status"] == "WARN" for c in checks) else "OK"
    rep = {"what": "Research Machine health report. Download and send it to Claude to diagnose problems.", "generated": snap["at"] + " UTC", "overall": worst,
           "checks": checks, "thisRun": {**snap, "timings": timings, "problems": problems, "panelRows": int(len(J.P)) if J is not None else None,
                                         "coins": int(J.P.coin.nunique()) if J is not None else None, "valBar": C.VAL_T},
           "freshness": fresh, "live": live and {k: v for k, v in live.items() if k != "errors"}, "liveErrors": (live or {}).get("errors"),
           "referee": {"model": RM, "account": acct(RA), "paused": RA.get("paused"), "look": R.get("look"), "trainings": R.get("trainings"), "lastDecisions": (RA.get("decisions") or [])[-10:]},
           "accounts": {"best": acct(mem.get("account")), "team": acct(mem.get("team")), "allin": acct(mem.get("allin"))},
           "research": {"totalTests": mem.get("total_tests"), "champions": sum(1 for t in mem.get("tests", {}).values() if t.get("status") == "champion"),
                        "lastRuns": [{k: r.get(k) for k in ("at", "secs", "tested", "counts", "problems", "dataEnd")} for r in (mem.get("runs") or [])[:24]]},
           "history": hist}
    os.makedirs(C.DOCS_DIR, exist_ok=True)
    json.dump(rep, open(f"{C.DOCS_DIR}/health.json.tmp", "w"), default=lambda o: o.item() if hasattr(o, "item") else str(o), indent=1)
    os.replace(f"{C.DOCS_DIR}/health.json.tmp", f"{C.DOCS_DIR}/health.json")
    mem["healthSummary"] = {"overall": worst, "checks": [[c["check"], c["status"], c["detail"]] for c in checks], "at": snap["at"]}
    log(f"health: {worst} ({sum(c['status']=='OK' for c in checks)}/{len(checks)} OK)")
    return rep
