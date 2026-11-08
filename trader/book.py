"""Per-coin tick history and the features the agents read.

Price is the curve's marginal price, virtual SOL over virtual tokens. History is kept as parallel
lists (binary-searchable by slot) plus two monotonic deques that give the trailing high over the
long and short windows in amortised O(1).
"""

from bisect import bisect_right
from collections import deque

LONG_WINDOW = 2500  # slots, ~17 minutes
SHORT_WINDOW = 300  # slots, ~2 minutes
KEEP = 6000  # ticks of history kept per coin; trimmed to KEEP // 2 when exceeded


class Book:
    __slots__ = ("mint", "created", "slots", "px", "real", "buy", "sol", "n", "hi_long", "hi_short", "complete")

    def __init__(self, mint, created):
        self.mint = mint
        self.created = created  # slot of the CreateEvent, None if first seen through a trade
        self.slots = []
        self.px = []
        self.real = []  # real SOL reserve, lamports
        self.buy = []
        self.sol = []  # trade size, lamports
        self.n = 0
        self.hi_long = deque()
        self.hi_short = deque()
        self.complete = False

    def add(self, slot, price, real_lamports, is_buy, sol_lamports):
        self.slots.append(slot)
        self.px.append(price)
        self.real.append(real_lamports)
        self.buy.append(is_buy)
        self.sol.append(sol_lamports)
        self.n += 1
        if len(self.slots) > KEEP:
            cut = KEEP // 2
            del self.slots[:cut], self.px[:cut], self.real[:cut], self.buy[:cut], self.sol[:cut]
        for dq, window in ((self.hi_long, LONG_WINDOW), (self.hi_short, SHORT_WINDOW)):
            while dq and dq[-1][1] <= price:
                dq.pop()
            dq.append((slot, price))
            while dq[0][0] < slot - window:
                dq.popleft()

    def price_at(self, slot):
        """Marginal price after the last trade at or before `slot`, None before the first one."""
        i = bisect_right(self.slots, slot) - 1
        return self.px[i] if i >= 0 else None

    def features(self, slot):
        """The decision-time feature dict for this coin, or None while there is too little history."""
        if len(self.px) < 8:
            return None
        px = self.px[-1]
        lo = len(self.px) - 6
        buys = 0
        flow = 0
        for k in range(lo, len(self.px)):
            if self.buy[k]:
                buys += 1
                flow += self.sol[k]
            else:
                flow -= self.sol[k]
        back = self.price_at(slot - 10)
        return {
            "real": self.real[-1] / 1e9,
            "age": slot - self.created if self.created is not None else 10**9,
            "dd_long": px / self.hi_long[0][1] - 1,
            "dd_short": px / self.hi_short[0][1] - 1,
            "buys6": buys,
            "flow6": flow / 1e9,
            "lastbuy": self.buy[-1],
            "ret10": px / back - 1 if back else 0.0,
            "px": px,
        }
