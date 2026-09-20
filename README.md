<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img alt="options-edge" src="assets/logo-light.svg" width="540">
  </picture>
</p>

<p align="center">
  <img alt="python" src="https://img.shields.io/badge/python-3.11-2BD9FF">
  <img alt="data" src="https://img.shields.io/badge/data-tastytrade%20%2B%20dxfeed-2BD9FF">
  <img alt="store" src="https://img.shields.io/badge/store-Supabase-2BD9FF">
  <img alt="trading" src="https://img.shields.io/badge/trades%20placed-never-FFB74D">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-2BD9FF"></a>
</p>

<p align="center">
  <a href="#how-it-works">How it works</a> ·
  <a href="#signals">Signals</a> ·
  <a href="#liquidity-gate">Liquidity gate</a> ·
  <a href="#setup">Setup</a> ·
  <a href="#local-testing">Local testing</a> ·
  <a href="#status">Status</a>
</p>

**Finds options priced cheaper than recent price action and news justify, and sends the survivors
to Telegram.** Twice every weekday, as a Claude Code cloud routine.

> **Alerts only. Nothing in this repo places a trade.**

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

The split is deliberate. Math that should be reproducible lives in code. The *"does this move make
sense given what happened"* question lives with the model, because it is not a formula.

## Signals

Four of them work on day one with no history:

| Signal | What it means |
| --- | --- |
| **IV / HV20** | Options are pricing less movement than the stock is actually delivering |
| **Term structure** | Front-month IV is cheap relative to back |
| **Skew (25Δ)** | Which side, calls or puts, is the underpriced leg |
| **Peer-relative** | This name's IV against the median of its cohort |

IV rank needs banked history, so it stays `null` until Supabase has `MIN_HISTORY_DAYS_FOR_RANK`
observations (default 15 — about 8 trading days at two runs per day).

## Liquidity gate

This runs *before* any scoring. Thin options screen as cheap for the same reason they are thin, and
the spread eats more than the edge:

| Filter | Threshold |
| --- | --- |
| Open interest | ≥ 250 |
| Volume | ≥ 25 |
| Bid | ≥ $0.10 |
| Spread | ≤ 10% of mid |
| \|Delta\| | 0.15 – 0.60 |
| DTE | 14 – 60 |

All tunable in `scanner/config.py`.

## Setup

### 1. Supabase

Run `sql/schema.sql` in the SQL editor. It creates `iv_history`, `alerts_sent`, and an
`iv_rank_current` view.

> **Free-tier note:** projects pause after ~7 days of inactivity. Two runs every weekday keeps it
> warm. If the routine is paused for over a week, the first run back fails on connect and the
> project needs a manual unpause.

### 2. Credentials

| Variable | Where from |
| --- | --- |
| `TASTY_CLIENT_SECRET`, `TASTY_REFRESH_TOKEN` | my.tastytrade.com → Manage → API Access → OAuth applications |
| `ALPACA_API_KEY`, `ALPACA_API_SECRET` | Alpaca free paper account |
| `FINNHUB_API_KEY` | Finnhub free tier |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase project settings → API |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | [@BotFather](https://t.me/BotFather), then `getUpdates` for the chat id |

Locally: copy `.env.example` to `.env`. In the cloud: set them as environment variables on the
routine's environment. **Never commit them.**

tastytrade gives real-time data once the account is funded with any amount. Unfunded accounts get
14 days of live data, then drop to delayed.

### 3. Cloud environment network allowlist

**This is the step that silently breaks everything if you miss it.** The Default environment uses
Trusted network access, which blocks everything outside its package-registry allowlist. Set
network access to **Custom** and allow:

```
api.tastyworks.com
api.tastytrade.com
*.dxfeed.com
data.alpaca.markets
finnhub.io
api.telegram.org
<your-project>.supabase.co
```

Two details worth knowing, verified against tastytrade SDK 13.2.0 rather than guessed:

- REST is **`api.tastyworks.com`**, not `api.tastytrade.com` — the brand and the API domain differ.
  `api.tastytrade.com` is kept only as a redirect fallback.
- The dxfeed host is **not hardcoded anywhere**. `DXLinkStreamer` calls `/api-quote-tokens` and
  connects to whatever `dxlink-url` comes back, so the wildcard is deliberate: pinning a single
  dxfeed hostname breaks the moment tastytrade points that field somewhere else.

Keep *"include default list of common package managers"* checked so `pip` still works.

> A blocked host returns 403 with `x-deny-reason: host_not_allowed` — **and the run still shows
> green**, because green only means the session did not crash.

### 4. Routine

Prompt: `ROUTINE_PROMPT.md`. Setup script: `setup.sh`. Two weekday schedule triggers. Connectors
trimmed to Supabase only, since a routine that runs autonomously with no approval prompts should
not hold write access to everything else on the account.

## Local testing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
set -a && source .env && set +a
python -m scanner.run --tickers MU NVDA --max 5
```

Dry-run an alert without sending it:

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

Breakeven uses the fillable side of the spread, never the mid: ask for buys, bid for sells.

## Status

Untested against live credentials. The tastytrade streamer field names for `Summary` (open
interest) and `Trade` (volume) are the most likely thing to need a fix on the first real run, since
those come from dxfeed rather than the tastytrade REST API.

## License

MIT — see [LICENSE](LICENSE).
