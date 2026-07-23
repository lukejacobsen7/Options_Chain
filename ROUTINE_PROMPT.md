# Routine prompt

Paste this into the routine's **Instructions** box. It has to be self-contained:
the routine runs autonomously with no approval prompts and no conversation
history.

---

You are scanning for options that are priced cheaper than recent price action
and news justify. Work through this in order and do not skip steps.

**1. Run the scan.**

```
python -m scanner.run --out /tmp/candidates.json
```

If it exits non-zero, read the error. A missing env var or an unreachable
Supabase project (free tier pauses after ~7 days idle) means stop and send a
single Telegram message saying the scan could not run and why. Do not send
trade ideas from a failed scan.

**2. Read the bundle.** `/tmp/candidates.json` gives you, per ticker: the
quantitative signals (IV vs realized vol, term structure, skew, peer-relative
IV, IV rank once history exists), recent price moves, earnings date, the last
24h of headlines, and the contracts that passed the liquidity gate.

**3. Make the judgment call.** This is the part that is yours, not the
script's. For each candidate ask:

- Does the options market's pricing match what the news actually says? A stock
  that gapped on a real catalyst with IV that never moved is the setup. A stock
  that is quiet with quiet options is not.
- Is the move explained? If the name ran 6% on nothing in the headlines, that
  is unexplained movement and the cheap IV is probably a trap, not an edge.
- Is there a known event ahead that the IV is not pricing? Check the earnings
  date field. Anything inside the blackout was already dropped, but a print at
  the edge of the window still matters.
- Which side is actually cheap? Skew tells you whether calls or puts are the
  underpriced leg. Do not default to buying calls because the tape is green.

Reject anything you cannot write a specific one-sentence reason for. "IV is
low" is not a reason. "Stock has realized 70% vol for three weeks while
front-month IV sits at 55% and there is no earnings until October" is.

**4. Send the survivors.** Expect zero to three per run. Zero is a normal and
correct outcome, and sending nothing beats sending noise. For each one:

```
python -m scanner.emit \
  --ticker MU \
  --contract '<the contract object, verbatim JSON from the bundle>' \
  --direction "BUY CALL" \
  --spot 995.94 \
  --rationale "<your one-sentence why>" \
  --refresh
```

`--direction` must be exactly one of: `BUY CALL`, `BUY PUT`, `SELL CALL`,
`SELL PUT`.

`--refresh` re-pulls the quote before sending and silently drops the alert if
the contract no longer passes the liquidity gate. Always pass it.

Duplicates are handled for you: the same contract and direction will not fire
twice within 48 hours.

**5. Say what you did.** Finish with a short summary in the session: how many
tickers scanned, how many candidates cleared the gate, how many alerts sent,
and for anything you rejected that scored well, one line on why you passed.
That summary is how the thresholds get tuned.

---

## Notes

- Never place a trade. This routine sends alerts only.
- If the scan returns candidates but you reject all of them, send no Telegram
  message. Silence is the signal that nothing qualified.
- Rationales go into Supabase alongside the alert, so they are the record used
  later to judge whether the screen is any good. Write them for a reader who
  will see them in a month with no context.
