# solana-trader

A graduation hunter for the make.fun `memecoin-backtest-48h-01` pump.fun replay (`agent-fun/replay/v2`). Python 3.13,
standard library only. License: MIT (OSI-approved).

It buys curves that have climbed to 55-66 SOL of real reserve, builds about 10% of equity per coin, sells at +40% (or -60%),
and trades each coin once. See `STRATEGY.md` for the rules and, more importantly, the honest limits: one dev window, about 43
trades, and a stress test where 11 of 12 rebuilt copies of the window lost money.

The first version of this repo (an EV-gated agent ensemble that held cash unless it could prove an edge) is kept under
`trader/engine.py`, `agents.py`, `ev.py`, `paper.py` and `tools/`; it made +0.02 SOL on dev, which was noise.

## Layout

| Path | What |
| --- | --- |
| `main.py` | Entry point: wires the SDK shim to `GradEngine` |
| `trader/grad.py` | The graduation hunter: entry band, position building, exits, qualifier sleeve |
| `trader/book.py` | Per-coin tick history, trailing highs (monotonic deques), features |
| `trader/agents.py` | The agent roster and `roster(scale)` |
| `trader/paper.py` | Shadow ledger: heap-scheduled paper fills under the harness's fill model |
| `trader/ev.py` | Windowed samples, shrunk posterior, live/paper gate with hysteresis |
| `trader/engine.py` | Allocator, exits, sizing, risk stop, qualifier sleeve |
| `tools/` | The data-heap research scripts (below); not used at runtime |
| `tests/` | `python -m unittest discover -s tests` |
| `vendor/makefun_bot.py` | Kit SDK shim, byte-identical to the kit's |

## The data heap: what makes a price point viable?

The research scripts replay the frozen dev window (128,058 events, 1,222 coins) under the harness's
own timing (decide at slot s, fill at s+2) and fee model, and rank what they find.

| Script | Question | Finding |
| --- | --- | --- |
| `tools/mine.py` | Does any feature bucket beat fees at fixed horizons? | No. Mean net return is -3.0% (25 slots), -4.7% (100), -8.3% (400). No bucket is positive in both halves of the window. |
| `tools/mine_exits.py` | Do take-profit / stop exits rescue the dip, burst and flow patterns? | No. The best cell (reversion-style dips on strong curves) is about 0.0% in the second half and -5% in the first. |
| `tools/mine_wallets.py` | Does following wallets whose past buys paid off work, scored strictly online through a resolve-heap? | No. Every score tier loses money in the second half (-2% to -12% per trade, by horizon). |
| `tools/rule_search.py` | Does a heap of 20,000 random multi-feature rules, picked in-sample, hold out of sample? | No. Best in-sample coin-level t is +0.9; the top 40 average -3.1% out of sample, 2 of 40 positive. |

Context that explains why:

- Round-trip drag is about 2.5% in curve fees plus 0.21 mSOL in transaction fees, so a price point
  is only viable if it predicts a move of roughly +3% net of the 2-slot latency, and none does.
- The 7-trade coins that "complete" in the data are born complete and are not tradable; the organic
  coins that reach 75+ SOL of real reserve mostly collapse back to the launch price.
- Ground truth from the kit's own grader on dev: reversion -3.705 SOL, sniper -0.500, momentum -9.470
  (win rates 27%, 12%, 5%), consistent with the -3% to -5% per trade the paper ledger measures.
- Signal agents often score worse than the random-entry controls: dips are falling knives and bursts
  buy the top.

## Result on the dev window (kit grader, process driver)

Graduation hunter (current `main.py`): net PnL +3.15 SOL, 43 coins with a round trip of at least 0.01 SOL, 136 fills, max
drawdown 18%, +2.42 SOL at 2x fees, guardrails met, L1 regrade reproduced byte for byte. Three coins made 25% of the gain; the
90% interval is -1.3 to +7.7 SOL. The kit's `--stress 12` (rebuilt copies of the window) lost money on 11 of 12 copies (median
-4.1 SOL; the copies probably lack real graduation dynamics, so this is a warning, not a forecast), so read the dev score as an upper estimate, not a forecast.

Earlier ensemble (`trader/engine.py`): +0.022 SOL, 24 coins, 0.4% drawdown: noise, with a -0.007 SOL result at 2x fees.

## Running

```bash
PYTHONHASHSEED=0 python3 -P -s main.py          # speaks the protocol on stdin/stdout
python -m unittest discover -s tests            # unit tests
python tools/rule_search.py <kit>/dataset/events 20000 7
```

The grader's exact command is `python3 -P -s /bot/main.py`; `main.py` adds its own directory and
`vendor/` to `sys.path`. The bot reads no clock, environment or randomness, so both graded
executions produce the same action log.

## Scaling the earlier ensemble

`Engine(hello, scale=N)` adds exit-rule variants of every tradable agent. Each variant is judged
independently with the same burden of proof, so more agents means more chances to find a real
regime, not more risk: nothing trades until the gate clears it. Per-batch cost grows with agents
times coins touched.
