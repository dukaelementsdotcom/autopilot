"""BLITZ LAB: Aggressive Trial-and-Error & Dynamic Kelly Execution Engine.

Runs multiple high-aggression experimental paper portfolios in parallel alongside
the conservative referee account:
  1. dynamic_kelly   : Scales margin dynamically (15% -> 85%) via Kelly f* = (p*b - q)/b
  2. flash_scalp_5   : Rapid +5% target exits with tight -1.5% invalidation at 15x
  3. high_conv_20x   : 20x isolated leverage on multi-tier Macro+Micro+Referee consensus
  4. contrarian_fade : Fades extreme micro crowding/liquidation spikes

All entries occur strictly at the open of new unseen hours; exits replay real hourly highs/lows
with synthetic liquidation barriers (closing before exchange liquidation).
"""
import numpy as np
from . import config as C

START = 500.0
HOUR = 3600

ARMS = {
    "dynamic_kelly": {"label": "Dynamic Kelly (15-85% Margin)", "lev": 10, "tp": 0.04, "sl": 0.015, "max_slots": 2, "hold": 48},
    "flash_scalp_5": {"label": "Flash Scalp (+5% / -1.5% @ 15x)", "lev": 15, "tp": 0.05, "sl": 0.015, "max_slots": 3, "hold": 24},
    "high_conv_20x": {"label": "High-Conviction Breakout (20x)", "lev": 20, "tp": 0.06, "sl": 0.018, "max_slots": 1, "hold": 48},
    "contrarian_fade": {"label": "Liquidation/Funding Fade (12x)", "lev": 12, "tp": 0.035, "sl": 0.015, "max_slots": 2, "hold": 36},
}


def _kelly_fraction(p, tp, sl):
    """Dynamic Kelly sizing capped to [0.15, 0.85] for high-conviction aggressive compounding."""
    b = max(0.5, tp / max(1e-4, sl))
    q = 1.0 - p
    f = (p * b - q) / b
    return float(np.clip(f, 0.15, 0.85))


def _step_arm(arm_key, cfg, state, J, candles_by_coin, signals, t_now):
    """Advance one experimental arm through unseen hours up to t_now."""
    if state is None:
        state = {
            "label": cfg["label"],
            "cash": START,
            "start": START,
            "equity": START,
            "peak": START,
            "maxDD": 0.0,
            "open": [],
            "closed": [],
            "lastT": t_now,
        }

    lev = cfg["lev"]
    tp_pct = cfg["tp"]
    sl_pct = cfg["sl"]
    # Synthetic liquidation shield: never let loss exceed 80% of margin (i.e. 0.80 / lev)
    eff_sl = min(sl_pct, 0.80 / lev)

    # 1. Mark and close existing open positions on bars since lastT
    still_open = []
    for pos in state["open"]:
        c_df = candles_by_coin.get(pos["coin"])
        if c_df is None:
            still_open.append(pos)
            continue
        bars = c_df[(c_df["t"] > pos["lastCheck"]) & (c_df["t"] <= t_now)]
        closed = False
        for _, bar in bars.iterrows():
            sd = pos["sd"]
            ent = pos["entry"]
            hi, lo, cl, bt = float(bar["H"]), float(bar["L"]), float(bar["Cl"]), int(bar["t"])
            pos["lastCheck"] = bt

            # Check synthetic stop first (conservative execution)
            sl_px = ent * (1.0 - sd * eff_sl)
            tp_px = ent * (1.0 + sd * tp_pct)
            hit_sl = (lo <= sl_px) if sd > 0 else (hi >= sl_px)
            hit_tp = (hi >= tp_px) if sd > 0 else (lo <= tp_px)
            timed_out = (bt - pos["t"]) >= cfg["hold"] * HOUR

            if hit_sl or hit_tp or timed_out:
                if hit_sl:
                    raw_ret = -eff_sl
                    why = "SL"
                elif hit_tp:
                    raw_ret = tp_pct
                    why = "TP"
                else:
                    raw_ret = sd * (cl / ent - 1.0)
                    why = "TIME"

                net_ret = lev * (raw_ret - C.FEE)
                net_ret = max(-1.0, net_ret)  # Isolated margin: cannot lose >100% of margin
                pnl = pos["margin"] * net_ret
                state["cash"] += pos["margin"] + pnl
                state["closed"].append({
                    "coin": pos["coin"],
                    "side": "LONG" if sd > 0 else "SHORT",
                    "t_in": pos["t"],
                    "t_out": bt,
                    "margin": round(pos["margin"], 2),
                    "lev": lev,
                    "ret_pct": round(net_ret * 100, 2),
                    "pnl": round(pnl, 2),
                    "why": why,
                })
                closed = True
                break
        if not closed:
            still_open.append(pos)

    state["open"] = still_open

    # 2. Evaluate new signals at t_now if slots available and cash > $10
    if len(state["open"]) < cfg["max_slots"] and state["cash"] > 10.0:
        for sig in signals:
            if len(state["open"]) >= cfg["max_slots"]:
                break
            if any(p["coin"] == sig["coin"] for p in state["open"]):
                continue
            if sig["arm"] != arm_key:
                continue

            if arm_key == "dynamic_kelly":
                frac = _kelly_fraction(sig["prob"], tp_pct, eff_sl)
            elif arm_key == "high_conv_20x":
                frac = 0.45
            elif arm_key == "flash_scalp_5":
                frac = 0.30
            else:
                frac = 0.25

            margin = min(state["cash"] * 0.95, state["cash"] * frac)
            if margin < 5.0:
                continue

            c_df = candles_by_coin.get(sig["coin"])
            if c_df is None or len(c_df) == 0:
                continue
            entry_px = float(c_df["Cl"].iloc[-1])
            state["cash"] -= margin
            state["open"].append({
                "coin": sig["coin"],
                "sd": sig["sd"],
                "t": t_now,
                "lastCheck": t_now,
                "entry": entry_px,
                "margin": margin,
                "prob": sig["prob"],
            })

    # 3. Update equity & drawdown stats
    locked_margin = sum(p["margin"] for p in state["open"])
    eq = state["cash"] + locked_margin
    state["equity"] = round(eq, 2)
    state["peak"] = round(max(state.get("peak", START), eq), 2)
    dd = (1.0 - eq / state["peak"]) * 100.0 if state["peak"] > 0 else 0.0
    state["maxDD"] = round(max(state.get("maxDD", 0.0), dd), 2)
    state["closed"] = state["closed"][-200:]
    state["lastT"] = t_now
    return state


