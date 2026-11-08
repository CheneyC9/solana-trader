"""Path-dependent EV miner: triple-barrier exits (take-profit / stop / max-hold) per decision point.

Reuses the loader from mine.py. For every (coin, slot) decision point it records features known at
decision time, then walks the coin forward under the harness's fill model (decide at slot s, fill
against the last trade at slot <= s+2) and records the net return of the round trip for each exit
rule in GRID. Output: mean net return, win rate and sample size by feature cell, split by window half.

    python tools/mine_exits.py <kit>/dataset/events
"""

import sys
from collections import defaultdict

import mine

FEE = mine.FEE
LAT = mine.LATENCY
GRID = [(0.10, 0.07, 400), (0.20, 0.10, 600), (0.30, 0.15, 900), (0.08, 0.05, 200)]


def build(coins):
    pts = []
    for mint, c in coins.items():
        tr, cr = c["tr"], c["create"]
        if cr is None or len(tr) < 8:
            continue
        slots = [t["slot"] for t in tr]
        px = [mine.price(t) for t in tr]
        real = [int(t["real_sol_reserves"]) / 1e9 for t in tr]
        isbuy = [t["is_buy"] for t in tr]
        sol = [int(t["sol_amount"]) / 1e9 for t in tr]
        for i in range(len(tr)):
            s = slots[i]
            if i + 1 < len(tr) and slots[i + 1] == s:
                continue
            if real[i] < 3.0:
                continue
            ei = mine.state_at(slots, None, tr, s + LAT)
            pe = px[ei]
            # trailing high over 2500 slots and 300 slots
            hi_long = hi_short = 0.0
            for k in range(i, -1, -1):
                if s - slots[k] > 2500:
                    break
                hi_long = max(hi_long, px[k])
                if s - slots[k] <= 300:
                    hi_short = max(hi_short, px[k])
            win = range(max(0, i - 5), i + 1)
            buys = sum(1 for k in win if isbuy[k])
            flow = sum((sol[k] if isbuy[k] else -sol[k]) for k in win)
            feat = {
                "dd_long": px[i] / hi_long - 1,
                "dd_short": px[i] / hi_short - 1,
                "real": real[i],
                "buys6": buys,
                "lastbuy": isbuy[i],
                "flow6": flow,
                "age": s - cr["slot"],
            }
            res = {}
            for tp, sl, hold in GRID:
                out = None
                for j in range(ei + 1, len(tr)):
                    sj = slots[j]
                    if j + 1 < len(tr) and slots[j + 1] == sj:
                        pass
                    r = px[j] / pe - 1
                    if r >= tp or r <= -sl or sj - s >= hold:
                        xi = mine.state_at(slots, None, tr, sj + LAT)
                        out = px[xi] / pe * (1 - FEE) / (1 + FEE) - 1
                        break
                if out is None:
                    out = px[-1] / pe * (1 - FEE) / (1 + FEE) - 1
                res[(tp, sl, hold)] = out
            pts.append((s, mint, feat, res))
    return pts


def cell_report(pts, name, pred, mid):
    for g in GRID:
        a = [p[3][g] for p in pts if pred(p[2]) and p[0] < mid]
        b = [p[3][g] for p in pts if pred(p[2]) and p[0] >= mid]
        n = len(a) + len(b)
        if n < 40:
            continue
        coins = len({p[1] for p in pts if pred(p[2])})
        ma = sum(a) / len(a) if a else float("nan")
        mb = sum(b) / len(b) if b else float("nan")
        win = (sum(1 for v in a + b if v > 0)) / n
        flag = " <== +/+" if ma > 0 and mb > 0 else ""
        print("%-34s tp%.2f sl%.2f h%-4d n=%5d coins=%3d win=%.2f  H1 %+.4f  H2 %+.4f%s" % (name, g[0], g[1], g[2], n, coins, win, ma, mb, flag))


def main():
    coins = mine.load(sys.argv[1])
    pts = build(coins)
    mid = sorted(p[0] for p in pts)[len(pts) // 2]
    print("points", len(pts))
    cells = [
        ("all", lambda f: True),
        ("real>=25", lambda f: f["real"] >= 25),
        ("real>=25 dd_long[-.45,-.3]", lambda f: f["real"] >= 25 and -0.45 <= f["dd_long"] <= -0.30),
        ("real>=25 dd_long[-.45,-.3] buys>=3 last", lambda f: f["real"] >= 25 and -0.45 <= f["dd_long"] <= -0.30 and f["buys6"] >= 3 and f["lastbuy"]),
        ("real>=10 dd_long[-.5,-.25] buys>=3 last", lambda f: f["real"] >= 10 and -0.5 <= f["dd_long"] <= -0.25 and f["buys6"] >= 3 and f["lastbuy"]),
        ("real>=10 dd_long[-.3,-.15] buys>=3 last", lambda f: f["real"] >= 10 and -0.3 <= f["dd_long"] <= -0.15 and f["buys6"] >= 3 and f["lastbuy"]),
        ("real>=10 dd_short[-.25,-.1] buys>=4", lambda f: f["real"] >= 10 and -0.25 <= f["dd_short"] <= -0.10 and f["buys6"] >= 4),
        ("real>=10 dd_long>-.05 buys6==6 flow>0.5", lambda f: f["real"] >= 10 and f["dd_long"] > -0.05 and f["buys6"] == 6 and f["flow6"] > 0.5),
        ("real 3-10 dd_long[-.5,-.2] buys>=3 last", lambda f: 3 <= f["real"] < 10 and -0.5 <= f["dd_long"] <= -0.2 and f["buys6"] >= 3 and f["lastbuy"]),
    ]
    for name, pred in cells:
        cell_report(pts, name, pred, mid)
        print()


if __name__ == "__main__":
    main()
