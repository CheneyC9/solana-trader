"""The allocator: turns agent signals into real orders, but only for agents the gate has cleared.

Per batch: fold in the slot's events, book the fills that came back, advance the paper ledger,
collect every agent's signal on the coins that traded, push the signals of live agents onto a
max-heap keyed by the agent's lower confidence bound on net return, and spend the free position
slots on the best candidates. Exits follow the exit rule of the agent that opened the position.
Everything is a pure function of the messages received: no clock, no randomness, no I/O.
"""

import heapq
import sys

from .agents import roster
from .book import Book
from .ev import Gate
from .paper import Ledger

BUY_SLIP_BPS = 300
SELL_SLIP_BPS = 3000
NOMINAL_LAMPORTS = 100_000_000  # size the paper ledger charges transaction fees against
BASE_FRAC = 0.03  # share of equity one position may take before the 1%-of-reserve cap
MIN_BUY = 30_000_000  # below this the two transaction fees are a visible drag
CAP_MARGIN = 90  # percent of the per-reply size cap actually used
MAX_NEW_PER_BATCH = 2
DD_STOP = 0.80  # stop opening positions below this share of peak equity
REENTRY_COOLDOWN = 300

# Qualifier sleeve. The contest's guardrail wants >= 20 distinct coins with a round trip of at least
# 0.01 SOL; an entry that trades nothing ranks below every entry that does. While no agent has proven
# a positive EV the sleeve takes the least-negative one, at small size and a slow pace, until enough
# distinct coins qualify, then stops. It is a deliberate, bounded cost, not an edge.
SLEEVE_TARGET = 24  # distinct qualifying coins to reach (the bar is 20)
SLEEVE_START = 4500  # slots of paper-only warm-up before the sleeve may trade (~30 minutes)
SLEEVE_PACE = 1200  # slots between sleeve entries (~8 minutes), spreading them over the window
SLEEVE_SIZE = 40_000_000  # lamports per sleeve entry (before the per-reply size cap)
SLEEVE_MIN_N = 15  # paper results an agent needs before the sleeve may pick it
QUALIFY_LAMPORTS = 10_500_000  # round trip size (buys incl. curve fees) that counts: 0.01 SOL plus margin


class Meta:
    __slots__ = ("agent", "entry_slot", "cost")

    def __init__(self, agent, entry_slot, cost):
        self.agent = agent
        self.entry_slot = entry_slot
        self.cost = cost


