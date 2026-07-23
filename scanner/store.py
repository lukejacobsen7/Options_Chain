"""Supabase persistence: IV history and alert deduplication.

Every routine run clones the repo fresh, so there is no local disk that
survives between runs. Supabase is the state.

Free-tier note: projects pause after ~7 days with no activity. Two runs every
weekday keeps it warm, so this only matters if the routine is paused for a
week or more. If it does pause, the first run back will fail on connect and
the project needs a manual unpause in the Supabase dashboard.
"""

import datetime as dt
from typing import Dict, List, Optional

from supabase import Client, create_client

from . import config

_client: Optional[Client] = None


def client() -> Client:
    global _client
    if _client is None:
        if not (config.SUPABASE_URL and config.SUPABASE_KEY):
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY not set")
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


# ---------------------------------------------------------------------------
# IV history
# ---------------------------------------------------------------------------

def record_iv_snapshot(rows: List[dict]) -> None:
    """Append one ATM-IV observation per ticker per run.

    Rows: {ticker, observed_at, atm_iv, hv20, spot, iv_hv_ratio,
           term_points, skew_points}
    """
    if not rows:
        return
    try:
        client().table("iv_history").insert(rows).execute()
    except Exception as exc:  # noqa: BLE001
        print(f"[store] iv_history insert failed: {exc}")


def iv_history(ticker: str, days: int = 365) -> List[float]:
    """Banked ATM IV observations for a ticker, oldest first."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    try:
        resp = (
            client()
            .table("iv_history")
            .select("atm_iv, observed_at")
            .eq("ticker", ticker)
            .gte("observed_at", since)
            .order("observed_at")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[store] iv_history read failed for {ticker}: {exc}")
        return []
    return [r["atm_iv"] for r in (resp.data or []) if r.get("atm_iv") is not None]


def iv_history_bulk(tickers: List[str], days: int = 365) -> Dict[str, List[float]]:
    return {t: iv_history(t, days) for t in tickers}


# ---------------------------------------------------------------------------
# Alert dedupe
# ---------------------------------------------------------------------------

def already_alerted(occ_symbol: str, direction: str, within_hours: int = 48) -> bool:
    """True if this exact contract and direction fired recently.

    Without this the 10am and 2pm runs will happily send the same idea twice,
    and a signal that persists for a week sends it ten times.
    """
    since = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=within_hours)
    ).isoformat()
    try:
        resp = (
            client()
            .table("alerts_sent")
            .select("id")
            .eq("occ_symbol", occ_symbol)
            .eq("direction", direction)
            .gte("sent_at", since)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[store] dedupe check failed: {exc}")
        return False  # fail open; a duplicate beats a missed alert
    return bool(resp.data)


def record_alert(row: dict) -> None:
    """Row: {occ_symbol, ticker, direction, strike, expiration, sent_at,
    iv, delta, breakeven, rationale}"""
    try:
        client().table("alerts_sent").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        print(f"[store] alert record failed: {exc}")


def health_check() -> bool:
    """Confirm the project is awake before a run does real work."""
    try:
        client().table("iv_history").select("id").limit(1).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[store] health check failed (project paused?): {exc}")
        return False
