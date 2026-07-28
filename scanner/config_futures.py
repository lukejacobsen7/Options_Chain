"""Futures-options profile: MES / MNQ / M2K.

The equity scanner and this one want genuinely different thresholds, not the
same numbers pointed at different symbols. Three things drive that:

  - **Expiries are days, not weeks.** Micro E-mini options list roughly two
    weeks of Mon-Thu expiries. A 14-60 DTE band matches nothing.
  - **Liquidity is an order of magnitude thinner.** SPY strikes carry five-
    figure open interest; an MES weekly strike a few hundred. Reusing
    MIN_OPEN_INTEREST=250 would gate away the entire product.
  - **There are no earnings.** Index futures have macro catalysts (FOMC, CPI,
    NFP) that no earnings calendar knows about, so the blackout is off and the
    judgment step in the routine prompt has to carry that load instead.

`apply()` mutates the shared `config` module in place. Every consumer reads
`config.X` at call time rather than importing the constants, so overriding the
module is enough and signals.py / alert.py / store.py stay untouched.
"""

from . import config

# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
# tastytrade addresses futures products with a leading slash. Keep this list
# short: each product is a chain pull, and unlike equities there is no long
# tail worth scanning.
WATCHLIST = ["/MES", "/MNQ", "/M2K"]

# Dollars per index point, per contract. This is what turns a quoted premium
# into "what does this actually cost me", which is the number that matters on a
# small account. MES and M2K are $5; MNQ is $2.
MULTIPLIERS = {
    "/MES": 5.0,
    "/MNQ": 2.0,
    "/M2K": 5.0,
    "/MYM": 0.50,
}

# Human labels for the alert summary.
PRODUCT_NAMES = {
    "/MES": "Micro E-mini S&P 500",
    "/MNQ": "Micro E-mini Nasdaq-100",
    "/M2K": "Micro E-mini Russell 2000",
    "/MYM": "Micro E-mini Dow",
}

# Only three liquid members, but they are a real cohort: all US equity index,
# all micro-sized, all driven by the same macro tape. If MES IV sits well under
# the group median, that is a genuine relative signal rather than noise.
PEER_GROUPS = {
    "index_micros": ["/MES", "/MNQ", "/M2K"],
}

# ---------------------------------------------------------------------------
# Liquidity gate
# ---------------------------------------------------------------------------
# Deliberately looser than equities. These still have to be real markets -- a
# 20% spread on a $60 option is $12 of instant drag -- but holding them to SPY
# thresholds returns an empty scan every single run.
MIN_OPEN_INTEREST = 25
MIN_VOLUME = 5
MAX_SPREAD_PCT_OF_MID = 0.20
# MES ticks at 0.25 index points below 5.00, so a 0.25 bid is one tick. Below
# that there is no market, just a quote.
MIN_ABS_BID = 0.25

# ---------------------------------------------------------------------------
# Expiry / strike selection
# ---------------------------------------------------------------------------
# Two weeks of Mon-Thu expiries is roughly what CME lists. Anything under 1 DTE
# is same-day gamma, which is not what this screen is for.
MIN_DTE = 1
MAX_DTE = 21

# Slightly wider than the equity band. On a 3-DTE contract a 0.15 delta is
# already far out; allowing 0.10 keeps the cheap tail in scope where the whole
# point is a small premium with a defined floor.
MIN_ABS_DELTA = 0.10
MAX_ABS_DELTA = 0.65

# ---------------------------------------------------------------------------
# Cheapness thresholds
# ---------------------------------------------------------------------------
# Index vol mean-reverts harder and trades in a tighter band than single names,
# so the cheap/rich cutoffs sit closer to 1.0. A 0.90 ratio that is unremarkable
# on NVDA is a real signal on MES.
IV_HV_CHEAP = 0.95
IV_HV_RICH = 1.25

# Term structure in vol points. Index term structure is normally upward sloping,
# so front-cheap-vs-back is the base case and the bar has to be higher than the
# equity -4.0 to mean anything.
TERM_CHEAP_POINTS = -6.0

# Equity index carries persistent put skew -- that is the resting state, not
# news. The threshold is well above the equity 8.0 so ordinary skew does not
# read as "calls are cheap" on every single run.
SKEW_CALL_CHEAP_POINTS = 12.0

IV_RANK_CHEAP = 25.0
MIN_HISTORY_DAYS_FOR_RANK = 15

# No earnings on an index. Zero disables the blackout entirely; macro catalysts
# are handled by judgment in the routine prompt, not by a calendar lookup.
EARNINGS_BLACKOUT_DAYS = 0

# ---------------------------------------------------------------------------
# Run size controls
# ---------------------------------------------------------------------------
# Three products instead of 37 tickers, and short-dated chains are small, so the
# band can be tight without starving the scan. Strikes beyond +/-8% of spot on a
# 3-DTE contract are outside the delta band anyway.
MONEYNESS_BAND = 0.08
SCAN_CONCURRENCY = 3
MAX_CANDIDATES = 3
MAX_CONTRACTS_PER_CANDIDATE = 14
MAX_HEADLINES_PER_CANDIDATE = 6

# Overriding these names on the shared config module is what switches profiles.
# Secrets and Supabase settings are deliberately absent: those stay shared.
_OVERRIDES = (
    "WATCHLIST",
    "PEER_GROUPS",
    "MIN_OPEN_INTEREST",
    "MIN_VOLUME",
    "MAX_SPREAD_PCT_OF_MID",
    "MIN_ABS_BID",
    "MIN_DTE",
    "MAX_DTE",
    "MIN_ABS_DELTA",
    "MAX_ABS_DELTA",
    "IV_HV_CHEAP",
    "IV_HV_RICH",
    "TERM_CHEAP_POINTS",
    "SKEW_CALL_CHEAP_POINTS",
    "IV_RANK_CHEAP",
    "MIN_HISTORY_DAYS_FOR_RANK",
    "EARNINGS_BLACKOUT_DAYS",
    "MONEYNESS_BAND",
    "SCAN_CONCURRENCY",
    "MAX_CANDIDATES",
    "MAX_CONTRACTS_PER_CANDIDATE",
    "MAX_HEADLINES_PER_CANDIDATE",
)


def apply() -> None:
    """Point the shared config module at the futures thresholds.

    Call this before importing anything that reads config, and before the first
    scan. It is idempotent.
    """
    here = globals()
    for name in _OVERRIDES:
        setattr(config, name, here[name])


def multiplier(product: str) -> float:
    """Dollars per index point. Defaults to 1.0 for anything unmapped."""
    return MULTIPLIERS.get(product, 1.0)


def product_name(product: str) -> str:
    return PRODUCT_NAMES.get(product, product)
