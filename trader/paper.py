"""Shadow ledger: every agent signal is paper-traded under the harness's own execution model.

A signal decided at slot s enters at the last recorded price at or before s + latency (and is
dropped, like a rejected order, when that price has run past the slippage guard). It then exits on
the agent's take-profit, stop or max-hold, again filling `latency` slots after the decision. Both
fills are scheduled on a min-heap keyed by due slot, so the cost per batch is the number of fills
that are due, not the number of open paper positions. Costs charged: 125 bps curve fees each way
and the two transaction fees against a nominal trade size.
"""

import heapq

ENTRY, OPEN, EXITING = 0, 1, 2


class PaperTrade:
    __slots__ = ("agent", "mint", "state", "dec_px", "entry_px", "entry_slot", "key")

    def __init__(self, agent, mint, dec_px, key):
        self.agent = agent
        self.mint = mint
        self.state = ENTRY
        self.dec_px = dec_px
        self.entry_px = 0.0
        self.entry_slot = 0
        self.key = key


class Ledger:
    def __init__(self, agents, gate, latency, fee_frac, tx_frac, slip, cooldown=100):
        self.agents = agents
        self.gate = gate
        self.latency = latency
        self.fee_frac = fee_frac
        self.tx_frac = tx_frac  # two transaction fees as a share of the nominal trade size
        self.slip = slip
        self.cooldown = cooldown
        self.fills = []  # (due_slot, seq, trade)
        self.deadlines = []  # (deadline_slot, seq, trade)
        self.by_mint = {}  # mint -> open trades on it
        self.busy = {}  # (agent, mint) -> slot before which the pair may not signal again
        self.seq = 0
        self.open_count = 0

    def can_open(self, agent, mint, slot):
        return self.busy.get((agent, mint), -1) <= slot and self.open_count < 600

    def open(self, agent, mint, slot, dec_px):
        key = (agent, mint)
        t = PaperTrade(agent, mint, dec_px, key)
        self.busy[key] = 10**12  # blocked until this trade resolves
        self.by_mint.setdefault(mint, []).append(t)
        self._push(self.fills, slot + self.latency, t)
        self.open_count += 1

    def _push(self, heap, due, t):
        self.seq += 1
        heapq.heappush(heap, (due, self.seq, t))

    def _close(self, t, slot):
        self.by_mint[t.mint].remove(t)
        self.busy[t.key] = slot + self.cooldown
        self.open_count -= 1

    def step(self, slot, books, touched):
        """Advance to `slot`: settle due fills, then test exits on touched coins and expired holds."""
        while self.fills and self.fills[0][0] <= slot:
            due, _, t = heapq.heappop(self.fills)
            book = books[t.mint]
            px = book.price_at(due)
            if t.state == ENTRY:
                if px is None or px / t.dec_px - 1 > self.slip:
                    self._close(t, slot)  # rejected: the market ran away from the decision price
                    continue
                t.state, t.entry_px, t.entry_slot = OPEN, px, due
                ag = self.agents[t.agent]
                self._push(self.deadlines, due + ag.hold, t)
            else:
                gross = (px if px is not None else t.entry_px) / t.entry_px
                net = gross * (1 - self.fee_frac) / (1 + self.fee_frac) - 1 - self.tx_frac
                self.gate.record(t.agent, net)
                self._close(t, slot)
        for mint in touched:
            for t in list(self.by_mint.get(mint, ())):
                if t.state == OPEN:
                    book = books[mint]
                    r = book.px[-1] / t.entry_px - 1
                    ag = self.agents[t.agent]
                    if r >= ag.tp or r <= -ag.sl or book.complete:
                        t.state = EXITING
                        self._push(self.fills, slot + self.latency, t)
        while self.deadlines and self.deadlines[0][0] <= slot:
            _, _, t = heapq.heappop(self.deadlines)
            if t.state == OPEN:
                t.state = EXITING
                self._push(self.fills, slot + self.latency, t)
