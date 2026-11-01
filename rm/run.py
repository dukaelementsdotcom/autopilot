"""One research day. Run by GitHub Actions every day (or by hand):  python -m rm.run"""
import os, json, time, random, datetime as dt
import numpy as np
from . import config as C, fetch, features, ideas, report, account
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

def now_iso(): return dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")

def load_memory():
    p = f"{C.STATE_DIR}/memory.json"
    if os.path.exists(p):
        with open(p) as f: return json.load(f)
    return {"version": 1, "born": now_iso(), "tests": {}, "seen": [], "families": {}, "cursor": 0, "runs": [], "lessons": [], "events": [], "ai": {}, "total_tests": 0}

def save_memory(mem):
    os.makedirs(C.STATE_DIR, exist_ok=True)
    tmp = f"{C.STATE_DIR}/memory.json.tmp"
    with open(tmp, "w") as f: json.dump(mem, f, separators=(",", ":"))
    os.replace(tmp, f"{C.STATE_DIR}/memory.json")

def compact(mem):
    """keep knowledge, drop detail: old dead tests are folded into their family's counters (already counted) and removed"""
    T = mem["tests"]
    if len(T) <= C.KEEP_DETAILED_TESTS: return 0
    dead = sorted((k for k, v in T.items() if v["status"].startswith("dead")), key=lambda k: T[k]["at"])
    drop = dead[: len(T) - C.KEEP_DETAILED_TESTS]
    near = sorted((k for k, v in T.items() if v["status"] == "near"), key=lambda k: T[k]["at"])
    drop += near[: max(0, len(near) - 1500)]                 # old near-misses go too (their family keeps the count)
    for k in drop: T.pop(k, None)
    mem["seen"] = mem["seen"][-150000:]                       # fingerprints of tested ideas (≈2.5 MB max)
    return len(drop)

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
    mem = load_memory(); seen = set(mem["seen"]); events = []; certified_now = []
    log("── 1/6 data"); fetch.update(log=log)
    P = features.build(log=log); J = Judge(P); cols = J.cols
    log(f"windows: discovery < {dt.datetime.utcfromtimestamp(J.val0).date()} ≤ validation < {dt.datetime.utcfromtimestamp(J.hold0).date()} ≤ holdout ≤ {dt.datetime.utcfromtimestamp(J.t_end).date()}")

    log("── 2/6 live review of champions")
    for k, t in mem["tests"].items():
        if t["status"] != "champion": continue
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
    log("── 3/6 promotions"); promote()

    log("── 4/6 new ideas"); rng = random.Random(int(time.time())); queue = []
    def add(h, src, gen=0, why=""):
        if h and valid(h, cols) and hid(h) not in seen and all(hid(h) != hid(q[0]) for q in queue): queue.append((h, src, gen, why))
    if os.environ.get("GROQ_API_KEY"):
        got, model = ideas.ai_ideas(mem, cols); mem["ai"] = {"at": now_iso(), "model": model, "ideas": len(got)}
        for h, why in got: add(h, "ai", 0, why)
    good = [t for t in mem["tests"].values() if t["status"] in ("champion", "validated")]
    near = [t for t in mem["tests"].values() if t["status"] == "near"]
    for a, b in list(zip(good, good[1:] + good[:1]))[:40]: add(ideas.combine(a["h"], b["h"]), "combine", max(a["gen"], b["gen"]) + 1)
    parents = [p for p in good + near if p["gen"] < C.MAX_GENERATION]; kids = {}
    for _ in range(int(budget * 0.4)):                        # evolve: at most 6 children per parent per day, ≤ 20% of the budget
        if not parents or sum(kids.values()) >= budget * 0.2: break
        p = rng.choice(parents); pk = hid(p["h"])
        if kids.get(pk, 0) >= 6: continue
        n0 = len(queue); add(ideas.mutate(p["h"], cols, rng), "evolve", p["gen"] + 1)
        if len(queue) > n0: kids[pk] = kids.get(pk, 0) + 1
    for name, sp, cap in (("ccur", ideas.classic_space(cols), 0.12), ("scur", ideas.structural_space(), 0.10)):   # open-source classics + market-neutral ideas
        n0 = len(queue); mem.setdefault(name, 0)
        while len(queue) - n0 < budget * cap and mem[name] < len(sp):
            h, why = sp[mem[name]]; add(h, "classic" if name == "ccur" else "structural", 0, why); mem[name] += 1
    space = ideas.scan_space(cols)
    while len(queue) < budget * 0.9 and mem["cursor"] < len(space):
        add(space[mem["cursor"]], "scan"); mem["cursor"] += 1
    while len(queue) < budget: add(ideas.explore(cols, rng), "explore")
    counts = {}; new_valid = 0
    for h, src, gen, why in queue[:budget]:
        try: res = J.evaluate(h)
        except Exception as e: log(f"   ! {e}"); continue
        k, rec = record(mem, h, res, src, gen, why); seen.add(k)
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        if res["status"] == "validated": new_valid += 1
    log(f"   tested {sum(counts.values())}: {counts}")
    promote()

    for t in sorted((t for t in mem["tests"].values() if t["status"] == "champion" and not t.get("no")), key=lambda t: t.get("promotedOn", "")):
        mem["nextNo"] = mem.get("nextNo", 1); t["no"] = mem["nextNo"]; mem["nextNo"] += 1
    if os.environ.get("GROQ_API_KEY"):                       # plain-English explanations for the dashboard
        todo = [t for t in mem["tests"].values() if t["status"] in ("champion", "validated") and not t.get("plain")][:12]
        for t in todo:
            p = ideas.ai_plain(t["text"])
            if p: t["plain"] = p
    log("── 5/6 machine portfolio + lessons")
    champs = {k: t for k, t in mem["tests"].items() if t["status"] == "champion"}
    port = []
    for k, t in champs.items():
        for at, r in (t.get("live") or {}).get("last", []): port.append((at, r))
    mem["portfolio"] = {"champions": len(champs), "recent": sorted(port)[-30:]}
    if os.environ.get("GROQ_API_KEY") and (not mem.get("lessonsAt") or (dt.datetime.utcnow() - dt.datetime.strptime(mem["lessonsAt"], "%Y-%m-%d %H:%M")).days >= 7):
        L = ideas.ai_lessons(mem)
        if L: mem["lessons"] = L; mem["lessonsAt"] = now_iso()
    try: account.step(mem, J, now_iso(), log=log)
    except Exception as e: log(f"account step failed: {e}")
    dropped = compact(mem)

    run_rec = {"at": now_iso(), "tested": sum(counts.values()), "counts": counts, "validated": new_valid, "events": [[e, mem["tests"][k]["text"]] for e, k in events if k in mem["tests"]],
               "dataEnd": dt.datetime.utcfromtimestamp(int(P.t.max())).strftime("%Y-%m-%d %H:%M"), "secs": round(time.time() - t0), "scanDone": f"{mem['cursor']}/{len(space)}", "classicDone": mem["ccur"], "structuralDone": mem["scur"], "compacted": dropped}
    mem["runs"] = [run_rec] + mem["runs"][:60]
    mem["events"] = (run_rec["events"] + mem["events"])[:60]
    log("── 6/6 report"); save_memory(mem); report.write(mem, J, log=log); report.alert(certified_now, log=log)
    log(f"done in {run_rec['secs']} s")
    return run_rec

if __name__ == "__main__":
    run()
