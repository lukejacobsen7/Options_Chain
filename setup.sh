#!/usr/bin/env bash
# Dependency install.
#
# Two constraints learned the hard way:
#
#  1. The cloud environment runs its setup script BEFORE the repository is
#     cloned, so nothing here may reference a file from this repo. Packages are
#     named inline rather than read from requirements.txt.
#  2. Recent Debian/Ubuntu images mark the system Python as externally managed
#     (PEP 668), which makes a bare `pip install` fail outright. Try the normal
#     path first, fall back to --break-system-packages.
#  3. The image dpkg-installs PyJWT and cryptography with no RECORD file, so pip
#     can neither uninstall nor upgrade them - it aborts the ENTIRE transaction
#     and tastytrade silently never lands. The distro cryptography also panics
#     (pyo3_runtime.PanicException) when a modern PyJWT imports its Rust
#     bindings. --ignore-installed puts a complete pip-managed set in
#     /usr/local/lib/pythonX/dist-packages, which precedes the distro path on
#     sys.path, so nothing is uninstalled and nothing mixes.
#
# Deliberately NOT `set -e`: a failing pip mirror or an already-satisfied
# upgrade should not abort the whole session before Claude Code even starts.
set -uo pipefail

# cryptography is named explicitly: it is only a PyJWT *extra*, so it would not
# otherwise be in the dependency closure, and the distro copy would be used.
PKGS=("PyJWT>=2.10" "cryptography>=42" "tastytrade>=13.2,<14" "supabase>=2.9" "requests>=2.32")

echo "--- installing dependencies ---"
python3 -m pip install --upgrade pip >/dev/null 2>&1 \
  || python3 -m pip install --upgrade pip --break-system-packages >/dev/null 2>&1 \
  || echo "pip self-upgrade skipped"

if ! python3 -m pip install --ignore-installed "${PKGS[@]}"; then
    echo "retrying with --break-system-packages"
    python3 -m pip install --ignore-installed --break-system-packages "${PKGS[@]}"
fi

echo "--- import check ---"
python3 - <<'PY'
import sys
missing = []
for mod in ("tastytrade", "supabase", "requests"):
    try:
        __import__(mod)
    except ImportError as exc:
        missing.append(f"{mod} ({exc})")
if missing:
    print("IMPORTS FAILED: " + "; ".join(missing))
    sys.exit(1)
print("imports ok")
PY

echo "--- environment check ---"
python3 - <<'PY'
import os

required = [
    "TASTY_CLIENT_SECRET", "TASTY_REFRESH_TOKEN",
    "SUPABASE_URL", "SUPABASE_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
]
optional = ["ALPACA_API_KEY", "ALPACA_API_SECRET", "FINNHUB_API_KEY"]

missing = [k for k in required if not os.environ.get(k)]
print("MISSING REQUIRED: " + ", ".join(missing) if missing else "all required env vars present")

absent = [k for k in optional if not os.environ.get(k)]
if absent:
    print("optional not set (news/earnings context degraded): " + ", ".join(absent))
PY

echo "--- setup complete ---"
