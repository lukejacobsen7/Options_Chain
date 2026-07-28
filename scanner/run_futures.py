"""Entrypoint for the futures-options scan (MES / MNQ / M2K).

Same split of labour as `scanner.run`: this does the deterministic work and
prints JSON, the routine prompt has Claude apply judgment and publish. The
signal math, Supabase store, dedupe, and alert format are shared code -- only
the data source and the thresholds differ.

Three things this does that the equity scan does not:

  - **Resolves the active contract month.** Futures roll. Spot and realized vol
    come from whichever contract is currently front, looked up at runtime.
  - **Reports dollars.** Every contract carries a `cost_usd`, because a quote of
    12.00 means $60 on MES and $24 on MNQ, and on a small account that is the
    number that decides whether a trade is takeable.
  - **Skips the earnings calendar entirely.** An index has no earnings. The
    catalysts that matter are macro (FOMC, CPI, NFP) and no calendar in this
    repo knows them, so the routine prompt carries that judgment instead.

The profile must be applied before anything reads config, hence the import
order below.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from typing import Dict, List, Optional

from . import config_futures

config_futures.apply()  # noqa: E402 - must precede any config read

from . import config, news, signals, store  # noqa: E402
from .futures import describe, front_futures, load_futures_chain  # noqa: E402
from .tasty import (  # noqa: E402
    Contract,
    daily_closes,
    historical_iv,
    hydrate,
    make_session,
    underlying_quote,
)

# News has no futures feed, so borrow the cash-index ETF as a proxy. The tape
# that moves SPY moves MES; it is the same risk, differently wrapped.
NEWS_PROXY = {"/MES": "SPY", "/MNQ": "QQQ", "/M2K": "IWM", "/MYM": "DIA"}


async def scan_product(session, product: str, spot: Optional[float], fut_symbol: Optional[str]) -> Dict:
    contracts: List[Contract] = await load_futures_chain(session, product, spot)
    if not contracts:
        return {"ticker": product, "error": "no contracts in DTE band"}

    await hydrate(session, contracts)

    # Realized vol needs the underlying future's own history. If the front month
    # is newly rolled it may have too few bars, in which case HV comes back
    # short and the signal layer returns None rather than guessing.
    closes = await daily_closes(session, fut_symbol) if fut_symbol else []

    return {"ticker": product, "contracts": contracts, "closes": closes}


def contract_payload(c: Contract) -> Dict:
    return {
        "occ_symbol": c.occ_symbol,
        "streamer_symbol": c.streamer_symbol,
        "expiration": c.expiration.isoformat(),
        "dte": c.dte,
        "strike": c.strike,
        "type": c.option_type,
        "root_symbol": c.root_symbol,
        "exercise_style": c.exercise_style,
        "settlement_type": c.settlement_type,
        "multiplier": c.multiplier,
        "last": c.last,
        "change": c.change,
        "bid": c.bid,
        "ask": c.ask,
        "mid": c.mid,
        # The two numbers that decide takeability on a $1k account.
        "cost_usd": c.cost_usd,
        "max_loss_usd": c.cost_usd,
        "spread_pct": None if c.spread_pct is None else round(c.spread_pct, 4),
        "volume": c.volume,
        "open_interest": c.open_interest,
        "iv": c.iv,
        "delta": c.delta,
        "theta": c.theta,
        "vega": c.vega,
    }


async def main_async(products: List[str], max_candidates: int) -> Dict:
    missing = config.missing_secrets()
    if missing:
        return {"error": f"missing env vars: {', '.join(missing)}"}

    # Same degrade-don't-abort posture as the equity scan: the day-one signals
    # need no banked history, so a paused Supabase project costs IV rank and
    # dedupe but not the run.
    supabase_ok = store.health_check()
    if not supabase_ok:
        print(
            "[futures] supabase unreachable - scanning anyway. IV rank will be "
            "null and alert dedupe is OFF, so a repeat alert is possible.",
            file=sys.stderr,
        )

    session = make_session()

    fronts = front_futures(session, products)
    unresolved = [p for p in products if p not in fronts]
    if unresolved:
        print(f"[futures] no active contract for {', '.join(unresolved)}", file=sys.stderr)

    fut_symbols = {p: f.streamer_symbol for p, f in fronts.items() if f.streamer_symbol}
    quotes = await underlying_quote(session, list(fut_symbols.values())) if fut_symbols else {}
    spots = {p: quotes[s] for p, s in fut_symbols.items() if s in quotes}

    missing_spot = [p for p in products if p not in spots]
    if missing_spot:
        print(f"[futures] no spot for {', '.join(missing_spot)}, full chain pull", file=sys.stderr)

    semaphore = asyncio.Semaphore(config.SCAN_CONCURRENCY)

    async def guarded(product: str):
        async with semaphore:
            try:
                return await scan_product(session, product, spots.get(product), fut_symbols.get(product))
            except Exception as exc:  # noqa: BLE001 - one bad product must not kill the run
                print(f"[futures] {product} failed: {exc}", file=sys.stderr)
                return {"ticker": product, "error": str(exc)}

    results = await asyncio.gather(*(guarded(p) for p in products))
    raw = {r["ticker"]: r for r in results}

    # Peer-relative needs the whole cohort's ATM IV first. With only three
    # members the median is thin, but MES/MNQ/M2K really are the same trade in
    # different clothes, so a genuine dispersion between them is informative.
    atm_by_product = {}
    for product, data in raw.items():
        if "contracts" not in data:
            continue
        exps = signals.expirations(data["contracts"])
        if exps:
            iv = signals.atm_iv(data["contracts"], exps[0])
            if iv:
                atm_by_product[product] = iv

    history = store.iv_history_bulk(products) if supabase_ok else {}

    for product in products:
        if len(history.get(product, [])) >= config.MIN_HISTORY_DAYS_FOR_RANK:
            continue
        symbol = fut_symbols.get(product)
        if not symbol:
            continue
        try:
            seeded = await historical_iv(session, symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"[futures] iv seed failed for {product}: {exc}", file=sys.stderr)
            continue
        if len(seeded) > len(history.get(product, [])):
            print(f"[futures] seeded {len(seeded)} IV points for {product} from dxfeed")
            history[product] = seeded

    # No earnings lookup: an index does not report. Headlines still help, via
    # the cash ETF that tracks the same risk.
    proxies = [NEWS_PROXY[p] for p in products if p in NEWS_PROXY]
    proxy_headlines = news.recent_news(proxies) if proxies else {}

    candidates = []
    snapshot_rows = []

    for product, data in raw.items():
        if "contracts" not in data:
            continue

        sig = signals.build_signals(
            ticker=product,
            contracts=data["contracts"],
            closes=data["closes"],
            spot=spots.get(product),
            atm_by_ticker=atm_by_product,
            iv_history=history.get(product, []),
            earnings_date=None,
        )

        if sig.atm_iv:
            snapshot_rows.append(
                {
                    "ticker": product,
                    "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "atm_iv": sig.atm_iv,
                    "hv20": sig.hv.get("hv20"),
                    "spot": sig.spot,
                    "iv_hv_ratio": sig.iv_hv_ratio,
                    "term_points": sig.term_points,
                    "skew_points": sig.skew_points,
                }
            )

        tradeable = signals.eligible_contracts(data["contracts"])
        if not tradeable:
            continue

        proxy = NEWS_PROXY.get(product)
        candidates.append(
            {
                "ticker": product,
                "product": describe(product),
                "multiplier": config_futures.multiplier(product),
                "front_contract": fut_symbols.get(product),
                "spot": sig.spot,
                "notional_usd": (
                    None if sig.spot is None else round(sig.spot * config_futures.multiplier(product), 2)
                ),
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
                "quant_reasons": sig.reasons,
                "news_proxy": proxy,
                "news": proxy_headlines.get(proxy, [])[: config.MAX_HEADLINES_PER_CANDIDATE] if proxy else [],
                "contracts": [
                    contract_payload(c)
                    for c in signals.select_for_model(tradeable, config.MAX_CONTRACTS_PER_CANDIDATE)
                ],
            }
        )

    if supabase_ok:
        store.record_iv_snapshot(snapshot_rows)

    candidates.sort(key=lambda c: c["cheap_score"], reverse=True)

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile": "futures",
        "products_scanned": len(raw),
        "front_contracts": fut_symbols,
        "supabase_available": supabase_ok,
        "dedupe_active": supabase_ok,
        "iv_history_days_banked": {p: len(h) for p, h in history.items()},
        "candidates": candidates[:max_candidates],
    }


def main():
    parser = argparse.ArgumentParser(description="Scan futures options for mispricing")
    parser.add_argument("--products", nargs="*", default=config_futures.WATCHLIST)
    parser.add_argument("--max", type=int, default=config_futures.MAX_CANDIDATES)
    parser.add_argument("--out", default=None, help="write JSON here instead of stdout")
    args = parser.parse_args()

    result = asyncio.run(main_async(args.products, args.max))
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
