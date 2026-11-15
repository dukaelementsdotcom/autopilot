"""CROSS-EXCHANGE OPEN INTEREST AND FUNDING (Coinalyze, the same free key as the liquidations): per coin, summed over the
Binance, Bybit and OKX perpetuals, daily since 2020.
  open interest  how much leverage is open in total (not just on Binance), how fast it is growing, and where it is building
  funding        weighted by each exchange's open interest: what longs pay shorts across the market, and how much the
                 exchanges disagree (a crowded side on one exchange)
A day's numbers count only from 01:00 UTC the next day (no peeking), exactly like the liquidation witness.
Refreshed once a UTC day after 03:00 (two calls per market; 40 calls a minute are allowed)."""
import os, json, time, numpy as np, pandas as pd
from . import config as C
from . import liq

DAY = 86400; H = 3600
COLS = ["w_x_oi1", "w_x_oi7", "w_x_oiz", "w_x_bn7", "w_x_fr", "w_x_frz", "w_x_fr7", "w_x_frsp", "w_x_moi7", "w_x_mfr", "w_x_frrel"]
P_D = lambda: f"{C.DATA_DIR}/raw/cz_oifr_d.parquet"
LAG = 1 + 1 / 24
START = 1598918400                       # 2020-09-01

def _pull(path, mp, t_from, t_to, budget, t0, extra):
    sym2coin = {s: c for c, v in mp.items() for s in v}; syms = list(sym2coin); rows = []; done = 0
    for i in range(0, len(syms), 20):
        if time.time() - t0 > budget: break
        part = syms[i:i + 20]
        for item in liq._get(path, calls=len(part), symbols=",".join(part), interval="daily", **{"from": int(t_from), "to": int(t_to)}, **extra):
            s = item.get("symbol")
            for h in item.get("history") or []:
                rows.append((int(h["t"]), sym2coin.get(s), s, float(h.get("c") if h.get("c") is not None else np.nan)))
        done += len(part)
    df = pd.DataFrame(rows, columns=["t", "coin", "sym", "v"])
    if len(df) and df.t.max() > 1e11: df["t"] = df.t // 1000
    return df, done, len(syms)

def update(log=print, budget=900):
    if not os.environ.get("COINALYZE_KEY", "").strip(): return {"skipped": "no COINALYZE_KEY secret"}
    t0 = time.time(); now = int(time.time()); today = time.strftime("%Y-%m-%d", time.gmtime())
    SP = f"{C.STATE_DIR}/xoi_state.json"
    try: st = json.load(open(SP))
    except Exception: st = {}
    have = os.path.exists(P_D())
    if have and (st.get("day") == today or time.gmtime().tm_hour < 3): return {"fresh": True, "days": st.get("days")}
    mp = liq.markets(log)
    if not mp: return {"error": "no markets matched"}
    t_from = (now - 10 * DAY) if have else START
    oi, d1, n1 = _pull("/open-interest-history", mp, t_from, now, budget, t0, {"convert_to_usd": "true"})
    fr, d2, n2 = _pull("/funding-rate-history", mp, t_from, now, budget, t0, {})
    if oi.empty and fr.empty: return {"error": "nothing returned"}
    new = pd.merge(oi.rename(columns={"v": "oi"}), fr.rename(columns={"v": "fr"}), on=["t", "coin", "sym"], how="outer")
    new = new[new.t < now // DAY * DAY]                                               # completed days only
    if have:
        try: new = pd.concat([pd.read_parquet(P_D()), new])
        except Exception: pass
    new = new.drop_duplicates(["t", "sym"], keep="last").sort_values(["t", "coin", "sym"])
    os.makedirs(os.path.dirname(P_D()), exist_ok=True); new.to_parquet(P_D() + ".tmp", index=False); os.replace(P_D() + ".tmp", P_D())
    complete = d1 >= n1 and d2 >= n2; info = {"days": int(new.t.nunique()), "from": str(pd.to_datetime(new.t.min(), unit="s").date()), "markets": int(new.sym.nunique()), "complete": complete}
    if complete: json.dump({"at": now, "day": today, **info}, open(SP, "w"))
    open(f"{C.DATA_DIR}/raw/version_xoi.txt", "w").write(f"{today}|{info['days']}")
    log(f"cross-exchange OI & funding: {info}, {time.time() - t0:.0f}s"); return info

def daily_table(d):
    """per coin and day: total open interest, Binance's share, OI-weighted funding, funding spread across exchanges"""
    d = d.copy(); d["t"] = d.t // DAY * DAY
    try: codes = [k for k, v in json.load(open(liq.P_M())).get("exchanges", {}).items() if "binance" in v]
    except Exception: codes = ["A"]
    d["bn"] = d.sym.str.rsplit(".", n=1).str[-1].isin(codes)                          # which markets are on Binance
    d["w"] = d.oi.where(d.oi > 0)
    g = d.groupby(["coin", "t"])
    tab = pd.DataFrame({"oi": g.oi.sum(min_count=1),
                        "bn": d[d.bn].groupby(["coin", "t"]).oi.sum(min_count=1),
                        "fr": (d.fr * d.w).groupby([d.coin, d.t]).sum(min_count=1) / d.w.where(d.fr.notna()).groupby([d.coin, d.t]).sum(min_count=1),
                        "frsp": g.fr.max() - g.fr.min()}).reset_index()
    return tab

def features(P, log=print):
    if not os.path.exists(P_D()): return {}
    from .witnesses import _daily_to_hourly
    tab = daily_table(pd.read_parquet(P_D()))
    if tab.t.nunique() < 400: return {}
    OI = tab.pivot(index="t", columns="coin", values="oi").sort_index(); BN = tab.pivot(index="t", columns="coin", values="bn").reindex(OI.index)
    FR = tab.pivot(index="t", columns="coin", values="fr").reindex(OI.index); SPd = tab.pivot(index="t", columns="coin", values="frsp").reindex(OI.index)
    lo = np.log(OI.where(OI > 0))
    z = lambda x, n=60: (x - x.rolling(n, min_periods=20).median()) / (x.rolling(n, min_periods=20).std() + 1e-9)
    F = {"w_x_oi1": lo.diff(1), "w_x_oi7": lo.diff(7), "w_x_oiz": z(lo), "w_x_bn7": (BN / OI) - (BN / OI).shift(7),
         "w_x_fr": FR, "w_x_frz": z(FR), "w_x_fr7": FR.rolling(7, min_periods=3).mean(), "w_x_frsp": SPd}
    tot = OI.sum(axis=1, min_count=5); mfr = (FR * OI).sum(axis=1, min_count=5) / OI.where(FR.notna()).sum(axis=1, min_count=5)
    M = {"w_x_moi7": np.log(tot).diff(7), "w_x_mfr": mfr}
    ts = np.sort(P.t.unique()); tP = P.t.values; coin = P.coin.values
    out = {k: np.full(len(P), np.nan, "float32") for k in COLS}
    for k, s in M.items(): out[k] = pd.Series(_daily_to_hourly(ts, s.index.values, s.values, LAG), index=ts).reindex(tP).astype("float32").values
    for c in OI.columns:
        m = coin == c
        if not m.any(): continue
        for k, f in F.items(): out[k][m] = pd.Series(_daily_to_hourly(ts, f.index.values, f[c].values, LAG), index=ts).reindex(tP[m]).values
    out["w_x_frrel"] = (out["w_x_fr"] - out["w_x_mfr"]).astype("float32")
    return out
