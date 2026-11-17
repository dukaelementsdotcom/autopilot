"""THE OVERSEER: one part whose only job is to watch all the others, so nobody has to check the dashboard every day.

Every hourly run it checks three things and writes them into the health report (so anything broken still turns into an e-mail):
  1. DATA PROMISES   every source of evidence states who uses it and from when. Data that has reached its date but is not being
                     used, or that stopped arriving, is a broken promise.
  2. AUTOMATIC PARTS the things that are supposed to happen on their own really happened: contestants keep being scored, a
                     contestant that earned promotion was promoted, the fast lane runs, last night's evolution ran, the demo copy
                     is in sync, runs are not slowing down.
  3. SAFE REPAIRS    a short, fixed list of repairs it may do by itself (restart the fast lane, re-run a missed evolution night).
                     Nothing that changes trading or the judge is on that list.
Once a day (first run after 04:00 UTC) it writes a plain-English daily report and posts it as a GitHub issue in the repository,
which GitHub e-mails to the owner: what happened to the money, what the system did by itself, every data promise, the race,
and what (if anything) needs a human."""
import html, os, json, time, datetime as dt
import numpy as np
from . import config as C

H = 3600; DAY = 86400
OWNER = "dukaelementsdotcom"
REPORT_HOUR = 4                     # 04:00 UTC = 07:00 in Nairobi

# ── 1. data promises ───────────────────────────────────────────────────────────────────────────────────────────────────────
CLASSIC = [  # (source, witness that uses it)
    ("Order-book history (Binance archive since 2023)", "book"),
    ("Liquidations on Binance, Bybit and OKX (Coinalyze, since 2020)", "liq"),
    ("Macro: Nasdaq, S&P 500, VIX, US yields, dollar", "macro"),
    ("On-chain: BTC/ETH exchange inflows and balances", "onchain"),
    ("Global markets: stocks worldwide, gold, oil, chips", "global"),
    ("Mood: Fear & Greed, options fear gauge", "mood"),
    ("Stablecoin supply (new money in / out)", "flows"),
    ("Sector rotation (from prices)", "sectors"),
    ("Shock memory (size of the last moves)", "shock"),
    ("Spot-vs-perp CVD divergence (Binance archive)", "cvd"),
    ("Cross-exchange open interest and funding (Coinalyze)", "xoi"),
]
LIVE = [("Own server recorder: live order book, Binance live funding, live liquidations", "recorder"),
        ("Whales: the 50 largest Hyperliquid accounts", "whales"),
        ("Headlines by topic + the AI brief's risk mood", "news")]
IN_ARBITER = [("Hyperliquid premium and funding", "p_hlprem", "the Arbiter's leverage witness"),
              ("Spot market (real money vs leverage)", "p_sshare", "the Arbiter's real-money witness"),
              ("Options fear gauge (Deribit)", "p_dvol", "the Arbiter's mood witness")]

def _recent_cp(R, hours=8):
    L = R.get("ledger") or []; newest = max((x["t"] for x in L[-400:]), default=0)
    seen = set()
    for x in L[-1500:]:
        if x["t"] >= newest - hours * H: seen.update((x.get("cp") or {}).keys())
    return seen

