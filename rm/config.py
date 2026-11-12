"""Settings for the Research Machine. Everything else adapts automatically."""
import os
COINS = ("BTC ETH SOL XRP BNB ADA DOGE AVAX LINK LTC DOT BCH TRX UNI ATOM NEAR FIL ETC AAVE ARB OP "
         "SUI APT INJ TIA SEI WLD FET HBAR XLM ALGO ICP VET CRV LDO IMX STX RUNE ZEC 1000PEPE 1000SHIB ORDI WIF JUP ONDO PENDLE KAS "
         "ENA TON TAO HYPE "
         "LUNA FTT SRM").split()   # last 3 collapsed in 2022: kept so rules must survive crashes (no survivorship bias)
START = "2020-09-01"            # positioning data (open interest, long/short ratios) starts here
DATA_DIR = os.environ.get("RM_DATA", "data")       # raw + panel (GitHub cache; rebuilt automatically if lost)
STATE_DIR = os.environ.get("RM_STATE", "state")    # the machine's memory (saved to the 'state' branch, overwritten each run)
DOCS_DIR = os.environ.get("RM_DOCS", "docs")       # the public report page (GitHub Pages)
ARCHIVE = "https://data.binance.vision/data/futures/um"
FEE = 0.001                     # 0.1% of the position per round trip (taker + slippage)
# judging windows (they roll forward as data grows)
HOLDOUT_DAYS = 120              # the newest months: hidden final exam, used once per idea
VALID_DAYS = 365                # the year before that: validation
# funnel thresholds
MIN_DISC_TRADES, DISC_T = 40, 2.5
MIN_VAL_TRADES, VAL_T = 20, 2.0
MIN_HOLD_TRADES = 8
LIVE_REVIEW_TRADES = 30         # after this many live paper trades a champion is reviewed
TESTS_PER_RUN = int(os.environ.get("RM_BUDGET", "1500"))
LLM_IDEAS_PER_RUN = 10
MAX_GENERATION = 2              # mutations of mutations of … at most this deep (protects against curve-fitting)
KEEP_DETAILED_TESTS = 4000      # older dead tests are folded into family summaries (storage stays small)
EXITS = {                       # exit menu: name -> (target, stop, max hours) ; or ("ret", hours)
    "b1_1_24": (0.01, 0.01, 24), "b2_2_48": (0.02, 0.02, 48), "b1_2_24": (0.01, 0.02, 24),
    "b2_1_24": (0.02, 0.01, 24), "b05_15_24": (0.005, 0.015, 24), "b3_3_72": (0.03, 0.03, 72),
    "r4": ("ret", 4), "r24": ("ret", 24), "r72": ("ret", 72),
}

# paper account shown on the dashboard: every trade uses the same margin and leverage
PAPER_MARGIN = 50.0             # $ margin per trade
LEVERAGE = 10                   # 10x  -> a $50 margin controls a $500 position

# live relay (Vercel function in Frankfurt, see relay/). Empty = archive only (about a day behind).
RELAY_URL = os.environ.get("RM_RELAY_URL", "https://btc-relay-two.vercel.app/api/live")
# several relays may be listed (comma-separated, best first): each batch tries them in order, so one host going down changes nothing
RELAY_URLS = [u.strip() for u in os.environ.get("RM_RELAY_URLS", "").split(",") if u.strip()] or ([os.environ["RM_OWN_RELAY"]] if os.environ.get("RM_OWN_RELAY") else []) + [
    "https://research-machine-relay.onrender.com/api/live",      # Render, Frankfurt, free (main)
    RELAY_URL]                                                    # Vercel, Frankfurt, free (backup)
DEAD = ("LUNA", "FTT", "SRM")       # collapsed coins: history only, nothing live

# ── risk limits and the kill switch (human decisions; the learner cannot change them) ──────────────────────────────────
RISK_TRADE_MAX = 0.08        # hard ceiling: no single trade risks more than 8% of the account at its stop (Arbiter: 4%; the 'bold' contestant: 8%)
KILL_DD = 0.30               # kill switch: the Arbiter's account 30% below its peak -> everything closed, stops, waits for a human
CAL_DRIFT_TOL = 0.05         # kill switch: graded calls winning this far below predicted (and beyond chance) over 30+ separate market moves
KILL_RESET = ""              # human reset: set to the current UTC time ("2026-11-20 09:00") to restart after a kill
RULE_DWELL_H = 168           # best-rule account: a selected rule stays at least a week ...
RULE_DWELL_DD = 0.15         # ... unless the account fell 15% from its peak under it
RULE_REPLACE_PP = 0.10       # a replacement must beat it by 0.10 points per trade on unseen trades ...
RULE_REPLACE_N = 50          # ... over at least 50 unseen trades each

DEMO_BUDGET = 500            # the Binance demo uses this much of its futures wallet as "the account" (the rest is never touched)
DEMO_LEV = (20, 30)          # demo experiment: leverage only between 20x and 30x (the highest that keeps liquidation beyond the stop)
DEMO_MARGIN = {"S": 0.30, "A": 0.30, "B": 0.15}   # margin per trade as a share of the demo account, by grade
DEMO_MARGIN_MODE = "CROSSED" # cross margin: the account backs all trades together (a real 500 USDT cross account is checked below)
DEMO_MARGIN_TOTAL = 0.60     # all open demo trades together use at most this share of the account as margin