def liquidation_value(vq, vt, rq, tokens, fee_frac):
    """SOL a sell of `tokens` would pay against reserves (vq, vt, real quote rq), curve fees deducted."""
    if tokens <= 0 or vt + tokens <= 0:
        return 0
    q = min(tokens * vq // (vt + tokens), rq)
    return int(q * (1 - fee_frac))


class Engine:
    def __init__(self, hello, scale=1):
        fees = hello["fees"]
        self.fee_frac = (int(fees["protocol_bps_fallback"]) + int(fees["creator_bps_fallback"])) / 10_000
        self.tx = int(fees["base_fee_lamports"]) + int(fees["priority_fee_lamports"])
        self.latency = int(hello["latency_slots"])
        self.max_pos = int(hello["caps"]["max_open_positions"])
        self.max_orders = int(hello["caps"]["max_orders_per_batch"])
        self.agents = roster(scale)
        self.gate = Gate(len(self.agents))
        self.ledger = Ledger(
            self.agents, self.gate, self.latency, self.fee_frac, 2 * self.tx / NOMINAL_LAMPORTS, BUY_SLIP_BPS / 10_000
        )
        self.books = {}
        self.meta = {}  # mint -> Meta of the live position
        self.intents = {}  # client_id -> (agent index, mint) of a buy in flight
        self.cool = {}  # mint -> slot before which no new live entry
        self.peak = int(hello["capital"]["lamports"])
        self.live_trades = [0] * len(self.agents)
        self.qualified = set()  # coins with a closed round trip of at least QUALIFY_LAMPORTS
        self.sleeve_open = 0  # sleeve buys sent and not yet closed
        self.sleeve_mints = set()
        self.last_sleeve = -(10**12)

    # ------------------------------------------------------------------ ingest

    def _ingest(self, batch):
        slot = batch["slot"]
        touched = []
        seen = set()
        for e in batch["events"]:
            kind = e["event"]
            mint = e["mint"]
            if kind == "CreateEvent":
                self.books[mint] = Book(mint, e["slot"])
                continue
            book = self.books.get(mint)
            if book is None:
                book = self.books[mint] = Book(mint, None)
            if kind == "TradeEvent":
                vt = int(e["virtual_token_reserves"])
                if vt > 0:
                    book.add(slot, int(e["virtual_quote_reserves"]) / vt, int(e["real_quote_reserves"]), e["is_buy"], int(e["sol_amount"]))
            else:
                book.complete = True
            if mint not in seen:
                seen.add(mint)
                touched.append(mint)
        return touched

    def _book_fills(self, state, batch):
        slot = batch["slot"]
        for f in batch["fills"]:
            mint = f["mint"]
            if f["side"] == "buy":
                intent = self.intents.pop(f["client_id"], None)
                if intent is not None and f["status"] == "filled":
                    held = state.positions.get(mint)
                    cost = held.cost_lamports if held else int(f["quote_lamports"])
                    self.meta[mint] = Meta(intent[0], f["slot"], max(1, cost))
                elif intent is not None and intent[2]:
                    self.sleeve_mints.discard(mint)  # the sleeve buy never filled: release its slot
                    self.sleeve_open -= 1
            elif f["status"] == "filled" and mint not in state.positions:
                m = self.meta.pop(mint, None)
                if m is not None:
                    realized = (int(f["quote_lamports"]) - m.cost - 2 * self.tx) / m.cost
                    self.gate.record(m.agent, realized)
                    self.live_trades[m.agent] += 1
                    self.cool[mint] = slot + REENTRY_COOLDOWN
                    if m.cost >= QUALIFY_LAMPORTS:
                        self.qualified.add(mint)
                    if mint in self.sleeve_mints:
                        self.sleeve_mints.discard(mint)
                        self.sleeve_open -= 1

    # ------------------------------------------------------------------ decide

    def on_batch(self, state, batch):
        slot = batch["slot"]
        touched = self._ingest(batch)
        self._book_fills(state, batch)
        self.ledger.step(slot, self.books, touched)
        marks = {m["mint"]: m for m in batch["marks"]}
        sell_pending = {p.order["mint"] for p in state.pending.values() if p.order["side"] == "sell"}
        buy_pending = {p.order["mint"] for p in state.pending.values() if p.order["side"] == "buy"}

        orders = []
        equity = state.cash
        for mint, held in state.positions.items():
            vq, vt, rq = self._reserves(state, marks, mint)
            value = liquidation_value(vq, vt, rq, held.token_amount, self.fee_frac)
            equity += value
            if mint in sell_pending or len(orders) >= self.max_orders:
                continue
            m = self.meta.get(mint)
            if m is None or self._should_exit(m, value, slot):
                orders.append({"side": "sell", "mint": mint, "token_amount": held.token_amount, "max_slippage_bps": SELL_SLIP_BPS})
        self.peak = max(self.peak, equity)

        cands = []
        sleeve = None  # the best (posterior mean, -agent, mint) signal this batch, for the qualifier sleeve
        for mint in touched:
            book = self.books[mint]
            if book.complete:
                continue
            f = book.features(slot)
            if f is None:
                continue
            for idx, ag in enumerate(self.agents):
                if not ag.pred(f, mint, slot):
                    continue
                if self.ledger.can_open(idx, mint, slot):
                    self.ledger.open(idx, mint, slot, f["px"])
                if ag.tradable and self.gate.live[idx]:
                    heapq.heappush(cands, (-self.gate.lcb(idx), idx, mint))
                if self.gate.samples[idx].n >= SLEEVE_MIN_N:
                    key = (self.gate.posterior(idx)[0], -idx, mint)
                    if sleeve is None or key > sleeve:
                        sleeve = key

        if equity >= DD_STOP * self.peak:
            self._enter(state, cands, orders, equity, slot, buy_pending, sleeve)
        return orders

    def _should_exit(self, m, value, slot):
        ag = self.agents[m.agent]
        r = value / m.cost - 1
        return r >= ag.tp or r <= -ag.sl or slot - m.entry_slot >= ag.hold

    def _reserves(self, state, marks, mint):
        mk = marks.get(mint)
        if mk is not None:
            return int(mk["virtual_quote_reserves"]), int(mk["virtual_token_reserves"]), int(mk["real_quote_reserves"])
        c = state.curves[mint]
        return c.virtual_quote_reserves, c.virtual_token_reserves, c.real_quote_reserves

    def _enter(self, state, cands, orders, equity, slot, buy_pending, sleeve):
        free = self.max_pos - state.committed_position_count()
        spent = 0
        new = 0
        taken = set()
        while cands and free > 0 and new < MAX_NEW_PER_BATCH and len(orders) < self.max_orders:
            neg_lcb, idx, mint = heapq.heappop(cands)
            if mint in taken or mint in state.positions or mint in buy_pending or self.cool.get(mint, -1) > slot:
                continue
            lcb = -neg_lcb
            want = int(equity * BASE_FRAC * min(1.0, max(0.25, lcb / 0.03)))
            size = min(state.max_buy_lamports(mint) * CAP_MARGIN // 100, want, state.free_cash_lamports() - spent - 2 * self.tx)
            if size < MIN_BUY:
                continue
            self._buy(state, orders, idx, mint, size, False)
            spent += size + self.tx
            taken.add(mint)
            free -= 1
            new += 1
        if sleeve is None or free <= 0 or len(orders) >= self.max_orders or slot < SLEEVE_START:
            return
        if slot - self.last_sleeve < SLEEVE_PACE or len(self.qualified) + self.sleeve_open >= SLEEVE_TARGET:
            return
        _, neg_idx, mint = sleeve
        if mint in taken or mint in state.positions or mint in buy_pending or mint in self.qualified or self.cool.get(mint, -1) > slot:
            return
        size = min(state.max_buy_lamports(mint) * CAP_MARGIN // 100, SLEEVE_SIZE, state.free_cash_lamports() - spent - 2 * self.tx)
        if size < QUALIFY_LAMPORTS + 500_000:
            return  # too thin a curve to make a qualifying round trip
        self._buy(state, orders, -neg_idx, mint, size, True)
        self.sleeve_open += 1
        self.sleeve_mints.add(mint)
        self.last_sleeve = slot

    def _buy(self, state, orders, idx, mint, size, is_sleeve):
        cid = state.client_id()
        self.intents[cid] = (idx, mint, is_sleeve)
        orders.append({"side": "buy", "client_id": cid, "mint": mint, "quote_lamports": size, "max_slippage_bps": BUY_SLIP_BPS})

    # ------------------------------------------------------------------ report

    def report(self, out=sys.stderr):
        m0, v0 = self.gate.prior()
        out.write("baseline prior %+.4f (pool n=%d)  qualified coins %d\n" % (m0, self.gate.pool.n, len(self.qualified)))
        for i, ag in enumerate(self.agents):
            mu, se = self.gate.posterior(i)
            out.write(
                "%-14s paper n=%4d  post %+.4f se %.4f  %s  live trades %d\n"
                % (ag.name, self.gate.samples[i].n, mu, se, "LIVE" if self.gate.live[i] else "paper", self.live_trades[i])
            )
