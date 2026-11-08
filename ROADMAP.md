# Build board

Guiding principles: **one judge** (the referee) makes every decision; everything else feeds it as evidence.
**Fluidity:** no hard-coded numbers, every setting is learned and re-tuned from live evidence.
**Every addition is a challenger:** a cloned judge with the new ability is scored on the same live cases and takes over only if it is more accurate **and** finds at least as many Grade A trades.

## Status
- [x] Stage 1: referee account, ~50 coins, exams, exchange-style UI, health report
- [x] Stage 2: spot vs futures, Hyperliquid, self-adapting referee, live scorecard, post-mortems, monthly report
- [x] Render relay (main) + Vercel (backup), Vercel/cron/Upstash clean-up
- [ ] Baseline: ~2 clean nights + ~40 scored calls (expected Tue/Wed)

## Next build (after the baseline)
- [ ] **Challenger system**: cloned judge in shadow; promotion = more accurate + at least as many Grade A + no worse calibration; "upgrade under test" line on the dashboard
- [ ] **Fluid pacing**: learned trade pace (no hard 1/day or 7/week), several trades a day allowed, weekly pace by count **and** profit, surplus carries into next week, new top tier **Grade S**
- [ ] **Fluid risk**: total open-risk cap, same-bet (correlation) guard, daily and weekly brakes, all learned and slow-moving
- [ ] **Re-judge open trades**: every hour ask "would I open this now?"; graded response (tighten / partial / close / close-and-flip with its own grade); confirmation rule against whipsaws (tuned by the scorecard); portfolio net-exposure view; cut losers whose reason vanished, give locked winners rope
- [ ] **Runners**: learned runner targets (+6-10% when evidence supports), trail width by evidence
- [ ] **Honest timing**: paper fills at the real price at decision time (not the hour's open)
- [ ] **Account clean-up**: remove team + all-in accounts; keep best-rule account
- [ ] **Live lab**: every champion paper-traded in shadow; best-rule account picks its champion by live record
- [ ] **Rules feed the judge**: fair witnesses (votes only after discovery date), rules propose / judge approves (meta-labelling), market sensor (which rule styles win now = choppy vs trending)
- [ ] **Research aimed at the goal**: favour rules whose wins are >= +2%
- [ ] **Outside world**: Nasdaq, S&P, NVIDIA, chip stocks, VIX, dollar, 10y yield (free daily); risk-on/off regime; sectors (AI, memes, L1, DeFi, exchange) + sector vs stock twin; macro calendar (Fed, CPI, jobs) + no new trades just before; news events (GDELT) tagged by AI + memory of similar past events; check 24/7 stock perps for hourly stock signal
- [ ] **On-chain (free)**: exchange flows, stablecoin flows per chain (Coin Metrics community, DefiLlama)
- [ ] **Relay resilience**: if both relays fail, manage open positions from spot prices (no new trades on incomplete data); look for a second free Frankfurt host that Binance does not block
- [ ] **Coin-specific trust**: the judge learns its own record per coin (memes included) and demands more evidence where weak

## Build after (accuracy upgrades, each as a challenger)
- [ ] Train on every hour (3x examples)
- [ ] Panel of 3 differently-trained judges (majority verdict)
- [ ] Three distances: 1h / 4h / daily views must not contradict
- [ ] Market states: hidden-Markov regimes + change-point detection; a specialist judge per state (may bring longs back)
- [ ] Daily witness reliability (multiplicative weights / Hedge)
- [ ] Lead-lag network (which coins/markets move first; later stocks -> crypto)
- [ ] Conformal prediction (honest certainty for Grade S)
- [ ] Split-jury doubt (disagreement raises the bar)
- [ ] Entry timing: wait up to ~3h for a better price or skip
- [ ] Fluid stops: per-coin wick depth, close-based stops, time stops, wider-stop-smaller-size; permanent disaster stop
- [ ] Pre-mortem stop + trapped-trader levels (from open interest) for targets and stops
- [ ] Appeals court: classify losses (wrong read / timing / noise) and adjust trust
- [ ] Best case of the day (bar eases through the day)
- [ ] Runner modelling (survival models), exit choice per market state (bandits)
- [ ] Shape matching (matrix profile) for the similar-moments witness
- [ ] Research engine: branch-and-bound / A* style search
- [ ] Champion conditions become new measurements (feature factory); log judge-vs-rule disagreements; exit lessons from the live lab
- [ ] Universe 70-80 coins with a liquidity filter (as a challenger); judge still trades the most liquid ~40 of the day

## Later / only if the evidence asks for it
- [ ] Liquidation + order-book recording via the Render relay
- [ ] Bybit / OKX positioning
- [ ] Deep-learning judge as a challenger
- [ ] Paid data only through the **self-funded upgrade budget**: share of real profits, value measured on free samples first, proposal with expected gain vs cost and payback, auto-recommend cancel if it does not pay

## Real-money stage (after >= 1 month of paper AND the gate)
Gate: Grade A about daily (fluid), live hit rate near prediction over 25+ calls, >= 1 month of paper.
- [ ] Binance USDT-M futures on your own verified account; API key: futures only, no withdrawals, locked to a small EU server (~$5/month, paid from profits)
- [ ] Judge sets margin mode (isolated default, cross when evidence supports and futures wallet holds only the trading budget), leverage per trade (liquidation >= ~2.5x the stop distance), size by risk
- [ ] Stop-loss + take-profit placed on the exchange with every entry; disaster stop always
- [ ] Rollout: you confirm -> small automatic -> full automatic
- [ ] Paper vs real reconciliation (slippage, fees, funding); size shrinks automatically if reality is worse
- [ ] Heartbeat alert (e.g. email) if runs stop while positions are open; extra :47 position check
- [ ] "Never trade" list for the real account (your risk preference)
- [ ] Real-money checklist card (exact entry / stop / target / size for your amount)

## Housekeeping (you)
- [ ] Create a new Groq key, put it in GitHub secret GROQ, delete the old one
- [ ] Create a new GitHub token for cron-job.org, update the job's Authorization header, delete the old token
- [x] Delete old Vercel projects (btc-trading, btc_vercel_v25_1)
- [ ] Delete old cron jobs (BTC Trading Engine, BTC Brain) and the Upstash BTCLAB database
- [ ] Each morning: download the health report and send it
