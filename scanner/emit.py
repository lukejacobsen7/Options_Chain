"""Publish one alert into the session. Called by the routine's model step.

Usage:
    python -m scanner.emit --contract '<json from the candidate bundle>' \
        --ticker MU --direction "BUY CALL" --spot 995.94 \
        --rationale "Stock ripped 3.8% on the HBM headline but front-month IV
                     never moved; calls are pricing a quieter tape than the
                     stock is actually delivering."

The contract JSON is copied verbatim out of run.py's output, so the alert
reflects the prices the scan actually saw. If the run is more than a few
minutes old, pass --refresh to re-pull the quote first.

The alert prints to stdout, so it becomes part of the routine's session
transcript. Tapping the run's push notification opens that transcript.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys

from . import alert
from .tasty import Contract, hydrate, make_session


def contract_from_payload(ticker: str, payload: dict) -> Contract:
    c = Contract(
        underlying=ticker,
        streamer_symbol=payload["streamer_symbol"],
        occ_symbol=payload["occ_symbol"],
        expiration=dt.date.fromisoformat(payload["expiration"]),
        strike=float(payload["strike"]),
        option_type=payload["type"],
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
    # `change` is derived from prev_close, so reconstruct it from the payload.
    if payload.get("change") is not None and c.last is not None:
        c.prev_close = c.last - payload["change"]
    return c


async def refresh(contract: Contract) -> Contract:
    session = make_session()
    await hydrate(session, [contract])
    return contract


def main():
    parser = argparse.ArgumentParser(description="Publish one options alert")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--contract", required=True, help="contract JSON from run.py")
    parser.add_argument("--direction", required=True, choices=list(alert.DIRECTIONS))
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--spot", type=float, default=None)
    parser.add_argument("--refresh", action="store_true", help="re-pull quote first")
    parser.add_argument("--dry-run", action="store_true", help="print without deduping or recording")
    args = parser.parse_args()

    try:
        payload = json.loads(args.contract)
    except json.JSONDecodeError as exc:
        print(f"bad --contract JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    contract = contract_from_payload(args.ticker, payload)

    if args.refresh:
        contract = asyncio.run(refresh(contract))
        if not contract.passes_liquidity():
            print("refreshed quote no longer passes the liquidity gate, dropping this one")
            sys.exit(0)

    if args.dry_run:
        print(alert.format_alert(contract, args.direction, args.rationale, args.spot))
        return

    published = alert.send_if_new(contract, args.direction, args.rationale, args.spot)
    print("published" if published else "not published (duplicate within 48h)")


if __name__ == "__main__":
    main()
