"""tastytrade data access: option chains, greeks, quotes, and daily candles.

Quotes and greeks arrive over the DXLink websocket, not REST. The pattern here
is snapshot-and-close: subscribe, collect until we have what we asked for or a
timeout fires, then drop the connection. A routine run is short-lived, so there
is no reason to hold a stream open.
"""

import asyncio
import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tastytrade import DXLinkStreamer, Session
from tastytrade.dxfeed import Candle, Greeks, Quote, Summary, Trade
from tastytrade.instruments import get_option_chain

from . import config

# How long to wait for a batch of streamer events before giving up.
STREAM_TIMEOUT_S = 25.0
# dxfeed rejects very large subscribe batches; chunk them.
SUB_CHUNK = 400


@dataclass
class Contract:
    """One option contract with everything the alert format needs."""

    underlying: str
    streamer_symbol: str
    occ_symbol: str
    expiration: dt.date
    strike: float
    option_type: str  # "C" or "P"

    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    prev_close: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    iv: Optional[float] = None  # decimal, e.g. 0.9311
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None

    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def spread_pct(self) -> Optional[float]:
        m, s = self.mid, self.spread
        if not m or s is None or m <= 0:
            return None
        return s / m

    @property
    def change(self) -> Optional[float]:
        if self.last is None or self.prev_close is None:
            return None
        return self.last - self.prev_close

    @property
    def dte(self) -> int:
        return (self.expiration - dt.date.today()).days

    def passes_liquidity(self) -> bool:
        if (self.open_interest or 0) < config.MIN_OPEN_INTEREST:
            return False
        if (self.volume or 0) < config.MIN_VOLUME:
            return False
        if (self.bid or 0) < config.MIN_ABS_BID:
            return False
        sp = self.spread_pct
        if sp is None or sp > config.MAX_SPREAD_PCT_OF_MID:
            return False
        return True


def make_session() -> Session:
    """OAuth session. Both values come from the cloud environment."""
    missing = [
        n
        for n, v in (
            ("TASTY_CLIENT_SECRET", config.TASTY_CLIENT_SECRET),
            ("TASTY_REFRESH_TOKEN", config.TASTY_REFRESH_TOKEN),
        )
        if not v
    ]
    if missing:
        raise RuntimeError(f"missing tastytrade credentials: {', '.join(missing)}")
    return Session(config.TASTY_CLIENT_SECRET, config.TASTY_REFRESH_TOKEN)


async def load_chain(session: Session, underlying: str) -> List[Contract]:
    """Fetch the chain skeleton for one underlying, filtered to our DTE band."""
    chain = await get_option_chain(session, underlying)
    today = dt.date.today()
    out: List[Contract] = []

    for expiration, options in chain.items():
        dte = (expiration - today).days
        if not (config.MIN_DTE <= dte <= config.MAX_DTE):
            continue
        for opt in options:
            out.append(
                Contract(
                    underlying=underlying,
                    streamer_symbol=opt.streamer_symbol,
                    occ_symbol=opt.symbol,
                    expiration=expiration,
                    strike=float(opt.strike_price),
                    option_type="C" if str(opt.option_type).upper().startswith("C") else "P",
                )
            )
    return out


async def _collect(streamer, event_cls, symbols, sink):
    """Subscribe and drain events until every symbol reports or we time out."""
    if not symbols:
        return
    for i in range(0, len(symbols), SUB_CHUNK):
        await streamer.subscribe(event_cls, symbols[i : i + SUB_CHUNK])

    wanted = set(symbols)
    seen = set()
    try:
        async with asyncio.timeout(STREAM_TIMEOUT_S):
            async for event in streamer.listen(event_cls):
                sym = event.event_symbol
                if sym in wanted:
                    sink(sym, event)
                    seen.add(sym)
                if len(seen) >= len(wanted):
                    break
    except (asyncio.TimeoutError, TimeoutError):
        # Partial data is fine. Illiquid strikes often never print a Trade
        # event, and we would rather score what we have than fail the run.
        pass