def step(mem, J, now_iso, log=print):
    """Run one Blitz Lab step across all aggressive experimental arms."""
    P = J.P
    if P is None or len(P) == 0:
        return

    t_now = int(J.t.max())
    blitz = mem.setdefault("blitz", {"arms": {}, "updatedAt": now_iso})

    # Build lightweight candle lookup for top liquid coins
    latest = P[P.t == t_now]
    if len(latest) == 0:
        return

    rv = mem.get("regimeVotes") or {}
    comp_bull = rv.get("composite_bull_pct", 50.0) / 100.0
    ref = mem.get("referee") or {}
    cands = (ref.get("look") or {}).get("rows") or ref.get("candidates") or []

    # Generate arm-specific signals from Referee candidates + Regime Votes + Microstructure extremes
    signals = []
    for c in cands:
        coin = c.get("coin")
        p_win = float(c.get("prob") or c.get("p") or 0.52)
        sd = 1 if str(c.get("side", "")).lower() == "long" else -1
        if not coin:
            continue

        # Arm 1: Dynamic Kelly takes any candidate with positive edge > 54%
        if p_win >= 0.54:
            signals.append({"arm": "dynamic_kelly", "coin": coin, "sd": sd, "prob": p_win})

        # Arm 2: Flash scalp takes high-momentum candidates
        if p_win >= 0.56:
            signals.append({"arm": "flash_scalp_5", "coin": coin, "sd": sd, "prob": p_win})

        # Arm 3: High-conviction 20x requires Macro+Micro regime alignment
        aligned = (sd > 0 and comp_bull >= 0.60) or (sd < 0 and comp_bull <= 0.40)
        if p_win >= 0.60 and aligned:
            signals.append({"arm": "high_conv_20x", "coin": coin, "sd": sd, "prob": p_win})

    # Arm 4: Contrarian fade looks at extreme funding / OI crowding in latest cross-section
    if "p_hlfund24" in latest.columns and "p_r4" in latest.columns:
        for _, row in latest.iterrows():
            fr = row.get("p_hlfund24")
            r4 = row.get("p_r4")
            if np.isfinite(fr) and np.isfinite(r4):
                if fr > 0.0008 and r4 > 0.03:
                    signals.append({"arm": "contrarian_fade", "coin": row["coin"], "sd": -1, "prob": 0.58})
                elif fr < -0.0008 and r4 < -0.03:
                    signals.append({"arm": "contrarian_fade", "coin": row["coin"], "sd": 1, "prob": 0.58})

    # Slice recent 72h candles per coin for fast replay
    recent_P = P[P.t >= t_now - 72 * HOUR]
    candles_by_coin = {coin: df.sort_values("t") for coin, df in recent_P.groupby("coin")}

    summary = []
    for key, cfg in ARMS.items():
        st = _step_arm(key, cfg, blitz["arms"].get(key), J, candles_by_coin, signals, t_now)
        blitz["arms"][key] = st
        n_cl = len(st["closed"])
        wins = sum(1 for x in st["closed"] if x["pnl"] > 0)
        wr = round(100.0 * wins / n_cl, 1) if n_cl else 0.0
        ret_pct = round((st["equity"] / START - 1.0) * 100.0, 2)
        summary.append(f"{key}: ${st['equity']} ({ret_pct:+.1f}%, {n_cl}t, WR {wr}%)")

    blitz["updatedAt"] = now_iso
    log("blitz lab: " + " | ".join(summary))
