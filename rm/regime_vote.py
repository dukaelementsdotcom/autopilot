"""REGIME VOTE: Dynamic Macro & Micro Voting Engine with Rolling IC & Time-Decay.

Computes real-time probabilistic Macro, Micro, and Composite Bullishness odds (0-100%)
by evaluating every available macro and microstructure factor in Panel P, weighting each
by its strictly causal rolling Information Coefficient (IC) with exponential time-decay
and micro-to-macro shock feedback.
"""
import os, json, time, math
import numpy as np, pandas as pd
from . import config as C

# Taxonomy of all Macro & Micro factors (prefix, display name, category, cluster, polarity)
# polarity: +1 means higher value is bullish for crypto; -1 means higher value is bearish (e.g. DXY, VIX, funding crowding)
FACTOR_CATALOG = [
    # ── MACRO: Monetary, Rates, FX, Equities, Commodities ──────────────────────
    ("w_ndx1",      "Nasdaq-100 (1d)",          "macro", "Equities",       +1),
    ("w_ndx5",      "Nasdaq-100 (5d)",          "macro", "Equities",       +1),
    ("w_spx5",      "S&P 500 (5d)",             "macro", "Equities",       +1),
    ("w_g_spx5",    "Global SPX (5d)",          "macro", "Equities",       +1),
    ("w_g_spx20",   "Global SPX (20d)",         "macro", "Equities",       +1),
    ("w_g_ndx5",    "Global NDX (5d)",          "macro", "Equities",       +1),
    ("w_g_ndx20",   "Global NDX (20d)",         "macro", "Equities",       +1),
    ("w_g_dax5",    "DAX Europe (5d)",          "macro", "Equities",       +1),
    ("w_g_nikkei5", "Nikkei Japan (5d)",        "macro", "Equities",       +1),
    ("w_g_hsi5",    "Hang Seng China (5d)",     "macro", "Equities",       +1),
    ("w_g_nvda5",   "NVIDIA AI Proxy (5d)",     "macro", "Tech/AI",        +1),
    ("w_g_chips5",  "Semiconductors SMH (5d)",  "macro", "Tech/AI",        +1),
    ("w_g_coin5",   "Coinbase COIN (5d)",       "macro", "Crypto Proxy",   +1),
    ("w_dxy5",      "US Dollar DXY (5d)",       "macro", "FX & Dollar",    -1),
    ("w_g_dxy5",    "DXY Momentum (5d)",        "macro", "FX & Dollar",    -1),
    ("w_g_dxy20",   "DXY Trend (20d)",          "macro", "FX & Dollar",    -1),
    ("w_y10_5",     "US 10Y Yield (5d)",        "macro", "Rates & Debt",   -1),
    ("w_vix",       "VIX Fear Level",           "macro", "Volatility",     -1),
    ("w_vix5",      "VIX 5d Change",            "macro", "Volatility",     -1),
    ("w_g_gold5",   "Gold (5d)",                "macro", "Commodities",    +1),
    ("w_g_oil5",    "Crude Oil WTI (5d)",       "macro", "Commodities",    -1),
    ("w_g_link",    "BTC-Equities Correlation", "macro", "Cross-Asset",    +1),
    ("w_f_stab7",   "Stablecoin Supply (7d)",   "macro", "Liquidity",      +1),
    ("w_f_stab30",  "Stablecoin Supply (30d)",  "macro", "Liquidity",      +1),
    ("w_f_stab90",  "Stablecoin Supply (90d)",  "macro", "Liquidity",      +1),
    ("w_g_fng",     "Crypto Fear & Greed",      "macro", "Sentiment",      +1),
    ("w_g_fng7",    "Fear & Greed (7d Chg)",    "macro", "Sentiment",      +1),
    ("w_g_dvol",    "BTC Deribit DVOL",         "macro", "Options Vol",    -1),
    ("w_g_edvol",   "ETH Deribit DVOL",         "macro", "Options Vol",    -1),

    # ── MICRO: On-Chain, Orderbook, CVD, OI, Liquidations, Whales, News ────────
    ("w_btcnet",    "BTC Exchange Netflow",     "micro", "On-Chain",       -1),
    ("w_btcsup7",   "BTC Exchange Supply (7d)", "micro", "On-Chain",       -1),
    ("w_ethnet",    "ETH Exchange Netflow",     "micro", "On-Chain",       -1),
    ("w_ethsup7",   "ETH Exchange Supply (7d)", "micro", "On-Chain",       -1),
    ("w_sec24",     "Sector Rotation (24h)",    "micro", "Sectors",        +1),
    ("w_sec168",    "Sector Rotation (7d)",     "micro", "Sectors",        +1),
    ("w_shock1",    "BTC 1h Volatility Shock",  "micro", "Shocks",         +1),
    ("w_shock4",    "BTC 4h Volatility Shock",  "micro", "Shocks",         +1),
    ("w_shockN",    "72h Shock Frequency",      "micro", "Shocks",         -1),
    ("w_b_imb1",    "Orderbook Imbalance 1%",   "micro", "Orderbook",      +1),
    ("w_b_imb5",    "Orderbook Imbalance 5%",   "micro", "Orderbook",      +1),
    ("w_b_ofi",     "Order Flow Imbalance",     "micro", "Orderbook",      +1),
    ("w_b_dep",     "Orderbook Depth Ratio",    "micro", "Orderbook",      +1),
    ("w_l_mnet1",   "Market Net Liquidations",  "micro", "Liquidations",   +1),
    ("w_l_msize1",  "Market Liq Burst (24h)",   "micro", "Liquidations",   -1),
    ("w_c_div24",   "Spot-Perp CVD Div (24h)",  "micro", "CVD Flow",       +1),
    ("w_c_div72",   "Spot-Perp CVD Div (72h)",  "micro", "CVD Flow",       +1),
    ("w_c_mkt",     "Market-Wide CVD Impulse",  "micro", "CVD Flow",       +1),
    ("w_x_oi1",     "Cross-Exch OI (24h)",      "micro", "Leverage",       +1),
    ("w_x_moi7",    "Market-Wide OI (7d)",      "micro", "Leverage",       +1),
    ("w_x_mfr",     "Market-Wide Funding",      "micro", "Leverage",       -1),
    ("w_r_imb",     "Live Recorder Book Lean",  "micro", "Live Tape",      +1),
    ("w_r_fr",      "Live Binance Funding",     "micro", "Live Tape",      -1),
    ("w_r_mlnet24", "Live Market Liquidations", "micro", "Live Tape",      +1),
    ("w_h_mkt",     "Hyperliquid Whale Lean",   "micro", "Whales",         +1),
    ("w_h_mkt24",   "Hyperliquid Whale 24h",    "micro", "Whales",         +1),
    ("w_n_burst",   "News Headline Burst",      "micro", "News Flow",      +1),
    ("w_n_risk",    "AI News Risk Mood",        "micro", "News Flow",      +1),
]


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-15.0, min(15.0, float(x)))))


