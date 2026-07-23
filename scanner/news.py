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


def earnings_dates(symbols: List[str], days_ahead: int = 45) -> Dict[str, Optional[dt.date]]:
    """Next earnings date per symbol. Missing key or failure yields all None."""
    out: Dict[str, Optional[dt.date]] = {s: None for s in symbols}
    if not config.FINNHUB_API_KEY:
        return out

    today = dt.date.today()
    params = {
        "from": (today - dt.timedelta(days=config.EARNINGS_BLACKOUT_DAYS)).isoformat(),
        "to": (today + dt.timedelta(days=days_ahead)).isoformat(),
        "token": config.FINNHUB_API_KEY,
    }

    try:
        resp = requests.get(FINNHUB_EARNINGS_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[news] finnhub earnings fetch failed: {exc}")
        return out

    wanted = set(symbols)
    for row in payload.get("earningsCalendar", []):
        sym = row.get("symbol")
        if sym not in wanted:
            continue
        try:
            date = dt.date.fromisoformat(row["date"])
        except (KeyError, ValueError):
            continue
        if out[sym] is None or date < out[sym]:
            out[sym] = date
    return out
