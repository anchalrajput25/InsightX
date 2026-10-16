# backend/evaluation/stats.py
#
# Statistics that keep benchmark claims honest on small golden sets:
#
#   bootstrap_ci     95% confidence interval of an accuracy
#                    (percentile bootstrap over cases, fixed seed)
#   wilson_ci        closed-form interval, used when n is tiny
#   mcnemar          paired test: did variant B really change accuracy
#                    vs variant A on the same cases? (exact binomial)
#   calibration      does the trust score mean what it says?
#                    (reliability bins + expected calibration error)
#
# Pure Python, no numpy/scipy dependency.

import math
import random
from typing import Dict, List, Optional, Sequence, Tuple

BOOTSTRAP_SAMPLES = 2000
SEED = 7


def wilson_ci(successes: int, total: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    if total == 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def bootstrap_ci(outcomes: Sequence[float], samples: int = BOOTSTRAP_SAMPLES, seed: int = SEED
                 ) -> Tuple[Optional[float], Optional[float]]:
    """
    Percentile bootstrap of the mean. One value per CASE: a bool, or a
    case's pass rate over repeats (resampling cases, not rows, keeps
    repeats of one case from looking like independent evidence).
    """
    n = len(outcomes)
    if n == 0:
        return None, None
    values = [float(outcome) for outcome in outcomes]
    # Plain right/wrong outcomes: the Wilson interval (the bootstrap
    # collapses to 100%-100% when every answer was right, which is not
    # what 30 questions can prove). Bootstrap for pass rates over repeats.
    if n < 10 or all(value in (0.0, 1.0) for value in values):
        return wilson_ci(round(sum(values)), n)
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(samples))
    return round(means[int(0.025 * samples)], 4), round(means[min(samples - 1, int(0.975 * samples))], 4)


def mcnemar(a: Sequence[bool], b: Sequence[bool]) -> Dict[str, Optional[float]]:
    """
    Paired comparison on the same cases. only_a: A right, B wrong;
    only_b: B right, A wrong. Two-sided exact binomial p-value on the
    discordant pairs (the right test for small benchmarks).
    """
    if len(a) != len(b):
        raise ValueError("Paired outcomes must have the same length.")
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    discordant = only_a + only_b
    if discordant == 0:
        p_value = 1.0
    else:
        k = min(only_a, only_b)
        tail = sum(math.comb(discordant, i) for i in range(k + 1)) / (2 ** discordant)
        p_value = min(1.0, 2 * tail)
    return {"only_a": only_a, "only_b": only_b, "p_value": round(p_value, 4), "significant": p_value < 0.05}


def sign_test(a: Sequence[float], b: Sequence[float]) -> Dict[str, Optional[float]]:
    """
    Paired comparison per case when cases were run several times: a and
    b are each case's pass rate (0..1) under two variants. Cases where A
    did better count for A, where B did better for B, ties are dropped;
    two-sided exact binomial test. With one run per case this is exactly
    McNemar's test.
    """
    if len(a) != len(b):
        raise ValueError("Paired outcomes must have the same length.")
    only_a = sum(1 for x, y in zip(a, b) if x > y)
    only_b = sum(1 for x, y in zip(a, b) if y > x)
    discordant = only_a + only_b
    if discordant == 0:
        p_value = 1.0
    else:
        k = min(only_a, only_b)
        p_value = min(1.0, 2 * sum(math.comb(discordant, i) for i in range(k + 1)) / (2 ** discordant))
    return {"only_a": only_a, "only_b": only_b, "p_value": round(p_value, 4), "significant": p_value < 0.05}


def calibration(confidences: Sequence[float], outcomes: Sequence[bool], bins: int = 5) -> Dict[str, object]:
    """
    Reliability of a confidence score in [0, 1]: per bin the mean
    confidence vs the observed accuracy, and the expected calibration
    error (weighted mean gap; 0 = perfectly calibrated).
    """
    pairs = [(min(1.0, max(0.0, float(c))), o) for c, o in zip(confidences, outcomes) if c is not None]
    coverage = {"scored": len(pairs), "total": len(confidences)}
    if not pairs:
        return {"bins": [], "ece": None, **coverage}
    rows: List[Dict[str, float]] = []
    ece = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [(c, o) for c, o in pairs if (low <= c < high) or (index == bins - 1 and c == 1.0)]
        if not members:
            continue
        confidence = sum(c for c, _ in members) / len(members)
        accuracy = sum(1 for _, o in members if o) / len(members)
        ece += len(members) / len(pairs) * abs(accuracy - confidence)
        rows.append({"low": low, "high": high, "count": len(members),
                     "confidence": round(confidence, 4), "accuracy": round(accuracy, 4)})
    return {"bins": rows, "ece": round(ece, 4), **coverage}
