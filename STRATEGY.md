# Strategy

## What it trades on

One pattern: pump.fun curves in the last stretch before graduation. A curve graduates at 85 SOL of real reserve. On the
24-hour dev window, 20 organic coins crossed 55 SOL; 17 of them climbed another 45% or more in price, and each then either
graduated (7 coins) or fell about 90%. So the bot buys the run-up and sells before the fall. Holding to graduation loses on
dev (a mean of -10% to -30% per trade at 50 to 65 SOL), so it does not.

- **Entry:** real SOL reserve in 55 to 66 SOL, coin at least 60 slots old with a CreateEvent seen, not complete, never
  traded by the bot before (one entry per coin).
- **Sizing:** about 10% of equity (1 SOL at the start, roughly quarter-Kelly for the measured edge), fixed when the entry
  opens and built over a few slots, since one reply may spend at most 1% of the coin's real reserve (the bot uses 90% of
  that). At most 3 such coins at once.
- **Exit:** sell everything at +40% liquidation value after fees (the usual run-up to about 75 SOL), at -60%, or after
  20,000 slots; graduation closes the position at the last trade's reserves. The stop is wide on purpose: graduating coins
  often dip 10 to 25% first, and a rug gaps through any stop. Exits allow 100% slippage so a coin that dumps between decision
  and fill is still sold, and a top-up in flight never delays an exit.
- **Risk governor:** new entries scale with equity against its peak: full size down to a 12% drawdown, linearly to zero at
  45%. It keeps a bad run from compounding toward the contest's 50% drawdown bar, never shrinks a position being built, and
  costs about 0.2 SOL on dev.

## Qualifier sleeve (a bounded cost, not an edge)

The guardrail wants 20 distinct coins with a round trip of at least 0.01 SOL. If the hunter trades fewer, a sleeve buys
0.04 SOL in coins above 4 SOL of real reserve, one every ~8 minutes after a 30-minute warm-up, and sells at +15%, -10% or
after 900 slots, until 24 coins qualify. It ignores the governor on purpose. On dev the hunter alone reaches 43 coins.

## Tried and dropped

A stream buying 8% dips on coins at 50 to 80 SOL had a small edge per trade but only +0.47 SOL on dev (negative at 2x fees)
and took coins and slots just before the crossing entry; it stays in the code, off (`b_on`). Speed, buy-share and age filters
did not help. Sizes of 12% to 20% raised drawdown to 29% to 50% with no better mean.

## What to expect

Dev window: +2.84 SOL net, 43 coins, 56% of round trips profitable, 18% maximum drawdown, +2.15 SOL at doubled fees, from
one window and about 43 trades. The 90% interval is -1.4 to +7.3 SOL and neighbouring settings range from -0.8 to +3.5 SOL,
so most of the score is luck about which coins ran. The kit's stress copies (rebuilt from other coins' flow, probably without real pump-and-dump structure) lost on 11 of 12, median -3.5 SOL, worst -3.9 (-6.2 before the governor): a warning, not a forecast. The rare large loss is a coin
dumped right after entry: about -90% of that position, roughly 0.9 SOL, about twice a day on dev. Expect anything from a few
SOL of gain to a few SOL of loss on the final day, with no reliable edge.
