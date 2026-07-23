"""Cheapness signals.

Four of these need no history and work on day one:
  1. IV vs realized volatility
  2. Term structure (front vs back expiry)
  3. Skew (25-delta put IV vs 25-delta call IV)
  4. Peer-relative IV within a cohort

IV rank needs banked history and stays None until Supabase has enough days.
"""

import datetime as dt
import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config
from .tasty import Contract


# ---------------------------------------------------------------------------
# Realized volatility
# ---------------------------------------------------------------------------

def realized_vol(closes: List[float], window: int) -> Optional[float]:
    """Annualized close-to-close realized vol over the last `window` returns."""
    if len(closes) < window + 1:
        return None
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(len(closes) - window, len(closes))
        if closes[i - 1] > 0
    ]
    if len(rets) < 2:
        return None
    return statistics.stdev(rets) * math.sqrt(252)


def hv_profile(closes: List[float]) -> Dict[str, Optional[float]]:
    return {f"hv{w}": realized_vol(closes, w) for w in config.HV_WINDOWS}


# ---------------------------------------------------------------------------
# ATM / delta-targeted lookups
# ---------------------------------------------------------------------------

def atm_iv(contracts: List[Contract], expiration: dt.date) -> Optional[float]:
    """IV of the contract closest to 50 delta for a given expiration."""
    pool = [
        c for c in contracts
        if c.expiration == expiration and c.iv and c.delta is not None
    ]
    if not pool:
        return None
    nearest = min(pool, key=lambda c: abs(abs(c.delta) - 0.50))
    return nearest.iv


def iv_at_delta(
    contracts: List[Contract],
    expiration: dt.date,
    option_type: str,
    target_delta: float,
) -> Optional[float]:
    pool = [
        c for c in contracts
        if c.expiration == expiration
        and c.option_type == option_type
        and c.iv
        and c.delta is not None
    ]
    if not pool:
        return None
    nearest = min(pool, key=lambda c: abs(abs(c.delta) - target_delta))
    return nearest.iv


def expirations(contracts: List[Contract]) -> List[dt.date]:
    return sorted({c.expiration for c in contracts})


# ---------------------------------------------------------------------------
# Structural signals
# ---------------------------------------------------------------------------

def term_structure(contracts: List[Contract]) -> Optional[float]:
    """Front ATM IV minus back ATM IV, in vol points.

    Negative means the front is cheap relative to the back, which is the
    unusual direction and worth a look.
    """
    exps = expirations(contracts)
    if len(exps) < 2:
        return None
    front, back = atm_iv(contracts, exps[0]), atm_iv(contracts, exps[-1])
    if front is None or back is None:
        return None
    return (front - back) * 100


def skew(contracts: List[Contract], expiration: dt.date) -> Optional[float]:
    """25-delta put IV minus 25-delta call IV, in vol points.

    Large positive means puts carry a big premium, so calls are the relatively
    cheap side even when the name looks expensive outright.
    """
    put_iv = iv_at_delta(contracts, expiration, "P", 0.25)
    call_iv = iv_at_delta(contracts, expiration, "C", 0.25)
    if put_iv is None or call_iv is None:
        return None
    return (put_iv - call_iv) * 100


def peer_relative(ticker: str, atm_by_ticker: Dict[str, float]) -> Optional[float]:
    """This name's ATM IV minus the median of its peer group, in vol points."""
    group = None
    for _, members in config.PEER_GROUPS.items():
        if ticker in members:
            group = members
            break
    if not group:
        return None
    peers = [atm_by_ticker[t] for t in group if t != ticker and t in atm_by_ticker]
    if len(peers) < 2:
        return None
    own = atm_by_ticker.get(ticker)
    if own is None:
        return None
    return (own - statistics.median(peers)) * 100


