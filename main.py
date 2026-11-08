"""agent-fun/replay/v2 entry point: python3 -P -s main.py with PYTHONHASHSEED=0.

hello  -> build the engine (agent roster, EV gate, paper ledger)
batch  -> Engine.on_batch returns this slot's orders (possibly none)
done   -> print the per-agent EV report to stderr (stdout carries replies only)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))

import makefun_bot  # noqa: E402
from trader.engine import Engine  # noqa: E402


class Bot:
    def __init__(self):
        self.engine = None

    def on_hello(self, state):
        self.engine = Engine(state.hello)

    def on_batch(self, state, batch):
        return self.engine.on_batch(state, batch)

    def on_done(self, state, done):
        self.engine.report()


if __name__ == "__main__":
    bot = Bot()
    makefun_bot.run(bot.on_batch, bot.on_hello, bot.on_done)
