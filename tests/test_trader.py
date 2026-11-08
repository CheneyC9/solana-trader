"""Unit tests for the EV gate, the book features, the paper ledger and the engine's determinism.

    python -m unittest discover -s tests -v
"""

import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import makefun_bot  # noqa: E402
from trader.agents import roster  # noqa: E402
from trader.book import Book  # noqa: E402
from trader.engine import Engine, liquidation_value  # noqa: E402
from trader.ev import Gate, Sample  # noqa: E402
from trader.paper import Ledger  # noqa: E402

HELLO = {
    "type": "hello",
    "protocol": "agent-fun/replay/v2",
    "seed": "7",
    "capital": {"lamports": "10000000000"},
    "caps": {"max_open_positions": 5, "max_order_bps_of_real_reserve": 100, "max_orders_per_batch": 16},
    "latency_slots": 2,
    "fees": {"protocol_bps_fallback": 95, "creator_bps_fallback": 30, "base_fee_lamports": "5000", "priority_fee_lamports": "100000", "failed_tx_bps": 200},
}


class SampleTest(unittest.TestCase):
    def test_window_keeps_running_sums_exact(self):
        s = Sample(3)
        for x in (1.0, 2.0, 3.0, 4.0, 5.0):
            s.add(x)
        self.assertEqual(s.n, 3)
        self.assertAlmostEqual(s.mean(), 4.0)
        self.assertAlmostEqual(s.var(), 1.0)


class GateTest(unittest.TestCase):
    def test_losing_agent_never_goes_live(self):
        g = Gate(2)
        for _ in range(200):
            g.record(0, -0.03)
            g.record(1, -0.04)
        self.assertFalse(any(g.live))

    def test_proven_winner_goes_live_and_is_dropped_when_it_turns(self):
        g = Gate(2)
        for i in range(120):
            g.record(1, -0.03 + (0.01 if i % 2 else -0.01))  # the market baseline, a noisy loser
            g.record(0, 0.10 + (0.02 if i % 2 else -0.02))
        self.assertTrue(g.live[0])
        self.assertFalse(g.live[1])
        for _ in range(150):
            g.record(0, -0.08)
        self.assertFalse(g.live[0])

    def test_too_few_results_stay_paper_even_when_perfect(self):
        g = Gate(1)
        for _ in range(10):
            g.record(0, 0.5)
        self.assertFalse(g.live[0])

    def test_posterior_is_shrunk_toward_the_baseline(self):
        g = Gate(2)
        for _ in range(100):
            g.record(1, -0.04)
        for _ in range(5):
            g.record(0, 0.20)
        mu, _ = g.posterior(0)
        self.assertLess(mu, 0.10)


class BookTest(unittest.TestCase):
    def test_trailing_high_and_drawdown(self):
        b = Book("m", 0)
        prices = [1.0, 1.2, 1.5, 1.4, 1.3, 1.2, 1.1, 1.05, 1.0]
        for i, p in enumerate(prices):
            b.add(i * 5, p, 5_000_000_000, i % 2 == 0, 100_000_000)
        f = b.features(40)
        self.assertAlmostEqual(f["dd_long"], 1.0 / 1.5 - 1)
        self.assertEqual(b.price_at(12), 1.5)  # last trade at or before slot 12 is the slot-10 one
        self.assertEqual(b.price_at(7), 1.2)
        self.assertIsNone(b.price_at(-1))

    def test_window_expiry_forgets_old_highs(self):
        b = Book("m", 0)
        for i in range(10):
            b.add(i, 2.0, 1, True, 1)
        for i in range(10):
            b.add(10_000 + i, 1.0, 1, True, 1)
        self.assertAlmostEqual(b.features(10_009)["dd_long"], 0.0)


