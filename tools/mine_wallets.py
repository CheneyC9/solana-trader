"""Online wallet-skill miner: does following wallets whose past buys paid off beat the fee drag?

Strictly causal. A wallet's buy only enters its score once HORIZON slots have passed (the resolve
heap releases it), so the score at decision time uses nothing the bot could not have known. The
candidate trade is then priced the way the harness would fill it: enter at slot+2, exit HORIZON
slots later, 125 bps fees each way.

    python tools/mine_wallets.py <kit>/dataset/events [horizon]
"""

import heapq
import sys
from collections import defaultdict

import mine

FEE = mine.FEE
LAT = mine.LATENCY


def main():
    coins = mine.load(sys.argv[1])
    horizon = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    arr = {}
    stream = []
    for mint, c in coins.items():
        tr = c["tr"]
        if not tr:
            continue
        slots = [t["slot"] for t in tr]
        px = [mine.price(t) for t in tr]
        arr[mint] = (slots, px, [int(t["real_sol_reserves"]) / 1e9 for t in tr])
        for i, t in enumerate(tr):
            stream.append((t["slot"], t["chain_seq"], mint, i))
    stream.sort()
    mid = stream[len(stream) // 2][0]

    heap = []  # (resolve_slot, wallet, ret)
    stat = defaultdict(lambda: [0.0, 0])  # wallet -> [sum ret, n]
    tiers = defaultdict(lambda: [[], []])
    for slot, _, mint, i in stream:
        while heap and heap[0][0] <= slot:
            _, w, r = heapq.heappop(heap)
            stat[w][0] += r
            stat[w][1] += 1
        slots, px, real = arr[mint]
        t = coins[mint]["tr"][i]
        if not t["is_buy"]:
            continue
        w = t["user"]
        xi = mine.state_at(slots, None, None, slot + horizon)
        if slot + horizon >= slots[-1]:
            continue  # unresolved by the end of the window
        own = px[xi] / px[i] - 1  # their own fill -> horizon, the skill measure
        heapq.heappush(heap, (slot + horizon, w, own))
        sm, n = stat[w]
        if n < 4 or real[i] < 3.0:
            continue
        score = sm / (n + 4)
        ei = mine.state_at(slots, None, None, slot + LAT)
        xj = mine.state_at(slots, None, None, slot + LAT + horizon)
        net = px[xj] / px[ei] * (1 - FEE) / (1 + FEE) - 1
        edges = [-9, -0.05, -0.01, 0.0, 0.01, 0.03, 0.08, 9]
        k = max(j for j in range(len(edges) - 1) if edges[j] <= score)
        tiers[k][0 if slot < mid else 1].append(net)
    print("horizon", horizon, "wallets scored", len(stat))
    for k in sorted(tiers):
        a, b = tiers[k]
        ma = sum(a) / len(a) if a else float("nan")
        mb = sum(b) / len(b) if b else float("nan")
        print("score tier %d  n=%6d  H1 %+.4f (%d)  H2 %+.4f (%d)" % (k, len(a) + len(b), ma, len(a), mb, len(b)))


if __name__ == "__main__":
    main()
