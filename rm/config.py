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
DEAD = ("LUNA", "FTT", "SRM")       # collapsed coins: history only, nothing live
