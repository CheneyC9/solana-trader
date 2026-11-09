"""Graduation hunter: build a position in curves that have climbed to the last stretch before graduation, and sell into the run.

A pump.fun curve graduates at 85 SOL of real reserve. Past about 55 SOL the buying that got a coin there tends to carry it
toward that line, and a coin that stalls short of it tends to fall back hard. So the bot buys coins whose real reserve sits
in [entry_lo, entry_hi], building the position over several slots (a reply may spend only 1% of the coin's real reserve),
sells on a take-profit or a stop measured on liquidation value after fees, and lets the replay close whatever graduates.
A small, slow qualifier sleeve exists only to reach the contest's 20-distinct-coin guardrail if the hunter trades fewer.

Pure function of the messages received: no clock, no randomness, no I/O.
"""

import sys

from bisect import bisect_right

from .book import Book

DEFAULTS = {
    "entry_lo": 55.0,  # real SOL reserve where entries start
    "entry_hi": 66.0,  # no new money above this
    "target_frac": 0.10,  # position cost target as a share of equity
    "tp": 0.40,  # take profit on liquidation value over cost
    "sl": 0.60,  # stop on liquidation value under cost (wide on purpose: graduating coins dip first, and a rug gaps through any stop)
    "max_hold": 20000,  # slots
    "max_pos": 3,  # coins held at once by the hunter (the cap is 5)
    "min_age": 60,  # slots since creation before a coin may be bought
    "dd_stop": 0.0,  # stop opening below this share of peak equity (off: the stop-outs it caused cost more than it saved on dev)
    "cooldown": 10**12,  # one entry per coin: coins that stopped out never came back on dev
    "buy_slip": 400,
    "sell_slip": 3000,
    "cap_margin": 90,  # percent of the per-reply size cap used
    "min_buy": 20_000_000,
    "speed_slots": 0,  # look-back for the climb filter (0 = off)
    "speed_min": 0.0,  # real SOL the coin must have gained over speed_slots
    "max_age": 10**12,  # slots since creation after which a coin is not bought
    "buyfrac_k": 0,  # trades looked at for the buy share filter (0 = off)
    "buyfrac_min": 0.0,
    "exit_real": 1e9,  # sell once the coin's real reserve reaches this many SOL (a level exit, on top of tp)
    # stream B: dip buys inside the near-graduation zone
    "b_on": 0,  # off: on dev the dip stream took coins and slots just before the crossing entry and added little (+0.47 alone, negative at 2x fees)
    "b_lo": 50.0,  # real SOL reserve band for dip buys
    "b_hi": 80.0,
    "b_dip": 0.08,  # buy when the price is this far under its 300-slot high
    "b_tp": 0.15,
    "b_sl": 0.25,
    "b_hold": 1500,  # slots
    "b_cool": 300,  # slots before the same coin may be dip-bought again
    "b_frac": 0.05,  # position size as a share of equity (before the governor)
    "b_max": 3,  # dip positions at once
    # risk governor: sizes scale with equity against its peak, from 1 (at gov_hi) down to 0 (at gov_lo)
    "gov_hi": 0.88,
    "gov_lo": 0.55,
}

QUALIFY_LAMPORTS = 10_050_000  # buys of a round trip (curve fees included) this large count toward the 20-coin bar (0.01 SOL + margin)
SLEEVE_TARGET = 24
SLEEVE_START = 4500
SLEEVE_PACE = 1200
SLEEVE_SIZE = 40_000_000
SLEEVE_TP = 0.15
SLEEVE_SL = 0.10
SLEEVE_HOLD = 900


class Meta:
    __slots__ = ("kind", "entry_slot", "max_cost", "bought")

    def __init__(self, kind, entry_slot):
        self.kind = kind
        self.entry_slot = entry_slot
        self.max_cost = 0  # largest position cost seen in an account snapshot
        self.bought = 0  # lamports paid by this entry's filled buys, curve fees included (from the fills)


