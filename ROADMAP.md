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
Built Nov 11. Waiting for data history: recorder, whale and news witnesses (auto-trained at ~21 days). Still open: measurement-level attribution, live lab (every champion in its own shadow account), state-aware research and research aimed at +2% (the research engine is the testing ground; changed last).
**A1. Foundation**
- [x] Split the referee into brain / policy / ledger / challengers
- [x] Challenger system: registry, promotion rule, one-click rollback, an on/off switch for every challenger. Challengers are scored on **every ledger candidate** (about 100 a day), not only on traded calls, so they are judged in days instead of months
- [x] Subtraction tests (judge without each witness)
- [x] Ledgers moved out of the main memory file; staggered training (at most one brain per run); run-time guard that skips training, never trading
- [x] Calls made in the same market move count as one bet (effective number of bets) in every score

**A2. Honest scoring**
- [x] Calibration table on the dashboard (predicted odds vs what happened), per side, market state and coin group
- [x] Skipped-trade review: error type of every miss (wrong read / stopped first / over- or under-confident / bar too high) from the full price path
- [x] Missed-winners count: what caution cost vs what it saved; if caution costs money, the judge loosens itself
- [x] Honest timing: paper fills at the live price at decision time, not the hour's open

**A3. Trade shape, pacing and risk (policy challengers)**
- [x] Trade shape adapts to volatility (built; FAILED its final exam on the newest months, so it is not admitted; retried after each weekly retrain): the target grows with the stop, so a wild market no longer forces an 80% bar
- [ ] Best trade shape per situation (first version tested: chased +6% targets and lost on the exam; redesign later): odds for a ladder of targets (+2/+4/+8%) and horizons (4h/24h/72h); pick the highest expected profit; learned runner targets and trail width
- [x] Fluid pacing: learned trade pace (no fixed 1 a day or 7 a week), several trades a day allowed, weekly pace by count and profit, surplus carries over, new top tier **Grade S**
- [x] Fluid risk: total open-risk cap, same-bet (correlation) guard, daily and weekly brakes
- [x] Edge-proportional sizing: fractional Kelly from calibrated odds, bigger only when the edge is proven

**A4. Learning which evidence was right**
- [x] Every scored candidate, traded or skipped, adjusts how much each witness is trusted, per market state (recursive, Kalman-style online learning with a forgetting factor; the forgetting speed itself is chosen by competing challengers)
- [ ] Inside each witness (witness-level 'which one misled' is live; measurement-level needs the raw values stored, next): which measurement pushed the odds the wrong way; misleading measurements are down-weighted, and missing ones become research targets
- [x] Coin-specific trust: the judge learns its record per coin and coin group and demands more evidence where it is weak

