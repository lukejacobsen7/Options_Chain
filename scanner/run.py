"""Entrypoint. Produces the candidate bundle the routine's model step reads.

Deliberate split of labour:
  - this script does all the deterministic work (chains, greeks, vol math,
    liquidity gating, news gathering) and prints JSON
  - the routine prompt has Claude read that JSON, apply judgment about whether
    the price action makes sense against the news, and then call
    `python -m scanner.emit` for the ideas that survive

Nothing here sends an alert. Scanning and deciding stay separate on purpose.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from typing import Dict, List, Optional

from . import config, news, signals, store
from .tasty import (
    Contract,
    daily_closes,
    historical_iv,
    hydrate,
    load_chain,
    make_session,
    underlying_quote,
)


async def scan_ticker(session, ticker: str, spot: Optional[float]) -> Dict:
    contracts: List[Contract] = await load_chain(session, ticker, spot)
    if not contracts:
        return {"ticker": ticker, "error": "no contracts in DTE band"}

    await hydrate(session, contracts)
    closes = await daily_closes(session, ticker)

    return {
        "ticker": ticker,
        "contracts": contracts,
        "closes": closes,
    }


def contract_payload(c: Contract) -> Dict:
    return {
        "occ_symbol": c.occ_symbol,
        "streamer_symbol": c.streamer_symbol,
        "expiration": c.expiration.isoformat(),
        "dte": c.dte,
        "strike": c.strike,
        "type": c.option_type,
        "last": c.last,
        "change": c.change,
        "bid": c.bid,
        "ask": c.ask,
        "mid": c.mid,
        "spread_pct": None if c.spread_pct is None else round(c.spread_pct, 4),
        "volume": c.volume,
        "open_interest": c.open_interest,
        "iv": c.iv,
        "delta": c.delta,
        "theta": c.theta,
        "vega": c.vega,
    }


async def main_async(tickers: List[str], max_candidates: int) -> Dict:
    missing = config.missing_secrets()
    if missing:
        return {"error": f"missing env vars: {', '.join(missing)}"}

    if not store.health_check():
        return {"error": "supabase unreachable (free-tier project may be paused)"}

    session = make_session()

    # Spot first: the moneyness filter in load_chain depends on it, and that
    # filter is what keeps a 37-name watchlist from pulling tens of thousands
    # of contracts over the websocket.
    spots = await underlying_quote(session, tickers)
    missing_spot = [t for t in tickers if t not in spots]
    if missing_spot:
        print(f"[run] no spot for {', '.join(missing_spot)}, full chain pull", file=sys.stderr)

    semaphore = asyncio.Semaphore(config.SCAN_CONCURRENCY)

    async def guarded(ticker: str):
        async with semaphore:
            try:
                return await scan_ticker(session, ticker, spots.get(ticker))
            except Exception as exc:  # noqa: BLE001 - one bad ticker must not kill the run
                print(f"[run] {ticker} failed: {exc}", file=sys.stderr)
                return {"ticker": ticker, "error": str(exc)}

    results = await asyncio.gather(*(guarded(t) for t in tickers))
    raw = {r["ticker"]: r for r in results}

    # ATM IV for every ticker first, since peer-relative needs the whole cohort.
    atm_by_ticker = {}
    for ticker, data in raw.items():
        if "contracts" not in data:
            continue
        exps = signals.expirations(data["contracts"])
        if exps:
            iv = signals.atm_iv(data["contracts"], exps[0])
            if iv:
                atm_by_ticker[ticker] = iv

    history = store.iv_history_bulk(tickers)

    # Where Supabase has not banked enough snapshots yet, try seeding IV history
    # from dxfeed's daily candles so IV rank is live on day one.
    for ticker in tickers:
        if len(history.get(ticker, [])) >= config.MIN_HISTORY_DAYS_FOR_RANK:
            continue
        try:
            seeded = await historical_iv(session, ticker)
        except Exception as exc:  # noqa: BLE001
            print(f"[run] iv seed failed for {ticker}: {exc}", file=sys.stderr)
            continue
        if len(seeded) > len(history.get(ticker, [])):
            print(f"[run] seeded {len(seeded)} IV points for {ticker} from dxfeed")
            history[ticker] = seeded

    earnings = news.earnings_dates(tickers)
    headlines = news.recent_news(tickers)

    candidates = []
    snapshot_rows = []

    for ticker, data in raw.items():
        if "contracts" not in data:
            continue

        sig = signals.build_signals(
            ticker=ticker,
            contracts=data["contracts"],
            closes=data["closes"],
            spot=spots.get(ticker),
            atm_by_ticker=atm_by_ticker,
            iv_history=history.get(ticker, []),
            earnings_date=earnings.get(ticker),
        )

        if sig.atm_iv:
            snapshot_rows.append(
                {
                    "ticker": ticker,
                    "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "atm_iv": sig.atm_iv,
                    "hv20": sig.hv.get("hv20"),
                    "spot": sig.spot,
                    "iv_hv_ratio": sig.iv_hv_ratio,
                    "term_points": sig.term_points,
                    "skew_points": sig.skew_points,
                }
            )

        if sig.in_earnings_blackout:
            print(
                f"[run] {ticker} skipped: earnings {sig.earnings_date} inside blackout",
                file=sys.stderr,
            )
            continue

        tradeable = signals.eligible_contracts(data["contracts"])
        if not tradeable:
            continue

        candidates.append(
            {
                "ticker": ticker,
                "spot": sig.spot,
                "cheap_score": sig.cheap_score,
                "signals": {
                    "atm_iv": sig.atm_iv,
                    "hv": sig.hv,
                    "iv_hv_ratio": sig.iv_hv_ratio,
                    "term_points": sig.term_points,
                    "skew_points": sig.skew_points,
                    "peer_points": sig.peer_points,
                    "iv_rank": sig.iv_rank,
                    "price_change_pct_1d": sig.price_change_pct_1d,
                    "price_change_pct_5d": sig.price_change_pct_5d,
                },
                "earnings_date": sig.earnings_date.isoformat() if sig.earnings_date else None,
                "quant_reasons": sig.reasons,
                "news": headlines.get(ticker, [])[: config.MAX_HEADLINES_PER_CANDIDATE],
                "contracts": [
                    contract_payload(c)
                    for c in signals.select_for_model(
                        tradeable, config.MAX_CONTRACTS_PER_CANDIDATE
                    )
                ],
            }
        )

    # Always bank the IV observation, even for names we skip. History is the
    # whole point of the nightly cadence.
    store.record_iv_snapshot(snapshot_rows)

    candidates.sort(key=lambda c: c["cheap_score"], reverse=True)

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tickers_scanned": len(raw),
        "iv_history_days_banked": {t: len(h) for t, h in history.items()},
        "candidates": candidates[:max_candidates],
    }


def main():
    parser = argparse.ArgumentParser(description="Scan for mispriced options")
    parser.add_argument("--tickers", nargs="*", default=config.WATCHLIST)
    parser.add_argument("--max", type=int, default=config.MAX_CANDIDATES)
    parser.add_argument("--out", default=None, help="write JSON here instead of stdout")
    args = parser.parse_args()

    result = asyncio.run(main_async(args.tickers, args.max))
    payload = json.dumps(result, indent=2, default=str)

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(payload)
        print(f"wrote {args.out}")
    else:
        print(payload)

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
