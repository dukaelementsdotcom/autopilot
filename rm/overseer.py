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
import os, json, time, datetime as dt
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
    for name, w in CLASSIC:
        s = (st.get(w) or {}).get("state", "")
        ok, txt = racing(w)
        if ok: out.append({"source": name, "user": f"witness-{w}", "status": "in use", "detail": txt})
        elif "back-filling" in s: out.append({"source": name, "user": f"witness-{w}", "status": "on track", "detail": s})
        else: out.append({"source": name, "user": f"witness-{w}", "status": "BROKEN", "detail": f"should be racing but is not ({s or 'no status'})"})
    for name, w in LIVE:
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
def checks(mem, J, now=None):
    """returns health-style checks; also stores the overseer's view in memory for the dashboard and the daily report"""
    now = now or time.time(); O = mem.setdefault("overseer", {})
    pr = promises(mem, J); pa, rep = parts(mem, now)
    O.update(promises=pr, parts=pa, at=_utc(now))
    broken = [p for p in pr if p["status"] == "BROKEN"]
    out = [{"check": "data promises kept (every source used when due)", "status": "OK" if not broken else "FAIL",
            "detail": ("; ".join(f"{p['source']}: {p['detail']}" for p in broken) if broken else
                       f"{sum(p['status'] == 'in use' for p in pr)} in use, {sum(p['status'] == 'on track' for p in pr)} on track, 0 broken")}]
    for c in pa: out.append({"check": c["check"], "status": "OK" if c["ok"] else ("WARN" if c["warn"] else "FAIL"), "detail": c["detail"]})
    if rep: out.append({"check": "overseer repairs this run", "status": "WARN", "detail": "; ".join(rep)})
    return out

# ── daily: a plain-English report, posted as a GitHub issue (GitHub e-mails it) ────────────────────────────────────────────
def daily(mem, rep, log=print, force=False):
    now = time.time(); O = mem.setdefault("overseer", {}); day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if not force and (O.get("reportDay") == day or time.gmtime(now).tm_hour < REPORT_HOUR): return None
    md, title = compose(mem, rep, now)
    os.makedirs(C.DOCS_DIR, exist_ok=True); open(f"{C.DOCS_DIR}/daily.md", "w").write(md)
    O["reportDay"] = day; O["lastReport"] = {"at": _utc(now), "title": title}
    try:
        r = _gh("POST", "/issues", json={"title": title, "body": md + f"\n\n@{OWNER}"})
        if r is not None and r.ok:
            num = r.json().get("number"); prev = O.get("issue")
            if prev: _gh("PATCH", f"/issues/{prev}", json={"state": "closed"})
            O["issue"] = num; log(f"overseer: daily report posted as issue #{num}")
        else: log(f"overseer: could not post the daily report ({getattr(r, 'status_code', 'no token')}: {getattr(r, 'text', '')[:120]})"); O["postError"] = getattr(r, "status_code", "no token")
    except Exception as e: log(f"overseer: daily report not posted: {e}")
    return md

def _money(x): return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"

