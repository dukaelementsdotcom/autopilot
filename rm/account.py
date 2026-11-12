"""The $500 account: trades ONLY the machine's current best rule ("champion of champions"), compounding, forward in time.
Honest by construction:
  - it starts at the moment it is created; it never trades on data from before its own start
  - the best rule is chosen at the END of a run and only used for hours that arrive LATER (no hindsight)
  - every entry is at the real open price of the hour the signal fired; exits replay real hourly highs/lows (TP / stop / time)
  - Binance's archive arrives ~1 day late, so trades are booked a day after they happen; open positions are marked at LIVE prices
Sizing: each trade uses 10% of the account as margin at 10x leverage; at most 3 trades open at once; a trade can lose at most its margin."""
import numpy as np, requests
from . import config as C
from .judge import lev_returns, trade_lev

START = 500.0; MARGIN_FRAC = 0.10; LEV = 10; SLOTS = 3; MIN_TRADES = 30
HOUR = 3600

def _score(J, t):
    """how convincing a champion is, using only trades AFTER its discovery+validation windows (final exam + live)"""
    tt, rr = J.series(t["h"], t_from=J.hold0)
    lv = trade_lev(t["h"]); s = J.stats(tt, rr, lv)
    if s.get("n", 0) < MIN_TRADES or s.get("mean", 0) <= 0 or s.get("levMean", 0) <= 0: return -99, s
    # rank by how reliably it makes money AS TRADED (10x isolated incl. liquidations; rotation at 1x), not by raw win rate
    sl = J.stats(tt, lev_returns(rr, lv), 1)
    return sl.get("t", 0), s

def ranked(mem, J, kinds=("event", "union")):
    """champions ranked by their record after their exams; near-duplicates (twins) are skipped"""
    out = []
    for k, t in mem["tests"].items():
        if t["status"] != "champion" or t["h"]["kind"] not in kinds: continue
        sc, s = _score(J, t)
        if sc > -99: out.append((sc, k, s))
    return sorted(out, key=lambda x: (x[0], str(x[1])), reverse=True)

def pick_leader(mem, J, kinds=("event", "union")):
    """the best-ranked champion, skipping rules whose LIVE record in the current market state says they fail here"""
    R = mem.get("referee") or {}; reg = str(R.get("regimeNow")); T = R.get("ruleTrust") or {}
    r = [x for x in ranked(mem, J, kinds) if ((T.get(str(mem["tests"][x[1]].get("no"))) or {}).get(reg) or {}).get("state") != "warning"]
    return r[0] if r else None

def select_rule(mem, J, A, now_iso, kinds=("event", "union")):
    """RULE SELECTION with dwell and a replacement margin: the rule in charge stays at least a week (unless the account fell 15% from its
    peak under it, or it lost its champion status / is failing in this market state), and a replacement must beat it by 0.10 points per
    trade on unseen trades, with at least 50 unseen trades each. Not enough data -> no replacement. Returns (key, score, stats, log line)."""
    import time, calendar
    tests = mem["tests"]; lead = A.get("leader"); best = pick_leader(mem, J, kinds)
    inc_ok = bool(lead and lead in tests and tests[lead]["status"] == "champion")
    no = lambda k: tests[k].get("no") if k in tests else None
    if not best: return (lead if inc_ok else None), None, None, f"incumbent #{no(lead) if lead else None}, candidate none, dwell_days -, replace False, reason no eligible candidate"
    sc, k, s = best
    if not inc_ok:
        return k, sc, s, f"incumbent {'#' + str(no(lead)) + ' (no longer a champion)' if lead else 'none'}, candidate #{no(k)}, dwell_days -, replace True, reason no valid incumbent"
    if k == lead: return lead, sc, s, f"incumbent #{no(lead)}, candidate #{no(k)}, dwell_days -, replace False, reason incumbent is still the best"
    since = A.get("leaderSince") or (A.get("leaders") or [{}])[-1].get("at") or A.get("born")
    try: t_since = calendar.timegm(time.strptime(since, "%Y-%m-%d %H:%M"))
    except Exception: t_since = 0
    dwell_h = (calendar.timegm(time.strptime(now_iso, "%Y-%m-%d %H:%M")) - t_since) / 3600
    eq = A.get("equity", A["cash"]); pk = max([c[1] for c in A.get("curve", []) if c[0] >= t_since] + [A.get("leaderEq", eq), eq]); dd = 1 - eq / pk
    R = mem.get("referee") or {}; warned = (((R.get("ruleTrust") or {}).get(str(no(lead))) or {}).get(str(R.get("regimeNow"))) or {}).get("state") == "warning"
    _, s_inc = _score(J, tests[lead]); head = f"incumbent #{no(lead)}, candidate #{no(k)}, dwell_days {dwell_h / 24:.1f}"
    if dwell_h < C.RULE_DWELL_H and dd <= C.RULE_DWELL_DD and not warned:
        return lead, None, None, f"{head}, replace False, reason dwell (a week minimum; drawdown under it {dd:.0%}, limit {C.RULE_DWELL_DD:.0%})"
    if s.get("n", 0) < C.RULE_REPLACE_N or s_inc.get("n", 0) < C.RULE_REPLACE_N:
        return lead, None, None, f"{head}, replace False, reason not enough unseen trades (candidate {s.get('n', 0)}, incumbent {s_inc.get('n', 0)}, need {C.RULE_REPLACE_N} each)"
    if s.get("mean", 0) <= s_inc.get("mean", 0) + C.RULE_REPLACE_PP:
        return lead, None, None, f"{head}, replace False, reason margin (candidate {s.get('mean')}% per unseen trade vs incumbent {s_inc.get('mean')}%, needs +{C.RULE_REPLACE_PP})"
    return k, sc, s, f"{head}, replace True, reason candidate {s.get('mean')}% per unseen trade vs incumbent {s_inc.get('mean')}% over {s.get('n')} / {s_inc.get('n')} unseen trades" + (" (drawdown override)" if dwell_h < C.RULE_DWELL_H else "")