def promises(mem, J):
    from . import witnesses as W, livew as LW
    R = mem.get("referee") or {}; st = R.get("witnesses") or {}; chal = R.get("chal") or {}; sc = R.get("chalScores") or {}
    cp = _recent_cp(R); out = []
    def racing(n):
        k = f"witness-{n}"; s = sc.get(k) or {}
        return k in chal and k in cp, (f"racing: {s.get('bets', 0)} separate market moves scored, lead {s.get('lead', 0):+.2f} (needs +{s.get('zNeeded', 4)})" if s else "racing: first scores in ~2 days")
    wait = mem.setdefault("overseer", {}).setdefault("waitingSince", {})
    for name, w in CLASSIC:
        s = (st.get(w) or {}).get("state", "")
        ok, txt = racing(w)
        if ok: wait.pop(w, None); out.append({"source": name, "user": f"witness-{w}", "status": "in use", "detail": txt}); continue
        since = wait.setdefault(w, int(time.time())); hrs = (time.time() - since) / H
        if "back-filling" in s or "failed its exams" in s: out.append({"source": name, "user": f"witness-{w}", "status": "on track", "detail": s})
        elif hrs < 48:          # new, or the brain just retrained: one witness trains per run, then its first live scores appear
            out.append({"source": name, "user": f"witness-{w}", "status": "on track", "detail": f"{s or 'waiting to be trained'}: training and entering the race within the next runs ({hrs:.0f}h so far, allowed 48h)"})
        else: out.append({"source": name, "user": f"witness-{w}", "status": "BROKEN", "detail": f"should be racing but is not after {hrs:.0f}h ({s or 'no status'})"})
    for name, w in LIVE + [(v["what"].split(":")[0], k) for k, v in LW.AUTO.items()]:
        s = st.get(w) or {}; left = LW.warm_left(w); lt = LW.last_t(w)
        stale = lt is None or time.time() - lt > (4 if w != "news" else 6) * H
        ok, txt = racing(w)
        if stale: out.append({"source": name, "user": f"witness-{w}", "status": "BROKEN", "detail": f"the recording stopped (newest entry {_ago(lt)}); its warm-up cannot finish"})
        elif ok: out.append({"source": name, "user": f"witness-{w}", "status": "in use", "detail": txt})
        elif left > 0: out.append({"source": name, "user": f"witness-{w}", "status": "on track", "detail": s.get("state", f"recording; {left:.0f} days left")})
        elif "not admitted" in s.get("state", ""):
            late = left == 0 and LW.days(w) - LW.LIVE[w]["warm"] > 14
            out.append({"source": name, "user": f"witness-{w}", "status": "BROKEN" if late else "on track", "detail": s["state"] + (" (for over 2 weeks: its evidence may simply carry no edge)" if late else "")})
        else:
            late = LW.days(w) - LW.LIVE[w]["warm"] > 1.5
            out.append({"source": name, "user": f"witness-{w}", "status": "BROKEN" if late else "on track", "detail": "warm-up finished but it has not trained" if late else "warm-up finished: training in the next runs"})
    try:                                    # recorded now, used from January (the owner's plan): the promise is that it keeps recording
        import pandas as pd
        d = pd.read_parquet(os.path.join(C.STATE_DIR, "collect", "deribit_opt.parquet"), columns=["t"]); lt = int(d.t.max()); days_ = (lt - int(d.t.min())) / DAY
        out.append({"source": "Deribit options: 25-delta skew, IV term structure, dealer gamma (BTC/ETH)", "user": "the January bull-market build (storage only until then)",
                    "status": "on track" if time.time() - lt < 4 * H else "BROKEN", "detail": f"{days_:.0f} days recorded, newest {_ago(lt)}" if time.time() - lt < 4 * H else f"the recording stopped (newest {_ago(lt)})"})
    except Exception: pass
    if J is not None:
        newest = J.P.t.max(); row = J.P[J.P.t == newest]
        for name, col, user in IN_ARBITER:
            f = float(row[col].notna().mean()) if col in row else 0.0
            out.append({"source": name, "user": user, "status": "in use" if f >= 0.5 else "BROKEN", "detail": f"present for {f:.0%} of coins at the newest hour"})
    try:
        sm = json.load(open(os.path.join(C.EVO_DIR, "summary.json"))); ln = sm.get("lastNight") or {}
        act = json.load(open(os.path.join(C.EVO_DIR, "staged.json"))).get("active", [])
        out.append({"source": "Evolution engine's inventions (nightly)", "user": "staged as witnesses when they pass the validation year", "status": "in use" if act else "on track",
                    "detail": f"last night {ln.get('evals', 0):,} ideas tried, {ln.get('new', 0)} new best-in-class, {len(act)} staged in the race; bar t>={sm.get('bar')}"})
    except Exception: pass
    return out

def _ago(t):
    if not t: return "never"
    h = (time.time() - t) / H; return f"{h:.1f} h ago" if h < 48 else f"{h / 24:.1f} days ago"

# ── 2. automatic parts ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _gh(method, path, **kw):
    import requests
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"); repo = os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo: return None
    r = requests.request(method, f"https://api.github.com/repos/{repo}{path}", headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}, timeout=30, **kw)
    return r

