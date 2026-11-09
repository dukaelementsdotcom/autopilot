# Build A: technical plan

Goal: everything on the "Next build" list, shipped once, with every new ability entering as a **challenger** (a cloned judge
that differs by exactly one ability, scored on the same live cases, promoted automatically only if it is better).

## 1. Architecture changes
- **Split the referee into modules**: `brain` (models + training), `policy` (grades, pacing, risk, sizing), `ledger`
  (scorecard, calibration ledger, missed winners), `challengers` (registry, scoring, promotion, rollback).
- **Challenger registry**: each challenger = {name, what differs, model file, created, calls scored, hit vs predicted,
  Grade A/day, simulated P&L}. Promotion rule: more accurate AND >= Grade A rate AND calibration not worse, over >= 40 calls.
  The previous champion model is kept for one-click rollback.
- **Ledgers out of memory.json**: scorecard / calibration / challenger calls move to compressed files in the state branch
  (memory.json stays small and fast); old rows are summarised, not deleted.

## 2. Compute budget (GitHub runner: 4 cores, 16 GB, run must stay < ~40 min)
- Today: ~15 min per run, ~4.2 GB peak; training ~4-7 min per brain.
- **Staggered training**: at most one brain (champion or challenger) is (re)trained per hourly run, oldest first;
  models are cached between runs. ~7 brains -> each retrained about every 7 hours of runs, champion weekly as now.
- Scoring all challengers every hour is cheap (seconds).
- Hyperliquid back-fill finishes first (it currently uses up to 10 min per run).
- Guard: if a run nears 40 min, training is skipped (never the trading or scoring).

## 3. New data (each optional: missing data = NaN, never a crash; each has a health check)
| Source | What | Access | Frequency |
|---|---|---|---|
| Own server recorder | order-book depth (0.2% / 1%), live funding, liquidations | `/rec/get` on 13.48.108.50 (key) | minute -> hourly features |
| Stooq | Nasdaq-100, S&P 500, NVIDIA, SOX, dollar index | free CSV | daily |
| FRED | VIX, US 10-year yield | free CSV (no key) | daily |
| Macro calendar | FOMC, CPI, payrolls dates 2026-2027 | static file, yearly refresh | - |
| GDELT | news events, tagged by the AI (type, who, severity) | free API | hourly |
| Coin Metrics community / DefiLlama | exchange flows (BTC/ETH), stablecoin flows per chain | free API | daily |
| Hyperliquid | largest accounts' positions (whales) | public API | hourly |

## 4. Policy changes (all learned, none hard-coded)
- Fluid pacing (learned opportunity rate, weekly pace by count and profit, carry-over, Grade S), total open-risk cap,
  same-bet guard, daily/weekly brakes, runner targets, trade shape adapting to volatility, edge-proportional sizing.
- Honest timing: paper fills at the live mark price at decision time (from the relay / recorder), not the hour's open.

## 5. Safety and testing
- Every new part wrapped in `safe()`; feature flags to switch any challenger off.
- Local end-to-end run on real data (memory + time), a 10-day replay through the whole policy, desktop + phone screenshots.
- Deploy once, watch the first 3 GitHub runs, then daily health reports.

## 6. Dashboard
- Rename to **Volvox** (research engine: **Drosophila**). Overview opens on the referee; research moves to its own section.
- New: "Upgrade under test" panel (challengers and their scores), calibration table, Grade A pace, recorder status.

## Order of work
1. Recorder ingestion + new data sources (start collecting first) 2. challenger registry + ledgers 3. policy challengers
4. outside-world challenger 5. live lab / rules as witnesses 6. dashboard 7. full test, deploy, watch.