def liquidation_value(vq, vt, rq, tokens, fee_frac):
    """SOL a sell of `tokens` would pay against reserves (vq, vt, real quote rq), curve fees deducted."""
    if tokens <= 0 or vt + tokens <= 0:
        return 0
    q = min(tokens * vq // (vt + tokens), rq)
    return int(q * (1 - fee_frac))


class GradEngine:
    def __init__(self, hello, params=None):
        self.p = dict(DEFAULTS)
        if params:
            self.p.update(params)
        fees = hello["fees"]
        self.fee_frac = (int(fees["protocol_bps_fallback"]) + int(fees["creator_bps_fallback"])) / 10_000
        self.tx = int(fees["base_fee_lamports"]) + int(fees["priority_fee_lamports"])
        self.max_orders = int(hello["caps"]["max_orders_per_batch"])
        self.max_pos = int(hello["caps"]["max_open_positions"])
        self.books = {}
        self.meta = {}  # mint -> Meta of a live position
        self.cool = {}  # mint -> slot before which no new entry
        self.peak = int(hello["capital"]["lamports"])
        self.qualified = set()
        self.last_sleeve = -(10**12)
        self.first_slot = None
        self.scale = 1.0
        self.closed = 0
        self.wins = 0
        self.trace = []  # one line per entry and exit, printed at the end for research

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

    def _reserves(self, state, marks, mint):
        mk = marks.get(mint)
        if mk is not None:
            return int(mk["virtual_quote_reserves"]), int(mk["virtual_token_reserves"]), int(mk["real_quote_reserves"])
        c = state.curves[mint]
        return c.virtual_quote_reserves, c.virtual_token_reserves, c.real_quote_reserves

    # ------------------------------------------------------------------ decide

    def on_batch(self, state, batch):
        p = self.p
        slot = batch["slot"]
        if self.first_slot is None:
            self.first_slot = slot
        touched = self._ingest(batch)
        marks = {m["mint"]: m for m in batch["marks"]}
        for f in batch["fills"]:
            if f["side"] == "buy" and f["status"] == "filled":
                fm = self.meta.get(f["mint"])
                if fm is not None:
                    fm.bought += int(f["quote_lamports"])
        sell_pending = {q.order["mint"] for q in state.pending.values() if q.order["side"] == "sell"}
        buy_pending = {}
        for q in state.pending.values():
            if q.order["side"] == "buy":
                buy_pending[q.order["mint"]] = buy_pending.get(q.order["mint"], 0) + int(q.order["quote_lamports"])

        # books of closed positions
        for mint in [m for m in self.meta if m not in state.positions and m not in buy_pending]:
            meta = self.meta.pop(mint)
            if meta.bought == 0 and meta.max_cost == 0:
                continue  # the buy never filled (rejected or failed): no position existed, so no cooldown and no round trip
            self.cool[mint] = slot + (p["b_cool"] if meta.kind == "dip" else p["cooldown"])
            self.closed += 1
            if max(meta.max_cost, meta.bought) >= QUALIFY_LAMPORTS:
                self.qualified.add(mint)

        orders = []
        equity = state.cash
        for mint, held in state.positions.items():
            vq, vt, rq = self._reserves(state, marks, mint)
            value = liquidation_value(vq, vt, rq, held.token_amount, self.fee_frac)
            equity += value
            meta = self.meta.get(mint)
            if meta is None:
                meta = self.meta[mint] = Meta("grad", slot)
            meta.max_cost = max(meta.max_cost, held.cost_lamports)
            if mint in sell_pending or mint in buy_pending or len(orders) >= self.max_orders:
                continue
            r = value / max(1, held.cost_lamports) - 1
            if meta.kind == "sleeve":
                out = r >= SLEEVE_TP or r <= -SLEEVE_SL or slot - meta.entry_slot >= SLEEVE_HOLD
            elif meta.kind == "dip":
                out = r >= p["b_tp"] or r <= -p["b_sl"] or slot - meta.entry_slot >= p["b_hold"]
            else:
                out = r >= p["tp"] or r <= -p["sl"] or slot - meta.entry_slot >= p["max_hold"] or rq / 1e9 >= p["exit_real"]
            if out:
                orders.append({"side": "sell", "mint": mint, "token_amount": held.token_amount, "max_slippage_bps": p["sell_slip"]})
                sell_pending.add(mint)  # nothing more is bought on this coin in this reply
                self.trace.append("SELL %s %s r=%+.3f real=%.1f cost=%.3f held=%d" % (mint[:6], meta.kind, r, rq / 1e9, held.cost_lamports / 1e9, slot - meta.entry_slot))
        self.peak = max(self.peak, equity)

        span = max(1e-9, p["gov_hi"] - p["gov_lo"])
        self.scale = min(1.0, max(0.0, (equity / self.peak - p["gov_lo"]) / span))
        if equity >= p["dd_stop"] * self.peak:
            self._enter(state, touched, orders, equity, slot, buy_pending, sell_pending, marks)
        return orders

    def _enter(self, state, touched, orders, equity, slot, buy_pending, sell_pending, marks):
        p = self.p
        target = int(equity * p["target_frac"] * self.scale)
        spent = self.tx * sum(1 for o in orders if o["side"] == "sell")
        held_grad = sum(1 for m in self.meta.values() if m.kind == "grad")
        committed = state.committed_position_count()
        best_sleeve = None
        cands = []
        for mint in touched:
            book = self.books[mint]
            if book.complete or not book.px or mint in sell_pending:
                continue
            mk = marks.get(mint)  # a mark includes our own impact on a coin we hold
            real = (int(mk["real_quote_reserves"]) if mk is not None else book.real[-1]) / 1e9
            age = slot - book.created if book.created is not None else 10**9
            if p["entry_lo"] <= real <= p["entry_hi"] and p["min_age"] <= age <= p["max_age"] and self._filters(book, slot, real):
                cands.append((-real, mint))
            elif (
                4.0 <= real < p["entry_lo"]
                and (best_sleeve is None or real > best_sleeve[0])
                and mint not in state.positions
                and mint not in buy_pending
                and mint not in self.qualified
                and self.cool.get(mint, -1) <= slot
            ):
                best_sleeve = (real, mint)
        cands.sort()
        for _, mint in cands:
            if len(orders) >= self.max_orders:
                break
            held = state.positions.get(mint)
            pend = buy_pending.get(mint, 0)
            if held is None and pend == 0:
                if self.cool.get(mint, -1) > slot or committed >= self.max_pos or held_grad >= p["max_pos"]:
                    continue
                opening = True
            else:
                meta = self.meta.get(mint)
                if meta is not None and meta.kind != "grad":
                    continue
                opening = False
            have = (held.cost_lamports if held else 0) + pend
            want = target - have
            size = min(state.max_buy_lamports(mint) * p["cap_margin"] // 100, want, state.free_cash_lamports() - spent - 2 * self.tx)
            if size < p["min_buy"]:
                continue
            cid = state.client_id()
            orders.append({"side": "buy", "client_id": cid, "mint": mint, "quote_lamports": size, "max_slippage_bps": p["buy_slip"]})
            spent += size + self.tx
            if opening:
                committed += 1
                held_grad += 1
                self.meta[mint] = Meta("grad", slot)
                b = self.books[mint]
                self.trace.append("BUY  %s grad real=%.1f age=%s slot=%d size=%.3f" % (mint[:6], b.real[-1] / 1e9, slot - b.created if b.created is not None else -1, slot, size / 1e9))
        # stream B: dip buys inside the near-graduation zone
        if p["b_on"] and self.scale > 0:
            bought = {o["mint"] for o in orders if o["side"] == "buy"}
            nb = sum(1 for m in self.meta.values() if m.kind == "dip")
            bc = []
            for mint in touched:
                book = self.books[mint]
                if book.complete or not book.px or not book.hi_short or mint in bought or mint in sell_pending:
                    continue
                if mint in state.positions or mint in buy_pending or self.cool.get(mint, -1) > slot:
                    continue
                mk = marks.get(mint)
                real = (int(mk["real_quote_reserves"]) if mk is not None else book.real[-1]) / 1e9
                if not (p["b_lo"] <= real <= p["b_hi"]):
                    continue
                dip = book.px[-1] / book.hi_short[0][1] - 1
                if dip <= -p["b_dip"]:
                    bc.append((dip, mint))
            bc.sort()
            for dip, mint in bc:
                if len(orders) >= self.max_orders or nb >= p["b_max"] or committed >= self.max_pos:
                    break
                want = int(equity * p["b_frac"] * self.scale)
                size = min(state.max_buy_lamports(mint) * p["cap_margin"] // 100, want, state.free_cash_lamports() - spent - 2 * self.tx)
                if size < p["min_buy"]:
                    continue
                cid = state.client_id()
                orders.append({"side": "buy", "client_id": cid, "mint": mint, "quote_lamports": size, "max_slippage_bps": p["buy_slip"]})
                spent += size + self.tx
                committed += 1
                nb += 1
                self.meta[mint] = Meta("dip", slot)
                self.trace.append("BUY  %s dip dip=%+.3f slot=%d size=%.3f scale=%.2f" % (mint[:6], dip, slot, size / 1e9, self.scale))
        # qualifier sleeve
        if best_sleeve is None or len(orders) >= self.max_orders or slot - self.first_slot < SLEEVE_START:
            return
        sleeve_open = sum(1 for m in self.meta.values() if m.kind == "sleeve")
        if slot - self.last_sleeve < SLEEVE_PACE or len(self.qualified) + sleeve_open >= SLEEVE_TARGET or committed >= self.max_pos:
            return
        mint = best_sleeve[1]
        size = min(state.max_buy_lamports(mint) * p["cap_margin"] // 100, SLEEVE_SIZE, state.free_cash_lamports() - spent - 2 * self.tx)
        if size < QUALIFY_LAMPORTS + 500_000:
            return
        cid = state.client_id()
        orders.append({"side": "buy", "client_id": cid, "mint": mint, "quote_lamports": size, "max_slippage_bps": p["buy_slip"]})
        self.meta[mint] = Meta("sleeve", slot)
        self.last_sleeve = slot

    def _filters(self, book, slot, real):
        p = self.p
        if p["speed_slots"]:
            i = bisect_right(book.slots, slot - p["speed_slots"]) - 1
            before = book.real[i] / 1e9 if i >= 0 else 0.0
            if real - before < p["speed_min"]:
                return False
        k = p["buyfrac_k"]
        if k and len(book.buy) >= k:
            if sum(1 for b in book.buy[-k:] if b) / k < p["buyfrac_min"]:
                return False
        return True

    # ------------------------------------------------------------------ report

    def report(self, out=sys.stderr):
        out.write("\n".join(self.trace) + "\n")
        out.write("closed %d  qualified coins %d  params %s\n" % (self.closed, len(self.qualified), self.p))