def iv_rank(current_iv: float, history: List[float]) -> Optional[float]:
    """Percentile of current IV within banked history. None until enough days."""
    if len(history) < config.MIN_HISTORY_DAYS_FOR_RANK:
        return None
    lo, hi = min(history), max(history)
    if hi <= lo:
        return None
    return (current_iv - lo) / (hi - lo) * 100


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class TickerSignals:
    ticker: str
    spot: Optional[float] = None
    atm_iv: Optional[float] = None
    hv: Dict[str, Optional[float]] = field(default_factory=dict)
    iv_hv_ratio: Optional[float] = None
    term_points: Optional[float] = None
    skew_points: Optional[float] = None
    peer_points: Optional[float] = None
    iv_rank: Optional[float] = None
    earnings_date: Optional[dt.date] = None
    price_change_pct_1d: Optional[float] = None
    price_change_pct_5d: Optional[float] = None
    reasons: List[str] = field(default_factory=list)

    @property
    def in_earnings_blackout(self) -> bool:
        if not self.earnings_date:
            return False
        days = (self.earnings_date - dt.date.today()).days
        return abs(days) <= config.EARNINGS_BLACKOUT_DAYS

    @property
    def cheap_score(self) -> float:
        """Rough 0-100 cheapness score. Ordering heuristic, not a verdict.

        The model makes the actual call using news context. This only decides
        what gets looked at.
        """
        score = 0.0
        if self.iv_hv_ratio is not None:
            if self.iv_hv_ratio < config.IV_HV_CHEAP:
                score += min(40, (config.IV_HV_CHEAP - self.iv_hv_ratio) * 120)
            elif self.iv_hv_ratio > config.IV_HV_RICH:
                score -= min(30, (self.iv_hv_ratio - config.IV_HV_RICH) * 60)
        if self.term_points is not None and self.term_points < config.TERM_CHEAP_POINTS:
            score += min(20, abs(self.term_points - config.TERM_CHEAP_POINTS) * 2)
        if self.skew_points is not None and self.skew_points > config.SKEW_CALL_CHEAP_POINTS:
            score += min(15, (self.skew_points - config.SKEW_CALL_CHEAP_POINTS) * 1.5)
        if self.peer_points is not None and self.peer_points < 0:
            score += min(15, abs(self.peer_points) * 1.2)
        if self.iv_rank is not None and self.iv_rank < config.IV_RANK_CHEAP:
            score += min(20, (config.IV_RANK_CHEAP - self.iv_rank) * 0.8)
        return round(max(0.0, score), 1)


def build_signals(
    ticker: str,
    contracts: List[Contract],
    closes: List[float],
    spot: Optional[float],
    atm_by_ticker: Dict[str, float],
    iv_history: List[float],
    earnings_date: Optional[dt.date],
) -> TickerSignals:
    sig = TickerSignals(ticker=ticker, spot=spot, earnings_date=earnings_date)

    exps = expirations(contracts)
    if exps:
        sig.atm_iv = atm_iv(contracts, exps[0])
        sig.skew_points = skew(contracts, exps[0])

    sig.hv = hv_profile(closes)
    sig.term_points = term_structure(contracts)
    sig.peer_points = peer_relative(ticker, atm_by_ticker)

    primary_hv = sig.hv.get(f"hv{config.HV_PRIMARY}")
    if sig.atm_iv and primary_hv:
        sig.iv_hv_ratio = round(sig.atm_iv / primary_hv, 3)

    if sig.atm_iv:
        sig.iv_rank = iv_rank(sig.atm_iv, iv_history)

    if len(closes) >= 2:
        sig.price_change_pct_1d = round((closes[-1] / closes[-2] - 1) * 100, 2)
    if len(closes) >= 6:
        sig.price_change_pct_5d = round((closes[-1] / closes[-6] - 1) * 100, 2)

    # Human-readable rationale the model can quote or override.
    if sig.iv_hv_ratio is not None and sig.iv_hv_ratio < config.IV_HV_CHEAP:
        sig.reasons.append(
            f"IV/HV{config.HV_PRIMARY} = {sig.iv_hv_ratio}: options pricing less "
            f"movement than the stock has delivered"
        )
    if sig.term_points is not None and sig.term_points < config.TERM_CHEAP_POINTS:
        sig.reasons.append(
            f"term structure {sig.term_points:+.1f} pts: front cheap vs back"
        )
    if sig.skew_points is not None and sig.skew_points > config.SKEW_CALL_CHEAP_POINTS:
        sig.reasons.append(
            f"skew {sig.skew_points:+.1f} pts: puts bid up, calls the cheaper side"
        )
    if sig.peer_points is not None and sig.peer_points < 0:
        sig.reasons.append(
            f"{abs(sig.peer_points):.1f} vol pts below peer median"
        )
    if sig.iv_rank is not None and sig.iv_rank < config.IV_RANK_CHEAP:
        sig.reasons.append(f"IV rank {sig.iv_rank:.0f}")

    return sig


def select_for_model(contracts: List[Contract], limit: int) -> List[Contract]:
    """Pick the contracts worth showing the model.

    Balanced across calls and puts, favouring the 35-delta area where the
    liquidity and the leverage both tend to be. Skew often means only one side
    is cheap, so handing over calls only would prejudge the trade.
    """
    if len(contracts) <= limit:
        return contracts

    def rank(c: Contract) -> float:
        return abs(abs(c.delta) - 0.35)

    calls = sorted([c for c in contracts if c.option_type == "C"], key=rank)
    puts = sorted([c for c in contracts if c.option_type == "P"], key=rank)

    out: List[Contract] = []
    while len(out) < limit and (calls or puts):
        if calls:
            out.append(calls.pop(0))
        if len(out) < limit and puts:
            out.append(puts.pop(0))
    return out[:limit]


def eligible_contracts(contracts: List[Contract]) -> List[Contract]:
    """Liquidity gate plus the delta band. Runs before anything expensive."""
    out = []
    for c in contracts:
        if c.delta is None or not c.passes_liquidity():
            continue
        if not (config.MIN_ABS_DELTA <= abs(c.delta) <= config.MAX_ABS_DELTA):
            continue
        out.append(c)
    return out