async def hydrate(session: Session, contracts: List[Contract]) -> List[Contract]:
    """Fill quotes, greeks, volume, and open interest onto contracts in place."""
    by_symbol: Dict[str, Contract] = {c.streamer_symbol: c for c in contracts}
    symbols = list(by_symbol)
    if not symbols:
        return contracts

    def on_quote(sym, e):
        c = by_symbol[sym]
        c.bid = _f(e.bid_price)
        c.ask = _f(e.ask_price)

    def on_greeks(sym, e):
        c = by_symbol[sym]
        c.iv = _f(e.volatility)
        c.delta = _f(e.delta)
        c.gamma = _f(e.gamma)
        c.theta = _f(e.theta)
        c.vega = _f(e.vega)
        if c.last is None:
            c.last = _f(e.price)

    def on_summary(sym, e):
        c = by_symbol[sym]
        c.open_interest = _f(getattr(e, "open_interest", None))
        c.prev_close = _f(getattr(e, "prev_day_close_price", None))

    def on_trade(sym, e):
        c = by_symbol[sym]
        c.volume = _f(getattr(e, "day_volume", None))
        price = _f(getattr(e, "price", None))
        if price:
            c.last = price

    async with DXLinkStreamer(session) as streamer:
        await _collect(streamer, Quote, symbols, on_quote)
        await _collect(streamer, Greeks, symbols, on_greeks)
        await _collect(streamer, Summary, symbols, on_summary)
        await _collect(streamer, Trade, symbols, on_trade)

    return contracts


async def daily_closes(session: Session, underlying: str, lookback_days: int = 90) -> List[float]:
    """Daily closes for the underlying, oldest first. Used for realized vol."""
    start = dt.datetime.now() - dt.timedelta(days=lookback_days * 2)  # calendar pad
    closes: List[tuple] = []

    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe_candle([underlying], interval="1d", start_time=start)
        try:
            async with asyncio.timeout(STREAM_TIMEOUT_S):
                async for candle in streamer.listen(Candle):
                    close = _f(candle.close)
                    if close:
                        closes.append((candle.time, close))
                    if len(closes) >= lookback_days + 5:
                        break
        except (asyncio.TimeoutError, TimeoutError):
            pass

    closes.sort(key=lambda t: t[0])
    return [c for _, c in closes]


async def historical_iv(session: Session, underlying: str, lookback_days: int = 365) -> List[float]:
    """Daily implied vol from dxfeed candles, oldest first.

    dxfeed populates `imp_volatility` on daily candles for many underlyings. If
    it comes back for our names, IV rank works from the first run instead of
    waiting for Supabase to bank two weeks of snapshots. If it comes back empty
    the scanner falls back to banked history, so this is upside only.
    """
    start = dt.datetime.now() - dt.timedelta(days=int(lookback_days * 1.5))
    points: List[tuple] = []

    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe_candle([underlying], interval="1d", start_time=start)
        try:
            async with asyncio.timeout(STREAM_TIMEOUT_S):
                async for candle in streamer.listen(Candle):
                    iv = _f(getattr(candle, "imp_volatility", None))
                    if iv:
                        points.append((candle.time, iv))
                    if len(points) >= lookback_days:
                        break
        except (asyncio.TimeoutError, TimeoutError):
            pass

    points.sort(key=lambda t: t[0])
    return [iv for _, iv in points]


async def underlying_quote(session: Session, symbols: List[str]) -> Dict[str, float]:
    """Current mid price for each underlying."""
    out: Dict[str, float] = {}

    def on_quote(sym, e):
        bid, ask = _f(e.bid_price), _f(e.ask_price)
        if bid and ask:
            out[sym] = (bid + ask) / 2

    async with DXLinkStreamer(session) as streamer:
        await _collect(streamer, Quote, symbols, on_quote)
    return out


def _f(value) -> Optional[float]:
    """Coerce to float, treating None/NaN/garbage as missing."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f
