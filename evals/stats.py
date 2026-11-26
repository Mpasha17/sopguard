"""Small statistics helpers (no numpy dependency): percentiles and bootstrap confidence intervals."""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence


def percentile(xs: Sequence[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = (len(s) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def bootstrap_ci(
    xs: Sequence[float],
    stat: Callable[[Sequence[float]], float] = mean,
    n: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    if not xs:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    m = len(xs)
    vals = sorted(stat([xs[rng.randrange(m)] for _ in range(m)]) for _ in range(n))
    return vals[int(n * alpha / 2)], vals[int(n * (1 - alpha / 2)) - 1]


def paired_diff_ci(a: Sequence[float], b: Sequence[float], n: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """Mean of (a - b) over paired items with a bootstrap 95% CI."""
    d = [x - y for x, y in zip(a, b, strict=True)]
    lo, hi = bootstrap_ci(d, n=n, seed=seed)
    return mean(d), lo, hi
