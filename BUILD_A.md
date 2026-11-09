# Build A: technical plan

Goal: the whole "Build A" list on ROADMAP.md, shipped once and tested end to end. Every ability that changes a decision
enters as a **challenger**: a clone of the judge that differs by exactly one change. The current judge keeps trading until a
challenger proves itself.

## 0. Start conditions (checked Wed Nov 11, 09:00 Nairobi)
- About 24 clean hourly runs in a row, no outages, health OK
- 30 to 40 scored scorecard calls, plus the first scored calibration-ledger candidates (needed for A2/A4)
- Collectors healthy for at least 1 day (recorder, macro, on-chain, whales; news switched to RSS feeds)
- If the conditions are not met: build anyway, but challengers start with the data we have and promotion simply waits for evidence

## 1. Architecture (A1)
- Modules: `brain` (family models, combiner, training), `policy` (grades, shape, pacing, risk, sizing), `ledger` (scorecard,
  calibration ledger, skipped-trade review, missed winners), `challengers` (registry, scoring, promotion, rollback), `witness_*` (new evidence).
- **Challenger registry** entry: name, the one change, model file, created, cases scored, Brier/log-loss vs champion,
  calibration error per band, simulated Grade A rate and P&L, effective number of bets.
- **Promotion** (all must hold, over at least 40 effective bets): lower log-loss than the champion, Grade A rate not lower, calibration error not worse,
  simulated P&L not worse. The previous champion is kept for one-click rollback. Automatic demotion if live results turn worse after promotion.
- **Scored on every ledger candidate** (about 100 a day), not only on traded calls, which makes judging about 20x faster.
- **Subtraction tests**: for each witness, a "without it" challenger; witnesses that add nothing are removed.
- **Effective number of bets**: calls in the same 6-hour window and on the same side count as one cluster, weighted 1/cluster size.
- **Feature flags** in `config.py` for every challenger and witness (on, shadow, off).
- Ledgers move to compressed files in the saved memory (`state/ledger/*.jsonl.gz`); memory.json stays small. Old rows are summarised, never silently lost.

## 2. Compute budget (GitHub runner: 4 cores, 16 GB, public repo so no minute limit; a run must stay under about 40 minutes)
- Today: about 15 to 22 minutes per run, about 4.2 GB peak; training 4 to 8 minutes per brain.
- **Staggered training**: at most one brain (champion or challenger) is (re)trained per run, the stalest first; models are cached.
  Budget: at most 8 active challengers at a time; the rest wait in a queue and enter as others are promoted or dropped.
- Policy challengers (A3) do not need their own brain: they reuse the champion's odds, so scoring them costs seconds.
- Witness challengers (A5) need a brain each: queued 2 at a time.
- Guard: if a run nears 40 minutes, training and research are skipped, never trading, scoring or saving.

## 3. New evidence (A5): each one optional (missing data means NaN, never a crash) and each with a health check
| Witness | Source (already recording) | Features (hourly) |
|---|---|---|
| Recorder | own server `/rec/get` (incremental, compressed) | liquidations long/short (1h, 4h, z-score), book imbalance 0.2%/1%, spread, live funding, premium |
| Outside world | FRED (+ stock data source to add) | 1d/5d changes, VIX level/change, risk-on/off state, days to Fed/CPI/jobs |
| Whales | Hyperliquid clearinghouseState, top 50 | net whale position per coin, change over 24h, whale count |
| On-chain | Coin Metrics, DefiLlama | exchange net flow BTC/ETH (z-score), stablecoin supply change |
| Sectors | coin groups from config | sector return vs market, rotation score |
| News | RSS feeds, AI-tagged | event type, severity, crypto vs macro, similar-past-event outcome |
Data with less than about 3 weeks of history is used only as a challenger with "unfamiliar" handling (it can never be a reason to trade on its own).

## 4. Learning (A2 + A4)
- Calibration bands per side, market state and coin group; skipped-trade error types from mfe/mae/mfeH/maeH/end (recorded since Nov 10).
- Online combiner: per market state, a slow logistic update of witness weights from every scored candidate (small learning rate, batch
  every 24h, weights shrink toward the trained combiner). Measurement-level attribution inside each witness (permutation / tree contributions)
  on wrong calls, aggregated weekly. Misleading measurements get a down-weight challenger; blind spots go to the research engine as targets.

## 5. Policy (A3): all learned, none hard-coded
- Trade shape: target ladder (+2/+3/+4/+8%) x horizon (4/24/72h) from the champion's odds per shape; pick the highest expected value after fees;
  the stop stays volatility-based (1.25x daily volatility, clipped 1.5% to 4%, the clip itself learned later in Build B).
- Pacing: learned opportunity rate (rolling 14 days), several trades a day allowed, weekly pace by count and profit, carry-over, Grade S (top tier, conformal later).
- Risk: total open-risk cap, same-bet guard (correlation of open positions), daily and weekly brakes; fractional Kelly sizing capped by margin_cap.
- Honest timing: entry at the live mark price at decision time (relay/recorder), never the hour's open.

## 6. Safety and testing (A8)
- Every new part wrapped in `safe()`; a failing challenger never touches the champion.
- Local end-to-end run on real data (memory and time measured); a 10-day replay through the whole policy; crash rehearsal (March 2020, LUNA, FTX, recent shocks).
- Desktop and phone screenshots of the dashboard.
- Deploy once, watch the first 3 GitHub runs, then the daily health reports. Rollback plan: one commit revert plus restored models.

## 7. Dashboard (A7)
- Volvox branding (Drosophila for research). Overview opens on the referee; research moves to its own section.
- New panels: upgrade under test (each challenger and its scores), calibration table, Grade A pace, recorder and server status.

## Order of work (each step tested before the next)
1. A1 foundation and ledgers  2. A2 honest scoring  3. A3 policy challengers  4. A4 learning  5. A5 witnesses
6. A6 clean-up and live lab  7. A7 dashboard  8. A8 full test, deploy, watch
