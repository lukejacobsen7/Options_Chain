"""Futures-options data access. Thin layer over tasty.py.

Everything expensive -- the DXLink snapshot-and-close pattern, quote/greeks
hydration, daily candles -- is reused from tasty.py unchanged. What is actually
different about futures lives here:

  1. **Chains come from a different endpoint.** `get_future_option_chain`
     instead of `get_option_chain`, though it returns the same
     `{expiration: [option, ...]}` shape, so the loader looks familiar.
  2. **There is no single "underlying symbol."** An equity option references
     AAPL forever. A futures option references a specific contract month, so
     spot and realized vol have to come from whichever future is currently
     active, resolved at runtime.
  3. **Premium is not dollars.** A quote of 12.00 on MES costs $60, on MNQ
     $24. Every contract carries its multiplier so the alert can say what the
     trade actually costs, which on a small account is the number that decides
     whether it is takeable at all.
"""

import datetime as dt
from typing import Dict, List, Optional

from tastytrade import Session
from tastytrade.instruments import Future, get_future_option_chain

from . import config, config_futures
from .tasty import Contract


def _bare(product: str) -> str:
    """`/MES` -> `MES`. The instruments endpoint wants product codes bare."""
    return product.lstrip("/")


def front_futures(session: Session, products: List[str]) -> Dict[str, Future]:
    """The currently active contract month for each product.

    Futures roll, so this cannot be hardcoded. Prefer the contract the exchange
    flags as the active month; fall back to the nearest expiry that has not
    passed yet, which is what active month means anyway when the flag is absent.
    """
    codes = [_bare(p) for p in products]
    result = Future.get(session, product_codes=codes)
    if isinstance(result, Future):
        result = [result]

    today = dt.date.today()
    by_product: Dict[str, List[Future]] = {}
    for fut in result or []:
        if fut.expiration_date and fut.expiration_date < today:
            continue
        by_product.setdefault(f"/{_bare(fut.product_code or '')}", []).append(fut)

    out: Dict[str, Future] = {}
    for product in products:
        candidates = by_product.get(product) or []
        if not candidates:
            continue
        active = [f for f in candidates if getattr(f, "active_month", False)]
        pool = active or candidates
        pool.sort(key=lambda f: f.expiration_date or dt.date.max)
        out[product] = pool[0]
    return out


async def load_futures_chain(
    session: Session,
    product: str,
    spot: Optional[float] = None,
) -> List[Contract]:
    """Chain skeleton for one futures product, filtered to the DTE band.

    Same moneyness pre-filter as the equity path and for the same reason: drop
    strikes before subscribing, not after. It matters less here than on SPY
    because these chains are small, but a full MNQ surface is still thousands of
    contracts and there is no reason to hydrate the wings.

    Note the extra `option_root_symbol` guard. tastytrade returns every expiry
    landing on a given date in one list, so a product with both weekly and
    end-of-month roots can hand back two different contracts for the same day.
    Both are kept -- they are genuinely different instruments -- but the root is
    recorded so the alert can name which one it means.
    """
    mult = config_futures.multiplier(product)
    chain = await _chain(session, product)
    today = dt.date.today()

    lo = hi = None
    if spot:
        lo = spot * (1 - config.MONEYNESS_BAND)
        hi = spot * (1 + config.MONEYNESS_BAND)

    out: List[Contract] = []
    for expiration, options in chain.items():
        dte = (expiration - today).days
        if not (config.MIN_DTE <= dte <= config.MAX_DTE):
            continue
        for opt in options:
            # Closing-only contracts cannot be opened, so an alert on one is
            # unactionable no matter how cheap it looks.
            if getattr(opt, "is_closing_only", False) or getattr(opt, "active", True) is False:
                continue
            strike = float(opt.strike_price)
            if lo is not None and not (lo <= strike <= hi):
                continue
            out.append(
                Contract(
                    underlying=product,
                    streamer_symbol=opt.streamer_symbol,
                    occ_symbol=opt.symbol,
                    expiration=expiration,
                    strike=strike,
                    option_type="C" if str(opt.option_type).upper().startswith("C") else "P",
                    multiplier=mult,
                    root_symbol=getattr(opt, "option_root_symbol", None),
                    exercise_style=getattr(opt, "exercise_style", None),
                    settlement_type=getattr(opt, "settlement_type", None),
                )
            )
    return out


async def _chain(session: Session, product: str):
    """Fetch a chain, tolerating either symbol convention.

    tastytrade wants the slashed form for futures option chains, but it costs
    nothing to retry bare rather than fail a whole product on a formatting
    guess.
    """
    try:
        return get_future_option_chain(session, product)
    except Exception:  # noqa: BLE001 - fall through to the other convention
        return get_future_option_chain(session, _bare(product))


def dollars(points: Optional[float], multiplier: float) -> Optional[float]:
    """Index points -> dollars for one contract."""
    if points is None:
        return None
    return round(points * multiplier, 2)


def describe(product: str) -> str:
    """`/MES ($5/pt, Micro E-mini S&P 500)` for the run summary."""
    return f"{product} (${config_futures.multiplier(product):g}/pt, {config_futures.product_name(product)})"
