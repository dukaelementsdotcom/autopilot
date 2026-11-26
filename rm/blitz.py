"""BLITZ LAB v3 (owner's idea, rebuilt Nov 27): aggressive paper experiments, nothing off the table, but a FAIR comparison.
Every arm sees the SAME entry stream: the active Arbiter's graded calls (A/B/S, best expected value first), published each hour by the blitz contestants as R['blitzStream'].
The arms differ only in HOW MUCH they put in and HOW they exit, so the results say which sizing/exit works on identical entries:
  grid arms   margin 20% / 50% / 90% of the account  x  exits scalp (+1.125%) / std (+2%) / wide (+5%, stop 1.5x)   one trade at a time (all-in style)   stop = the Arbiter's volatility stop for that coin
  flash_scalp_5    the owner's original: +5% target, fixed -1.5% stop, 15x, 30% margin
  contrarian_fade  the owner's original idea, fixed: SHORT a coin whose funding AND 4h run-up are both in the top 5% of its own history; LONG when both are in the bottom 5%
Entries at the close of the newest completed hour; exits replay the next real hourly highs/lows (same bar: stop first); isolated margin, leverage never above what leaves a buffer before liquidation; 0.1% round-trip cost.
v1 arms (percentile-unit bug, Kelly on the wrong probability, fixed stops) are parked in mem['blitz']['parked'], not deleted."""
import numpy as np, pandas as pd
from . import config as C

START = 500.0
HOUR = 3600
MARGINS = (0.2, 0.5, 0.9)
EXITS = {"scalp": {"tgt": 0.01125, "stopx": 1.0, "hold": 12}, "std": {"tgt": 0.02, "stopx": 1.0, "hold": 48}, "wide": {"tgt": 0.05, "stopx": 1.5, "hold": 48}}
VERSION = 3

def _arms():
    A = {}
    for m in MARGINS:
        for ek, e in EXITS.items():
            A[f"m{int(m*100)}_{ek}"] = {"label": f"{int(m*100)}% margin · +{e['tgt']*100:.3g}% target · stop {e['stopx']:.1f}x vol · {e['hold']}h cap", "kind": "grid", "margin": m, **e}
    A["flash_scalp_5"] = {"label": "Flash Scalp (+5% / fixed -1.5% stop, 15x, 30% margin)", "kind": "fixed", "margin": 0.30, "lev": 15, "tgt": 0.05, "sl": 0.015, "hold": 24}
    A["contrarian_fade"] = {"label": "Funding+run-up extreme fade (12x, 25% margin, +3.5% / -1.5%)", "kind": "fade", "margin": 0.25, "lev": 12, "tgt": 0.035, "sl": 0.015, "hold": 36}
    return A

ARMS = _arms()

def _mtm(pos, cl):
    return pos["margin"] * (1.0 + pos["lev"] * (pos["sd"] * (cl / pos["entry"] - 1.0) - C.FEE))

