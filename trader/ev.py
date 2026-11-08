"""Expected-value bookkeeping: a windowed return sample per agent and a shrunk posterior over it.

Every agent's net return per round trip (fees and transaction costs included) is shrunk toward the
market baseline, the pooled mean of everything all agents have traded, which on this venue sits
below zero. An agent only earns real money when its shrunk mean, less z standard errors, is
positive: the burden of proof is on the signal, not on cash.
"""

from collections import deque
from math import sqrt

PRIOR_FLOOR = -0.05  # the baseline prior is clamped into [PRIOR_FLOOR, PRIOR_CEIL]
PRIOR_CEIL = 0.0
DEFAULT_VAR = 0.01  # 10% sd per round trip until the pool has data


class Sample:
    """The last `window` returns with running sums, so mean and variance cost O(1)."""

    __slots__ = ("q", "window", "s", "ss")

    def __init__(self, window):
        self.q = deque()
        self.window = window
        self.s = 0.0
        self.ss = 0.0

    def add(self, x):
        self.q.append(x)
        self.s += x
        self.ss += x * x
        if len(self.q) > self.window:
            old = self.q.popleft()
            self.s -= old
            self.ss -= old * old

    @property
    def n(self):
        return len(self.q)

    def mean(self):
        return self.s / len(self.q) if self.q else 0.0

    def var(self):
        n = len(self.q)
        if n < 2:
            return DEFAULT_VAR
        return max(1e-8, (self.ss - self.s * self.s / n) / (n - 1))


class Gate:
    """Posterior mean and standard error per agent, plus the live/paper switch with hysteresis."""

    def __init__(self, n_agents, window=150, pool_window=3000, k0=20, z_on=1.3, margin=0.0, n_min=30):
        self.samples = [Sample(window) for _ in range(n_agents)]
        self.pool = Sample(pool_window)
        self.k0 = k0
        self.z_on = z_on
        self.margin = margin
        self.n_min = n_min
        self.live = [False] * n_agents

    def record(self, idx, ret):
        self.samples[idx].add(ret)
        self.pool.add(ret)
        self._update(idx)

    def prior(self):
        if self.pool.n < 20:
            return PRIOR_FLOOR / 2, DEFAULT_VAR
        return min(PRIOR_CEIL, max(PRIOR_FLOOR, self.pool.mean())), self.pool.var()

    def posterior(self, idx):
        """(mean, standard error) of agent idx's net return per round trip after shrinkage."""
        m0, v0 = self.prior()
        s = self.samples[idx]
        n = s.n
        mu = (self.k0 * m0 + n * s.mean()) / (self.k0 + n)
        pv = (self.k0 * v0 + n * s.var()) / (self.k0 + n)
        return mu, sqrt(pv / (self.k0 + n))

    def lcb(self, idx, z=1.0):
        mu, se = self.posterior(idx)
        return mu - z * se

    def _update(self, idx):
        s = self.samples[idx]
        if s.n < self.n_min:
            self.live[idx] = False
            return
        mu, se = self.posterior(idx)
        if self.live[idx]:
            self.live[idx] = mu > 0.0  # stay while the shrunk mean is still positive
        else:
            self.live[idx] = mu - self.z_on * se > self.margin
