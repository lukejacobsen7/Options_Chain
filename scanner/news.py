"""News and earnings context.

tastytrade has no news endpoint, so headlines come from Alpaca (Benzinga-sourced,
free tier) and the earnings calendar from Finnhub's free tier. Both are pulled
over plain REST; neither host is on the cloud environment's default allowlist,
so they must be added under Custom network access or every call returns 403.
"""

import datetime as dt
from typing import Dict, List, Optional

import requests

from . import config

ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"

TIMEOUT = 20


def recent_news(symbols: List[str], hours: int = 24, limit: int = 50) -> Dict[str, List[dict]]:
    """Headlines per symbol from the last `hours`. Empty dict if unconfigured."""
    if not (config.ALPACA_API_KEY and config.ALPACA_API_SECRET):
        return {}

    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    params = {
        "symbols": ",".join(symbols),
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": limit,
        "sort": "desc",
    }
    headers = {
        "APCA-API-KEY-ID": config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_API_SECRET,
    }

    try:
        resp = requests.get(ALPACA_NEWS_URL, params=params, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - a news failure must not kill the run
        print(f"[news] alpaca fetch failed: {exc}")
        return {}

    out: Dict[str, List[dict]] = {s: [] for s in symbols}
    for item in payload.get("news", []):
        entry = {
            "headline": item.get("headline"),
            "summary": (item.get("summary") or "")[:400],
            "source": item.get("source"),
            "created_at": item.get("created_at"),
            "url": item.get("url"),
        }
        for sym in item.get("symbols", []):
            if sym in out:
                out[sym].append(entry)
    return out


# Finnhub's free tier caps the earnings calendar response at this many rows and
# gives no indication that it truncated. Worse, it returns the FURTHEST dates
# first, so a request spanning several weeks silently drops the soonest prints -
# exactly the ones the earnings blackout exists to catch. A single Jul-Sep query
# returned 1500 rows covering only the back of the window: 20 of 37 watchlist
# names had earnings in range and only NVDA (Aug 26) survived, while PYPL (2
# days out) and RIVN (4 days out) came back as None and screened as clean
# cheap-IV candidates. Query in slices small enough to stay under the cap.
FINNHUB_ROW_CAP = 1500
EARNINGS_SLICE_DAYS = 5


# Look at least as far out as the longest contract we would ever trade, plus a
# pad for dates that slip. A 45-day lookahead against a 60-day MAX_DTE means a
# 55-DTE contract can straddle an earnings date the scan never saw.
EARNINGS_LOOKAHEAD_DAYS = config.MAX_DTE + 10


def earnings_dates(
    symbols: List[str], days_ahead: int = EARNINGS_LOOKAHEAD_DAYS
) -> Dict[str, Optional[dt.date]]:
    """Next earnings date per symbol.

    None means "nothing scheduled that we could see", which is not the same as
    "nothing scheduled". Any slice that fails or comes back truncated is logged
    loudly, because a silently missing earnings date turns the blackout filter
    off for that name.
    """
    out: Dict[str, Optional[dt.date]] = {s: None for s in symbols}
    if not config.FINNHUB_API_KEY:
        return out

    today = dt.date.today()
    start = today - dt.timedelta(days=config.EARNINGS_BLACKOUT_DAYS)
    end = today + dt.timedelta(days=days_ahead)
    wanted = set(symbols)
    degraded = False

    def absorb(lo: dt.date, hi: dt.date) -> None:
        """Fetch lo..hi, subdividing if the response comes back at the row cap.

        Peak earnings weeks blow through the cap even on a 5-day slice, so the
        slice width cannot be a fixed constant. Bisect until each request fits
        or we are down to a single day.
        """
        nonlocal degraded
        params = {"from": lo.isoformat(), "to": hi.isoformat(), "token": config.FINNHUB_API_KEY}
        try:
            resp = requests.get(FINNHUB_EARNINGS_URL, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            rows = resp.json().get("earningsCalendar", [])
        except Exception as exc:  # noqa: BLE001
            print(f"[news] finnhub earnings fetch failed for {lo}..{hi}: {exc}")
            degraded = True
            return

        if len(rows) >= FINNHUB_ROW_CAP:
            if lo < hi:
                mid = lo + (hi - lo) // 2
                absorb(lo, mid)
                absorb(mid + dt.timedelta(days=1), hi)
                return
            # A single day over the cap is as far as we can subdivide.
            print(f"[news] WARNING finnhub truncated single day {lo} at {len(rows)} rows")
            degraded = True

        for row in rows:
            sym = row.get("symbol")
            if sym not in wanted:
                continue
            try:
                date = dt.date.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue
            if out[sym] is None or date < out[sym]:
                out[sym] = date

    cursor = start
    while cursor <= end:
        slice_end = min(cursor + dt.timedelta(days=EARNINGS_SLICE_DAYS - 1), end)
        absorb(cursor, slice_end)
        cursor = slice_end + dt.timedelta(days=1)

    if degraded:
        print(
            "[news] WARNING earnings calendar is incomplete for this run; treat "
            "any null earnings_date as UNKNOWN, not as 'no earnings scheduled'"
        )

    found = sum(1 for v in out.values() if v)
    print(f"[news] earnings dates resolved for {found}/{len(symbols)} tickers")
    return out
