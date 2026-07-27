"""Environment preflight. Answers "is this run even possible" before spending
time on a scan.

The cloud environment fails in a specific, nasty way: a blocked host returns
403 with `x-deny-reason: host_not_allowed`, the library swallows it, the scan
produces nothing, and the run still reports green. This checks each dependency
explicitly and says which ones are broken.

Exits 0 always. It is a report, not a gate.
"""

import os
import sys

import requests

from . import config

TIMEOUT = 15

# (label, url, what a healthy response looks like)
HOSTS = [
    ("tastytrade REST", "https://api.tastyworks.com/", (200, 401, 403, 404)),
    ("alpaca news", "https://data.alpaca.markets/v1beta1/news", (200, 401, 403)),
    ("finnhub", "https://finnhub.io/api/v1/quote?symbol=AAPL", (200, 401, 403)),
]

# Alerts are published into the session transcript, so there is no outbound
# messaging credential to check any more.
REQUIRED = [
    "TASTY_CLIENT_SECRET",
    "TASTY_REFRESH_TOKEN",
    "SUPABASE_URL",
    "SUPABASE_KEY",
]
OPTIONAL = ["ALPACA_API_KEY", "ALPACA_API_SECRET", "FINNHUB_API_KEY"]


def check_host(label, url, ok_codes):
    """A blocked host is the failure we care about, not an unhappy status code.

    Any HTTP response at all proves the allowlist let us out. 401/403 from the
    service itself is fine here; we are testing the network, not the creds.

    Redirects are deliberately NOT followed. A host that answers with a 30x has
    demonstrably let us out, which is the whole question here, and chasing the
    redirect can land on a different host that is not allowlisted and 403s -
    which is exactly how this check once reported a healthy service as blocked.
    """
    try:
        resp = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
    except requests.exceptions.RequestException as exc:
        return False, f"unreachable: {type(exc).__name__}"

    deny = resp.headers.get("x-deny-reason")
    if deny:
        return False, f"BLOCKED BY ENVIRONMENT ({deny}) - add to allowed domains"
    if resp.status_code in ok_codes:
        return True, f"http {resp.status_code}"
    return True, f"http {resp.status_code} (reachable)"


def main():
    print("=" * 62)
    print("PREFLIGHT")
    print("=" * 62)

    print("\nnetwork:")
    blocked = []
    for label, url, ok_codes in HOSTS:
        ok, detail = check_host(label, url, ok_codes)
        print(f"  {'OK  ' if ok else 'FAIL'}  {label:<18} {detail}")
        if not ok:
            blocked.append(label)

    # Supabase host is project-specific, so build it from the configured URL.
    if config.SUPABASE_URL:
        ok, detail = check_host(
            "supabase", f"{config.SUPABASE_URL.rstrip('/')}/rest/v1/", (200, 401, 403)
        )
        print(f"  {'OK  ' if ok else 'FAIL'}  {'supabase':<18} {detail}")
        if not ok:
            blocked.append("supabase")
    else:
        print("  SKIP  supabase           SUPABASE_URL not set")

    print("\ncredentials:")
    missing_required = []
    for name in REQUIRED:
        present = bool(os.environ.get(name))
        print(f"  {'OK  ' if present else 'MISS'}  {name}")
        if not present:
            missing_required.append(name)

    for name in OPTIONAL:
        present = bool(os.environ.get(name))
        print(f"  {'OK  ' if present else '--  '}  {name}{'' if present else '  (optional)'}")

    print("\n" + "=" * 62)
    if blocked:
        print(f"NETWORK BLOCKED: {', '.join(blocked)}")
        print("The run cannot work. Fix the environment's allowed domains.")
    if missing_required:
        print(f"MISSING REQUIRED: {', '.join(missing_required)}")
    if not blocked and not missing_required:
        print("READY")
    print("=" * 62)

    # Machine-readable line for the routine to grep.
    verdict = "READY" if (not blocked and not missing_required) else "NOT_READY"
    print(f"\nPREFLIGHT_VERDICT={verdict}")


if __name__ == "__main__":
    main()
