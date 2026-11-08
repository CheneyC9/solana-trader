"""Data heap miner: where do bonding-curve price points carry positive expected value after costs?

Offline research tool (not used at runtime). It replays the dev window event stream, builds one
decision point per (coin, slot), measures the forward round-trip return under the harness's own
execution model (fill at slot+2, 125 bps curve fees each way, constant-product impact ignored at
these sizes) and prints mean net return by feature bucket, split into first and second half of
the window so a bucket only counts if it holds in both.

    python tools/mine.py <kit>/dataset/events [horizon_slots ...]
"""

import glob
import gzip
import json
import math
import sys
from collections import defaultdict

FEE = 0.0125  # protocol 95 bps + creator 30 bps, charged on each side
LATENCY = 2


def load(dirpath):
    coins = defaultdict(lambda: {"create": None, "tr": [], "complete": None})
    for path in sorted(glob.glob(dirpath + "/part-*.ndjson.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                e = json.loads(line)
                c = coins[e["mint"]]
                if e["event"] == "CreateEvent":
                    c["create"] = e
                elif e["event"] == "TradeEvent":
                    c["tr"].append(e)
                elif e["event"] == "CompleteEvent":
                    c["complete"] = e["slot"]
    return coins


def price(e):
    return int(e["virtual_sol_reserves"]) / int(e["virtual_token_reserves"])


def state_at(slots, idx_of_slot, tr, slot):
    """Index of the last trade with slot <= `slot` (binary search over the slot list)."""
    lo, hi = 0, len(slots)
    while lo < hi:
        mid = (lo + hi) // 2
        if slots[mid] <= slot:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def points(coins, horizons):
    out = []
    for mint, c in coins.items():
        tr = c["tr"]
        cr = c["create"]
        if cr is None or len(tr) < 5:
            continue
        slots = [t["slot"] for t in tr]
        t0 = cr["slot"]
        p0 = int(cr["virtual_sol_reserves"]) / int(cr["virtual_token_reserves"])
        last_idx = len(tr) - 1
        last_dec_slot = None
        for i, t in enumerate(tr):
            s = t["slot"]
            if i + 1 < len(tr) and tr[i + 1]["slot"] == s:
                continue  # decide once per slot, after the slot's last trade
            real = int(t["real_sol_reserves"]) / 1e9
            if real < 1.0:  # size cap (1% of real) would make 0.01 SOL impossible
                continue
            ei = state_at(slots, None, tr, s + LATENCY)
            if ei < 0:
                continue
            pe = price(tr[ei])
            feat = {}
            feat["age"] = s - t0
            feat["n"] = i + 1
            feat["real"] = real
            feat["mult"] = price(t) / p0
            # momentum over trailing windows (in slots)
            for w in (10, 50, 200):
                j = state_at(slots, None, tr, s - w)
                feat["ret%d" % w] = price(t) / price(tr[j]) - 1 if j >= 0 else 0.0
            # buy pressure over the last 10 trades
            lo = max(0, i - 9)
            win = tr[lo : i + 1]
            feat["buyfrac"] = sum(1 for x in win if x["is_buy"]) / len(win)
            net = sum((1 if x["is_buy"] else -1) * int(x["sol_amount"]) for x in win) / 1e9
            feat["flow"] = net
            feat["drawdown"] = price(t) / max(price(x) for x in tr[: i + 1]) - 1
            fwd = {}
            for h in horizons:
                xi = state_at(slots, None, tr, s + LATENCY + h)
                if xi <= ei and s + LATENCY + h < slots[-1]:
                    xi = ei
                px = price(tr[xi])
                gross = px / pe
                fwd[h] = gross * (1 - FEE) / (1 + FEE) - 1
            out.append((s, mint, feat, fwd))
    return out


BUCKETS = {
    "age": [0, 50, 150, 400, 1000, 3000, 10000, 1e9],
    "n": [0, 10, 25, 60, 150, 400, 1e9],
    "real": [1, 2, 4, 8, 16, 32, 64, 1e9],
    "mult": [0, 1.0, 1.5, 2, 3, 5, 10, 1e9],
    "ret10": [-1, -0.1, -0.03, -0.005, 0.005, 0.03, 0.1, 9],
    "ret50": [-1, -0.2, -0.08, -0.02, 0.02, 0.08, 0.2, 9],
    "ret200": [-1, -0.3, -0.12, -0.04, 0.04, 0.12, 0.3, 9],
    "buyfrac": [-0.01, 0.2, 0.4, 0.6, 0.8, 1.01],
    "flow": [-9, -1, -0.3, -0.05, 0.05, 0.3, 1, 99],
    "drawdown": [-1.01, -0.5, -0.3, -0.15, -0.05, -0.01, 0.01],
}


def bucket(edges, v):
    for k in range(len(edges) - 1):
        if edges[k] <= v < edges[k + 1]:
            return k
    return len(edges) - 2


def report(pts, horizons, mid_slot):
    print("decision points:", len(pts), "coins:", len({p[1] for p in pts}))
    for h in horizons:
        allv = [p[3][h] for p in pts]
        print("H=%d  mean net %.4f  win %.3f  n=%d" % (h, sum(allv) / len(allv), sum(1 for v in allv if v > 0) / len(allv), len(allv)))
    for h in horizons:
        print("\n=== horizon %d slots ===" % h)
        for name, edges in BUCKETS.items():
            rows = defaultdict(lambda: [[], []])
            for s, mint, f, fwd in pts:
                rows[bucket(edges, f[name])][0 if s < mid_slot else 1].append(fwd[h])
            for k in sorted(rows):
                a, b = rows[k]
                if len(a) + len(b) < 150:
                    continue
                ma = sum(a) / len(a) if a else float("nan")
                mb = sum(b) / len(b) if b else float("nan")
                flag = "  <== +/+" if (ma > 0.005 and mb > 0.005) else ""
                print("%-8s [%g,%g) n=%5d  H1 %+.4f (%d)  H2 %+.4f (%d)%s" % (name, edges[k], edges[k + 1], len(a) + len(b), ma, len(a), mb, len(b), flag))


def main():
    d = sys.argv[1]
    horizons = [int(x) for x in sys.argv[2:]] or [25, 100, 400]
    coins = load(d)
    pts = points(coins, horizons)
    slots = sorted(p[0] for p in pts)
    report(pts, horizons, slots[len(slots) // 2])


if __name__ == "__main__":
    main()
