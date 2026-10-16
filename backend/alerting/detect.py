# backend/alerting/detect.py
#
# Is the latest value unusual? Pure maths, no AI, no database.
#
# A series is a list of points {"t": datetime or None, "v": float},
# oldest first. A rule says what counts as unusual:
#
#   smart   learns the normal level from the past: a robust trend line
#           (Theil-Sen), weekday habits for daily numbers, month habits
#           for monthly ones, and the usual spread around them (MAD).
#           Fires when the latest value is far outside that range.
#   limit   above / below a fixed number
#   change  sudden jump or drop against the previous period, the same
#           day last week or the same period last year
#   trend   kept falling (or rising) several periods in a row
#   stale   no new data for some periods (the data stopped arriving)
#
# Severity is "warning" or "critical": critical when the number is half
# as far again past the trigger (or the rule says always critical).

import calendar
import math
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional

RULE_TYPES = ("smart", "limit", "change", "trend", "stale")
SENSITIVITY = {"low": 4.0, "medium": 3.0, "high": 2.2}
GRAINS = ("day", "week", "month", "quarter", "year")
WINDOW = 60          # past points the smart baseline learns from
MIN_HISTORY = 6      # fewer past points: not enough to judge "unusual"
CRITICAL_AT = 1.5    # how far past the trigger makes it critical

DEFAULT_RULE = {
    "type": "smart", "direction": "both", "sensitivity": "medium", "op": "above", "value": 0.0,
    "percent": 30.0, "compare_to": "previous", "periods": 3, "severity": "auto", "skip_unfinished": True,
}


def clean_rule(rule: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(DEFAULT_RULE)
    out.update({k: v for k, v in (rule or {}).items() if k in DEFAULT_RULE and v is not None})
    if out["type"] not in RULE_TYPES:
        raise ValueError(f"Unknown alert type: {out['type']}")
    if out["direction"] not in ("both", "up", "down"):
        out["direction"] = "both"
    if out["sensitivity"] not in SENSITIVITY:
        out["sensitivity"] = "medium"
    if out["op"] not in ("above", "below"):
        out["op"] = "above"
    if out["compare_to"] not in ("previous", "week", "year"):
        out["compare_to"] = "previous"
    if out["severity"] not in ("auto", "warning", "critical"):
        out["severity"] = "auto"
    out["value"] = float(out["value"])
    out["percent"] = abs(float(out["percent"])) or 30.0
    out["periods"] = max(1, int(out["periods"]))
    out["skip_unfinished"] = bool(out["skip_unfinished"])
    return out


# ------------------------------------------------------------
# time helpers
# ------------------------------------------------------------

def infer_grain(times: List[Optional[datetime]]) -> Optional[str]:
    """day / week / month / quarter / year from the usual gap between points."""
    times = sorted(t for t in times if t is not None)
    gaps = [(b - a).total_seconds() / 86400 for a, b in zip(times, times[1:]) if b > a]
    if not gaps:
        return None
    gap = median(gaps)
    if gap < 2:
        return "day"
    if gap < 10:
        return "week"
    if gap < 45:
        return "month"
    if gap < 135:
        return "quarter"
    return "year"


def period_start(t: datetime, grain: Optional[str]) -> datetime:
    t = t.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    if grain == "week":
        return t - timedelta(days=t.weekday())
    if grain == "month":
        return t.replace(day=1)
    if grain == "quarter":
        return t.replace(month=(t.month - 1) // 3 * 3 + 1, day=1)
    if grain == "year":
        return t.replace(month=1, day=1)
    return t


def shift(t: datetime, grain: Optional[str], periods: int) -> datetime:
    """The period `periods` steps after t (negative: before)."""
    if grain in ("month", "quarter", "year"):
        months = periods * {"month": 1, "quarter": 3, "year": 12}[grain]
        index = t.year * 12 + t.month - 1 + months
        year, month = divmod(index, 12)
        day = min(t.day, calendar.monthrange(year, month + 1)[1])
        return t.replace(year=year, month=month + 1, day=day)
    return t + timedelta(days=periods * (7 if grain == "week" else 1))


def periods_between(earlier: datetime, later: datetime, grain: Optional[str]) -> int:
    if grain in ("month", "quarter", "year"):
        months = (later.year - earlier.year) * 12 + later.month - earlier.month
        return months // {"month": 1, "quarter": 3, "year": 12}[grain]
    days = (period_start(later, "day") - period_start(earlier, "day")).days
    return days // (7 if grain == "week" else 1)


def label(t: Optional[datetime], grain: Optional[str]) -> str:
    if t is None:
        return "latest reading"
    if grain == "reading":
        return "the check on " + t.strftime("%d %b %Y %H:%M")
    if grain == "month":
        return t.strftime("%b %Y")
    if grain == "quarter":
        return f"Q{(t.month - 1) // 3 + 1} {t.year}"
    if grain == "year":
        return str(t.year)
    if grain == "week":
        return "week of " + t.strftime("%d %b %Y")
    if t.hour or t.minute:
        return t.strftime("%d %b %Y %H:%M")
    return t.strftime("%d %b %Y")


def fill_gaps(points: List[Dict[str, Any]], grain: Optional[str], limit: int = 5000) -> List[Dict[str, Any]]:
    """Periods with no rows count as 0 (a day with no orders is news)."""
    if grain is None or len(points) < 2 or any(p["t"] is None for p in points):
        return points
    known = {period_start(p["t"], grain): p["v"] for p in points}
    out, t, end = [], period_start(points[0]["t"], grain), period_start(points[-1]["t"], grain)
    while t <= end and len(out) < limit:
        out.append({"t": t, "v": float(known.get(t, 0.0))})
        t = shift(t, grain, 1)
    return out


def drop_unfinished(points: List[Dict[str, Any]], grain: Optional[str], now: Optional[datetime]) -> List[Dict[str, Any]]:
    """Today / this month is not over yet, so its number is still low: leave it out."""
    if not points or grain is None or now is None or points[-1]["t"] is None:
        return points
    if period_start(points[-1]["t"], grain) >= period_start(now, grain):
        return points[:-1]
    return points


# ------------------------------------------------------------
# numbers in words
# ------------------------------------------------------------

def fmt(value: Optional[float]) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    size = abs(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e4, "K")):
        if size >= limit:
            return f"{value / limit:,.1f}{suffix}"
    if float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.2f}" if size < 100 else f"{value:,.1f}"


