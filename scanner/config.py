"""Configuration and tunable thresholds for the options edge scanner."""

import os

# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------
# Luke fills this in. Keep it to 10-30 names: every ticker costs a chain pull
# and the routine has a wall-clock budget.
WATCHLIST = [
    # semis and semi-adjacent
    "NVDA", "MU", "AMD", "INTC", "AVGO", "ASML", "AMAT", "LRCX", "SNDK", "PSI",
    # AI infra / compute
    "CRWV", "NBIS", "IONQ", "PLTR",
    # mega cap tech
    "AMZN", "GOOGL", "IBM", "FFIV",
    # financials / exchanges / crypto
    "C", "PYPL", "COIN", "CBOE", "XLF",
    # energy
    "XOM", "VLO", "XLE", "OIH",
    # autos / industrials / utilities
    "TSLA", "RIVN", "GEV", "XLI", "XLU",
    # broad / other
    "SPY", "RSP", "GLD", "XLV", "SPCX",
]

# Peer groups let a name be scored against its own cohort rather than the
# whole market. A ticker with no group is scored on absolute signals only.
# Groups need at least 3 members for the median to mean anything.
PEER_GROUPS = {
    "semis": ["NVDA", "MU", "AMD", "INTC", "AVGO", "ASML", "AMAT", "LRCX", "SNDK", "PSI"],
    "ai_infra": ["CRWV", "NBIS", "IONQ", "PLTR", "NVDA"],
    "megacap_tech": ["AMZN", "GOOGL", "IBM", "FFIV"],
    "financials": ["C", "PYPL", "COIN", "CBOE", "XLF"],
    "energy": ["XOM", "VLO", "XLE", "OIH"],
    "autos_industrial": ["TSLA", "RIVN", "GEV", "XLI"],
    "broad_etf": ["SPY", "RSP", "XLV", "XLU", "GLD"],
}

# ---------------------------------------------------------------------------
# Liquidity gate
# ---------------------------------------------------------------------------
# Applied BEFORE any cheapness scoring. Thin options screen as "cheap" for the
# same reason they are thin, and the spread eats more than the edge.
MIN_OPEN_INTEREST = 250
MIN_VOLUME = 25
MAX_SPREAD_PCT_OF_MID = 0.10  # 10% of mid
MIN_ABS_BID = 0.10            # ignore near-worthless contracts

# ---------------------------------------------------------------------------
# Expiry / strike selection
# ---------------------------------------------------------------------------
MIN_DTE = 14
MAX_DTE = 60
# Only consider strikes within this absolute delta band. Keeps us off the
# far wings where IV is noisy and fills are bad.
MIN_ABS_DELTA = 0.15
MAX_ABS_DELTA = 0.60

# ---------------------------------------------------------------------------
# Cheapness thresholds
# ---------------------------------------------------------------------------
# IV / realized-vol ratio. Below CHEAP means options are pricing less movement
# than the stock has actually been delivering.
IV_HV_CHEAP = 0.90
IV_HV_RICH = 1.35

# Historical windows for realized vol, in trading days.
HV_WINDOWS = (10, 20, 30)
HV_PRIMARY = 20

# Term structure: front-month IV minus back-month IV, in vol points.
# Strongly negative = front cheap relative to back.
TERM_CHEAP_POINTS = -4.0

# Skew: 25-delta put IV minus 25-delta call IV, in vol points.
SKEW_CALL_CHEAP_POINTS = 8.0   # puts this much richer => calls relatively cheap

# IV rank (needs history in Supabase; None until enough days are banked).
IV_RANK_CHEAP = 25.0
MIN_HISTORY_DAYS_FOR_RANK = 15

# Earnings blackout: skip anything with earnings inside this window. Cheap IV
# ahead of a print is almost never a free lunch, and cheap IV right after one
# is just the crush that already happened.
EARNINGS_BLACKOUT_DAYS = 3

# ---------------------------------------------------------------------------
# Run size controls
# ---------------------------------------------------------------------------
# With ~37 tickers, pulling every strike in the DTE band would mean tens of
# thousands of contracts over the websocket. Filter strikes to a band around
# spot BEFORE subscribing: anything far from the money is outside the delta
# band anyway and would be discarded later.
MONEYNESS_BAND = 0.25  # keep strikes within +/-25% of spot

# Tickers scanned concurrently. dxfeed tolerates this fine and it keeps the
# run inside a sane wall clock; too high and subscriptions start timing out.
SCAN_CONCURRENCY = 6

# What reaches the model. These two numbers set the token cost of a run more
# than anything else, so keep them tight: the model needs enough contracts to
# pick a sensible strike, not the whole surface.
MAX_CANDIDATES = 8
MAX_CONTRACTS_PER_CANDIDATE = 12
MAX_HEADLINES_PER_CANDIDATE = 6

# ---------------------------------------------------------------------------
# Secrets (cloud environment variables, never committed)
# ---------------------------------------------------------------------------
TASTY_CLIENT_SECRET = os.environ.get("TASTY_CLIENT_SECRET", "")
TASTY_REFRESH_TOKEN = os.environ.get("TASTY_REFRESH_TOKEN", "")

ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.environ.get("ALPACA_API_SECRET", "")

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def missing_secrets():
    """Return the names of required env vars that are empty."""
    required = {
        "TASTY_CLIENT_SECRET": TASTY_CLIENT_SECRET,
        "TASTY_REFRESH_TOKEN": TASTY_REFRESH_TOKEN,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_KEY": SUPABASE_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }
    return [k for k, v in required.items() if not v]
