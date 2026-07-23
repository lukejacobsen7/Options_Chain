"""Telegram alert formatting and delivery.

Message layout is fixed by Luke's spec: the tastytrade chain row fields, plus
breakeven and the direction (buy/sell, call/put).

Breakeven uses the side of the spread you would actually get filled on, never
the mid: ask for buys, bid for sells. An optimistic breakeven is a lie.
"""

import datetime as dt
from typing import Optional

import requests

from . import config, store
from .tasty import Contract

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

BUY_CALL = "BUY CALL"
BUY_PUT = "BUY PUT"
SELL_CALL = "SELL CALL"
SELL_PUT = "SELL PUT"
DIRECTIONS = (BUY_CALL, BUY_PUT, SELL_CALL, SELL_PUT)


def breakeven(contract: Contract, direction: str) -> Optional[float]:
    """Breakeven at the fillable price."""
    if direction == BUY_CALL:
        return None if contract.ask is None else contract.strike + contract.ask
    if direction == BUY_PUT:
        return None if contract.ask is None else contract.strike - contract.ask
    if direction == SELL_CALL:
        return None if contract.bid is None else contract.strike + contract.bid
    if direction == SELL_PUT:
        return None if contract.bid is None else contract.strike - contract.bid
    raise ValueError(f"unknown direction: {direction}")


def _fmt(value, spec="{:.2f}", dash="-"):
    return dash if value is None else spec.format(value)


def _strike(value) -> str:
    """Strikes render bare: 12 not 12.00, 12.5 not 12.50."""
    if value is None:
        return "-"
    return f"{value:.10g}"


def format_alert(
    contract: Contract,
    direction: str,
    rationale: str,
    spot: Optional[float] = None,
) -> str:
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction: {direction}")

    be = breakeven(contract, direction)
    be_line = f"Breakeven ${_fmt(be)}"
    if be is not None and spot:
        move = (be / spot - 1) * 100
        be_line += f" ({move:+.1f}% from spot ${spot:.2f})"

    exp = contract.expiration.strftime("%b %-d '%y")
    iv_pct = "-" if contract.iv is None else f"{contract.iv * 100:.2f}%"

    return "\n".join(
        [
            f"{contract.underlying}  {direction}  {exp} ({contract.dte}d)",
            f"Strike {_strike(contract.strike)}          Δ {_fmt(contract.delta, '{:.4f}')}",
            f"Last {_fmt(contract.last)}          Chg {_fmt(contract.change)}",
            f"Bid {_fmt(contract.bid)} / Ask {_fmt(contract.ask)}   Mid {_fmt(contract.mid)}",
            f"Vol {_fmt(contract.volume, '{:.0f}')}    OI {_fmt(contract.open_interest, '{:.0f}')}    IV {iv_pct}",
            be_line,
            "",
            f"WHY: {rationale}",
        ]
    )


def send(text: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        print("[alert] telegram not configured, printing instead:\n" + text)
        return False
    try:
        resp = requests.post(
            TELEGRAM_URL.format(token=config.TELEGRAM_BOT_TOKEN),
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[alert] telegram send failed: {exc}")
        return False


def send_if_new(
    contract: Contract,
    direction: str,
    rationale: str,
    spot: Optional[float] = None,
) -> bool:
    """Format, dedupe, send, and record. Returns True if a message went out."""
    if store.already_alerted(contract.occ_symbol, direction):
        print(f"[alert] skipping duplicate {contract.occ_symbol} {direction}")
        return False

    text = format_alert(contract, direction, rationale, spot)
    if not send(text):
        return False

    store.record_alert(
        {
            "occ_symbol": contract.occ_symbol,
            "ticker": contract.underlying,
            "direction": direction,
            "strike": contract.strike,
            "expiration": contract.expiration.isoformat(),
            "sent_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "iv": contract.iv,
            "delta": contract.delta,
            "breakeven": breakeven(contract, direction),
            "rationale": rationale,
        }
    )
    return True