# ------------------------------------------------------------
# the smart baseline
# ------------------------------------------------------------

def _theil_sen(ys: List[float]):
    slopes = [(ys[j] - ys[i]) / (j - i) for i in range(len(ys)) for j in range(i + 1, len(ys))]
    slope = median(slopes) if slopes else 0.0
    intercept = median([y - slope * x for x, y in enumerate(ys)])
    return slope, intercept


def _season_key(t: Optional[datetime], grain: Optional[str]):
    if t is None:
        return None
    if grain == "day":
        return t.weekday()
    if grain == "month":
        return t.month
    return None


def baseline(history: List[Dict[str, Any]], target: Optional[datetime], grain: Optional[str],
             k: float) -> Optional[Dict[str, float]]:
    """Expected value for the next point and its normal range, learned from `history`."""
    window = history[-WINDOW:]
    # daily numbers need two weeks first, to learn the weekday habits
    if len(window) < (14 if grain == "day" else MIN_HISTORY):
        return None
    ys = [p["v"] for p in window]
    slope, intercept = _theil_sen(ys)
    residuals = [y - (intercept + slope * x) for x, y in enumerate(ys)]

    # habits: Mondays are busy, December is big...
    season: Dict[Any, float] = {}
    needed = {"day": 14, "month": 24}.get(grain or "", 10 ** 9)
    if len(window) >= needed or (grain == "month" and len(history) >= 24):
        groups: Dict[Any, List[float]] = {}
        for p, r in zip(window, residuals):
            key = _season_key(p["t"], grain)
            if key is not None:
                groups.setdefault(key, []).append(r)
        enough = 2
        season = {key: median(values) for key, values in groups.items() if len(values) >= enough}
    adjusted = [r - season.get(_season_key(p["t"], grain), 0.0) for p, r in zip(window, residuals)]
    center = median(adjusted)

    # The spread is measured "honestly": each point against habits learned
    # without it, so a few readings per weekday do not look too tidy.
    honest = []
    for index, (p, r) in enumerate(zip(window, residuals)):
        key = _season_key(p["t"], grain)
        if season and key in season:
            others = [o for j, (q, o) in enumerate(zip(window, residuals))
                      if j != index and _season_key(q["t"], grain) == key]
            r -= median(others) if others else 0.0
        honest.append(r - center)
    spread = 1.4826 * median([abs(r) for r in honest]) * (1 + 2.0 / len(window))
    typical = abs(median(ys))
    spread = max(spread, 0.02 * typical, 1e-9 if typical == 0 else 0.0, 1e-9)

    expected = intercept + slope * len(window) + center + season.get(_season_key(target, grain), 0.0)
    if all(y >= 0 for y in ys):
        expected = max(expected, 0.0)
    low, high = expected - k * spread, expected + k * spread
    if all(y >= 0 for y in ys):
        low = max(low, 0.0)
    return {"expected": expected, "low": low, "high": high, "spread": spread, "slope": slope}


# ------------------------------------------------------------
# evaluate one rule on one series
# ------------------------------------------------------------

def _severity(rule: Dict[str, Any], intensity: float) -> str:
    if rule["severity"] != "auto":
        return rule["severity"]
    return "critical" if intensity >= CRITICAL_AT else "warning"


