"""agent-fun/replay/v2 entry point: python3 -P -s main.py with PYTHONHASHSEED=0.

hello  -> build the graduation hunter
batch  -> GradEngine.on_batch returns this slot's orders (possibly none)
done   -> print a one-line summary to stderr (stdout carries replies only)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))

import makefun_bot  # noqa: E402
from trader.grad import GradEngine  # noqa: E402


class Bot:
    def __init__(self):
        self.engine = None

    def on_hello(self, state):
        self.engine = GradEngine(state.hello)

    def on_batch(self, state, batch):
        return self.engine.on_batch(state, batch)

    def on_done(self, state, done):
        self.engine.report()


if __name__ == "__main__":
    bot = Bot()
    makefun_bot.run(bot.on_batch, bot.on_hello, bot.on_done)