def pick_team(mem, J, size=5, max_overlap=0.35):
    """up to 5 champions that trade DIFFERENT situations (little overlap), best first"""
    team, fps = [], []
    for sc, k, s in ranked(mem, J):
        fp = J.fingerprint(mem["tests"][k]["h"])
        if all(len(fp & f) / max(1, min(len(fp), len(f))) <= max_overlap for f in fps): team.append((sc, k, s)); fps.append(fp)
        if len(team) >= size: break
    return team

def live_prices(coins):
    try:
        r = requests.get("https://data-api.binance.vision/api/v3/ticker/price", timeout=20)
        px = {d["symbol"]: float(d["price"]) for d in r.json()}
        out = {}
        for c in coins:                       # spot lists 1000PEPE as PEPE: same coin, 1000x the unit
            if c.startswith("1000") and f"{c}USDT" not in px: v = px.get(f"{c[4:]}USDT"); out[c] = v * 1000 if v else None
            else: out[c] = px.get(f"{c}USDT")
        return out
    except Exception: return {}

def step(mem, J, now_iso, log=print, key="account", team=False, lev=LEV, frac=MARGIN_FRAC, slots=SLOTS, kinds=("event", "union"), start=START):
    A = mem.get(key)
    if A is None:                                                   # birth: starts at the newest hour of data, trades only what comes after
        A = mem[key] = {"born": now_iso, "t0": int(J.t.max()), "lastT": int(J.t.max()), "cash": start, "start": start, "lev": lev, "open": [], "closed": [],
                              "leader": None, "leaders": [], "curve": [[int(J.t.max()), start]]}
    tests = mem["tests"]; lead = A.get("leader")
    rules = [k for k in (A.get("team") or ([lead] if lead else [])) if k in tests and tests[k]["status"] == "champion"]
    newest = int(J.t.max())
    # 1) replay the hours that arrived since the last run, in time order, with the leader chosen BEFORE they arrived
    def walk(p):
        w = J._walk(p["coin"], p["t"], p["sd"], p["exit"])
        if w: p["entry"] = float(w["entry"]); p["tp"] = w.get("tp"); p["sl"] = w.get("sl"); p["trail"] = w.get("trail"); p["_w"] = w
        return w
    def close_until(t_now):
        keep = []
        for p in sorted(A["open"], key=lambda p: (p.get("_w") or {}).get("exitT") or 1e18):
            w = p.get("_w") or walk(p)
            if w and w["how"] != "open" and w["exitT"] <= t_now:
                pnl = p["margin"] * float(lev_returns([w["ret"]], lev)[0]); A["cash"] = round(A["cash"] + pnl, 2)
                A["closed"].append({**{k: v for k, v in p.items() if k not in ("sd", "_w", "votes")}, "exitT": w["exitT"], "exitPx": w["exitPx"], "how": w["how"],
                                    "pct": round(w["ret"] * 100, 2), "usd": round(pnl, 2), "after": A["cash"]})
                A["curve"].append([w["exitT"], A["cash"]])
            else: keep.append(p)
        A["open"] = keep
    for p in A["open"]: walk(p)
    if rules and newest > A["lastT"]:
        ent = []
        for k in rules:
            for i, sd, ex in J.raw_entries(tests[k]["h"], t_lo=A["lastT"], t_hi=newest): ent.append((int(J.t[i]), i, sd, ex, k))
        ent.sort(key=lambda e: (e[0], e[1], str(e[4])))          # never compare the exit settings (dicts)
        slots_ = slots * (2 if team else 1); frac_ = frac / (2 if team else 1)
        for t0, i, sd, ex, k in ent:
            c = str(J.coin[i]); close_until(t0)
            same = [p for p in A["open"] if p["coin"] == c and p["sd"] == sd]
            if team and same and t0 == same[0]["t"] and k not in same[0].get("votes", []):     # another team rule agrees -> add to the position
                p = same[0]; p["votes"] = p.get("votes", []) + [k]; p["rule"] = f'{p["rule"]},{tests[k].get("no")}'
                extra = round(A["cash"] * frac_, 2) if len(p["votes"]) <= 3 else 0; p["margin"] = round(p["margin"] + extra, 2); continue
            if len(A["open"]) >= slots_ or any(p["coin"] == c for p in A["open"]): continue
            last_same = max([p["t"] for p in A["closed"] if p["coin"] == c] or [-1e18])
            if t0 - last_same < (tests[k]["h"].get("cool", 24) if tests[k]["h"]["kind"] != "rotate" else 0) * HOUR: continue
            p = {"coin": c, "side": "LONG" if sd > 0 else "SHORT", "sd": sd, "t": t0, "exit": ex, "rule": str(tests[k].get("no")), "votes": [k],
                 "margin": round(A["cash"] * frac_, 2)}
            walk(p)
            if "entry" not in p:                        # replay not possible yet: take the entry and levels straight from the signal hour
                from .judge import scale_exit
                e = float(J.O[i]); p["entry"] = e
                d = scale_exit(ex, J.dvol[i]) if isinstance(ex, dict) else ({"hold": C.EXITS[ex][1]} if C.EXITS[ex][0] == "ret" else {"tp": C.EXITS[ex][0], "sl": C.EXITS[ex][1], "hold": C.EXITS[ex][2]})
                if d: p["tp"] = e * (1 + sd * d["tp"]) if d.get("tp") else None; p["sl"] = e * (1 - sd * d["sl"]) if d.get("sl") else None; p["trail"] = d.get("trail")
            A["open"].append(p)
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
        p["unreal"] = round(p["margin"] * float(lev_returns([r], lev)[0]), 2); unreal += p["unreal"]
    A["equity"] = round(A["cash"] + unreal, 2)
    # 4) re-pick the champion of champions for the NEXT hours (re-tested every run on the newest data)
    if team:
        tm = pick_team(mem, J); new = [k for _, k, _ in tm]
        if new != A.get("team"):
            A["leaders"].append({"at": now_iso, "rule": ", ".join(f"#{tests[k].get('no')}" for k in new), "text": " | ".join((tests[k].get("plain") or tests[k]["text"])[:140] for k in new),
                                 "score": round(sum(sc for sc, _, _ in tm) / max(1, len(tm)), 2), "why": f"{len(new)} champions that trade different situations, best records first"})
            A["team"] = new
        best = None
    else:
        k_, sc_, s_, line = select_rule(mem, J, A, now_iso, kinds); best = (sc_, k_, s_) if (k_ and k_ != lead and s_ is not None) else None
        try:
            from . import integrity; integrity.audit(mem.setdefault("referee", {}), "rule_selection", line, log)
        except Exception: pass
        if not k_: lead = A["leader"] = None
    if best:
        sc, k, s = best
        if k != lead:
            A["leaderSince"] = now_iso; A["leaderEq"] = A.get("equity", A["cash"])
            A["leaders"].append({"at": now_iso, "rule": tests[k].get("no"), "text": tests[k].get("plain") or tests[k]["text"], "score": round(sc, 2),
                                 "why": f"best record after its exams: {s['n']} trades, avg {s['mean']:+}% per trade, {s['hit']}% won (t {s['t']})"})
            A["leader"] = k
    elif not team: pass
    elif lead and (lead not in tests or tests[lead]["status"] != "champion"): A["leader"] = None
    from . import archive; nm = key
    archive.trim(A["closed"], 400, f"{nm}_trades"); archive.trim(A["curve"], 1000, f"{nm}_curve"); A["leaders"] = A["leaders"][-50:]
    log(f"{key}: ${A['equity']} (cash ${A['cash']}, {len(A['open'])} open, {len(A['closed'])} closed), rules {[tests[k].get('no') for k in (A.get('team') or ([A['leader']] if A.get('leader') else []))]}")
    return A
