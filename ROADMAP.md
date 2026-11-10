# Build board

Product: **Volvox**, powered by the **Drosophila** research engine (check trademarks and domains before going public; note the closeness to "Volvo"). Technical plan for the next build: BUILD_A.md in the repo.

## Rules of the board
- **One judge.** The referee makes every decision. Everything else is evidence fed into it; nothing gets its own vote or account.
- **Every idea is a challenger.** A clone of the judge with exactly one change is scored on the same live cases. It takes over only if it is more accurate, finds at least as many Grade A trades and keeps its odds as honest. Most ideas are expected to fail and be dropped.
- **Subtraction too.** Every build also tests the judge without each witness. Evidence that adds nothing is removed. When scores tie, the simpler version wins.
- **One scoreboard:** honest odds that turn into profitable Grade A trades. Win big, lose small.
- **Fluidity.** No hard-coded numbers. Every setting is learned from evidence and moves slowly.
- **Data first.** New data is collected weeks before it is used, so it arrives with history.
- **Never stopped by technical limits.** Free tools only, bounded storage, health checks on every limit. Paid items only from real profits.

## Timeline
- **Now (paper):** baseline running since Nov 7; new data recording since Nov 9
- **Wed Nov 11: Build A** (foundation, honest scoring, trade shape, risk, learning, new evidence, clean-up, Volvox dashboard)
- **About 1 week after A: Build B** (accuracy upgrades), once Build A has run cleanly for a few days; its challengers join the queue as test slots free up
- **At least 1 month of paper AND the gate passed: Build C** (real money), earliest around mid-December
- **By April 2027:** decide how the AWS server is paid for after the free credits end on May 9, 2027 (from profits, or move)

## Status
- [x] Stage 1: referee account, about 50 coins, exams, exchange-style UI, health report
- [x] Stage 2: spot vs futures, Hyperliquid, self-adapting referee, live scorecard, post-mortems, monthly report
- [x] Own relay server (AWS Stockholm, fixed IP), with Render and Vercel as backups
- [x] Vercel, cron and Upstash clean-up
- [x] Storage can never block us: memory bounded and compressed, server compresses and deletes old files itself, health checks on disk, memory and CPU
- [ ] Baseline: about 24 clean runs and 30 to 40 scored calls before Build A