def compose(mem, rep, now):
    R = mem.get("referee") or {}; A = R.get("acct") or {}; O = mem.get("overseer") or {}
    eq = A.get("equity", A.get("cash", 0)); start = A.get("start", 500)
    curve = A.get("curve") or []; eq24 = next((v for t, v in reversed(curve) if t <= now - DAY), start)
    closed24 = [x for x in A.get("closed", []) if x.get("exitT", 0) > now - DAY]
    opened24 = [x for x in A.get("decisions", []) if x.get("t", 0) > now - DAY - 2 * H]
    checks = rep.get("checks") or []; bad = [c for c in checks if c["status"] != "OK"]
    pr = O.get("promises") or []; broken = [p for p in pr if p["status"] == "BROKEN"]
    needs = []
    if A.get("killed") or A.get("paused"): needs.append(f"**The kill switch stopped trading**: {A.get('paused')}. Only you can restart it (KILL_RESET in rm/config.py).")
    if broken: needs.append("**Data promise broken**: " + "; ".join(f"{p['source']} ({p['detail']})" for p in broken))
    fails = [c for c in bad if c["status"] == "FAIL" and not c["check"].startswith("data promises")]
    if fails: needs.append("**Something is broken**: " + "; ".join(f"{c['check']} ({c['detail'][:120]})" for c in fails))
    status = "needs you" if needs else "all fine"
    title = f"Volvox daily {time.strftime('%b %d', time.gmtime(now))}: {_money(eq)} ({(eq / eq24 - 1) * 100:+.1f}% in 24h) · {status}"
    L = [f"# {title}", ""]
    L += ["## Needs you", *(f"- {n}" for n in needs)] if needs else ["## Needs you", "Nothing. Everything below happened by itself."]
    L += ["", "## Money",
          f"- Arbiter paper account: **{_money(eq)}** ({(eq / start - 1) * 100:+.1f}% since {_money(start)} start; {(eq / eq24 - 1) * 100:+.1f}% in 24h), "
          f"{len(A.get('closed', []))} trades closed, {sum(1 for x in A.get('closed', []) if (x.get('usd') or 0) > 0)} won."]
    for x in closed24: L.append(f"  - closed {x['side'].lower()} {x['coin']}: {('+' if x['usd'] >= 0 else '')}{_money(x['usd'])} ({x.get('how')})")
    for x in A.get("open", []): L.append(f"  - open {x['side'].lower()} {x['coin']} since {_utc(x['t'])} UTC, {('+' if (x.get('unreal') or 0) >= 0 else '')}{_money(x.get('unreal') or 0)} so far")
    if not closed24 and not A.get("open"):
        lk = (R.get("look") or {}).get("rows") or []
        if lk: L.append(f"  - no trade in the last 24h; best candidate now: {lk[0]['side'].lower()} {lk[0]['coin']} at {lk[0]['prob']:.0%} odds, the bar is {lk[0]['need']:.0%}")
    D = R.get("demo") or {}
    if D.get("acct"): L.append(f"- Binance demo (real orders, 1:1 copy): **{_money(D['acct'].get('equity', D['acct'].get('cash', 0)))}**, " + ("in sync" if (D.get("sync") or {}).get("ok", True) else f"OUT OF SYNC: {D.get('sync')}"))
    W = (R.get("wide") or {}).get("vs") or {}
    if W: L.append(f"- Wider-universe contestant: {W.get('wide', 0):+.1f}% vs the Arbiter's {W.get('arbiterSinceBirth', 0):+.1f}% over the same days")
    L += ["", "## What it did by itself (last 24h)"]
    did = []
    if opened24: did.append(f"opened {len(opened24)} trade(s): " + ", ".join(f"{x['side'].lower()} {x['coin']} ({x['prob']:.0%})" for x in opened24))
    ad = R.get("adapt") or {}
    for s, v in ad.items(): did.append(f"{s}s: {v.get('why')}")
    for h in ((R.get("champ") or {}).get("history") or []):
        if h.get("at", "") >= _utc(now - DAY): did.append(f"PROMOTED {h['promoted']}: {h.get('what')}")
    for t in (R.get("trainings") or []):
        if t.get("at", "") >= _utc(now - DAY): did.append(f"retrained the Arbiter's brain ({t['at']} UTC)")
    for r in (O.get("repairsToday") or []):
        if r["at"] >= _utc(now - DAY): did.append(f"repair: {r['what']}")
    try:
        ln = json.load(open(os.path.join(C.EVO_DIR, "summary.json"))).get("lastNight") or {}
        did.append(f"evolution night: {ln.get('evals', 0):,} ideas tried, {ln.get('new', 0)} new best-in-class, staged {len(ln.get('staged') or [])}")
    except Exception: pass
    L += [f"- {d}" for d in did] or ["- nothing unusual"]
    L += ["", "## Data promises (is every source used when it is due?)", "| Source | Used by | Status |", "|---|---|---|"]
    for p in pr: L.append(f"| {p['source']} | {p['user']} | {'✅' if p['status'] == 'in use' else '⏳' if p['status'] == 'on track' else '❌'} {p['detail']} |")
    sc = R.get("chalScores") or {}
    def prog(s):
        if s.get("account"): return min(1.0, s.get("bets", 0) / 20) + (0.5 if s.get("better") else 0)
        return min(1.0, s.get("bets", 0) / 40) + max(0, s.get("lead", 0)) / max(1, s.get("zNeeded", 4))
    top = sorted([(n, s) for n, s in sc.items() if n != "champion"], key=lambda kv: -prog(kv[1]))[:5]
    L += ["", "## The race (closest to replacing the Arbiter)"]
    for n, s in top:
        if s.get("account"): L.append(f"- **{n}**: {s['me']['growth']:+.1f}% vs Arbiter {s['champion']['growth']:+.1f}% over the same days, {s.get('bets', 0)} of 20 trades" + (" - ahead; promoted automatically at 20 trades if it stays ahead" if s.get("better") and not s.get("ready") else ""))
        else: L.append(f"- **{n}**: lead {s.get('lead', 0):+.2f} (needs +{s.get('zNeeded', 4)}), {s.get('bets', 0)} of 40 separate market moves")
    L += ["", f"## Health: {rep.get('overall')}"]
    L += [f"- {c['status']}: {c['check']}: {c['detail'][:160]}" for c in bad] or ["- every check OK"]
    L += ["", "_Written automatically by the overseer. Full detail: the dashboard and docs/health.json._"]
    return "\n".join(L), title
