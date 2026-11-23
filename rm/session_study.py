"""SESSION + ENTRY STUDY (owner request Nov 23). No signals, no brain: it asks the raw questions behind the 'blitz' idea on all the history we hold.
  1. Volatility by UTC hour (where do the big hours sit?).
  2. A blitz-shaped trade (target +1.125% price = +22.5% on margin at 20x, the Arbiter's volatility stop, 12h cap, 0.1% cost) entered at EVERY hour,
     both sides, all coins: by entry hour: wins, stops, timeouts, average result, how fast the target is reached.
  3. Entry price: instead of entering at the hour open, rest a limit order 0.3 / 0.6 / 1.0% BETTER than the open (higher for shorts, lower for longs), valid 6h:
     how often it fills and whether the result per signal improves.
  4. When does the best entry price of the next 12h happen (UTC hour of the extreme) and how much better than the open is it?
Output: rm/studies/session_study.json plus a printed summary. A stop is assumed first when stop and target sit in the same hour (conservative).
usage: python -m rm.session_study"""
import os, json, numpy as np, datetime as dt
from . import config as C, features, referee as RF
from .judge import Judge

H = 3600; TGT = 0.01125; MAXH = 12; FEE = C.FEE; LEVX = 7.0            # 35% margin x 20x = 7 times the account
SESS = [("Asia 00-07", 0, 7), ("London 07-13", 7, 13), ("New York 13-21", 13, 21), ("Late 21-24", 21, 24)]

def sim(O, Hh, Ll, Cc, js, e, side, stp, maxh=MAXH, fill_bar=False):
    """first-touch outcome for entries at price e whose first managed bar is js: code +1 target / -1 stop / 0 time, net price return, bars used"""
    n = len(js); code = np.zeros(n, int); ret = np.full(n, np.nan); bars = np.full(n, maxh); done = np.zeros(n, bool); L = len(O)
    tp = e * (1 + side * TGT); sl = e * (1 - side * stp)
    for k in range(maxh):
        idx = np.minimum(js + k, L - 1); hi, lo = Hh[idx], Ll[idx]
        hit_sl = (lo <= sl) if side > 0 else (hi >= sl); hit_tp = (hi >= tp) if side > 0 else (lo <= tp)
        if fill_bar and k == 0: hit_tp = np.zeros_like(hit_tp)           # the bar where a limit order filled: only the stop can count (order of moves inside the bar is unknown)
        a = ~done & hit_sl; b = ~done & ~hit_sl & hit_tp
        code[a] = -1; ret[a] = (-stp - FEE)[a]; bars[a] = k + 1; code[b] = 1; ret[b] = TGT - FEE; bars[b] = k + 1; done |= a | b
    end = np.minimum(js + maxh - 1, L - 1); r0 = side * (Cc[end] / e - 1) - FEE
    ret[~done] = r0[~done]; return code, ret, bars

def agg(hours, code, ret, bars, label):
    out = {}
    for h in range(24):
        m = hours == h
        if m.sum() < 50: continue
        c, r, b = code[m], ret[m], bars[m]; hit = c == 1
        out[h] = {"n": int(m.sum()), "win%": round(100 * hit.mean(), 1), "stop%": round(100 * (c == -1).mean(), 1), "time%": round(100 * (c == 0).mean(), 1),
                  "avgNetPricePct": round(100 * float(r.mean()), 3), "avgAccountPct": round(100 * LEVX * float(r.mean()), 2), "medBarsToTarget": float(np.median(b[hit])) if hit.any() else None}
    return out