class LedgerTest(unittest.TestCase):
    def test_entry_and_exit_fill_two_slots_after_the_decision(self):
        ag = roster(1)
        gate = Gate(len(ag))
        led = Ledger(ag, gate, 2, 0.0125, 0.002, 0.03)
        book = Book("m", 0)
        for s in range(10):
            book.add(s, 1.0, 5_000_000_000, True, 1)
        books = {"m": book}
        led.open(0, "m", 10, 1.0)
        book.add(11, 1.0, 5_000_000_000, True, 1)
        led.step(11, books, ["m"])
        self.assertEqual(led.open_count, 1)
        book.add(12, 1.0, 5_000_000_000, True, 1)
        led.step(12, books, ["m"])  # entry fills here at 1.0
        book.add(13, 1.0 + ag[0].tp + 0.01, 5_000_000_000, True, 1)
        led.step(13, books, ["m"])  # take-profit decided
        book.add(15, 1.3, 5_000_000_000, True, 1)
        led.step(15, books, ["m"])  # exit fills two slots later
        self.assertEqual(led.open_count, 0)
        self.assertEqual(gate.samples[0].n, 1)
        self.assertGreater(gate.samples[0].mean(), 0.0)

    def test_entry_is_dropped_when_price_runs_past_the_slippage_guard(self):
        ag = roster(1)
        gate = Gate(len(ag))
        led = Ledger(ag, gate, 2, 0.0125, 0.002, 0.03)
        book = Book("m", 0)
        book.add(0, 1.0, 1, True, 1)
        led.open(0, "m", 0, 1.0)
        book.add(2, 1.2, 1, True, 1)
        led.step(2, {"m": book}, ["m"])
        self.assertEqual(led.open_count, 0)
        self.assertEqual(gate.samples[0].n, 0)


class MathTest(unittest.TestCase):
    def test_liquidation_value_caps_at_real_reserve_and_deducts_fees(self):
        v = liquidation_value(30_000_000_000, 1_000_000_000_000_000, 10**12, 10**15, 0.0125)
        self.assertEqual(v, int(min(10**15 * 30_000_000_000 // (2 * 10**15), 10**12) * 0.9875))
        self.assertEqual(liquidation_value(1, 1, 1, 0, 0.0125), 0)


def synthetic_batches(n_slots=9000):
    """A deterministic rising-and-falling tape on a few coins, in the harness's batch shape."""
    mints = ["Mnt%dQ" % (k + 1) + "Z" * 30 for k in range(4)]  # valid base58, no 0 O I l
    vt0, vq0 = 1_073_000_000_000_000, 30_000_000_000
    state = {m: [vt0, vq0, 0] for m in mints}
    batches = []
    for slot in range(1, n_slots):
        events = []
        if slot == 1:
            for m in mints:
                events.append({"event": "CreateEvent", "mint": m, "slot": slot, "virtual_token_reserves": str(vt0), "virtual_quote_reserves": str(vq0), "real_token_reserves": "793100000000000"})
        else:
            m = mints[slot % len(mints)]
            vt, vq, real = state[m]
            buy = (slot // 7 + slot % 5) % 3 != 0
            amt = 150_000_000 + (slot % 13) * 40_000_000
            if buy:
                vq2 = vq + amt
                vt2 = vt * vq // vq2
                real += amt
            else:
                amt = min(amt, real)
                vq2 = vq - amt
                vt2 = vt * vq // vq2
                real -= amt
            state[m] = [vt2, vq2, real]
            events.append({"event": "TradeEvent", "mint": m, "slot": slot, "is_buy": buy, "sol_amount": str(amt), "virtual_token_reserves": str(vt2), "virtual_quote_reserves": str(vq2), "real_token_reserves": str(793_100_000_000_000 - real), "real_quote_reserves": str(real)})
        if events:
            batches.append((slot, events))
    return batches


def run_engine():
    state = makefun_bot.BotState(HELLO)
    eng = Engine(HELLO)
    log = []
    for seq, (slot, events) in enumerate(synthetic_batches()):
        batch = {"type": "batch", "seq": seq, "slot": slot, "events": events, "marks": [], "fills": [], "account": {"cash_lamports": "10000000000", "positions": []}}
        state.apply_batch(batch)
        orders = state.to_orders(eng.on_batch(state, batch))
        state.record_orders(orders)
        log.append(makefun_bot.encode_reply(seq, orders))
    eng.report(io.StringIO())
    return log


class EngineTest(unittest.TestCase):
    def test_runs_the_protocol_and_is_deterministic(self):
        a = run_engine()
        b = run_engine()
        self.assertEqual(a, b)
        self.assertGreater(len(a), 1000)

    def test_roster_scales_by_exit_variants_only(self):
        base = roster(1)
        big = roster(4)
        self.assertGreater(len(big), len(base))
        self.assertTrue(all(a.tp > 0 and a.sl > 0 and a.hold > 0 for a in big))
        self.assertEqual(len({a.name for a in big}), len(big))


if __name__ == "__main__":
    unittest.main()
