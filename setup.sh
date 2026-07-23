#!/usr/bin/env bash
# Cloud environment setup script. Result is cached, so this does not re-run on
# every session.
set -euo pipefail

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "--- environment check ---"
python3 - <<'PY'
import os

required = [
    "TASTY_CLIENT_SECRET",
    "TASTY_REFRESH_TOKEN",
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]
optional = ["ALPACA_API_KEY", "ALPACA_API_SECRET", "FINNHUB_API_KEY"]

missing = [k for k in required if not os.environ.get(k)]
if missing:
    print("MISSING REQUIRED: " + ", ".join(missing))
else:
    print("all required env vars present")

absent = [k for k in optional if not os.environ.get(k)]
if absent:
    print("optional not set (news/earnings context degraded): " + ", ".join(absent))
PY

echo "--- import check ---"
python3 -c "import tastytrade, supabase, requests; print('imports ok')"