def main():
    blocks = []
    try: blocks = json.load(open(f"{C.STATE_DIR}/memory.json")).get("blocks") or []
    except Exception: pass
    J = Judge(features.build(blocks=blocks)); coins = sorted(set(J.coin.tolist())); print(len(coins), "coins")
    vol = {h: [] for h in range(24)}
    S = {s: {"hour": [], "code": [], "ret": [], "bars": [], "stp": []} for s in (1, -1)}
    IMP = {s: {d: {"fill": 0, "n": 0, "ret_sum_all": 0.0, "ret_sum_fill": 0.0, "wins": 0, "base_ret_sum": 0.0, "base_n": 0} for d in (0.003, 0.006, 0.01)} for s in (1, -1)}
    EXT = {s: {"imp": {h: [] for h in range(24)}, "hr": {h: np.zeros(24) for h in range(24)}, "early": {h: [] for h in range(24)}} for s in (1, -1)}
    for coin in coins:
        T, O, Hh, Ll, Cc = J._bars(coin); L = len(T)
        if L < 500: continue
        hh = (T // H) % 24; rng = (Hh - Ll) / np.where(O > 0, O, np.nan)
        for h in range(24): vol[h].append(float(np.nanmean(rng[hh == h])))
        idx = np.flatnonzero(J.coin == coin); t = J.t[idx]; j = np.searchsorted(T, t); ok = (j < L - MAXH - 8) & (j >= 0)
        j, idx, t = j[ok], idx[ok], t[ok]; ok = T[j] == t; j, idx, t = j[ok], idx[ok], t[ok]
        stp = np.asarray(RF.stop_pct(J.dvol[idx]), float); ok = np.isfinite(stp) & (stp > 0); j, t, stp = j[ok], t[ok], stp[ok]
        if len(j) < 100: continue
        hour = (t // H) % 24; e0 = O[j]
        for s in (1, -1):
            c, r, b = sim(O, Hh, Ll, Cc, j, e0, s, stp)
            for k, v in (("hour", hour), ("code", c), ("ret", r), ("bars", b), ("stp", stp)): S[s][k].append(v)
            for d in (0.003, 0.006, 0.01):                                   # limit order better than the open, valid 6 bars
                lim = e0 * (1 - s * d); fill = np.full(len(j), -1)
                for k in range(6):
                    idk = np.minimum(j + k, L - 1); hit = (Ll[idk] <= lim) if s > 0 else (Hh[idk] >= lim)
                    fill = np.where((fill < 0) & hit, k, fill)
                f = fill >= 0; X = IMP[s][d]; X["n"] += len(j); X["fill"] += int(f.sum()); X["base_ret_sum"] += float(r.sum()); X["base_n"] += len(j)
                if f.any():
                    c2, r2, _ = sim(O, Hh, Ll, Cc, np.minimum(j[f] + fill[f], L - 1), lim[f], s, stp[f], maxh=MAXH, fill_bar=True)
                    X["ret_sum_fill"] += float(r2.sum()); X["wins"] += int((c2 == 1).sum())
            win = np.stack([Hh[np.minimum(j + k, L - 1)] if s < 0 else Ll[np.minimum(j + k, L - 1)] for k in range(MAXH)], 1)
            ext = win.max(1) if s < 0 else win.min(1); am = win.argmax(1) if s < 0 else win.argmin(1); imp = (ext / e0 - 1) * (1 if s < 0 else -1)
            for h in range(24):
                m = hour == h
                if m.sum() == 0: continue
                EXT[s]["imp"][h].append(float(imp[m].mean())); EXT[s]["early"][h].append(float((am[m] <= 1).mean()))
                EXT[s]["hr"][h] += np.bincount(((t[m] // H + am[m]) % 24).astype(int), minlength=24)
    res = {"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "design": f"target +{TGT*100:.3f}% price (+22.5% on margin at 20x), Arbiter volatility stop, {MAXH}h cap, cost {FEE*100:.1f}%, every hour, all coins, no signal",
           "volatilityByUtcHour(avg hourly high-low %)": {h: round(100 * float(np.mean(v)), 3) for h, v in vol.items() if v}}
    for s, nm in ((1, "long"), (-1, "short")):
        hours = np.concatenate(S[s]["hour"]); code = np.concatenate(S[s]["code"]); ret = np.concatenate(S[s]["ret"]); bars = np.concatenate(S[s]["bars"])
        byh = agg(hours, code, ret, bars, nm); sess = {}
        for lab, a, b in SESS:
            m = (hours >= a) & (hours < b)
            if m.sum(): sess[lab] = {"n": int(m.sum()), "win%": round(100 * (code[m] == 1).mean(), 1), "stop%": round(100 * (code[m] == -1).mean(), 1), "avgAccountPct": round(100 * LEVX * float(ret[m].mean()), 2)}
        hit = code == 1; cum = {f"within {k}h": round(100 * float(((bars <= k) & hit).sum() / max(1, len(code))), 1) for k in (1, 2, 3, 4, 6, 8, 12)}
        res[nm] = {"all": {"n": int(len(code)), "win%": round(100 * hit.mean(), 1), "stop%": round(100 * (code == -1).mean(), 1), "time%": round(100 * (code == 0).mean(), 1), "avgAccountPct": round(100 * LEVX * float(ret.mean()), 2)},
                   "targetReachedShare": cum, "bySession": sess, "byEntryHourUtc": byh,
                   "limitEntry": {f"{int(d*1000)/10}% better": {"fillRate%": round(100 * X["fill"] / max(1, X["n"]), 1), "avgAccountPerSignalPct": round(100 * LEVX * X["ret_sum_fill"] / max(1, X["n"]), 3),
                                                                 "avgAccountPerSignalIfMarketEntryPct": round(100 * LEVX * X["base_ret_sum"] / max(1, X["base_n"]), 3), "winRateOfFilled%": round(100 * X["wins"] / max(1, X["fill"]), 1)} for d, X in IMP[s].items()},
                   "bestEntryNext12h": {h: {"avgImprovementPct": round(100 * float(np.mean(EXT[s]["imp"][h])), 3), "extremeInFirst2hShare%": round(100 * float(np.mean(EXT[s]["early"][h])), 1),
                                            "mostCommonUtcHourOfExtreme": int(np.argmax(EXT[s]["hr"][h]))} for h in range(24) if EXT[s]["imp"][h]}}
    os.makedirs("rm/studies", exist_ok=True); json.dump(res, open("rm/studies/session_study.json", "w"), indent=1)
    print("VOLATILITY by UTC hour:", res["volatilityByUtcHour(avg hourly high-low %)"])
    for nm in ("long", "short"):
        print(f"\n== {nm.upper()} ==", json.dumps(res[nm]["all"])); print("target reached within:", res[nm]["targetReachedShare"]); print("by session:", json.dumps(res[nm]["bySession"]))
        print("limit entries:", json.dumps(res[nm]["limitEntry"]))
        print("best entry hour table:", {h: (v["avgImprovementPct"], v["mostCommonUtcHourOfExtreme"]) for h, v in res[nm]["bestEntryNext12h"].items()})
    print("Wrote rm/studies/session_study.json")

if __name__ == "__main__": main()
