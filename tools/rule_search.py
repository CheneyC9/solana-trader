"""Rule heap with out-of-sample validation.

Random conjunctions of up to three feature thresholds, each paired with one of the exit rules from
mine_exits.GRID, are scored on the first half of the window (coin-level t-stat, so a hundred
consecutive slots of one coin count once). The best rules are kept in a heap and re-scored on the
second half. If the heap's second-half mean is not clearly above zero there is no edge to harvest
from these features, and the bot should hold cash instead of trading them.

    python tools/rule_search.py <kit>/dataset/events [n_rules] [seed]
"""

import heapq
import random
import sys

import numpy as np

import mine
import mine_exits

FEATS = ["dd_long", "dd_short", "real", "buys6", "lastbuy", "flow6", "age"]


def coin_stats(mask, ret, cid, ncoin):
    """Coin-level mean return and t-stat over the points selected by `mask`."""
    w = mask.astype(np.float64)
    cnt = np.bincount(cid, weights=w, minlength=ncoin)
    tot = np.bincount(cid, weights=w * ret, minlength=ncoin)
    keep = cnt > 0
    if keep.sum() < 20:
        return None
    m = tot[keep] / cnt[keep]
    return m.mean(), m.mean() / (m.std(ddof=1) / np.sqrt(len(m)) + 1e-12), int(keep.sum())


def main():
    n_rules = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    rng = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 7)
    coins = mine.load(sys.argv[1])
    pts = mine_exits.build(coins)
    mids = {m: i for i, m in enumerate(coins)}
    cid = np.array([mids[p[1]] for p in pts])
    slot = np.array([p[0] for p in pts])
    X = np.array([[float(p[2][f]) for f in FEATS] for p in pts])
    grid = mine_exits.GRID
    R = np.array([[p[3][g] for g in grid] for p in pts])
    mid = np.median(slot)
    h1, h2 = slot < mid, slot >= mid
    q = {f: np.percentile(X[h1, i], [10, 25, 50, 75, 90]) for i, f in enumerate(FEATS)}
    heap = []
    for t in range(n_rules):
        k = rng.choice([1, 2, 3])
        conds = []
        mask = h1.copy()
        for f in rng.sample(range(len(FEATS)), k):
            thr = rng.choice(list(q[FEATS[f]]))
            ge = rng.random() < 0.5
            conds.append((FEATS[f], ">=" if ge else "<=", float(thr)))
            mask &= (X[:, f] >= thr) if ge else (X[:, f] <= thr)
        gi = rng.randrange(len(grid))
        st = coin_stats(mask, R[:, gi], cid, len(coins))
        if st is None:
            continue
        item = (st[1], t, st[0], st[2], conds, gi)
        if len(heap) < 40:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    out = []
    for tstat, t, mean1, nc1, conds, gi in sorted(heap, reverse=True):
        mask = h2.copy()
        for f, op, thr in conds:
            col = X[:, FEATS.index(f)]
            mask &= (col >= thr) if op == ">=" else (col <= thr)
        st2 = coin_stats(mask, R[:, gi], cid, len(coins))
        out.append((tstat, mean1, nc1, st2, conds, gi))
    print("rules tried:", n_rules, " kept:", len(out))
    pos = 0
    sum2 = 0.0
    cnt2 = 0
    for tstat, mean1, nc1, st2, conds, gi in out[:15]:
        s2 = "n/a" if st2 is None else "%+.4f t=%+.2f coins=%d" % st2
        print("H1 %+.4f t=%+.2f coins=%d | H2 %s | exit tp%.2f sl%.2f | %s" % (mean1, tstat, nc1, s2, grid[gi][0], grid[gi][1], conds))
    for tstat, mean1, nc1, st2, conds, gi in out:
        if st2 is not None:
            cnt2 += 1
            sum2 += st2[0]
            pos += st2[0] > 0
    print("heap of %d: mean H2 coin-level return %+.4f, %d positive" % (cnt2, sum2 / max(1, cnt2), pos))


if __name__ == "__main__":
    main()
