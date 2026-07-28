"""Publish one futures-options alert. Called by the futures routine's model step.

Same job as `scanner.emit`, with two deliberate differences:

  - **Buy only.** `BUY CALL` and `BUY PUT` are the whole menu. Selling options
    on futures is undefined risk, and this account is explicitly a defined-risk
    account, so the constraint is enforced here rather than trusted to a prompt.
  - **Carries the multiplier**, so the alert prints what the contract actually
    costs in dollars and states the max loss.

Usage:
    python -m scanner.emit_futures --product /MES \
        --contract '<json from the candidate bundle>' \
        --direction "BUY PUT" --spot 7411.98 \
        --rationale "MES front-week IV is 11.2 against 20d realized of 14.8 ..."
"""

import argparse
import asyncio
import datetime as dt
import json
import sys

from . import config_futures

config_futures.apply()  # noqa: E402

from . import alert  # noqa: E402
from .tasty import Contract, hydrate, make_session  # noqa: E402

# Defined risk only. Deliberately narrower than alert.DIRECTIONS.
BUY_ONLY = (alert.BUY_CALL, alert.BUY_PUT)


def contract_from_payload(product: str, payload: dict) -> Contract:
    c = Contract(
        underlying=product,
        streamer_symbol=payload["streamer_symbol"],
        occ_symbol=payload["occ_symbol"],
        expiration=dt.date.fromisoformat(payload["expiration"]),
        strike=float(payload["strike"]),
        option_type=payload["type"],
        multiplier=float(payload.get("multiplier") or config_futures.multiplier(product)),
        root_symbol=payload.get("root_symbol"),
        exercise_style=payload.get("exercise_style"),
        settlement_type=payload.get("settlement_type"),
    )
    c.bid = payload.get("bid")
    c.ask = payload.get("ask")
    c.last = payload.get("last")
    c.volume = payload.get("volume")
    c.open_interest = payload.get("open_interest")
    c.iv = payload.get("iv")
    c.delta = payload.get("delta")
    c.theta = payload.get("theta")
    c.vega = payload.get("vega")
    if payload.get("change") is not None and c.last is not None:
        c.prev_close = c.last - payload["change"]
    return c


async def refresh(contract: Contract) -> Contract:
    session = make_session()
    await hydrate(session, [contract])
    return contract


def main():
    parser = argparse.ArgumentParser(description="Publish one futures options alert")
    parser.add_argument("--product", required=True, help="e.g. /MES")
    parser.add_argument("--contract", required=True, help="contract JSON from run_futures.py")
    parser.add_argument("--direction", required=True, choices=list(BUY_ONLY))
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--spot", type=float, default=None)
    parser.add_argument("--refresh", action="store_true", help="re-pull quote first")
    parser.add_argument("--max-cost", type=float, default=None,
                        help="drop the alert if one contract costs more than this many dollars")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        payload = json.loads(args.contract)
    except json.JSONDecodeError as exc:
        print(f"bad --contract JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    contract = contract_from_payload(args.product, payload)

    if args.refresh:
        contract = asyncio.run(refresh(contract))
        if not contract.passes_liquidity():
            print("refreshed quote no longer passes the liquidity gate, dropping this one")
            sys.exit(0)

    # An idea that does not fit the account is not an idea. Checked after the
    # refresh so it uses the live ask, not the one the scan happened to catch.
    if args.max_cost is not None:
        cost = contract.cost_usd
        if cost is None:
            print("no ask available, cannot price this contract, dropping")
            sys.exit(0)
        if cost > args.max_cost:
            print(f"contract costs ${cost:,.2f}, over the ${args.max_cost:,.2f} cap, dropping")
            sys.exit(0)

    if args.dry_run:
        print(alert.format_alert(contract, args.direction, args.rationale, args.spot))
        return

    published = alert.send_if_new(contract, args.direction, args.rationale, args.spot)
    print("published" if published else "not published (duplicate within 48h)")


if __name__ == "__main__":
    main()
