"""One research day. Run by GitHub Actions every day (or by hand):  python -m rm.run"""
import os, json, time, random, datetime as dt
import numpy as np
from . import config as C, fetch, fetch_ext, features, ideas, report, account
from .judge import Judge, hid, valid, family, describe
from .features import DESC

def load_env(path="secrets.env"):
    """local keys file (used when running on your own computer instead of GitHub)"""
    if not os.path.exists(path): return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip(): os.environ.setdefault(k.strip(), v.strip())

def due(mem, name, hours):
    """ration the free AI: each job runs at most every `hours` hours (Groq's free daily limits)"""
    clk = mem.setdefault("aiClock", {}); last = clk.get(name)
    if last and (time.time() - last) < hours * 3600 - 300: return False
    clk[name] = time.time(); return True

def now_iso(): return dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")

SEEN_MAX = 1_000_000
def load_memory():
    p = f"{C.STATE_DIR}/memory.json"
    if os.path.exists(p):
        with open(p) as f: mem = json.load(f)
        sp = f"{C.STATE_DIR}/seen.txt.gz"
        if os.path.exists(sp):
            import gzip
            with gzip.open(sp, "rt") as f: mem["seen"] = f.read().split()
        mem.setdefault("seen", [])
        return mem
    return {"version": 1, "born": now_iso(), "tests": {}, "seen": [], "families": {}, "cursor": 0, "runs": [], "lessons": [], "events": [], "ai": {}, "total_tests": 0}

def save_memory(mem):
    os.makedirs(C.STATE_DIR, exist_ok=True)
    import gzip
    seen = mem.pop("seen", [])[-SEEN_MAX:]
    try:
        with gzip.open(f"{C.STATE_DIR}/seen.txt.gz.tmp", "wt") as f: f.write("\n".join(seen))
        os.replace(f"{C.STATE_DIR}/seen.txt.gz.tmp", f"{C.STATE_DIR}/seen.txt.gz")
        tmp = f"{C.STATE_DIR}/memory.json.tmp"
        with open(tmp, "w") as f: json.dump(mem, f, separators=(",", ":"))
        os.replace(tmp, f"{C.STATE_DIR}/memory.json")
    finally: mem["seen"] = seen

def compact(mem):
    """keep knowledge, drop detail: old dead tests are folded into their family's counters (already counted) and removed"""
    T = mem["tests"]
    if len(T) <= C.KEEP_DETAILED_TESTS: return 0
    dead = sorted((k for k, v in T.items() if v["status"].startswith("dead")), key=lambda k: T[k]["at"])
    drop = dead[: len(T) - C.KEEP_DETAILED_TESTS]
    near = sorted((k for k, v in T.items() if v["status"] == "near"), key=lambda k: T[k]["at"])
    drop += near[: max(0, len(near) - 1500)]                 # old near-misses go too (their family keeps the count)
    for k in drop: T.pop(k, None)
    tw = sorted((k for k, v in T.items() if v["status"] == "twin"), key=lambda k: T[k]["at"])
    for k in tw[: max(0, len(tw) - 300)]: T.pop(k, None); drop.append(k)     # keep the newest 300 twins
    mem["seen"] = mem["seen"][-SEEN_MAX:]                     # fingerprints of every tested idea (never retested)
    return len(drop)

def dedupe(mem, J, log=print, limit=0.6):
    """near-copies of the same idea are merged: the best one stays champion, the others become its 'twin'"""
    from .account import _score
    champs = sorted(((_score(J, t)[0], k) for k, t in mem["tests"].items() if t["status"] == "champion"), reverse=True)
    kept = []; n = 0
    for sc, k in champs:
        t = mem["tests"][k]; fp = J.fingerprint(t["h"])
        twin = next((kk for kk, f in kept if len(fp & f) / max(1, min(len(fp), len(f))) > limit), None)
        if twin: t["status"] = "twin"; t["twinOf"] = mem["tests"][twin].get("no"); n += 1
        else: kept.append((k, fp))
    if n: log(f"   merged {n} near-duplicate champions into their best twin")
    return n

def record(mem, h, res, src, gen, why=""):
    k = hid(h); fam = family(h)
    rec = {"h": h, "text": describe(h, DESC), "src": src, "gen": gen, "at": now_iso(), **res}
    if why: rec["why"] = why
    mem["tests"][k] = rec; mem["seen"].append(k); mem["total_tests"] += 1
    F = mem["families"].setdefault(fam, {"n": 0, "best": 0, "ok": 0})
    F["n"] += 1; F["best"] = max(F["best"], res["disc"].get("t", 0) or 0)
    if res["status"] in ("validated",): F["ok"] += 1
    return k, rec

