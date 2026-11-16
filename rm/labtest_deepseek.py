"""SECOND OPINION (owner's request, Nov 16): ask a DeepSeek reasoning model on Groq the same design question Fable answered,
first independently, then to critique Fable's design. Research only; writes the answers to labtest.json."""
import os, json, re, time, requests

DATA = """Data the system collects:
1. Binance futures per coin, hourly since 2020 (~54 coins): OHLC, volume, trade count, taker buy/sell, open interest (1/4/24h change), long/short ratios (all accounts, top traders), funding; derived returns 1h-7d, 50-day trend, EMAs, RSI, Bollinger, ADX, Donchian breakouts, streaks, volatility; cross-coin ranks.
2. Spot vs futures: basis, spot share of volume, spot taker imbalance 24h, spot-vs-perp CVD 1/3/7 days.
3. Other venues: Hyperliquid premium and funding (since 2023); cross-exchange OI and funding (Binance+Bybit+OKX, daily since 2020); liquidations longs/shorts (daily since 2020, hourly from now); top-50 Hyperliquid whale positions (recording).
4. Order book: Binance archive depth at 0.2/1/5% (since 2023, daily lag); own-server minute recorder of depth, live funding, liquidations (recording).
5. Mood/options: Fear & Greed (since 2018), Coinbase premium, Deribit DVOL BTC/ETH hourly; Deribit option chain (ATM IV term structure, 25-delta risk reversal, butterfly, put/call OI, GEX), recording since Nov 15.
6. Flows/on-chain: stablecoin supply, BTC/ETH exchange flows and balances, active addresses, sector rotation.
7. Parallel markets daily: SPX, NDX, DJI, DAX, FTSE, Nikkei, HSI, DXY, US10Y, VIX, gold, oil, NVDA, SMH, COIN, BTC-stocks correlation.
8. News headlines by topic (since Nov 9), economic and IPO calendars.
9. Own records: scored candidate ledger, trades, demo fills/slippage (~0.29% worse than paper on market orders).
Not built: historical key levels (support/resistance) per coin, trade-level order flow, a combined trend reading."""

FACTS = """Constraints: +2% first take-profit (half), then TP2/TP3 scaled to the coin's volatility with the stop stepping up behind; stop 1.5-4% scaled to volatility; one new trade per UTC day, up to 4 open; hourly decisions (plus a 5-minute fast lane and a minute-level own server); ~40 liquid Binance USDT perpetuals; paper trading now, a December live exam, real money later.
What testing has found so far: a learned market-direction ('phase') model scored 52% (coin flip); learned cross-coin ranking failed walk-forward (rank correlation ~0, about -0.6%/month); outside-world witnesses (macro, global markets, mood, flows, on-chain, sectors) added essentially zero to prediction quality; a simple trend gate (no shorts while a trend reader says the market is rising) improved walk-forward from +2.0% to +3.4%/month and the worst period from -11.9% to -2.0%; 'up' days were followed by a higher market a week later 60% of the time, 'down' days 45%; pullback (limit) entries missed over half the trades with no quality gain; longs have never been profitable in any test; last week ~90% of ALL short candidates hit +2% because the market fell ~15%, and the model's own picks beat the average short by only ~1.6 points."""

Q1 = f"""You are an independent quantitative crypto trading expert. The owner of an automated trading system asks:
"How would you combine each piece of data, and how would you form grouped (or otherwise ungrouped) witnesses to: 1. clearly identify the trend of the day versus the macro trend; 2. specifically find good trades with sniper entries. At the end an Arbiter decides based on this."
He wants the trend identified from qualified, aggregated indicators (not hundreds of raw inputs), to ride a trend and stop riding it when the evidence says so; then per coin: key levels from its history, supply and demand, order flow and every measure that points to a direction, to find precise entries.

{DATA}

{FACTS}

Answer with your own independent design: Part 1 trend (which data in which grouped witness, daily vs macro, exact ride and exit rules, what to leave out and why); Part 2 entries (coin/side witnesses, how to build key levels, exact entry mechanics, what can realistically improve entries); how the Arbiter combines them (plain terms); and how to test each piece honestly. Be concrete and honest about what is unproven. Plain language, under 1,200 words."""

def ask(key, model, prompt, max_tokens=6000):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=300, headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0.6})
    if not r.ok: return None, f"HTTP {r.status_code}: {r.text[:300]}"
    txt = r.json()["choices"][0]["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip(), None

def main():
    key = os.environ.get("GROQ_API_KEY"); out = {}
    if not key: print("no GROQ_API_KEY"); return
    ms = [m["id"] for m in requests.get("https://api.groq.com/openai/v1/models", timeout=30, headers={"Authorization": f"Bearer {key}"}).json().get("data", [])]
    out["models"] = ms; print("Groq models:", ms)
    cand = [m for m in ms if "deepseek" in m.lower()] + [m for m in ms if "qwen" in m.lower() and ("qwq" in m.lower() or "32b" in m.lower())] + [m for m in ms if "gpt-oss-120b" in m.lower()]
    print("candidates:", cand)
    fable = open(os.path.join(os.path.dirname(__file__), "fable_design.txt")).read()
    for m in cand:
        a1, e1 = ask(key, m, Q1)
        if not a1: print(m, "failed:", e1); out.setdefault("errors", {})[m] = e1; continue
        q2 = f"""Here is your design from before:\n\n{a1}\n\nAnother reviewer proposed this design for the same problem:\n\n{fable}\n\nCritique the other design honestly: what it gets right, what it gets wrong or misses, and where your own design is better or worse. Then say what the final combined design should be. Under 800 words, plain language."""
        a2, e2 = ask(key, m, q2)
        out["model"] = m; out["independent"] = a1; out["critique"] = a2 or e2
        print("=" * 30, "MODEL", m, "=" * 30); print("### INDEPENDENT DESIGN\n" + a1); print("\n### CRITIQUE OF FABLE'S DESIGN\n" + (a2 or e2))
        break
    json.dump(out, open("labtest.json", "w"), indent=1)

if __name__ == "__main__": main()
