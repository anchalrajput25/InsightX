# backend/whatif/forecast.py
#
# Forecast the next periods of a metric from its history, in plain
# Python (no statsmodels needed).
#
#   model       exponential smoothing, chosen by how much history there is:
#                 >= 2 seasons + 2   Holt-Winters, additive season, damped trend
#                 >= 4 periods       Holt (damped trend)
#                 else               simple exponential smoothing
#               Smoothing weights are picked by grid search on one-step-ahead
#               errors (the model only ever sees the past of each point).
#   interval    95% band from the spread of the one-step errors, widening
#               with the horizon (sigma * sqrt(h)); never below 0 for a
#               metric that has never been negative.
#   backtest    the last few periods are hidden, the model is fitted on the
#               rest and scored on them (MAPE), next to a naive forecast
#               (last value, or the same period last season), so the
#               numbers say whether the model beats guessing.

import itertools
import math
import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

SEASONS = {"day": 7, "week": 52, "month": 12, "quarter": 4, "year": 0}
ALPHAS = (0.1, 0.2, 0.3, 0.5, 0.7, 0.9)
BETAS = (0.0, 0.05, 0.1, 0.2, 0.4)
GAMMAS = (0.0, 0.1, 0.2, 0.4)
PHIS = (1.0, 0.95, 0.85)
Z95 = 1.96
MAX_BACKTEST = 6


@dataclass
class Fit:
    model: str
    params: Dict[str, float]
    season: int
    one_step: List[Optional[float]]   # prediction of y[t] from y[:t]
    level: float
    trend: float
    seasonals: List[float]

    def ahead(self, horizon: int, start_index: int) -> List[float]:
        phi = self.params.get("phi", 1.0)
        values, damp = [], 0.0
        for k in range(1, horizon + 1):
            damp += phi ** k
            seasonal = self.seasonals[(start_index + k - 1) % self.season] if self.season else 0.0
            values.append(self.level + damp * self.trend + seasonal)
        return values


def _smooth(y: Sequence[float], season: int, alpha: float, beta: Optional[float], gamma: Optional[float],
            phi: float) -> Fit:
    n = len(y)
    if season:
        first = statistics.fmean(y[:season])
        level = first
        trend = (statistics.fmean(y[season:2 * season]) - first) / season if beta is not None else 0.0
        seasonals = [y[i] - first for i in range(season)]
    else:
        level = y[0]
        trend = (y[1] - y[0]) if beta is not None and n > 1 else 0.0
        seasonals = []
    one_step: List[Optional[float]] = []
    for t, actual in enumerate(y):
        seasonal = seasonals[t % season] if season else 0.0
        one_step.append(level + phi * trend + seasonal)
        new_level = alpha * (actual - seasonal) + (1 - alpha) * (level + phi * trend)
        if beta is not None:
            trend = beta * (new_level - level) + (1 - beta) * phi * trend
        if season:
            seasonals[t % season] = (gamma or 0.0) * (actual - new_level) + (1 - (gamma or 0.0)) * seasonal
        level = new_level
    name = "Holt-Winters (additive season, damped trend)" if season else ("Holt (damped trend)" if beta is not None
                                                                         else "Exponential smoothing")
    params = {"alpha": alpha, "phi": phi}
    if beta is not None:
        params["beta"] = beta
    if season:
        params["gamma"] = gamma or 0.0
    return Fit(name, params, season, one_step, level, trend if beta is not None else 0.0, seasonals)


def _sse(fit: Fit, y: Sequence[float], warmup: int) -> float:
    errors = [(a - p) ** 2 for a, p in zip(y[warmup:], fit.one_step[warmup:]) if p is not None]
    return sum(errors) / len(errors) if errors else math.inf


