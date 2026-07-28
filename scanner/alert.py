"""Alert formatting and publication.

Alerts are published into the routine's own session transcript, not to an
outside chat. The push notification for the run is the doorway; tapping it
opens the session, where the full assessment and the research behind it are
readable and can be discussed with Claude directly.

Message layout is fixed by Luke's spec: the tastytrade chain row fields, plus
breakeven and the direction (buy/sell, call/put).

Breakeven uses the side of the spread you would actually get filled on, never
the mid: ask for buys, bid for sells. An optimistic breakeven is a lie.
"""

import datetime as dt
from typing import Optional

from . import store
from .tasty import Contract

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

    lines = [
        f"{contract.underlying}  {direction}  {exp} ({contract.dte}d)",
        f"Strike {_strike(contract.strike)}          Δ {_fmt(contract.delta, '{:.4f}')}",
        f"Last {_fmt(contract.last)}          Chg {_fmt(contract.change)}",
        f"Bid {_fmt(contract.bid)} / Ask {_fmt(contract.ask)}   Mid {_fmt(contract.mid)}",
        f"Vol {_fmt(contract.volume, '{:.0f}')}    OI {_fmt(contract.open_interest, '{:.0f}')}    IV {iv_pct}",
        be_line,
    ]

    # Futures options are quoted in index points, so the quote alone does not
    # say what the trade costs -- 12.00 is $60 on MES and $24 on MNQ. Only added
    # when the multiplier is not the equity default, so the approved equity
    # alert layout is unchanged.
    if contract.multiplier != 100.0 and contract.cost_usd is not None:
        cost = f"Cost ${contract.cost_usd:,.2f}/contract  (${contract.multiplier:g}/pt)"
        if direction.startswith("BUY"):
            cost += f"   MAX LOSS ${contract.cost_usd:,.2f}"
        lines.append(cost)

    lines += ["", f"WHY: {rationale}"]
    return "\n".join(lines)


ALERT_BANNER = "=" * 46


def publish(text: str) -> bool:
    """Print the alert so it lands in the routine's session transcript.

    Stdout is the delivery channel now. There is no network hop, so unlike the
    old Telegram send this cannot half-fail: if the scan produced an alert, the
    alert is in the session.
    """
    print(f"\n{ALERT_BANNER}\n{text}\n{ALERT_BANNER}\n")
    return True


def send_if_new(
    contract: Contract,
    direction: str,
    rationale: str,
    spot: Optional[float] = None,
) -> bool:
    """Format, dedupe, publish, and record. True if an alert was published."""
    if store.already_alerted(contract.occ_symbol, direction):
        print(f"[alert] skipping duplicate {contract.occ_symbol} {direction} (alerted within 48h)")
        return False

    text = format_alert(contract, direction, rationale, spot)
    publish(text)

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