## Recording now (data only, no decisions change)
- [x] Own-server recorder: order-book depth every minute, live funding every 5 minutes, liquidations live (Binance's new stream address)
- [x] Outside world, daily: Nasdaq-100, S&P 500, VIX, US 10-year yield, dollar (FRED)
- [x] On-chain, daily: BTC and ETH exchange inflows, outflows and balances, active addresses (Coin Metrics)
- [x] Hyperliquid whales, hourly: positions of the 50 largest accounts
- [x] Calibration ledger: every scanned candidate with each witness's opinion and the full price path afterwards
- [x] News headlines, hourly: crypto, macro, geopolitics and regulation (public news feeds and Google News searches)
- [ ] Add NVIDIA, chip index and other daily stock data (Stooq or similar) so it also has history by the time it is used

## Build A (Wed Nov 11), in build order
**A1. Foundation**
- [ ] Split the referee into brain / policy / ledger / challengers
- [ ] Challenger system: registry, promotion rule, one-click rollback, an on/off switch for every challenger. Challengers are scored on **every ledger candidate** (about 100 a day), not only on traded calls, so they are judged in days instead of months
- [ ] Subtraction tests (judge without each witness)
- [ ] Ledgers moved out of the main memory file; staggered training (at most one brain per run); run-time guard that skips training, never trading
- [ ] Calls made in the same market move count as one bet (effective number of bets) in every score

**A2. Honest scoring**
- [ ] Calibration table on the dashboard (predicted odds vs what happened), per side, market state and coin group
- [ ] Skipped-trade review: error type of every miss (wrong read / stopped first / over- or under-confident / bar too high) from the full price path
- [ ] Missed-winners count: what caution cost vs what it saved; if caution costs money, the judge loosens itself
- [ ] Honest timing: paper fills at the live price at decision time, not the hour's open

**A3. Trade shape, pacing and risk (policy challengers)**
- [ ] Trade shape adapts to volatility: the target grows with the stop, so a wild market no longer forces an 80% bar
- [ ] Best trade shape per situation: odds for a ladder of targets (+2/+4/+8%) and horizons (4h/24h/72h); pick the highest expected profit; learned runner targets and trail width
- [ ] Fluid pacing: learned trade pace (no fixed 1 a day or 7 a week), several trades a day allowed, weekly pace by count and profit, surplus carries over, new top tier **Grade S**
- [ ] Fluid risk: total open-risk cap, same-bet (correlation) guard, daily and weekly brakes
- [ ] Edge-proportional sizing: fractional Kelly from calibrated odds, bigger only when the edge is proven

**A4. Learning which evidence was right**
- [ ] Every scored candidate, traded or skipped, adjusts how much each witness is trusted, per market state (recursive, Kalman-style online learning with a forgetting factor; the forgetting speed itself is chosen by competing challengers)
- [ ] Inside each witness: which measurement pushed the odds the wrong way; misleading measurements are down-weighted, and missing ones become research targets
- [ ] Coin-specific trust: the judge learns its record per coin and coin group and demands more evidence where it is weak

**A5. New evidence (each one a separate challenger witness)**
- [ ] Recorder witness: liquidation bursts, order-book imbalance, live funding and premium
- [ ] Outside-world witness: risk-on/off state from stocks, VIX, dollar and yields; macro calendar (Fed, CPI, jobs) with no new trades just before
- [ ] Whale witness (Hyperliquid's largest accounts)
- [ ] On-chain witness (exchange flows; stablecoin flows per chain from DefiLlama)
- [ ] Sectors (AI, memes, L1, DeFi, exchange tokens) and sector rotation
- [ ] News witness: headlines tagged by the AI (type, who, severity) plus a memory of similar past events; tested last, because it is the noisiest
- [ ] **Shock memory**: every past sudden move (BTC and Nasdaq since 2020, found from prices, the cause labelled from news where possible), split by bull/bear state, scheduled (Fed, CPI) vs surprise, and whether it was already priced in (drift before). It records how fast the reaction came (minutes / hours / at the next stock-market open) and whether it lasted or faded within 4h, 24h and 72h
- [ ] **Shock response for open trades** (challenger): when a shock hits against an open trade, the shock memory decides exit / take profit / hold, based on what similar shocks did. This is the only case where an open trade is re-judged
- [ ] Shock detector on the own server: records every minute-level shock with the market's reaction
- [ ] **Server guard (speed)**: each hourly run hands the own server the open trades plus a ready-made plan ("if a shock of this size hits against this trade: exit / take profit / hold"); the server watches every minute and acts on paper within about a minute, without waiting for the next hourly run. GitHub does the thinking, the server does the reacting

**A6. Rules feed the judge**
- [ ] Account clean-up: remove the team and all-in accounts; keep the best-rule account as the testing ground
- [ ] Live lab: every champion rule paper-traded in shadow; the best-rule account picks its champion **per market state** by live record (no dip-buy longs in a falling market unless they have proven themselves there)
- [ ] Highest standard for rules reaching the judge: a rule's vote counts only in the market states where its live and after-discovery record is good; a rule that keeps failing in a state becomes a **warning sign** there (its firing counts against its own side); rules that add nothing in the subtraction test are not fed at all
- [ ] Research looks for rules that know the market state (a rule can include "only in a falling, wild market"), so rules are specialists instead of islands
- [ ] Rules as fair witnesses (votes count only after their discovery date); rules propose and the judge approves; market sensor (which rule styles win now)
- [ ] Research aimed at the goal: favour rules whose wins are at least +2%; curiosity-driven research pointed at the judge's blind spots

**A7. Dashboard**
- [ ] Volvox branding (research engine shown as Drosophila); Overview opens on the referee; research moves to its own section
- [ ] Upgrade-under-test panel, calibration table, Grade A pace, recorder and server status

**A8. Test and deploy**
- [ ] Local end-to-end run on real data (memory and time), a 10-day replay through the whole policy, phone and desktop screenshots
- [ ] Crash rehearsal: replay March 2020, LUNA, FTX and recent shocks through sizing and exits
- [ ] Deploy once, watch the first 3 runs, then the daily health reports

## Build B (accuracy, about 1 week after A; each one a challenger)
- [ ] Market states: hidden-Markov and Gaussian-mixture regimes (competing), change-point detection, linked to macro data; a specialist judge per state (may bring longs back)
- [ ] Three time frames (1h / 4h / daily) must not contradict each other
- [ ] Panel of 3 differently trained judges; disagreement raises the bar
- [ ] Train on every hour (3x examples)
- [ ] Honest certainty for Grade S (conformal prediction)
- [ ] Entry timing: wait up to about 3 hours for a better price, or skip
- [ ] Fluid stops: per-coin wick depth, close-based stops, time stops, wider stop with smaller size; permanent disaster stop
- [ ] Trapped-trader levels and leverage-squeeze targets from open interest and recorded liquidations
- [ ] Lead-lag network (which coins and markets move first, stocks to crypto included); 24/7 stock perps as an hourly stock signal
- [ ] Session and clock effects (Asia / EU / US opens, weekends, funding times, US data releases)
- [ ] "Similar to my past mistakes" witness; shape matching for the similar-moments witness
- [ ] Exit choice per market state; runner modelling
- [ ] Best case of the day (bar eases through the day)
- [ ] Research engine: smarter search (branch-and-bound / A*), champion conditions become new measurements, judge-vs-rule disagreements logged
- [ ] Universe of 70 to 80 coins with a liquidity filter; the judge still trades the most liquid 40 or so of the day

## Build C: real money (at least 1 month of paper AND the gate)
Gate (fluid): Grade A about daily, live hit rate close to predicted over 25+ calls (counted as independent bets), at least 1 month of paper.
- [ ] Binance USDT-M futures on your own verified account; API key futures-only, no withdrawals, locked to the AWS server's IP
- [ ] Execution service on the AWS server: orders within seconds of each decision; stop-loss and take-profit placed on the exchange with every entry; permanent disaster stop
- [ ] Wake-up alarm with real orders: the server watches every minute (sharp moves, open-interest spikes, liquidation cascades) and wakes the judge immediately
- [ ] The judge sets margin mode and leverage per trade (liquidation at least about 2.5x the stop distance) and size by risk
- [ ] Rollout: you confirm, then small automatic, then full automatic
- [ ] Paper vs real reconciliation (slippage, fees, funding); size shrinks automatically if reality is worse
- [ ] Relay resilience: if the live data fails, manage open positions from spot prices; no new trades on incomplete data
- [ ] Heartbeat alert if runs stop while positions are open; extra position check at :47
- [ ] Never-trade list for the real account; real-money checklist card (exact entry, stop, target and size for your amount)
- [ ] House-money adds: add to confirmed winners using only that trade's profit

## Only if the evidence asks for it
- [ ] Re-judge open trades (parked): only if the scorecard shows trades being lost to market turns; reluctant by design
- [ ] Bybit / OKX positioning data
- [ ] Deep-learning judge as a challenger
- [ ] Paid data or servers only from the self-funded budget: a share of real profits, value proven on free samples first, a proposal with expected gain, cost and payback, cancelled automatically if it does not pay

## Housekeeping (you)
- [ ] Create a new Groq key, put it in GitHub secret GROQ, delete the old one
- [ ] Create a new GitHub token for cron-job.org, update the job's Authorization header, delete the old token
- [x] Delete old Vercel projects (btc-trading, btc_vercel_v25_1)
- [ ] Delete old cron jobs (BTC Trading Engine, BTC Brain) and the Upstash BTCLAB database
- [ ] Each morning: download the health report and send it