def run(budget=None, log=print):
    load_env(); t0 = time.time(); budget = budget or C.TESTS_PER_RUN
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and os.path.exists(f"{C.STATE_DIR}/memory.json"):    # two timers may fire: skip a duplicate
        last = (load_memory().get("runs") or [{}])[0].get("at")
        if last and (dt.datetime.utcnow() - dt.datetime.strptime(last, "%Y-%m-%d %H:%M")).total_seconds() < 25 * 60:
            log("another run finished less than 25 minutes ago: skipping this one"); return None
    mem = load_memory(); seen = set(mem["seen"]); events = []; certified_now = []; problems = []
    def safe(name, fn, *a, **k):
        """one broken part must never stop the whole run: log it, show it on the dashboard, carry on"""
        try: return fn(*a, **k)
        except Exception as e:
            import traceback; msg = f"{name}: {type(e).__name__}: {str(e)[:160]}"; problems.append(msg); log("   ! " + msg); log(traceback.format_exc()[-600:])
    # the more ideas ever tested, the more luck can hide: the validation bar rises slowly with the total (2.0 at 10k tests, 2.5 at 1M)
    C.VAL_T = round(max(2.0, 2.0 + 0.25 * np.log10(max(mem["total_tests"], 1) / 10000)), 2)
    log("── 1/6 data"); fetch.update(log=log); fetch_ext.update(log=log)
    blocks = mem.setdefault("blocks", [])
    for b in blocks: DESC[b["id"]] = f"invented measurement '{b.get('name') or b['id']}' = {b['expr']}"
    P = features.build(log=log, blocks=blocks); J = Judge(P); cols = J.cols
    log(f"windows: discovery < {dt.datetime.utcfromtimestamp(J.val0).date()} ≤ validation < {dt.datetime.utcfromtimestamp(J.hold0).date()} ≤ holdout ≤ {dt.datetime.utcfromtimestamp(J.t_end).date()}")

    log("── 2/6 live review of champions")
    def review(k, t):
        re = J.evaluate(t["h"])                       # re-exam every run: newest data, all coins (incl. collapsed ones), today's bar
        t["disc"], t["val"] = re["disc"], re["val"]
        if re["status"] not in ("validated", "near") or (re["status"] == "near" and re["val"].get("t", 0) < 1.0):
            t["status"] = "dead_reexam"; t["diedAt"] = now_iso(); events.append(("failed re-exam", k)); return
        L = J.live(t["h"], t["promotedAt"]); t["live"] = L
        t["signals"] = J.signals_now(t["h"])
        if L["n"] >= C.LIVE_REVIEW_TRADES:
            if L["mean"] < 0: t["status"] = "dead_live"; t["diedAt"] = now_iso(); events.append(("demoted", k))
            elif L["t"] >= 2 and not t.get("proven"): t["proven"] = now_iso(); events.append(("proven", k))
        if t["status"] == "champion" and not t.get("certified"):     # the undeniable bar (checked weekly per champion)
            last = t.get("certCheckedAt", 0)
            if J.t_end - last >= 7 * 86400:
                ok, checks = J.certify(t["h"], t["promotedAt"], mem["total_tests"], cols, ideas.mutate)
                t["certChecks"] = checks; t["certCheckedAt"] = J.t_end
                if ok: t["certified"] = now_iso(); events.append(("certified", k)); certified_now.append(t)
    for k, t in list(mem["tests"].items()):
        if t["status"] == "champion": safe(f"review of rule #{t.get('no')}", review, k, t)

    def promote():
        champs = sum(1 for t in mem["tests"].values() if t["status"] == "champion")
        for k, t in list(mem["tests"].items()):
            if t["status"] != "validated": continue
            hs = J.holdout(t["h"]); t["hold"] = hs
            min_h = {"event": C.MIN_HOLD_TRADES, "pair": 5, "carry": 20}.get(t["h"]["kind"], 12)
            if hs["n"] >= min_h and hs["mean"] > 0 and champs < 30:
                t["status"] = "champion"; t["promotedAt"] = J.t_end; t["promotedOn"] = now_iso(); champs += 1; events.append(("promoted", k))
                mem["nextNo"] = mem.get("nextNo", 1); t["no"] = mem["nextNo"]; mem["nextNo"] += 1
            elif hs["n"] < min_h: t["status"] = "validated"; t["waitHold"] = True       # not enough holdout trades yet — wait for more data
            else: t["status"] = "dead_hold"
    log("── 3/6 promotions"); safe("promotions", promote); safe("twin check", dedupe, mem, J, log)

    log("── 4/6 new ideas"); rng = random.Random(int(time.time())); queue = []
    def add(h, src, gen=0, why=""):
        if h and valid(h, cols) and hid(h) not in seen and all(hid(h) != hid(q[0]) for q in queue): queue.append((h, src, gen, why))
    def src_blocks():
        for b in blocks:                               # first tests for invented measurements that are now computed
            if not b.get("scanned") and ("p_" + b["id"]) in cols:
                for h in ideas.block_space(b["id"]): add(h, "invented", 0, f"first test of invented measurement {b['id']}")
                b["scanned"] = now_iso()
    def src_ai():
        got, model = ideas.ai_ideas(mem, cols); mem["ai"] = {"at": now_iso(), "model": model, "ideas": len(got)}
        for h, why in got: add(h, "ai", 0, why)
    def src_analyst():
        got, notes = ideas.ai_analyst(mem, cols)
        if notes is not None: mem["analyst"] = {"at": now_iso(), **notes}
        for h, why in got: add(h, "analyst", 1, why)
    def src_evolve():
        good = [t for t in mem["tests"].values() if t["status"] in ("champion", "validated")]
        ev = [t for t in good if t["h"]["kind"] == "event"]
        for i, a in enumerate(ev[:12]):                               # "X OR Y": two winners that trade different situations
            for b in ev[i + 1:12]:
                if a["h"]["exit"] == b["h"]["exit"]: add({"kind": "union", "parts": [a["h"], b["h"]], "cool": 24}, "merge", 2, "two winners that fire in different situations, traded together")
        near = [t for t in mem["tests"].values() if t["status"] == "near"]
        for a, b in list(zip(good, good[1:] + good[:1]))[:40]: add(ideas.combine(a["h"], b["h"]), "combine", max(a["gen"], b["gen"]) + 1)
        parents = [p for p in good + near if p["gen"] < C.MAX_GENERATION]; kids = {}
        for _ in range(int(budget * 0.4)):                    # evolve: at most 6 children per parent per run, ≤ 20% of the budget
            if not parents or sum(kids.values()) >= budget * 0.2: break
            p = rng.choice(parents); pk = hid(p["h"])
            if kids.get(pk, 0) >= 6: continue
            n0 = len(queue); add(ideas.mutate(p["h"], cols, rng), "evolve", p["gen"] + 1)
            if len(queue) > n0: kids[pk] = kids.get(pk, 0) + 1
    def src_lists():
        for name, sp, cap, src_ in (("bcur", ideas.bigmove_space(cols), 0.15, "big-move"), ("rcur", ideas.rotation_space(cols), 0.08, "rotation"),
                                    ("ccur", ideas.classic_space(cols), 0.12, "classic"), ("scur", ideas.structural_space(), 0.10, "structural")):
            n0 = len(queue); mem.setdefault(name, 0)
            while len(queue) - n0 < budget * cap and mem[name] < len(sp):
                h, why = sp[mem[name]]; add(h, src_, 0, why); mem[name] += 1
        space = ideas.scan_space(cols); mem["spaceN"] = len(space)
        while len(queue) < budget * 0.9 and mem["cursor"] < len(space):
            add(space[mem["cursor"]], "scan"); mem["cursor"] += 1
    safe("invented-measurement tests", src_blocks)
    if os.environ.get("GROQ_API_KEY") and due(mem, "ideas", 2): safe("AI researcher", src_ai)
    if os.environ.get("GROQ_API_KEY") and due(mem, "analyst", 6): safe("AI analyst", src_analyst)
    safe("evolution", src_evolve); safe("idea lists", src_lists)
    tries = 0
    while len(queue) < budget and tries < budget * 20: tries += 1; safe("exploration", lambda: add(ideas.explore(cols, rng), "explore"))
    counts = {}; new_valid = 0
    for h, src, gen, why in queue[:budget]:
        if time.time() - t0 > 35 * 60: log("   time limit: stopping tests early so the next hourly run is not delayed"); break
        try: res = J.evaluate(h)
        except Exception as e: log(f"   ! {e}"); continue
        k, rec = record(mem, h, res, src, gen, why); seen.add(k)
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        if res["status"] == "validated": new_valid += 1
    log(f"   tested {sum(counts.values())}: {counts}")
    # invented measurements: keep the useful ones, retire the useless, invent new ones (computed from the next run on)
    def lifecycle():
        rank_ = {"champion": 4, "validated": 3, "twin": 3, "near": 2, "dead_val": 1, "dead_disc": 0}
        used = {}
        for t in mem["tests"].values():
            hs = t["h"]["parts"] if t["h"]["kind"] == "union" else [t["h"]]
            for q in hs:
                for c in q.get("when", []):
                    bid = c["f"].removeprefix("p_")
                    if bid.startswith("u_"): u = used.setdefault(bid, [0, -1]); u[0] += 1; u[1] = max(u[1], rank_.get(t["status"], 0))
        keep = []
        for b in blocks:
            n_, best = used.get(b["id"], [0, -1]); b["tests"] = max(b.get("tests", 0), n_); b["best"] = {4: "champion", 3: "validated", 2: "near", 1: "failed validation", 0: "failed", -1: "-"}[best]
            if b.get("scanned") and b["tests"] >= 60 and best < 2: mem.setdefault("blocks_retired", []).append({k: b[k] for k in ("id", "expr", "why", "tests", "best")}); log(f"   retired invented measurement {b['id']}")
            else: keep.append(b)
        mem["blocks"] = keep; blocks[:] = keep; mem["blocks_retired"] = mem.get("blocks_retired", [])[-200:]
        if os.environ.get("GROQ_API_KEY") and len(keep) < 16 and due(mem, "blocks", 4):
            for b in ideas.ai_blocks(mem):
                if all(b["id"] != x["id"] for x in keep): b["at"] = now_iso(); keep.append(b); log(f"   invented measurement {b['id']} = {b['expr']}")
    safe("invented measurements", lifecycle)
    safe("promotions", promote); safe("twin check", dedupe, mem, J, log)

    for t in sorted((t for t in mem["tests"].values() if t["status"] == "champion" and not t.get("no")), key=lambda t: t.get("promotedOn", "")):
        mem["nextNo"] = mem.get("nextNo", 1); t["no"] = mem["nextNo"]; mem["nextNo"] += 1
    def plain():
        todo = [t for t in mem["tests"].values() if t["status"] in ("champion", "validated") and not t.get("plain")][:5]
        for t in todo:
            p = ideas.ai_plain(t["text"])
            if p: t["plain"] = p
    if os.environ.get("GROQ_API_KEY"): safe("plain-English explanations", plain)
    log("── 5/6 machine portfolio + lessons")
    champs = {k: t for k, t in mem["tests"].items() if t["status"] == "champion"}
    port = []
    for k, t in champs.items():
        for at, r in (t.get("live") or {}).get("last", []): port.append((at, r))
    mem["portfolio"] = {"champions": len(champs), "recent": sorted(port)[-30:]}
    def lessons():
        L = ideas.ai_lessons(mem)
        if L: mem["lessons"] = L; mem["lessonsAt"] = now_iso()
    if os.environ.get("GROQ_API_KEY") and (not mem.get("lessonsAt") or (dt.datetime.utcnow() - dt.datetime.strptime(mem["lessonsAt"], "%Y-%m-%d %H:%M")).days >= 7): safe("weekly lessons", lessons)
    safe("$500 account", account.step, mem, J, now_iso(), log=log)
    safe("team account", account.step, mem, J, now_iso(), log=log, key="team", team=True)
    safe("$700 all-in account", account.step, mem, J, now_iso(), log=log, key="allin", lev=1, frac=1.0, slots=1, kinds=("rotate",), start=700.0)
    dropped = safe("memory clean-up", compact, mem) or 0

    run_rec = {"at": now_iso(), "tested": sum(counts.values()), "counts": counts, "validated": new_valid, "events": [[e, mem["tests"][k]["text"]] for e, k in events if k in mem["tests"]],
               "dataEnd": dt.datetime.utcfromtimestamp(int(P.t.max())).strftime("%Y-%m-%d %H:%M"), "secs": round(time.time() - t0), "scanDone": f"{mem['cursor']}/{mem.get('spaceN', 0)}", "classicDone": mem["ccur"], "structuralDone": mem["scur"], "valBar": C.VAL_T, "compacted": dropped}
    run_rec["problems"] = problems
    mem["runs"] = [run_rec] + mem["runs"][:60]
    mem["events"] = (run_rec["events"] + mem["events"])[:60]
    log("── 6/6 report"); save_memory(mem)
    if safe("dashboard", report.write, mem, J, log=log) is None and problems and problems[-1].startswith("dashboard"):
        safe("simple dashboard", report.write_minimal, mem, problems)
    safe("alert", report.alert, certified_now, log=log)
    log(f"done in {run_rec['secs']} s")
    return run_rec

if __name__ == "__main__":
    run()