def fit_series(y: Sequence[float], season_length: int = 0) -> Fit:
    """The best exponential smoothing model for y (see the header)."""
    y = [float(v) for v in y]
    n = len(y)
    if n < 2:
        raise ValueError("At least 2 periods are needed to forecast.")
    if season_length and n >= 2 * season_length + 2:
        season, warmup = season_length, season_length
        grid = itertools.product(ALPHAS, BETAS, GAMMAS, PHIS)
        candidates = (_smooth(y, season, a, b, g, p) for a, b, g, p in grid)
    elif n >= 4:
        season, warmup = 0, 2
        candidates = (_smooth(y, 0, a, b, None, p) for a, b, p in itertools.product(ALPHAS, BETAS, PHIS))
    else:
        season, warmup = 0, 1
        candidates = (_smooth(y, 0, a, None, None, 1.0) for a in ALPHAS)
    return min(candidates, key=lambda fit: _sse(fit, y, warmup))


def _sigma(fit: Fit, y: Sequence[float]) -> float:
    warmup = fit.season or 2
    errors = [a - p for a, p in zip(y[warmup:], fit.one_step[warmup:]) if p is not None]
    if len(errors) < 2:
        errors = [a - p for a, p in zip(y[1:], fit.one_step[1:]) if p is not None]
    return statistics.pstdev(errors) if len(errors) >= 2 else abs(statistics.fmean(y)) * 0.1


def _mape(actual: Sequence[float], predicted: Sequence[float]) -> Optional[float]:
    pairs = [(a, p) for a, p in zip(actual, predicted) if a]
    if not pairs:
        return None
    return 100.0 * statistics.fmean(abs(a - p) / abs(a) for a, p in pairs)


def _naive(train: Sequence[float], horizon: int, season: int) -> List[float]:
    if season and len(train) >= season:
        return [train[len(train) - season + (k % season)] for k in range(horizon)]
    return [train[-1]] * horizon


def backtest(y: Sequence[float], season_length: int, horizon: int) -> Optional[Dict[str, object]]:
    """Hide the last periods, fit on the rest, score both the model and a naive guess."""
    n = len(y)
    hold = min(max(1, n // 5), max(1, horizon), MAX_BACKTEST)
    if n - hold < 4:
        return None
    train, test = list(y[:-hold]), list(y[-hold:])
    fit = fit_series(train, season_length)
    predicted = fit.ahead(hold, len(train))
    naive = _naive(train, hold, fit.season or (season_length if len(train) >= season_length > 0 else 0))
    return {
        "holdout_periods": hold,
        "mape": _round(_mape(test, predicted)),
        "naive_mape": _round(_mape(test, naive)),
        "model": fit.model,
        "actual": [_round(v) for v in test],
        "predicted": [_round(v) for v in predicted],
    }


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(value, digits)


def forecast(y: Sequence[float], horizon: int, season_length: int = 0) -> Dict[str, object]:
    """Point forecasts with a 95% band, the model used and its backtest."""
    y = [float(v) for v in y]
    fit = fit_series(y, season_length)
    sigma = _sigma(fit, y)
    non_negative = min(y) >= 0
    points = []
    for k, value in enumerate(fit.ahead(horizon, len(y)), start=1):
        half = Z95 * sigma * math.sqrt(k)
        low, high = value - half, value + half
        if non_negative:
            value, low, high = max(0.0, value), max(0.0, low), max(0.0, high)
        points.append({"step": k, "yhat": _round(value), "lower": _round(low), "upper": _round(high)})
    return {
        "model": fit.model,
        "params": {key: round(val, 3) for key, val in fit.params.items()},
        "season_length": fit.season,
        "sigma": _round(sigma),
        "points": points,
        "fitted": [_round(v) for v in fit.one_step],
        "backtest": backtest(y, season_length, horizon),
    }


def ratio(scenario: Sequence[float], baseline: Sequence[float], recent: int) -> Optional[float]:
    """Scenario / baseline over the most recent periods (how the levers scale the metric)."""
    pairs = list(zip(scenario, baseline))[-recent:] if recent else list(zip(scenario, baseline))
    total_base = sum(b for _, b in pairs)
    if not pairs or not total_base:
        return None
    return sum(s for s, _ in pairs) / total_base
