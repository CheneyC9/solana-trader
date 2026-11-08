"""The agent roster: each agent is a named entry predicate plus its own exit rule.

An agent never decides how much money to risk. It says "this state looks like my pattern" and how
it would exit (take-profit, stop, max hold). The ledger paper-trades every such signal, the gate
measures what the pattern actually earned net of costs, and only then does the allocator let it
trade real size. `roster(scale)` multiplies the base agents by exit-rule variants, so the ensemble
grows by changing one number; every variant is judged independently and by the same burden of proof.
"""

from zlib import crc32


class Agent:
    __slots__ = ("name", "family", "pred", "tp", "sl", "hold", "tradable")

    def __init__(self, name, family, pred, tp, sl, hold, tradable=True):
        self.name = name
        self.family = family
        self.pred = pred
        self.tp = tp
        self.sl = sl
        self.hold = hold
        self.tradable = tradable  # control agents calibrate the baseline and never trade real money


def _dip(min_real, lo, hi, buys, key="dd_long"):
    return lambda f, mint, slot: f["real"] >= min_real and lo <= f[key] <= hi and f["buys6"] >= buys and f["lastbuy"]


def _burst(min_real, ret10, buys, flow, floor):
    return lambda f, mint, slot: (
        f["real"] >= min_real and f["ret10"] >= ret10 and f["buys6"] >= buys and f["flow6"] >= flow and f["dd_long"] > floor
    )


def _young(max_age, min_real, buys, flow, ret10):
    return lambda f, mint, slot: (
        f["age"] <= max_age and f["real"] >= min_real and f["buys6"] >= buys and f["flow6"] >= flow and f["ret10"] >= ret10
    )


def _flush(min_real, lo, hi, flow):
    return lambda f, mint, slot: f["real"] >= min_real and lo <= f["dd_long"] <= hi and f["buys6"] <= 1 and f["flow6"] <= flow


def _late(min_real, buys):
    return lambda f, mint, slot: f["real"] >= min_real and f["buys6"] >= buys and f["dd_long"] > -0.03


def _control(min_real, one_in):
    # A deterministic pseudo-random gate: the baseline drag of entering at no particular signal.
    return lambda f, mint, slot: f["real"] >= min_real and crc32(("%s:%d" % (mint, slot)).encode()) % one_in == 0


# (name, family, predicate, take-profit, stop, max-hold slots, tradable)
BASE = [
    ("dip_deep", "dip", _dip(25, -0.45, -0.30, 3), 0.20, 0.10, 600, True),
    ("dip_mid", "dip", _dip(10, -0.50, -0.25, 3), 0.10, 0.07, 400, True),
    ("dip_shallow", "dip", _dip(10, -0.30, -0.15, 3), 0.08, 0.05, 200, True),
    ("dip_short", "dip", _dip(5, -0.25, -0.10, 4, "dd_short"), 0.10, 0.07, 300, True),
    ("burst_strong", "burst", _burst(10, 0.05, 5, 0.5, -0.05), 0.08, 0.05, 150, True),
    ("burst_wide", "burst", _burst(5, 0.10, 6, 0.0, -0.20), 0.15, 0.07, 200, True),
    ("burst_big", "burst", _burst(20, 0.03, 4, 0.0, -0.10), 0.10, 0.06, 300, True),
    ("young_flow", "young", _young(300, 1.5, 5, 0.5, 0.0), 0.15, 0.08, 200, True),
    ("young_run", "young", _young(1500, 3.0, 5, 0.0, 0.05), 0.20, 0.10, 400, True),
    ("flush", "flush", _flush(10, -0.50, -0.15, -0.5), 0.10, 0.06, 300, True),
    ("late_curve", "late", _late(60, 4), 0.15, 0.08, 600, True),
    ("ctrl_a", "control", _control(10, 50), 0.10, 0.07, 400, False),
    ("ctrl_b", "control", _control(3, 50), 0.20, 0.10, 600, False),
]

# Exit-rule multipliers applied to (take-profit, stop, hold) for each extra scale step.
VARIANTS = [(1.0, 1.0, 1.0), (0.6, 0.7, 0.6), (1.5, 1.4, 1.5), (0.8, 1.0, 2.0)]


def roster(scale=1):
    """The base agents, plus (scale - 1) exit-rule variants of every tradable one."""
    agents = [Agent(n, fam, p, tp, sl, hold, tr) for n, fam, p, tp, sl, hold, tr in BASE]
    for k in range(1, min(scale, len(VARIANTS))):
        a, b, c = VARIANTS[k]
        for n, fam, p, tp, sl, hold, tr in BASE:
            if tr:
                agents.append(Agent("%s~%d" % (n, k), fam, p, round(tp * a, 4), round(sl * b, 4), int(hold * c), True))
    return agents
