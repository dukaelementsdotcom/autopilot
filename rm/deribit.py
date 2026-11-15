"""DERIBIT OPTIONS RECORDER (storage only, for the January bull-market build; NOT a witness, used by nothing yet).
Deribit's free public data has no history for these, so they are recorded from now on: once an hour, two calls (BTC, ETH), the whole
option chain's mark implied volatilities, turned into
  atm7 / atm30 / atm90   at-the-money implied volatility at constant 7 / 30 / 90-day maturities (the term structure)
  rr25_7 / rr25_30       25-delta risk reversal: call IV minus put IV at 25 delta (positive = paying up for upside = greed)
  bf25_30                25-delta butterfly: how expensive both tails are vs at-the-money
  pcoi                   put / call open interest
  gex                    dealer gamma exposure in USD per 1% move, assuming dealers are long calls / short puts (a convention; sign may differ)
Hourly table: state/collect/deribit_opt.parquet. Once a day the compact chain is kept too (state/collect/deribit/<day>.jsonl.gz), so the
measures can be recomputed differently later. Deltas from Black-Scholes on Deribit's mark IV (r = 0).
Excluded from auto-discovery on purpose (the owner keeps the live system unchanged through December)."""
import os, json, gzip, time, math, re, datetime as dt
import numpy as np, pandas as pd, requests
from . import config as C

H = 3600
KEEP = lambda n: f"{C.STATE_DIR}/collect/{n}"
TABLE = "deribit_opt.parquet"
MON = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
UA = {"User-Agent": "Mozilla/5.0 (research-machine)"}

def _exp(name):
    m = re.match(r"^[A-Z]+-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:d\d+)?)-([CP])$", name)
    if not m: return None
    d = dt.datetime(2000 + int(m.group(3)), MON[m.group(2)], int(m.group(1)), 8, tzinfo=dt.timezone.utc)
    return d.timestamp(), float(m.group(4).replace("d", ".")), m.group(5)

def _ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def chain(cur):
    j = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency", params={"currency": cur, "kind": "option"}, headers=UA, timeout=20).json()
    rows = []
    for x in j.get("result") or []:
        e = _exp(x.get("instrument_name", ""))
        if not e or not x.get("mark_iv") or not x.get("underlying_price"): continue
        rows.append({"exp": e[0], "K": e[1], "cp": e[2], "iv": float(x["mark_iv"]) / 100, "S": float(x["underlying_price"]), "oi": float(x.get("open_interest") or 0)})
    return pd.DataFrame(rows)

def measures(df, now):
    """chain -> the hourly measures (pure function, unit-tested with synthetic chains)"""
    df = df[(df.exp > now + 6 * H) & (df.iv > 0.01)].copy()
    if df.empty: return {}
    df["T"] = (df.exp - now) / (365 * 86400); sq = df.iv * np.sqrt(df["T"])
    df["d1"] = (np.log(df.S / df.K) + 0.5 * df.iv ** 2 * df["T"]) / sq
    df["delta"] = [(_ncdf(d) if cp == "C" else _ncdf(d) - 1) for d, cp in zip(df.d1, df.cp)]
    df["gamma"] = np.exp(-0.5 * df.d1 ** 2) / np.sqrt(2 * np.pi) / (df.S * sq)
    per = []
    for ex, g in df.groupby("exp"):
        c = g[(g.cp == "C") & (g.K >= g.S)].sort_values("delta"); p = g[(g.cp == "P") & (g.K <= g.S)].sort_values("delta")
        if len(c) < 3 or len(p) < 3: continue
        iv = lambda d, x: float(np.interp(d, x.delta.values, x.iv.values))
        atm = (iv(0.5, c) + iv(-0.5, p)) / 2 if c.delta.max() >= 0.4 and p.delta.min() <= -0.4 else float(g.iloc[(g.K - g.S).abs().argsort()[:2]].iv.mean())
        c25, p25 = iv(0.25, c), iv(-0.25, p)
        per.append({"T": float(g["T"].iloc[0]) * 365, "atm": atm, "rr": c25 - p25, "bf": (c25 + p25) / 2 - atm})
    if len(per) < 2: return {}
    E = pd.DataFrame(per).sort_values("T")
    at = lambda col, days: float(np.interp(days, E["T"].values, E[col].values)) if E["T"].min() <= days * 1.5 else np.nan
    sgn = np.where(df.cp == "C", 1.0, -1.0)
    return {"atm7": at("atm", 7), "atm30": at("atm", 30), "atm90": at("atm", 90), "rr25_7": at("rr", 7), "rr25_30": at("rr", 30), "bf25_30": at("bf", 30),
            "pcoi": float(df[df.cp == "P"].oi.sum() / max(1e-9, df[df.cp == "C"].oi.sum())),
            "gex": float((sgn * df.gamma * df.oi * df.S ** 2 * 0.01).sum()), "spot": float(df.S.median()), "n": int(len(df))}

def record(log=print):
    t0 = time.time(); now = time.time(); hour = int(now) // H * H; rows = []; raw = {}
    for cur in ("BTC", "ETH"):
        try:
            df = chain(cur); m = measures(df, now)
            if m: rows.append({"t": hour, "cur": cur, **m}); raw[cur] = df
        except Exception as e: log(f"   deribit {cur}: {str(e)[:80]}")
        if time.time() - t0 > 25: break
    if not rows: return {"error": "no option chain"}
    os.makedirs(KEEP("deribit"), exist_ok=True); path = KEEP(TABLE); new = pd.DataFrame(rows)
    if os.path.exists(path):
        try: new = pd.concat([pd.read_parquet(path), new])
        except Exception: pass
    new = new.drop_duplicates(["t", "cur"], keep="last").sort_values(["t", "cur"]); new.to_parquet(path + ".tmp", index=False); os.replace(path + ".tmp", path)
    day = time.strftime("%Y-%m-%d", time.gmtime(now)); dp = KEEP(f"deribit/{day}.jsonl.gz")
    if not os.path.exists(dp):                                         # once a day: the compact chain, so the measures can be recomputed later
        with gzip.open(dp, "wt") as f:
            for cur, df in raw.items(): f.write(json.dumps({"t": hour, "cur": cur, "chain": df.round(5).values.tolist(), "cols": list(df.columns)}) + "\n")
    out = {"hours": int(new.t.nunique()), **{f"{r['cur']}_rr25_30": round(r["rr25_30"] * 100, 2) for r in rows if r.get("rr25_30") == r.get("rr25_30")}, "secs": round(time.time() - t0, 1)}
    log(f"deribit options: {out}"); return out