def _result(status: str, **extra) -> Dict[str, Any]:
    out = {"status": status, "fired": status == "fired", "severity": None, "value": None, "period": None,
           "t": None, "expected": None, "low": None, "high": None, "score": None, "change_pct": None,
           "title": "", "message": ""}
    out.update(extra)
    return out


def _wanted(direction: str, delta: float) -> bool:
    return direction == "both" or (direction == "up" and delta > 0) or (direction == "down" and delta < 0)


def evaluate(rule: Dict[str, Any], points: List[Dict[str, Any]], name: str = "This number",
             grain: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Check the latest point. `now` is used to skip an unfinished period and for 'stale'."""
    rule = clean_rule(rule)
    points = [p for p in points if p.get("v") is not None and not math.isnan(float(p["v"]))]
    if grain is None:
        grain = infer_grain([p["t"] for p in points])
    if rule["type"] == "stale":
        return _stale(rule, points, name, grain, now)
    if rule["skip_unfinished"]:
        points = drop_unfinished(points, grain, now)
    if not points:
        return _result("no_data", message=f"{name} has no data yet.")
    last = points[-1]
    base = {"value": last["v"], "t": last["t"], "period": label(last["t"], grain)}
    kind = rule["type"]
    if kind == "smart":
        return _smart(rule, points, name, grain, base)
    if kind == "limit":
        return _limit(rule, name, base)
    if kind == "change":
        return _change(rule, points, name, grain, base)
    return _trend(rule, points, name, base)


def _smart(rule, points, name, grain, base):
    k = SENSITIVITY[rule["sensitivity"]]
    band = baseline(points[:-1], points[-1]["t"], grain, k)
    if band is None:
        needed = 15 if grain == "day" else MIN_HISTORY + 1
        return _result("not_enough_data", **base,
                       message=f"{name} needs at least {needed} readings before it can learn what is normal.")
    value = base["value"]
    score = (value - band["expected"]) / band["spread"]
    extra = {**base, "expected": band["expected"], "low": band["low"], "high": band["high"], "score": score}
    if band["expected"]:
        extra["change_pct"] = (value - band["expected"]) / abs(band["expected"]) * 100
    outside = value > band["high"] or value < band["low"]
    if not outside or not _wanted(rule["direction"], score):
        return _result("ok", **extra, message=(
            f"{name} was {fmt(value)} for {base['period']}, inside the normal range "
            f"({fmt(band['low'])} to {fmt(band['high'])})."))
    word = "higher" if score > 0 else "lower"
    much = "much " if abs(score) >= k * CRITICAL_AT else ""
    pct = f" ({extra['change_pct']:+.0f}%)" if extra.get("change_pct") is not None else ""
    return _result("fired", **extra, severity=_severity(rule, abs(score) / k),
                   title=f"{name} is unusually {'high' if score > 0 else 'low'}",
                   message=(f"{name} was {fmt(value)} for {base['period']}, {much}{word} than the expected "
                            f"{fmt(band['expected'])}{pct}. Normal range: {fmt(band['low'])} to "
                            f"{fmt(band['high'])}."))


def _limit(rule, name, base):
    value, limit = base["value"], rule["value"]
    above = rule["op"] == "above"
    crossed = value > limit if above else value < limit
    extra = {**base, "expected": None, "low": None if above else limit, "high": limit if above else None}
    if not crossed:
        return _result("ok", **extra, message=(
            f"{name} was {fmt(value)} for {base['period']}, not {rule['op']} the limit of {fmt(limit)}."))
    gap = abs(value - limit)
    intensity = 1 + gap / max(abs(limit), 1e-9) if limit else 1 + gap
    return _result("fired", **extra, severity=_severity(rule, intensity),
                   title=f"{name} is {rule['op']} {fmt(limit)}",
                   message=f"{name} was {fmt(value)} for {base['period']}, {rule['op']} your limit of {fmt(limit)}.")


_COMPARE_WORDS = {"previous": "the period before", "week": "the same day last week", "year": "the same time last year"}


def _change(rule, points, name, grain, base):
    last = points[-1]
    if rule["compare_to"] == "previous" or last["t"] is None:
        before = points[-2] if len(points) > 1 else None
    else:
        steps = {"week": {"day": 7, "week": 1}, "year": {"day": 364, "week": 52, "month": 12, "quarter": 4,
                                                         "year": 1}}[rule["compare_to"]].get(grain or "")
        target = shift(last["t"], grain if steps and grain != "day" else "day", -(steps or 0)) if steps else None
        before = next((p for p in points if p["t"] is not None and period_start(p["t"], grain) == period_start(target, grain)),
                      None) if target else None
    if before is None:
        return _result("not_enough_data", **base,
                       message=f"{name} has no reading for {_COMPARE_WORDS[rule['compare_to']]} to compare with.")
    old, value = before["v"], base["value"]
    if old == 0:
        pct = 0.0 if value == 0 else math.copysign(100.0, value) * 10
    else:
        pct = (value - old) / abs(old) * 100
    extra = {**base, "expected": old, "change_pct": pct}
    if abs(pct) < rule["percent"] or not _wanted(rule["direction"], pct):
        return _result("ok", **extra, message=(
            f"{name} was {fmt(value)} for {base['period']}, {pct:+.0f}% vs {_COMPARE_WORDS[rule['compare_to']]}."))
    verb = "jumped" if pct > 0 else "dropped"
    return _result("fired", **extra, severity=_severity(rule, abs(pct) / rule["percent"]),
                   title=f"{name} {verb} {abs(pct):.0f}%",
                   message=(f"{name} {verb} from {fmt(old)} to {fmt(value)} ({pct:+.0f}%) for {base['period']}, "
                            f"compared with {_COMPARE_WORDS[rule['compare_to']]}."))


def _trend(rule, points, name, base):
    def streak(sign: int) -> int:
        count = 0
        for a, b in zip(reversed(points[:-1]), reversed(points)):
            if (b["v"] - a["v"]) * sign > 0:
                count += 1
            else:
                break
        return count

    downs, ups = streak(-1), streak(1)
    options = [(downs, "fell", "down"), (ups, "rose", "up")]
    count, verb, way = max(options, key=lambda item: item[0])
    if rule["direction"] != "both":
        count, verb, way = next(o for o in options if o[2] == rule["direction"])
    extra = {**base, "score": float(count)}
    if count < rule["periods"]:
        return _result("ok", **extra, message=(
            f"{name} has not kept {'falling' if way == 'down' else 'rising'} for {rule['periods']} periods "
            f"(latest run: {count})."))
    start = points[-1 - count]["v"]
    pct = (base["value"] - start) / abs(start) * 100 if start else None
    extra["change_pct"] = pct
    change = f" ({pct:+.0f}%)" if pct is not None else ""
    return _result("fired", **extra, severity=_severity(rule, count / rule["periods"]),
                   title=f"{name} {verb} {count} times in a row",
                   message=(f"{name} {verb} {count} periods in a row, from {fmt(start)} to {fmt(base['value'])}"
                            f"{change}, up to {base['period']}."))


def _stale(rule, points, name, grain, now):
    times = [p["t"] for p in points if p["t"] is not None]
    if not times:
        return _result("no_data", message=f"{name} has no dated data, so 'data stops arriving' cannot be checked.")
    last = max(times)
    now = now or datetime.now()
    gap = periods_between(period_start(last, grain), period_start(now, grain), grain)
    unit = {"week": "week", "month": "month", "quarter": "quarter", "year": "year"}.get(grain or "", "day")
    base = {"value": float(gap), "t": last, "period": label(last, grain), "score": float(gap)}
    if gap < rule["periods"]:
        return _result("ok", **base, message=f"{name} has fresh data (last: {label(last, grain)}).")
    return _result("fired", **base, severity=_severity(rule, gap / rule["periods"]),
                   title=f"No new data for {name}",
                   message=(f"{name} has had no new data since {label(last, grain)}: "
                            f"{gap:,} {unit}{'s' if gap != 1 else ''} without any rows."))


# ------------------------------------------------------------
# back-test: how the rule would have behaved in the past
# ------------------------------------------------------------

def backtest(rule: Dict[str, Any], points: List[Dict[str, Any]], name: str = "This number",
             grain: Optional[str] = None, now: Optional[datetime] = None, last: int = 180) -> Dict[str, Any]:
    """Run the rule at every past point. Gives the chart its normal-range band and the past alerts."""
    rule = clean_rule(rule)
    if grain is None:
        grain = infer_grain([p["t"] for p in points])
    if rule["skip_unfinished"] and rule["type"] != "stale":
        points = drop_unfinished(points, grain, now)
    start = max(0, len(points) - last)
    series, fired = [], []
    for i in range(start, len(points)):
        point = points[i]
        row = {"t": point["t"], "v": point["v"], "expected": None, "low": None, "high": None, "fired": False,
               "severity": None}
        if rule["type"] != "stale":
            result = evaluate({**rule, "skip_unfinished": False}, points[:i + 1], name, grain)
            row.update({key: result.get(key) for key in ("expected", "low", "high")})
            if result["fired"]:
                row.update({"fired": True, "severity": result["severity"]})
                fired.append({"t": point["t"], "period": result["period"], "severity": result["severity"],
                              "title": result["title"]})
        series.append(row)
    return {"grain": grain, "series": series, "fired": fired, "fired_count": len(fired),
            "checked": len(series)}