**A5. New evidence (each one a separate challenger witness)**
- [ ] Recorder witness: liquidation bursts, order-book imbalance, live funding and premium
- [x] Outside-world witness: risk-on/off state from stocks, VIX, dollar and yields; macro calendar (Fed, CPI, jobs) with no new trades just before
- [ ] Whale witness (Hyperliquid's largest accounts)
- [x] On-chain witness (exchange flows; stablecoin flows per chain from DefiLlama)
- [x] Sectors (AI, memes, L1, DeFi, exchange tokens) and sector rotation
- [ ] News witness: headlines tagged by the AI (type, who, severity) plus a memory of similar past events; tested last, because it is the noisiest
- [x] **Shock memory**: every past sudden move (BTC and Nasdaq since 2020, found from prices, the cause labelled from news where possible), split by bull/bear state, scheduled (Fed, CPI) vs surprise, and whether it was already priced in (drift before). It records how fast the reaction came (minutes / hours / at the next stock-market open) and whether it lasted or faded within 4h, 24h and 72h
- [x] **Shock response for open trades** (challenger): when a shock hits against an open trade, the shock memory decides exit / take profit / hold, based on what similar shocks did. This is the only case where an open trade is re-judged
- [x] Shock detector on the own server: records every minute-level shock with the market's reaction
- [x] **Server guard (speed)**: each hourly run hands the own server the open trades plus a ready-made plan ("if a shock of this size hits against this trade: exit / take profit / hold"); the server watches every minute and acts on paper within about a minute, without waiting for the next hourly run. GitHub does the thinking, the server does the reacting

**A6. Rules feed the judge**
- [x] Account clean-up: remove the team and all-in accounts; keep the best-rule account as the testing ground
- [ ] Live lab: every champion rule paper-traded in shadow; the best-rule account picks its champion **per market state** by live record (no dip-buy longs in a falling market unless they have proven themselves there)
- [x] Highest standard for rules reaching the judge: a rule's vote counts only in the market states where its live and after-discovery record is good; a rule that keeps failing in a state becomes a **warning sign** there (its firing counts against its own side); rules that add nothing in the subtraction test are not fed at all
- [ ] Research looks for rules that know the market state (a rule can include "only in a falling, wild market"), so rules are specialists instead of islands
- [x] Rules as fair witnesses (votes count only after their discovery date); rules propose and the judge approves; market sensor (which rule styles win now)
- [ ] Research aimed at the goal: favour rules whose wins are at least +2%; curiosity-driven research pointed at the judge's blind spots

**A7. Dashboard**
- [x] Volvox branding (research engine shown as Drosophila); Overview opens on the referee; research moves to its own section
- [x] Upgrade-under-test panel, calibration table, Grade A pace, recorder and server status

**A8. Test and deploy**
- [x] Local end-to-end run on real data (memory and time), a 10-day replay through the whole policy, phone and desktop screenshots
- [x] Crash rehearsal: replay March 2020, LUNA, FTX and recent shocks through sizing and exits
- [x] Deploy once, watch the first 3 runs, then the daily health reports

- [x] Leverage per trade up to 20x (liquidation always at least 2.5x the stop away; wild coins get less), Nov 11

## Next pass (from your Nov 11 notes): aim for the daily percentage, not a trade count
- [x] **Daily-goal allocator** (challenger, live Nov 11): one margin budget per opportunity window (learned, up to ~30% of the account inside the hard safety limits), spent either on ONE strong trade (and let it run) or split across 2-4 coins when several good-but-smaller edges appear at once; split only across trades that are NOT the same bet (correlation-aware: four shorts on coins that move together count as one trade). Judged on account growth (compounding), not number of trades
- [ ] **Smaller targets when that is where the edge is** (challenger): +1% / +1.5% targets allowed when their odds are high enough to pay after fees (two +1% trades = one +2% day)
- [ ] **Opportunity-sized risk**: Grade S + Kelly + allocator together: much bigger when the evidence is strong and proven, small or none when it is not
- [x] **Never too cautious by design** (lower-bar challenger live Nov 11): promotions already require at least as many Grade A trades and at least as much profit (a "safer but idle" upgrade cannot win); missed-winners count loosens the bar when caution costs money; a challenger with a LOWER bar runs alongside so over-caution is caught live

## Audit, Nov 11 (what the evidence says about the goal)
- [x] Promotions need proof (a consistent lead over 40+ separate 6-hour windows and 10+ days); self-adjustment counts separate market moves, not correlated calls
- [x] **A bar per trend state** (challenger, live Nov 11): on the newest four months it took ~2.6x the trades with a higher win rate (local test: 3.7 trades/week, +10.3%/month vs 1.4 and +2.3% for the single bar; deeper worst drop 22% vs 8%). Longs return only in rising and sideways markets
- [x] Pullback entries: tested, failed (missed more than half the trades, no better quality); not built
- [x] Walk-forward audit (7 past periods, Nov 11): shorts positive in 5 of 7, about +2% a month on average as the exam scores them (about +0.8% as traded); lost 12% a month in the 2025 rally. Longs about zero. The Nov 7 brain no longer matched the data: retrained early
- [x] Pyramiding lab test (Nov 11): fails on shorts (the Arbiter's short signals are quick small drops, only 3% became 20% moves); passes on longs through rare very large winners -> **runner** challenger (live Nov 11)
- [x] New approaches tested overnight Nov 11-12, walk-forward over past periods, none passed: (1) learned cross-sectional ranking, long top / short bottom coins, 24h and 72h: about -0.6% a month on average, rank correlation near zero; (2) screened composite of the reliable measurements: negative in most periods; (3) short-horizon ranking (4h / 8h): a small edge before costs (about 0.02-0.08% per trade) that fees erase; (4) learned phase judge (market up or down over 7 / 14 days): 52% accuracy, a coin flip, also when confident. Conclusion: with the current free hourly data there is no large hidden edge; the Arbiter's short edge remains the best evidence found
- [x] Trend-gate test (7 past periods, Nov 12): shorts switched off while the trend reader says "up" averaged +3.4% a month vs +2.0%, with the worst period cut from -11.9% to -2.0% and smaller drops in every period -> **trend gate** challenger (live Nov 12). Longs restricted to rising markets still lost slightly (-0.5% a month); smaller short targets (+0.8 / +1 / +1.5%) were worse than +2%; neither built
- [ ] Honest status of the goal: evidence supports ~1-4 good trades a week; lowering the bar to trade daily lost money in the test. More good trades must come from better evidence (trend reader, Compass, long-side edge), not from a lower bar

## NEXT (top priority): an accurate trend reader, shared by every part
Today the market state comes from ONE measure (BTC above/below its 50-day average, plus volatility): slow to turn, and it can flip on noise.
- [ ] Several views that must agree: BTC vs its 20/50/200-day averages and their slopes, 7- and 30-day moves, breadth (share of coins below their own averages, new lows vs new highs), funding and open interest across coins, Nasdaq trend
- [ ] Five states with a confidence: strong down / down / sideways / up / strong up (sideways is real and matters)
- [ ] Turn detection (change points) so a real reversal is caught early, and hysteresis so noise does not flip it back and forth
- [ ] Measured honestly: how often each state correctly called the next 1, 3 and 7 days, shown on the dashboard
- [ ] ONE trend reading feeds everything (the Arbiter, every challenger and specialist, the best-rule account), so no part can disagree with another about the market; enters as a challenger against today's reader

## NEXT, together with the trend reader: "Compass", a market-intelligence engine feeding the Arbiter
Bitcoin alone is ~20% of the picture. Compass reads the whole world every hour and hands the Arbiter (and the trend reader) evidence, never decisions.
- [ ] Global markets (long free history, trainable now): S&P 500, Nasdaq, Dow, Europe (DAX, FTSE), Asia (Nikkei, Hang Seng), dollar index, gold, oil, US 2y/10y yields, VIX; NVIDIA and chip stocks; crypto vs each (who leads whom, how tight the link is right now)
- [ ] Money flows: Bitcoin / Ethereum ETF daily flows (history since Jan 2024), stablecoin supply (DefiLlama), exchange flows (Coin Metrics, recording)
- [ ] Mood: Fear & Greed index (history since 2018), funding and positioning across coins, options mood (Deribit, recording from now)
- [ ] Calendar: Fed, CPI, jobs (economic calendar feed), big earnings (NVIDIA...), IPOs (Nasdaq IPO calendar), token unlocks; the Arbiter knows what is coming and how similar events moved crypto before
- [ ] News digest: headlines from Reuters / Bloomberg / CNBC / Yahoo Finance / MarketWatch / CoinDesk / The Block via public feeds; the AI tags each (type, assets, sectors such as AI coins, direction, severity) and writes a short daily market brief on the dashboard
- [ ] Event memory: every tagged event stored with what crypto and each sector did after 1h / 1d / 1w (e.g. how AI coins moved around big AI news and AI IPOs), so a new event is judged by what similar ones did
- [ ] **Event playbooks**: for every known upcoming event (weeks ahead), Compass builds a plan from past events of the same family: which coins are affected (e.g. AI coins TAO, FET, NEAR, WLD for AI news / AI IPOs), what happened before / on / after the day (run-up, "sell the news" or "buy the news"), how often and how big. The Arbiter turns it into conditional plans (e.g. "if TAO dips 5% in the 2 days before, consider a long"), traded only when the playbook has enough past cases and its own evidence agrees; every plan is paper-traded and scored, so playbooks earn trust like everything else. Event families with history in our data: crypto-company listings (Coinbase Apr 2021, Circle Jun 2025), big AI moments (ChatGPT Nov 2022), NVIDIA earnings (~20), Fed / CPI days (~100)
- [ ] Each Compass input enters as a witness challenger (no noise allowed in); the global-risk state also feeds the trend reader

## Wild-market specialists (from your Nov 11 notes; each a challenger, used only when the Arbiter judges the odds good)
- [x] Profit lock (live Nov 11): break-even once a trade is well on its way; after the first target it keeps at least half of the best open profit
- [x] Curious about unfamiliar situations (live Nov 11): every unfamiliar candidate is recorded and scored, grouped by kind; a kind with a proven record becomes tradable, a failing kind is avoided, a mixed kind stays "it is what it is"
- [x] **Pair trades** (live Nov 11): long the strongest coin, short the weakest, same dollar amount; the market's swings cancel, the gap is the profit; tight stops even in wild markets
- [ ] Liquidation-cascade fades (needs ~3 weeks of recorded liquidations)
- [x] Range trades in wild-but-sideways markets (+1% at the edges of a range)
- [x] News drift: trade the move after the first 15 minutes of CPI / Fed / big headlines, using the shock memory
- [ ] Weekly goal with carry-over: a strong day or week banks a head start; quiet days are fine when the week is on pace

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
- [ ] Rollout: **fully autonomous from day one, no approvals** (your choice: approvals delay trades). Stage 1 = a **$67 test account**: the referee sizes, enters, sets exchange stop and take-profit, accounts for fees and funding, manages and exits on its own; the budget grows only after the test account's real results match paper
- [ ] Hard safety limits that sit outside the referee and that it cannot change: permanent exchange-side disaster stop on every position, maximum leverage, maximum loss per day / week (trading pauses itself), kill switch, never more than the test budget in the futures wallet
- [ ] Small-account details: exchange minimum order sizes per coin, fees (about 0.1% round trip, which is large relative to a small account at leverage), funding payments, all included in every trade decision
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
