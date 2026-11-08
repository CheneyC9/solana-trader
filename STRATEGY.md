# Strategy

## What it trades on

Nothing, until the data says a pattern pays. On the 24-hour dev window every price-pattern family we
tested loses to fees: a random entry nets about -2.4% to -4.5% per round trip (125 bps curve fees each
way, two 105,000-lamport transaction fees, 2-slot latency), and the pilot's three house bots all lose
on dev (reversion -3.7 SOL, sniper -0.5, momentum -9.5). A search over 20,000 random multi-feature
rules, scored on one half of the window and tested on the other, found nothing: the 40 best
in-sample rules averaged -3.1% out of sample.

So the bot is built to *measure* edge online and to refuse to pay for any it cannot show.

- **Thirteen agents** (a roster of one-line entry predicates: dips, momentum bursts, young-coin flow,
  sell-flushes, late-curve runs, plus two signal-free controls). Each carries its own exit rule
  (take-profit, stop, max hold). `roster(scale)` multiplies them by exit-rule variants.
- **Shadow ledger.** Every agent signal is paper-traded under the harness's own execution model:
  decided at slot s, filled at the last recorded price at or before s+2, dropped if the market has run
  past the 3% slippage guard, exited by the agent's rule, charged fees and transaction costs. Fills are
  scheduled on a min-heap keyed by due slot.
- **EV gate.** An agent's mean net return is shrunk toward the market baseline (the pooled mean of all
  paper trades, clamped to at most 0). It goes live only with at least 30 results and a shrunk mean
  more than 1.3 standard errors above zero; it is dropped when the shrunk mean turns negative.
  Live results feed the same sample.
- **Allocator.** Signals from live agents go on a max-heap keyed by lower confidence bound; the free
  position slots (at most 5, at most 2 new per batch) go to the best candidates.

## Sizing and exits

Size is 3% of equity scaled by the agent's lower confidence bound (at least 25% of base), capped at
90% of the per-reply limit (1% of the coin's real SOL reserve), and never below 0.03 SOL, where the
transaction fees stop being a visible drag. A position exits on its agent's take-profit or stop,
measured on liquidation value after fees from the harness marks, or when its max hold expires.
Sells use a wide slippage allowance and retry each batch. Opening stops if equity falls to 80% of its
peak.

## Qualifier sleeve (a bounded cost, not an edge)

The contest's guardrail needs 20 distinct coins with a round trip of at least 0.01 SOL, and an entry
that trades nothing ranks below every entry that does. After a 30-minute paper-only warm-up the bot
therefore takes the agent with the least negative posterior mean, at 0.04 SOL, one entry per ~8
minutes, until 24 distinct coins qualify; then it stops. On dev this costs about 0.03 SOL in fees
and is noise around zero. If a live agent trades enough coins, the sleeve never fires.

## What to expect

No claimed edge. On dev the entry made +0.022 SOL with 24 qualifying coins, but three coins made 64%
of it, it is negative at 2x fees, and the 90% interval spans zero. Treat that as luck. The design
goal is to lose almost nothing when the market is negative-sum and to size up when a regime pays.
