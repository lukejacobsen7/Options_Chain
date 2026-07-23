# options-edge

Scans a watchlist for options priced cheaper than recent price action and news
justify, and sends the survivors to Telegram. Runs as a Claude Code cloud
routine twice each weekday.

Alerts only. Nothing here places a trade.

## How it works

```
run.py          deterministic: chains, greeks, vol math, liquidity gate, news
   |
   v
/tmp/candidates.json
   |
   v
Claude (routine) judgment: does the pricing match the news?
   |
   v
emit.py         formats, dedupes, sends to Telegram, records to Supabase
```

The split is deliberate. Math that should be reproducible lives in code. The
"does this move make sense given what happened" question lives with the model,
because it is not a formula.

## Signals

Four work on day one with no history:

| Signal | What it means |
| --- | --- |
| IV / HV20 | Options pricing less movement than the stock is delivering |
| Term structure | Front-month IV cheap relative to back |
| Skew (25Δ) | Which side, calls or puts, is the underpriced leg |
| Peer-relative | This name's IV vs the median of its cohort |

IV rank needs banked history and stays `null` until Supabase has
`MIN_HISTORY_DAYS_FOR_RANK` observations (default 15, so about 8 trading days
at two runs per day).

## Liquidity gate

Runs before any scoring. Thin options screen as cheap for the same reason they
are thin, and the spread eats more than the edge:

- open interest >= 250
- volume >= 25
- bid >= $0.10
- spread <= 10% of mid
- 0.15 <= |delta| <= 0.60
- 14 <= DTE <= 60

All tunable in `scanner/config.py`.

## Setup

### 1. Supabase

Run `sql/schema.sql` in the SQL editor. Creates `iv_history`, `alerts_sent`,
and an `iv_rank_current` view.

Free tier note: projects pause after ~7 days of inactivity. Two runs every
weekday keeps it warm. If the routine is paused for over a week, the first run
back fails on connect and the project needs a manual unpause.

### 2. Credentials

| Variable | Where from |
| --- | --- |
| `TASTY_CLIENT_SECRET`, `TASTY_REFRESH_TOKEN` | my.tastytrade.com, Manage, API Access, OAuth applications |
| `ALPACA_API_KEY`, `ALPACA_API_SECRET` | Alpaca free paper account |
| `FINNHUB_API_KEY` | Finnhub free tier |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase project settings, API |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | @BotFather, then `getUpdates` for the chat id |

Locally: copy `.env.example` to `.env`. In the cloud: set them as environment
variables on the routine's environment. Never commit them.

tastytrade gives real-time data once the account is funded with any amount.
Unfunded accounts get 14 days of live data, then drop to delayed.

### 3. Cloud environment network allowlist

**This is the step that silently breaks everything if missed.** The Default
environment uses Trusted network access, which blocks everything outside its
package-registry allowlist. Set network access to Custom and allow:

```
api.tastytrade.com
tasty-live-web.dxfeed.com
data.alpaca.markets
finnhub.io
api.telegram.org
<your-project>.supabase.co
```

Keep "include default list of common package managers" checked so `pip` still
works. A blocked host returns 403 with `x-deny-reason: host_not_allowed`, and
the run still shows green, because green only means the session did not crash.

### 4. Routine

Prompt: `ROUTINE_PROMPT.md`. Setup script: `setup.sh`. Two weekday schedule
triggers. Connectors trimmed to Supabase only, since a routine that runs
autonomously with no approval prompts should not hold write access to
everything else on the account.

## Local testing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
set -a && source .env && set +a
python -m scanner.run --tickers MU NVDA --max 5
```

Dry-run an alert without sending:

```bash
python -m scanner.emit --ticker MU --contract '<json>' \
  --direction "BUY CALL" --rationale "test" --dry-run
```

## Alert format

```
MU  BUY CALL  Aug 21 '26 (29d)

Strike 12.00          Δ 0.9416
Last 5.70          Chg +0.00
Bid 4.65 / Ask 5.10   Mid 4.88
Vol 0    OI 100    IV 93.11%
Breakeven $17.10 (+71.7% from spot $9.96)

WHY: <one sentence>
```

Breakeven uses the fillable side of the spread, never the mid: ask for buys,
bid for sells.

## Status

Untested against live credentials. The tastytrade streamer field names for
`Summary` (open interest) and `Trade` (volume) are the most likely thing to
need a fix on the first real run, since those come from dxfeed rather than the
tastytrade REST API.
