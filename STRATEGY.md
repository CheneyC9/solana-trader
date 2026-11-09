# Strategy

## What it trades on

One pattern: pump.fun curves in the last stretch before graduation. A curve graduates at 85 SOL of real reserve. On the
24-hour dev window, 20 organic coins crossed 55 SOL; 17 of them climbed another 45% or more in price toward that line, and
from there each either graduated (7 coins) or fell about 90% (the rest). So the bot buys the run-up and sells before the
fall, and lets the replay close whatever graduates.

- **Entry:** a coin whose real SOL reserve sits in 55 to 66 SOL, at least 60 slots old, not complete, never traded by the
  bot before (one entry per coin).
- **Sizing:** a position of about 10% of equity (1 SOL at the start), built over several slots because one reply may spend
  at most 1% of the coin's real reserve (the bot uses 90% of that cap). At most 3 such coins at once.
- **Exit:** sell the whole position when its liquidation value after fees is 40% over cost (the typical run-up from 55 SOL
  to about 75 SOL), or 60% under cost, or after 20,000 slots. Graduation closes the position at the last trade's
  reserves. The stop is wide on purpose: graduating coins often dip 10 to 25% first, and a rug gaps through any stop.
  Sells allow 30% slippage so they always go through.

## Qualifier sleeve (a bounded cost, not an edge)

The contest's guardrail wants 20 distinct coins with a round trip of at least 0.01 SOL. If the hunter trades fewer, a small
sleeve buys 0.04 SOL in coins above 4 SOL of real reserve, one every ~8 minutes after a 30-minute warm-up, and sells at +15%,
-10% or after 900 slots, until 24 coins qualify. On dev the hunter alone reaches 43 coins and the sleeve is a few cents.

## What to expect

Dev window: +3.15 SOL net, 43 coins, 56% of round trips profitable, 18% maximum drawdown, +2.42 SOL at doubled fees. That
is one window and about 43 trades; the 90% interval is -1.3 to +7.7 SOL, and neighbouring settings (entry level, exit
level) range from -0.8 to +3.2 SOL, so most of the dev score is luck about which coins ran. The kit's stress test, which
rebuilds the window from other coins' recorded flow, lost money on 11 of 12 copies (median -4.1 SOL); those copies are built from
other coins' flow and probably lack the real pump-and-dump structure, so they may understate the bot, but they cannot confirm
it either: treat the dev score as an upper estimate. The rare large loss is a coin that crosses 55 SOL
and is dumped before the bot can sell: about -90% of that position, roughly 0.9 SOL at the start, and it happened about twice a day on dev. Expect a result
anywhere from a few SOL down to a loss of a few SOL on the final day, with no reliable edge.
