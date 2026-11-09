"""GradEngine on hand-built batches: no entry below the band, a buy inside it, position building within the per-reply cap,
take profit, and one entry per coin."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vendor"))

import makefun_bot  # noqa: E402
from trader.grad import GradEngine  # noqa: E402

MINT = "So11111111111111111111111111111111111111112"
HELLO = {
    "type": "hello",
    "protocol": "agent-fun/replay/v2",
    "seed": "1",
    "capital": {"lamports": "10000000000"},
    "latency_slots": 2,
    "caps": {"max_open_positions": 5, "max_orders_per_batch": 16, "max_order_bps_of_real_reserve": 100},
    "fees": {"protocol_bps_fallback": 95, "creator_bps_fallback": 30, "base_fee_lamports": 5000, "priority_fee_lamports": 100000},
}


def trade(slot, real_sol, vsol=None, is_buy=True):
    real = int(real_sol * 1e9)
    vq = int(vsol * 1e9) if vsol else 30_000_000_000 + real
    vt = 1_073_000_000_000_000 * 30_000_000_000 // vq
    return {
        "event": "TradeEvent", "mint": MINT, "slot": slot, "is_buy": is_buy, "sol_amount": "1000000000",
        "virtual_quote_reserves": str(vq), "virtual_token_reserves": str(vt), "real_quote_reserves": str(real), "real_token_reserves": "500000000000000",
    }


def batch(seq, slot, events, account, marks=()):
    return {"type": "batch", "seq": seq, "slot": slot, "events": events, "fills": [], "marks": list(marks), "account": account}


class GradTest(unittest.TestCase):
    def setUp(self):
        self.state = makefun_bot.BotState(HELLO)
        self.eng = GradEngine(HELLO)
        self.seq = 0

    def feed(self, slot, events, account=None, marks=()):
        account = account or {"cash_lamports": "10000000000", "positions": []}
        b = batch(self.seq, slot, events, account, marks)
        self.seq += 1
        self.state.apply_batch(b)
        return self.eng.on_batch(self.state, b)

    def create(self, slot):
        return {"event": "CreateEvent", "mint": MINT, "slot": slot, "virtual_token_reserves": "1073000000000000",
                "virtual_quote_reserves": "30000000000", "real_token_reserves": "793100000000000"}

    def test_no_entry_below_band_then_buy_inside_it(self):
        self.feed(100, [self.create(100)])
        self.assertEqual(self.feed(200, [trade(200, 30.0)]), [])
        orders = self.feed(300, [trade(300, 56.0)])
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual((o["side"], o["mint"]), ("buy", MINT))
        self.assertLessEqual(int(o["quote_lamports"]), 56_000_000_000 * 100 // 10_000)  # within the 1% per-reply cap

    def test_no_entry_above_band(self):
        self.feed(100, [self.create(100)])
        self.assertEqual(self.feed(300, [trade(300, 70.0)]), [])

    def test_take_profit_sells_whole_position(self):
        self.feed(100, [self.create(100)])
        self.feed(300, [trade(300, 56.0)])
        # the position is held at the entry mark, then the curve runs up: value over cost by well above 40%
        held = {"cash_lamports": "9000000000", "positions": [{"mint": MINT, "token_amount": "1000000000000", "cost_lamports": "1000000000"}]}
        mark = {"mint": MINT, "slot": 400, "virtual_quote_reserves": str(110_000_000_000), "virtual_token_reserves": str(1_000_000_000_000 * 30), "real_quote_reserves": str(80_000_000_000), "real_token_reserves": "1"}
        orders = self.feed(400, [trade(400, 80.0)], held, [mark])
        sells = [o for o in orders if o["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(int(sells[0]["token_amount"]), 1_000_000_000_000)

    def test_one_entry_per_coin(self):
        self.feed(100, [self.create(100)])
        self.feed(300, [trade(300, 56.0)])
        held = {"cash_lamports": "9000000000", "positions": [{"mint": MINT, "token_amount": "1000000000000", "cost_lamports": "1000000000"}]}
        self.feed(350, [trade(350, 57.0)], held)
        flat = {"cash_lamports": "10000000000", "positions": []}
        self.feed(400, [trade(400, 40.0, is_buy=False)], flat)
        orders = self.feed(500, [trade(500, 57.0)], flat)
        self.assertEqual([o for o in orders if o["side"] == "buy"], [])


if __name__ == "__main__":
    unittest.main()