def parts(mem, now):
    R = mem.get("referee") or {}; O = mem.setdefault("overseer", {}); out = []; repairs = []
    def add(name, ok, detail, warn=True): out.append({"check": name, "ok": bool(ok), "warn": warn, "detail": detail})
    # contestants keep being scored (the scored-candidate count must grow; scoring lags 48h by design)
    rows = (R.get("chalStats") or {}).get("rows", 0); hist = O.setdefault("rowsHist", [])
    hist.append([int(now), rows]); del hist[:-60]
    old = [r for t, r in hist if t <= now - 36 * H]
    add("contestants keep being scored", not old or rows > old[-1], f"{rows} scored candidates" + (f" (36h ago: {old[-1]})" if old else ""))
    # a contestant that earned promotion must actually have been promoted
    sc = R.get("chalScores") or {}; due = [n for n, s in sc.items() if n != "champion" and s.get("ready") and s.get("better")]
    pend = O.setdefault("dueSince", {})
    for n in list(pend):
        if n not in due: pend.pop(n)
    for n in due: pend.setdefault(n, int(now))
    stuck = [n for n, t in pend.items() if now - t > 3 * H]
    add("earned promotions happen", not stuck, ("due but not promoted for 3h+: " + ", ".join(stuck)) if stuck else (f"due now: {', '.join(due)}" if due else "none due"), warn=False)
    # the fast lane (minute-level trade management and the demo copy) must be running
    try:
        r = _gh("GET", "/actions/workflows/fast.yml/runs", params={"per_page": 10})
        if r is not None and r.ok:
            ts = [dt.datetime.strptime(x["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() for x in r.json().get("workflow_runs", [])]
            last = max(ts) if ts else 0; fresh = now - last < 40 * 60
            add("fast lane runs (every 5 min)", fresh, f"last run {_ago(last)}")
            if not fresh and now - O.get("fastKick", 0) > 2 * H:
                k = _gh("POST", "/actions/workflows/fast.yml/dispatches", json={"ref": "main"}); O["fastKick"] = int(now)
                repairs.append(f"restarted the fast lane (it had not run for {_ago(last)}): {'ok' if k is not None and k.ok else 'failed'}")
    except Exception as e: add("fast lane runs (every 5 min)", False, f"could not check: {str(e)[:80]}")
    D = R.get("demo") or {}
    if D:
        sync = D.get("sync") or {}; err = D.get("error")
        gap = None
        try: gap = (D["acct"]["equity"] - (R["acct"].get("equity", R["acct"]["cash"]))) / R["acct"]["cash"] * 100
        except Exception: pass
        add("Binance demo copy in sync", sync.get("ok", True) and not err and (gap is None or abs(gap) < 3),
            (f"error: {err}; " if err else "") + (f"positions differ: {sync}; " if not sync.get("ok", True) else "") + (f"demo vs paper {gap:+.2f}%" if gap is not None else ""))
    # last night's evolution
    try:
        sm = json.load(open(os.path.join(C.EVO_DIR, "summary.json")))
        last = dt.datetime.strptime(sm["at"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp(); fresh = now - last < 30 * H
        add("nightly evolution ran", fresh, f"last night finished {_ago(last)}")
        hr = time.gmtime(now).tm_hour
        if not fresh and 3 <= hr <= 20 and now - O.get("evoKick", 0) > 20 * H:
            k = _gh("POST", "/actions/workflows/evolve.yml/dispatches", json={"ref": "main"}); O["evoKick"] = int(now)
            repairs.append(f"re-ran the missed evolution night: {'ok' if k is not None and k.ok else 'failed'}")
    except Exception as e: add("nightly evolution ran", False, f"no summary: {str(e)[:80]}")
    wh = (mem.get("collect") or {}).get("whales") or {}
    if wh: add("whale accounts answer", wh.get("ok", wh.get("accounts", 0)) >= 40, f"{wh.get('ok', '?')} of {wh.get('accounts', '?')} accounts answered")
    hh = [h.get("secs") for h in (mem.get("healthHist") or [])[:72] if h.get("secs")]
    if len(hh) >= 30:
        a, b = float(np.median(hh[:12])), float(np.median(hh[24:72]))
        add("runs are not slowing down", a < max(1.5 * b, 30 * 60), f"last 12 runs {a / 60:.1f} min vs before {b / 60:.1f} min (limit 100)")
    O["repairsToday"] = (O.get("repairsToday") or [])[-20:] + [{"at": _utc(now), "what": r} for r in repairs]
    return out, repairs

def _utc(t): return time.strftime("%Y-%m-%d %H:%M", time.gmtime(t))

# ── hourly: checks for the health report ───────────────────────────────────────────────────────────────────────────────────
# ── the loop's memory: what happened (for the daily report) and what was learned (for the nightly evolution) ──────────────
def _event(O, now, kind, what):
    O.setdefault("events", []).append({"at": _utc(now), "kind": kind, "what": what}); del O["events"][:-300]

def track(mem, now):
    """compare with the last run: what was discovered, admitted, promoted, parked, damped, staged or retired since then"""
    R = mem.get("referee") or {}; O = mem.setdefault("overseer", {}); prev = O.get("snap") or {}
    from . import witnesses as W
    st = R.get("witnesses") or {}
    snap = {"wit": {n: ("racing" if "challenger" in (v.get("state") or "") else "failed" if v.get("failed") else "waiting") for n, v in st.items()},
            "futile": sorted((R.get("chalFutile") or {}).keys()), "promos": len(((R.get("champ") or {}).get("history") or [])),
            "damp": sorted(f"{s}:{f}" for s, d in (R.get("damp") or {}).items() if not s.startswith("_") for f in (d or {})),
            "tables": sorted(O.get("tables") or []), "staged": [], "retired": []}
    try:
        sg = json.load(open(os.path.join(C.EVO_DIR, "staged.json"))); snap["staged"] = sorted(v["id"] for v in sg.get("active", [])); snap["retired"] = sorted(v["id"] for v in sg.get("retired", []))
    except Exception: pass
    if prev:
        for n, s in snap["wit"].items():
            p = (prev.get("wit") or {}).get(n)
            if s != p and s == "racing": _event(O, now, "admitted", f"witness '{n}' passed its exams and entered the race")
            if s != p and s == "failed": _event(O, now, "quarantined", f"witness '{n}' failed its exams ({(st.get(n) or {}).get('state', '')[:160]})")
        for n in set(snap["futile"]) - set(prev.get("futile") or []): _event(O, now, "killed", f"contestant '{n}' parked for 30 days: {((R.get('chalFutile') or {}).get(n) or {}).get('why', '')}")
        if snap["promos"] > prev.get("promos", 0):
            h = ((R.get("champ") or {}).get("history") or [])[-1]
            if h.get("undone"): _event(O, now, "promoted", f"UNDONE: the owner's promotion of '{h['undone']}' was reversed ({h.get('evidence', '')}); the Arbiter is back on its previous plan")
            else: _event(O, now, "promoted", f"PROMOTED '{h.get('promoted')}' into the Arbiter: {h.get('what')}")
        for x in set(snap["damp"]) - set(prev.get("damp") or []):
            s, f = x.split(":"); v = R["damp"][s][f]; _event(O, now, "damped", f"{s}s: the '{f}' vote shrunk {v['d']:.0%} (it pointed the wrong way, t={v['t']} over {v['moves']} market moves)")
        for x in set(prev.get("damp") or []) - set(snap["damp"]): _event(O, now, "released", f"{x.replace(':', 's: the ')} vote back to full weight")
        for x in set(snap["tables"]) - set(prev.get("tables") or []): _event(O, now, "discovered", f"new data table '{x}' found and wired in (joins the race after its warm-up)")
        for x in set(snap["staged"]) - set(prev.get("staged") or []): _event(O, now, "mutated", f"evolution staged voter {x} into the race")
        for x in set(snap["retired"]) - set(prev.get("retired") or []): _event(O, now, "killed", f"evolved voter {x} retired")
    O["snap"] = snap

def lessons(mem):
    """what the nightly evolution should learn from: which evidence failed and why, what was parked, what won (read by rm/evo/run.py)"""
    R = mem.get("referee") or {}; O = mem.setdefault("overseer", {})
    from . import witnesses as W
    st = R.get("witnesses") or {}; sc = R.get("chalScores") or {}
    failed = [{"witness": n, "why": (v.get("state") or "")[:200], "cols": W.WITNESSES.get(n, {}).get("cols", [])[:12]} for n, v in st.items() if v.get("failed") or "not admitted" in (v.get("state") or "")]
    parked = [{"contestant": n, "why": (v.get("why") or "")[:200]} for n, v in (R.get("chalFutile") or {}).items()]
    lead = sorted([(n, s.get("lead", 0)) for n, s in sc.items() if n.startswith("witness-") and s.get("lead", 0) > 1.0], key=lambda x: -x[1])
    winners = [{"witness": n[8:], "lead": l, "cols": W.WITNESSES.get(n[8:], {}).get("cols", [])[:12]} for n, l in lead]
    for h in ((R.get("champ") or {}).get("history") or [])[-5:]:
        n = h.get("promoted", "");
        if n.startswith("witness-"): winners.insert(0, {"witness": n[8:], "promoted": True, "cols": W.WITNESSES.get(n[8:], {}).get("cols", [])[:12]})
    damped = [{"side": s, "family": f, **v} for s, d in (R.get("damp") or {}).items() if not s.startswith("_") for f, v in (d or {}).items()]
    O["lessons"] = {"failed": failed, "parked": parked, "winners": winners, "damped": damped, "at": _utc(time.time())}
    return O["lessons"]

def checks(mem, J, now=None):
    """returns health-style checks; also stores the overseer's view in memory for the dashboard and the daily report"""
    now = now or time.time(); O = mem.setdefault("overseer", {})
    from . import livew as LW
    O["tables"] = sorted(LW.AUTO)
    try: track(mem, now); lessons(mem)
    except Exception as e: O["trackError"] = str(e)[:200]
    orph = []
    try: orph = LW.orphans(); O["oldOrphans"] = LW.old_orphans()
    except Exception as e: orph = [f"(could not check: {e})"]
    O["orphans"] = orph
    pr = promises(mem, J); pa, rep = parts(mem, now)
    O.update(promises=pr, parts=pa, at=_utc(now))
    broken = [p for p in pr if p["status"] == "BROKEN"]
    out = [{"check": "data promises kept (every source used when due)", "status": "OK" if not broken else "FAIL",
            "detail": ("; ".join(f"{p['source']}: {p['detail']}" for p in broken) if broken else
                       f"{sum(p['status'] == 'in use' for p in pr)} in use, {sum(p['status'] == 'on track' for p in pr)} on track, 0 broken")}]
    for c in pa: out.append({"check": c["check"], "status": "OK" if c["ok"] else ("WARN" if c["warn"] else "FAIL"), "detail": c["detail"]})
    out.append({"check": "no orphan data (every saved table has a user)", "status": "OK" if not orph else "WARN",
                "detail": ("unused: " + ", ".join(orph[:10])) if orph else f"every collected table is used ({len(LW.AUTO)} auto-discovered)"})
    if rep: out.append({"check": "overseer repairs this run", "status": "WARN", "detail": "; ".join(rep)})
    return out

# ── daily: a plain-English report, posted as a GitHub issue (GitHub e-mails it) ────────────────────────────────────────────
def daily(mem, rep, log=print, force=False):
    now = time.time(); O = mem.setdefault("overseer", {}); day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if not force and (O.get("reportDay") == day or time.gmtime(now).tm_hour < REPORT_HOUR): return None
    md, title = compose(mem, rep, now)
    os.makedirs(C.DOCS_DIR, exist_ok=True); open(f"{C.DOCS_DIR}/daily.md", "w").write(md)
    O["reportDay"] = day; O["lastReport"] = {"at": _utc(now), "title": title}
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):      # owner, Nov 17: Telegram instead of GitHub e-mail
        from .report import _send
        import re as _re
        txt = _re.sub(r"^#+ *", "", md, flags=_re.M).replace("**", "")
        _send(f"{html.escape(title)}\n\n{html.escape(txt)}"[:4000], log); O["sentVia"] = "telegram"; log("overseer: daily report sent by Telegram")
        return md
    try:
        r = _gh("POST", "/issues", json={"title": title, "body": md})
        if r is not None and r.ok:
            num = r.json().get("number"); prev = O.get("issue")
            if prev: _gh("PATCH", f"/issues/{prev}", json={"state": "closed"})
            O["issue"] = num; log(f"overseer: daily report posted as issue #{num}")
        else: log(f"overseer: could not post the daily report ({getattr(r, 'status_code', 'no token')}: {getattr(r, 'text', '')[:120]})"); O["postError"] = getattr(r, "status_code", "no token")
    except Exception as e: log(f"overseer: daily report not posted: {e}")
    return md

def _money(x): return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"

API_CHECKS = ("live relay", "collector:", "AI (Groq)", "whale accounts", "Hyperliquid data", "spot data", "own-server recorder", "fast lane", "AI market brief")

def compose(mem, rep, now):
    """the daily report: what Volvox discovered, promoted, killed or mutated by itself; a human is asked for something only when
    health is red or an outside service broke"""
    R = mem.get("referee") or {}; A = R.get("acct") or {}; O = mem.get("overseer") or {}
    eq = A.get("equity", A.get("cash", 0)); start = A.get("start", 500)
    curve = A.get("curve") or []; eq24 = next((v for t, v in reversed(curve) if t <= now - DAY), start)
    checks = rep.get("checks") or []
    red = [c for c in checks if c["status"] == "FAIL"]
    api = [c for c in checks if c["status"] != "OK" and c["check"].startswith(API_CHECKS)]
    needs = []
    if A.get("killed") or A.get("paused"): needs.append(f"**The kill switch stopped trading**: {A.get('paused')}. Only you can restart it (KILL_RESET in rm/config.py).")
    if red: needs.append("**Health is red**: " + "; ".join(f"{c['check']} ({c['detail'][:140]})" for c in red))
    if api: needs.append("**An outside service is failing**: " + "; ".join(f"{c['check']} ({c['detail'][:140]})" for c in api))
    ev = [e for e in (O.get("events") or []) if e["at"] >= _utc(now - DAY)]
    by = lambda *k: [e["what"] for e in ev if e["kind"] in k]
    title = f"Volvox daily {time.strftime('%b %d', time.gmtime(now))}: {_money(eq)} ({(eq / eq24 - 1) * 100:+.1f}% in 24h) · " + ("NEEDS YOU" if needs else "ran by itself")
    L = [f"# {title}", ""]
    if needs: L += ["## Needs you", *(f"- {n}" for n in needs), ""]
    def sec(head, items, empty):
        L.extend([f"## {head}", *(f"- {x}" for x in items)] if items else [f"## {head}", f"- {empty}"]); L.append("")
    # DISCOVERED: new data / data reaching its warm-up / tables found
    disc = by("discovered")
    for p in O.get("promises") or []:
        if p["status"] == "on track" and "recording" in p["detail"]: disc.append(f"{p['source']}: {p['detail']}")
    sec("Discovered", disc, "no new data today")
    sec("Promoted", by("promoted", "admitted"), "nothing promoted or admitted today")
    sec("Killed / quarantined", by("killed", "quarantined"), "nothing killed or quarantined today")
    mut = by("mutated")
    try:
        sm = json.load(open(os.path.join(C.EVO_DIR, "summary.json"))); ln = sm.get("lastNight") or {}
        mut.insert(0, f"last night: {ln.get('evals', 0):,} new voters tried ({ln.get('mode', 'normal')} mode), {ln.get('new', 0)} new best-in-class, {len(ln.get('staged') or [])} staged into the race; all-time {sm.get('N', 0):,} tried, bar t>={sm.get('bar')}")
    except Exception: pass
    sec("Mutated (evolution)", mut, "no evolution summary")
    adj = by("damped", "released")
    for s, v in (R.get("adapt") or {}).items(): adj.append(f"{s}s: {v.get('why')}")
    for r in (O.get("repairsToday") or []):
        if r["at"] >= _utc(now - DAY): adj.append(f"repair: {r['what']}")
    sec("Self-adjusted", adj, "no adjustments")
    closed24 = [x for x in A.get("closed", []) if x.get("exitT", 0) > now - DAY]
    L += ["## Money (one line)", f"- Arbiter {_money(eq)} ({(eq / start - 1) * 100:+.1f}% since start, {(eq / eq24 - 1) * 100:+.1f}% in 24h); "
          f"{len(closed24)} trade(s) closed today ({_money(sum(x.get('usd', 0) for x in closed24))}), {len(A.get('open', []))} open"
          + (f"; Binance demo {_money((R.get('demo') or {}).get('acct', {}).get('equity', 0))}" if (R.get('demo') or {}).get("acct") else ""), ""]
    near = [(n, s) for n, s in (R.get("chalScores") or {}).items() if n != "champion" and (s.get("better") or s.get("lead", 0) >= 0.6 * s.get("zNeeded", 4))]
    if near: L += ["## Closest to promotion", *(f"- {n}: " + (f"{s['me']['growth']:+.1f}% vs {s['champion']['growth']:+.1f}%, {s.get('bets', 0)} of 20 trades" if s.get("account") else f"lead {s.get('lead', 0):+.2f} of {s.get('zNeeded', 4)} needed, {s.get('bets', 0)} market moves") for n, s in near[:5]), ""]
    L += [f"_Health {rep.get('overall')}; {sum(p['status'] == 'in use' for p in O.get('promises') or [])} data sources in use, {sum(p['status'] == 'on track' for p in O.get('promises') or [])} on track. Written automatically by the overseer._"]
    return "\n".join(L), title