def _calendar_catalysts():
    """Read upcoming macro calendar & IPO events from state/collect if present."""
    events = []
    cal_p = f"{C.STATE_DIR}/collect/calendar.json"
    ipo_p = f"{C.STATE_DIR}/collect/ipos.json"
    if os.path.exists(cal_p):
        try:
            cal = json.load(open(cal_p))
            for e in cal[-10:]:
                if e.get("impact") == "High":
                    events.append({"title": e.get("title", "Macro Event"), "country": e.get("country", ""), "date": e.get("date", ""), "type": "macro"})
        except Exception:
            pass
    if os.path.exists(ipo_p):
        try:
            ipos = json.load(open(ipo_p))
            for k, v in list(ipos.items())[:8]:
                events.append({"title": f"IPO: {v.get('companyName', k)} ({k})", "country": "US", "date": v.get("expectedPriceDate") or v.get("pricedDate") or "", "type": "ipo"})
        except Exception:
            pass
    return events[:12]


def compute(P, log=print):
    """Compute real-time dynamic Macro, Micro, and Composite voting matrix from Panel P.

    Strictly causal: evaluates rolling IC on hours [t - 744, t - 24] against 24h forward BTC returns,
    applies exponential decay & micro-to-macro shock feedback, and outputs the current snapshot at t_max.
    """
    if P is None or len(P) == 0:
        return None

    # Build hourly market benchmark (BTC if available, else cross-sectional mean)
    btc = P[P.coin == "BTC"].sort_values("t") if "BTC" in P.coin.values else P.groupby("t", as_index=False).first().sort_values("t")
    if len(btc) < 72:
        return None

    btc = btc.reset_index(drop=True)
    # 24h forward return for historical IC calibration (strictly lagged by 24h before evaluation)
    fwd_r24 = btc["r24"].shift(-24) if "r24" in btc.columns else btc["Cl"].pct_change(24).shift(-24)

    # Micro-to-macro shock multiplier: if shock frequency or liq burst is elevated, boost shock/liq sensitivity
    shock_boost = 1.0
    if "w_shockN" in btc.columns:
        sn = float(btc["w_shockN"].iloc[-1]) if np.isfinite(btc["w_shockN"].iloc[-1]) else 0.0
        if sn >= 2.0:
            shock_boost = min(2.5, 1.0 + 0.35 * sn)

    factors_out = []
    macro_raw, macro_wsum = 0.0, 0.0
    micro_raw, micro_wsum = 0.0, 0.0

    # Window for rolling IC: last 30 days (720h) excluding the most recent 24h (strictly causal)
    end_idx = max(0, len(btc) - 24)
    start_idx = max(0, end_idx - 720)
    prev_end = max(0, end_idx - 24)
    prev_start = max(0, prev_end - 720)

    for col, name, cat, cluster, pol in FACTOR_CATALOG:
        if col not in btc.columns:
            continue
        series = pd.to_numeric(btc[col], errors="coerce")
        valid_mask = series.notna()
        if valid_mask.sum() < 48:
            continue

        cur_val = float(series.iloc[-1]) if np.isfinite(series.iloc[-1]) else float(series.dropna().iloc[-1])
        # Robust z-score of current factor relative to trailing 90d (2160h)
        hist = series.iloc[max(0, len(series) - 2160):].dropna()
        med = float(hist.median()) if len(hist) else 0.0
        mad = float((hist - med).abs().median()) * 1.4826 if len(hist) else 1.0
        z_cur = (cur_val - med) / max(mad, 1e-6)
        vote = float(np.tanh(0.65 * pol * z_cur))  # Smooth directional vote in [-1, +1]

        # Strictly causal Spearman-like IC (rank correlation with 24h forward return)
        ic_now = 0.05
        ic_prev = 0.05
        if end_idx - start_idx >= 72:
            s_win = series.iloc[start_idx:end_idx]
            y_win = fwd_r24.iloc[start_idx:end_idx]
            ok = s_win.notna() & y_win.notna()
            if ok.sum() >= 48 and s_win[ok].std() > 1e-9:
                c = float(s_win[ok].corr(y_win[ok], method="spearman"))
                if np.isfinite(c):
                    ic_now = max(0.01, pol * c)  # Reward aligned predictive power; floor at 0.01 so nothing is missed

        if prev_end - prev_start >= 72:
            s_p = series.iloc[prev_start:prev_end]
            y_p = fwd_r24.iloc[prev_start:prev_end]
            ok_p = s_p.notna() & y_p.notna()
            if ok_p.sum() >= 48 and s_p[ok_p].std() > 1e-9:
                c_p = float(s_p[ok_p].corr(y_p[ok_p], method="spearman"))
                if np.isfinite(c_p):
                    ic_prev = max(0.01, pol * c_p)

        # Focus exponent (gamma=1.6) + shock feedback boost for crisis/liquidity clusters
        mult = shock_boost if cluster in ("Shocks", "Liquidations", "Volatility", "Liquidity") else 1.0
        raw_w = (ic_now ** 1.6) * mult
        prev_w = (ic_prev ** 1.6) * mult

        # Freshness decay: if last value is stale (NaN at final row), decay its weight
        if not np.isfinite(series.iloc[-1]):
            raw_w *= 0.35

        if cat == "macro":
            macro_raw += raw_w * vote
            macro_wsum += raw_w
        else:
            micro_raw += raw_w * vote
            micro_wsum += raw_w

        factors_out.append({
            "id": col,
            "name": name,
            "category": cat,
            "cluster": cluster,
            "value": round(cur_val, 4),
            "vote": round(vote, 3),
            "raw_w": raw_w,
            "prev_w": prev_w,
            "ic": round(ic_now, 3),
        })

    if not factors_out:
        return None

    total_w = sum(f["raw_w"] for f in factors_out) or 1.0
    total_prev_w = sum(f["prev_w"] for f in factors_out) or 1.0

    for f in factors_out:
        w_pct = 100.0 * f["raw_w"] / total_w
        prev_pct = 100.0 * f["prev_w"] / total_prev_w
        delta = w_pct - prev_pct
        if delta > 0.25:
            status = "SURGING"
        elif delta < -0.25:
            status = "DECAYING"
        else:
            status = "ACTIVE"
        f["weight_pct"] = round(w_pct, 2)
        f["delta_24h"] = round(delta, 2)
        f["status"] = status
        f.pop("raw_w", None)
        f.pop("prev_w", None)

    factors_out.sort(key=lambda x: x["weight_pct"], reverse=True)

    macro_score = (macro_raw / macro_wsum) if macro_wsum > 0 else 0.0
    micro_score = (micro_raw / micro_wsum) if micro_wsum > 0 else 0.0
    comp_score = 0.45 * macro_score + 0.55 * micro_score

    res = {
        "at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()),
        "macro_bull_pct": round(100.0 * _sigmoid(2.2 * macro_score), 1),
        "micro_bull_pct": round(100.0 * _sigmoid(2.2 * micro_score), 1),
        "composite_bull_pct": round(100.0 * _sigmoid(2.4 * comp_score), 1),
        "shock_boost": round(shock_boost, 2),
        "factor_count": len(factors_out),
        "factors": factors_out,
        "catalysts": _calendar_catalysts(),
    }
    log(f"regime_vote: Macro={res['macro_bull_pct']}% | Micro={res['micro_bull_pct']}% | Composite={res['composite_bull_pct']}% ({res['factor_count']} factors)")
    return res