def _step_arm(key, cfg, state, candles, signals, t_now):
    if state is None:
        state = {"label": cfg["label"], "cash": START, "start": START, "equity": START, "peak": START, "maxDD": 0.0, "open": [], "closed": [], "lastT": t_now}
    # 1. replay the bars since each open trade was last looked at
    still = []
    for pos in state["open"]:
        df = candles.get(pos["coin"]); closed = False
        if df is None: still.append(pos); continue
        for _, bar in df[(df["t"] > pos["lastCheck"]) & (df["t"] <= t_now)].iterrows():
            sd, ent = pos["sd"], pos["entry"]; hi, lo, cl, bt = float(bar["H"]), float(bar["L"]), float(bar["Cl"]), int(bar["t"]); pos["lastCheck"] = bt
            sl_px = ent * (1.0 - sd * pos["sl"]); tp_px = ent * (1.0 + sd * pos["tgt"])
            hit_sl = (lo <= sl_px) if sd > 0 else (hi >= sl_px); hit_tp = (hi >= tp_px) if sd > 0 else (lo <= tp_px)
            timed = (bt - pos["t"]) >= pos["hold"] * HOUR
            if hit_sl or hit_tp or timed:
                raw, why = (-pos["sl"], "SL") if hit_sl else ((pos["tgt"], "TP") if hit_tp else (sd * (cl / ent - 1.0), "TIME"))
                net = max(-1.0, pos["lev"] * (raw - C.FEE)); pnl = pos["margin"] * net; state["cash"] += pos["margin"] + pnl
                state["closed"].append({"coin": pos["coin"], "side": "LONG" if sd > 0 else "SHORT", "t_in": pos["t"], "t_out": bt, "margin": round(pos["margin"], 2), "lev": pos["lev"], "ret_pct": round(net * 100, 2), "pnl": round(pnl, 2), "why": why})
                closed = True; break
        if not closed: still.append(pos)
    state["open"] = still
    # 2. new entry from the shared stream (one trade at a time) - never the same coin twice
    if not state["open"] and state["cash"] > 10.0:
        for sg in signals:
            df = candles.get(sg["coin"])
            if df is None or len(df) == 0: continue
            if cfg["kind"] == "grid": sl = float(np.clip(sg["stop"] * cfg["stopx"], 0.008, 0.08)); lev = int(max(1, min(20, 0.8 // sl)))
            else: sl, lev = cfg["sl"], cfg["lev"]
            margin = state["cash"] * cfg["margin"]
            if margin < 5.0: continue
            state["cash"] -= margin
            state["open"].append({"coin": sg["coin"], "sd": sg["sd"], "t": t_now, "lastCheck": t_now, "entry": float(df["Cl"].iloc[-1]), "margin": margin, "prob": sg.get("prob"), "lev": lev, "tgt": cfg["tgt"], "sl": round(sl, 5), "hold": cfg["hold"]})
            break
    # 3. equity marked to the latest close (unrealised included), drawdown
    eq = state["cash"]
    for p in state["open"]:
        df = candles.get(p["coin"]); eq += _mtm(p, float(df["Cl"].iloc[-1])) if df is not None and len(df) else p["margin"]
    state["equity"] = round(eq, 2); state["peak"] = round(max(state.get("peak", START), eq), 2)
    state["maxDD"] = round(max(state.get("maxDD", 0.0), (1.0 - eq / state["peak"]) * 100.0 if state["peak"] > 0 else 0.0), 2)
    state["closed"] = state["closed"][-200:]; state["lastT"] = t_now; state["label"] = cfg["label"]
    return state

def step(mem, J, now_iso, log=print):
    P = J.P
    if P is None or len(P) == 0: return
    t_now = int(J.t.max()); blitz = mem.setdefault("blitz", {"arms": {}, "updatedAt": now_iso})
    if blitz.get("ver") != VERSION:                                        # park the v1 arms (record kept), start clean
        old = blitz.get("arms") or {}
        if old: blitz.setdefault("parked", {})["v1 arms (to Nov 26: percentile-unit bug, Kelly on the wrong probability, fixed stops)"] = {k: {"equity": a.get("equity"), "closed": (a.get("closed") or [])[-30:]} for k, a in old.items()}
        blitz["arms"] = {}; blitz["ver"] = VERSION; log("blitz lab: v1 arms parked, v3 starts clean")
    latest = P[P.t == t_now]
    if len(latest) == 0: return
    st = (mem.get("referee") or {}).get("blitzStream") or {}
    signals = []
    if st.get("t") == t_now:                                               # only a stream from THIS hour (never trade on a stale one)
        for c in (st.get("calls") or [])[:8]: signals.append({"arm": "grid", "coin": c["coin"], "sd": 1 if c["side"] == "long" else -1, "prob": c.get("pt"), "stop": c["stop"]})
    fade = []
    if "p_hlfund24" in latest.columns and "p_r4" in latest.columns:        # p_* columns are percentile ranks (0-1) against the coin's own past 90 days
        for _, row in latest.iterrows():
            fr, r4 = row.get("p_hlfund24"), row.get("p_r4")
            if np.isfinite(fr) and np.isfinite(r4):
                if fr >= 0.95 and r4 >= 0.95: fade.append({"arm": "fade", "coin": row["coin"], "sd": -1, "prob": None, "stop": 0.015})
                elif fr <= 0.05 and r4 <= 0.05: fade.append({"arm": "fade", "coin": row["coin"], "sd": 1, "prob": None, "stop": 0.015})
    need = {s["coin"] for s in signals + fade} | {p["coin"] for a in blitz["arms"].values() for p in a.get("open", [])}
    candles = {}
    for coin in need:
        try:
            T, O, Hh, Ll, Cc = J._bars(coin); m = T >= t_now - 72 * HOUR
            candles[coin] = pd.DataFrame({"t": T[m], "O": O[m], "H": Hh[m], "L": Ll[m], "Cl": Cc[m]})
        except Exception as e: log(f"blitz lab: no bars for {coin}: {e}")
    summary = []
    for key, cfg in ARMS.items():
        sg = signals[:3] if key == "flash_scalp_5" else (fade if cfg["kind"] == "fade" else signals)
        blitz["arms"][key] = _step_arm(key, cfg, blitz["arms"].get(key), candles, sg, t_now)
    for key, a in blitz["arms"].items():
        n = len(a["closed"]); w = sum(1 for x in a["closed"] if x["pnl"] > 0); summary.append(f"{key} ${a['equity']:.0f} ({n}t/{w}w)")
    blitz["updatedAt"] = now_iso; blitz["stream"] = {"t": st.get("t"), "n": len(st.get("calls") or [])}
    log("blitz lab: " + " | ".join(summary))
