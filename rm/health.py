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
                "won": sum(1 for x in A.get("closed", []) if (x.get("usd") or 0) > 0), "openList": [{"coin": p.get("coin"), "side": p.get("side"), "entry": p.get("entry"), "mark": p.get("mark") or p.get("livePx"), "stop": p.get("stop") or p.get("sl"),
                                    "target": p.get("target") or p.get("tp"), "unreal": p.get("unreal"), "t": p.get("t")} for p in A.get("open", [])]}
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
    outs = [h["at"] for h in hist[:24] if (h.get("maxLagH") or 0) > 3]
    chk("no live-data outages (last 24 runs)", not outs, warn=len(outs) <= 2,
        detail=(f"{len(outs)} runs without fresh live data: {', '.join(outs[-1:] + outs[:1])} … (no new trades are made on stale data)") if outs else "live data arrived every run")
    chk("run finishes in time", snap["secs"] < 50 * 60, warn=snap["secs"] < 70 * 60, detail=f"this run {snap['secs']/60:.1f} min (limit 100)")
    chk("memory", rss < 10, warn=rss < 13, detail=f"peak {rss:.1f} GB of ~16 GB")
    chk("live data fresh", lags and max(lags) <= 2.5, warn=lags and max(lags) <= 6,
        detail=f"newest usable hour is {min(lags) if lags else '?'}–{max(lags) if lags else '?'} h old; stale coins: " + (", ".join(c for c, v in fresh.items() if (v.get('usableLagH') or 99) > 2.5) or "none"))
    chk("archive keeps arriving", alags and np.median(alags) <= 60, warn=alags and np.median(alags) <= 96, detail=f"median archive lag {np.median(alags) if alags else '?'} h (Binance publishes daily, ~1 day late is normal)")
    chk("live relay answers (own server, then Render, Vercel)", (live or {}).get("coins", 0) >= 40, warn=(live or {}).get("coins", 0) > 0,
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
    co = mem.get("collect") or {}
    for nm in ("recorder", "macro", "onchain", "whales", "news", "compass"):
        x = co.get(nm) or {}
        if nm == "recorder":
            stt = x.get("status") or {}
            chk("own-server recorder (order book, funding, liquidations)", "error" not in x and stt.get("ws") == "connected" and (stt.get("writes") or {}).get("depth", 0) > 0,
                warn=True, detail=json.dumps({k: x.get(k) for k in ("depth", "funding", "liq", "error")} | {"ws": stt.get("ws"), "writes": stt.get("writes"), "lastError": stt.get("lastError")})[:300])
            sy = stt.get("sys") or {}
            if sy:
                chk("own server resources (disk, memory, CPU)", (sy.get("diskFreeGB") or 0) > 3 and (sy.get("memFreeMB") or 0) > 150 and (sy.get("load15") or 0) < 1.0,
                    warn=(sy.get("diskFreeGB") or 0) > 1 and (sy.get("memFreeMB") or 0) > 60,
                    detail=f"disk free {sy.get('diskFreeGB')} GB, recordings {sy.get('recMB')} MB (old days compressed, kept {sy.get('keepDays')} days, oldest auto-deleted if space runs low), "
                           f"memory free {sy.get('memFreeMB')}/{sy.get('memTotalMB')} MB, load {sy.get('load15')} on {sy.get('cpus')} CPUs")
        else:
            chk(f"collector: {nm}", "error" not in x, warn=True, detail=json.dumps(x)[:200])
    # saved memory must stay far below GitHub's 100 MB per-file limit (a bigger file could not be saved at all)
    try:
        sizes = {f: os.path.getsize(os.path.join(dp, f)) / 1e6 for dp, _, fs in os.walk(C.STATE_DIR) for f in fs}
        big = max(sizes.items(), key=lambda kv: kv[1]) if sizes else ("-", 0); tot = sum(sizes.values())
        from . import archive
        chk("saved memory size", big[1] < 40 and tot < 400, warn=big[1] < 70, detail=f"total {tot:.1f} MB, largest file {big[0]} {big[1]:.1f} MB (GitHub limit 100 MB per file); permanent archive {archive.sizes()}")
    except Exception as e: chk("saved memory size", False, warn=True, detail=str(e))
    cal = (mem.get("referee") or {}).get("calibration") or {}
    chk("calibration ledger", cal.get("recorded", 0) > 0, warn=True, detail=f"{cal.get('recorded', 0)} candidates recorded, {cal.get('scored', 0)} scored")
    try:                                                   # the new evidence must keep arriving, or its challengers judge on stale data
        lagd = {}
        for nm, f, mx in (("macro", "macro.parquet", 5), ("on-chain", "onchain.parquet", 4)):
            t_ = pd.read_parquet(f"{C.DATA_DIR}/raw/{f}", columns=["t"]).t.max(); lagd[nm] = round((now - t_) / 86400, 1)
        wn = [c for c in (J.P.columns if J is not None else []) if c.startswith("w_")]
        wnow = float(J.P.loc[J.P.t == J.P.t.max(), wn].notna().mean().mean()) if wn else 0
        chk("new evidence keeps arriving (macro, on-chain, sectors, shocks)", lagd.get("macro", 99) <= 5 and lagd.get("on-chain", 99) <= 4 and wnow >= 0.7, warn=True,
            detail=f"newest macro day {lagd.get('macro')} days ago (weekends have no stock data), on-chain {lagd.get('on-chain')} days ago; {wnow:.0%} of witness measurements present at the newest hour")
    except Exception as e: chk("new evidence keeps arriving (macro, on-chain, sectors, shocks)", False, warn=True, detail=str(e)[:150])
    gs = (mem.get("referee") or {}).get("guardSync") or {}
    chk("server guard (minute-by-minute watch)", "error" not in gs and "skipped" not in gs, warn=True, detail=f"watching {gs.get('watching')} open positions; last events: {json.dumps((mem.get('referee') or {}).get('guardApply'))[:120]}")
    chs = (mem.get("referee") or {}).get("chal") or {}; rj = (mem.get("referee") or {}).get("chalRejected") or {}; cst = (mem.get("referee") or {}).get("chalStats") or {}
    chk("challengers (upgrades under test)", len(chs) > 0, warn=True, detail=f"{len(chs)} being scored ({sum(1 for c in chs.values() if c.get('acct'))} with shadow accounts), "
        f"{cst.get('rows', 0)} scored candidates, not admitted: {', '.join(f'{k} ({v[:60]})' for k, v in rj.items()) or 'none'}; guard: {json.dumps((mem.get('referee') or {}).get('guardSync'))[:80]}")
    chk("no broken parts", not problems, warn=len(problems) <= 2, detail="; ".join(problems[:6]) or "none")
    age = None
    try: age = (now - time.mktime(time.strptime(RM.get("trainedAt"), "%Y-%m-%d %H:%M"))) / 86400 if RM.get("trainedAt") else None
    except Exception: pass
    chk("referee brain trained and recent", age is not None and age < 9, warn=age is not None and age < 15, detail=f"trained {RM.get('trainedAt')} ({R.get('trainNote')})")
    chk("referee kill switch", not RA.get("paused"), detail=RA.get("paused") or "not triggered")
    ai = mem.get("aiStatus") or {}
    last_ok = bool(ai.get("last")) and ai["last"][-1].get("ok")          # one model failing is fine when the fallback model answered
    chk("AI (Groq) answers", not ai or ai.get("ok", True) or last_ok, warn=True, detail=json.dumps(ai)[:200] if ai else "no AI calls recorded this run")
    worst = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "WARN" if any(c["status"] == "WARN" for c in checks) else "OK"
    rep = {"what": "Research Machine health report. Download and send it to Claude to diagnose problems.", "generated": snap["at"] + " UTC", "overall": worst,
           "checks": checks, "thisRun": {**snap, "timings": timings, "problems": problems, "panelRows": int(len(J.P)) if J is not None else None,
                                         "coins": int(J.P.coin.nunique()) if J is not None else None, "valBar": C.VAL_T},
           "freshness": fresh, "live": live and {k: v for k, v in live.items() if k != "errors"}, "liveErrors": (live or {}).get("errors"),
           "referee": {"model": RM, "account": acct(RA), "paused": RA.get("paused"), "look": R.get("look"),
                       "calibration": R.get("calibration"), "collect": mem.get("collect"),
                       "scorecard": {"calls": len(R.get("shadow") or []), "scored": sum(1 for x in (R.get("shadow") or []) if x.get("y") is not None),
                                     "stats": R.get("shadowStats"), "callRate": R.get("callRateBySide"), "override": R.get("override"),
                                     "regimeOverride": R.get("regimeOverride"), "adapt": R.get("adapt"), "regimeNow": R.get("regimeNow")}, "trainings": R.get("trainings"), "lastDecisions": (RA.get("decisions") or [])[-10:]},
           "accounts": {"best": acct(mem.get("account"))},
           "challengers": {"champion": (R.get("champ") or {}).get("cfg"), "promotions": ((R.get("champ") or {}).get("history") or [])[-5:], "scores": R.get("chalScores"),
                           "stats": R.get("chalStats"), "rejected": R.get("chalRejected"), "witnesses": R.get("witnesses"), "shapeExam": R.get("shapeExam"),
                           "accounts": {n: {k: (c.get("acct") or {}).get(k) for k in ("equity", "cash")} | {"open": len((c.get("acct") or {}).get("open", [])), "closed": len((c.get("acct") or {}).get("closed", []))} for n, c in (R.get("chal") or {}).items() if c.get("acct")}},
           "review": R.get("review"), "auditLog": R.get("auditLog"), "integrity": {k: (R.get("obs") or {}).get(k) for k in ("judge", "risk", "learning", "regime", "arbiter", "bestRule", "haircut")},
           "research": {"totalTests": mem.get("total_tests"), "champions": sum(1 for t in mem.get("tests", {}).values() if t.get("status") == "champion"),
                        "lastRuns": [{k: r.get(k) for k in ("at", "secs", "tested", "counts", "problems", "dataEnd")} for r in (mem.get("runs") or [])[:24]]},
           "history": hist}
    os.makedirs(C.DOCS_DIR, exist_ok=True)
    json.dump(rep, open(f"{C.DOCS_DIR}/health.json.tmp", "w"), default=lambda o: o.item() if hasattr(o, "item") else str(o), indent=1)
    os.replace(f"{C.DOCS_DIR}/health.json.tmp", f"{C.DOCS_DIR}/health.json")
    mem["healthSummary"] = {"overall": worst, "checks": [[c["check"], c["status"], c["detail"]] for c in checks], "at": snap["at"]}
    log(f"health: {worst} ({sum(c['status']=='OK' for c in checks)}/{len(checks)} OK)")
    return rep
