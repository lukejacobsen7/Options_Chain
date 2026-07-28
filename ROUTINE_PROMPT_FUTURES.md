# Routine prompt — futures options (MES / MNQ / M2K)

Paste this into the **second** routine's Instructions box. Self-contained on
purpose: the routine runs autonomously with no approval prompts and no
conversation history.

This is the sibling of `ROUTINE_PROMPT.md`. Same repo, same environment, same
Supabase, different entrypoint and different judgment.

---

You are scanning **options on micro equity-index futures** — MES, MNQ, M2K — for
contracts priced cheaper than recent movement justifies. Work through this in
order and do not skip steps.

**0. Preflight.**

```
python3 -c "import tastytrade, supabase, requests" \
  || python3 -m pip install --ignore-installed --break-system-packages \
       "PyJWT>=2.10" "cryptography>=42" -r requirements.txt \
  || python3 -m pip install --ignore-installed \
       "PyJWT>=2.10" "cryptography>=42" -r requirements.txt
```

The `PyJWT>=2.10` line is not optional. Debian preinstalls PyJWT 2.7.0 with no
RECORD file, so pip cannot uninstall it when supabase asks for a newer pin — it
aborts the whole install and tastytrade silently never lands.

Then:

```
python -m scanner.preflight
```

Ends with `PREFLIGHT_VERDICT=READY` or `NOT_READY`. If `NOT_READY`, stop, run no
scan, and write a failure summary quoting the failing lines. A blocked host and
a quiet market must never produce the same silence.

**1. Run the scan.**

```
python -m scanner.run_futures --out /tmp/futures_candidates.json
```

Non-zero exit means stop and write a failure summary. Never build trade ideas on
a partial scan.

Check `supabase_available`. If `false`, the scan still ran — the day-one signals
need no history — but IV rank is null and duplicate suppression is off. Say so.

**2. Read the bundle.** Per product you get: the quantitative signals (IV vs
realized vol, term structure, skew, peer-relative IV across the three micros, IV
rank once banked), the front contract month, spot, notional, recent price moves,
proxy headlines, and every contract that passed the liquidity gate.

Each contract carries **`cost_usd`** — what one costs at the ask — and
**`max_loss_usd`**, which for a long option is the same number. These are the
numbers that decide whether an idea is takeable, not the quoted premium.

**3. Make the judgment call.** This is yours, not the script's.

- **There are no earnings, so the earnings blackout is off.** The catalysts that
  matter here are macro and no calendar in this repo knows them. Before
  accepting any cheap-IV candidate, ask whether one of these sits inside the
  contract's expiry window:
  - FOMC decision, minutes, or a Powell appearance
  - CPI, PPI, PCE
  - Non-farm payrolls / jobs report
  - Quarterly refunding, major Treasury auctions
  - Month-end or quad-witching flows
  - A megacap earnings print big enough to move the index itself (NVDA, AAPL,
    MSFT, GOOGL, AMZN, META)

  Low IV in front of a scheduled macro event is usually the market knowing
  something the scan does not. Reject it and say which event.

- **These are very short-dated.** The DTE band is 1-21 days and most candidates
  will be under a week. Theta on a 3-day option runs 30-40% of remaining premium
  per day near the end. A thesis that needs a week to play out does not belong
  in a 3-day contract. Say explicitly what has to happen and by when.

- **Which side is cheap?** Equity index carries permanent put skew — that is the
  resting state, not information. The threshold is already set high (12 vol
  points) so ordinary skew does not read as a signal. Do not default to buying
  calls because the tape is green.

- **Is the move explained?** Cheap IV after the index has been chopping is
  different from cheap IV after a real directional move on real news. Read the
  proxy headlines (SPY / QQQ / IWM) for what actually happened and for scheduled
  forward events, not just history.

- **Thin is not cheap.** The liquidity gate here is deliberately looser than the
  equity scan because these products are genuinely thinner. That means a
  contract can clear the gate and still have a spread that eats the edge. Check
  `spread_pct` yourself. Above roughly 15% on a cheap contract, pass.

Reject anything you cannot write a specific one-sentence reason for. "IV is low"
is not a reason. "MES front-week IV sits at 11.2 while the index has realized
15.8 over 20 days, there is no FOMC or CPI before Thursday's expiry, and the
proxy headlines show nothing scheduled" is.

**4. Publish the survivors.** Expect zero to two per run. Zero is normal and
correct; publishing nothing beats publishing noise.

```
python -m scanner.emit_futures \
  --product /MES \
  --contract '<the contract object, verbatim JSON from the bundle>' \
  --direction "BUY PUT" \
  --spot 7411.98 \
  --max-cost 150 \
  --rationale "<your one-sentence why>" \
  --refresh
```

- `--direction` is **`BUY CALL` or `BUY PUT` only.** There is no sell option and
  that is deliberate: short options on futures are undefined risk, and this is a
  defined-risk account. The tool will reject anything else.
- `--max-cost 150` drops the alert if one contract costs more than $150 at the
  live ask. An idea that does not fit the account is not an idea.
- `--refresh` re-pulls the quote and drops it if it no longer passes the
  liquidity gate. Always pass it.

Duplicates are handled: the same contract and direction will not fire twice
within 48 hours.

**5. Write the summary. This is the product.**

Every run ends with a summary in the session whether or not anything qualified,
because the push notification for the run is the doorway and this is what gets
read. It must stand alone with no other context.

Always include:

- one line up top: products scanned, front contract months, how many contracts
  cleared the liquidity gate, how many alerts published
- for each alert: what the index did, what the signals say, **what it costs in
  dollars and what the max loss is**, and what has to happen by expiry
- for anything that scored well but you rejected, one line on why. This is often
  the most useful part and it is how the thresholds get tuned.
- anything degraded: products that errored, null HV (a freshly rolled front
  month has too few bars), `supabase_available: false`, stale quotes because the
  market was closed

If nothing qualified, say so plainly and say what was closest and why it missed.
"Zero alerts" alone is a wasted notification.

---

## Notes

- **Never place a trade.** Analysis only.
- **Never suggest selling options**, spreads that are net credit, or outright
  futures positions. Defined risk only, and long premium is the only defined-risk
  structure this tool publishes.
- If the scan returns candidates but you reject all of them, publish no alerts
  and still write the summary.
- Rationales are stored in Supabase alongside the alert and become the record
  used later to judge whether the screen works. Write them for a reader with no
  context a month from now.
- HV coming back null right after a quarterly roll is expected, not a bug. The
  new front month has few bars. Weight those runs down rather than trusting a
  ratio built on a thin sample.
