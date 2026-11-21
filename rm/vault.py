"""DATA VAULT: an append-only backup of the recordings that can never be downloaded again (own-server order book, funding, liquidations,
Hyperliquid whales, Deribit options, news, the AI market briefs). The live copy sits in the 'state' branch, which is overwritten every run;
this copies each FINISHED UTC day once into the 'vault' branch as its own small file. Nothing is ever overwritten or deleted, so a bug that
damages the live copy can never reach the history.
usage: python -m rm.vault <state_dir> <vault_dir>"""
import os, sys, glob, shutil, datetime as dt
import pandas as pd

PARQUETS = ("rec_depth", "rec_funding", "rec_liq", "hl_whales", "deribit_opt")

def day_of(t):
    t = pd.to_numeric(t, errors="coerce"); t = t / 1000 if t.max() > 1e11 else t
    return pd.to_datetime(t, unit="s").dt.strftime("%Y-%m-%d")

def run(state, vault):
    today = dt.datetime.utcnow().strftime("%Y-%m-%d"); added = []
    for nm in PARQUETS:
        fp = f"{state}/collect/{nm}.parquet"
        if not os.path.exists(fp): continue
        d = pd.read_parquet(fp); d["_d"] = day_of(d["t"])
        for day, g in d.groupby("_d"):
            if day >= today: continue                              # only finished days: a day file is final
            out = f"{vault}/{nm}/{day}.parquet"
            if os.path.exists(out): continue
            os.makedirs(os.path.dirname(out), exist_ok=True); g.drop(columns="_d").to_parquet(out, index=False, compression="zstd"); added.append(out)
    for sub in ("news", "deribit"):
        for fp in glob.glob(f"{state}/collect/{sub}/2*.jsonl.gz"):
            day = os.path.basename(fp)[:10]; out = f"{vault}/{sub}/{day}.jsonl.gz"
            if day >= today or os.path.exists(out): continue
            os.makedirs(os.path.dirname(out), exist_ok=True); shutil.copy2(fp, out); added.append(out)
    for fn in ("digest.jsonl", "calendar.json", "ipos.json", "hl_top.json", "growth.jsonl"):                  # small and growing: keep the latest copy (history keeps every version)
        fp = f"{state}/collect/{fn}"
        if os.path.exists(fp):
            os.makedirs(f"{vault}/misc", exist_ok=True); shutil.copy2(fp, f"{vault}/misc/{fn}")
    if os.path.isdir(f"{state}/archive"):
        os.makedirs(f"{vault}/archive", exist_ok=True)
        for fp in glob.glob(f"{state}/archive/*"): shutil.copy2(fp, f"{vault}/archive/{os.path.basename(fp)}")
    return added

if __name__ == "__main__":
    a = run(sys.argv[1], sys.argv[2]); print(f"vault: {len(a)} new day files")
