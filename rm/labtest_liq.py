"""LAB TEST: does the liquidation collector work with the key, and what does the data look like?"""
import json, os, numpy as np, pandas as pd
from . import liq
out = {}
try:
    out["update"] = liq.update(budget=900)
    m = json.load(open(liq.P_M())); out["exchanges"] = m["exchanges"]; out["sample"] = {k: m["map"][k] for k in list(m["map"])[:6]}; out["unmatched"] = [c for c in liq.C.COINS if c not in m["map"]]
    d = pd.read_parquet(liq.P_D()); h = pd.read_parquet(liq.P_H())
    out["daily"] = {"rows": len(d), "from": str(pd.to_datetime(d.t.min(), unit="s")), "to": str(pd.to_datetime(d.t.max(), unit="s")), "coins": int(d.coin.nunique()),
                    "btcLast": d[d.coin == "BTC"].tail(4).to_dict("records"), "perYear": d.groupby(pd.to_datetime(d.t, unit="s").dt.year).coin.nunique().to_dict()}
    out["hourly"] = {"rows": len(h), "from": str(pd.to_datetime(h.t.min(), unit="s")), "to": str(pd.to_datetime(h.t.max(), unit="s")), "coins": int(h.coin.nunique())}
except Exception as e:
    import traceback; out["error"] = traceback.format_exc()[-1500:]
print(json.dumps(out, indent=1, default=str)); json.dump(out, open("labtest.json", "w"), default=str)
