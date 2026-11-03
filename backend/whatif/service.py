# backend/whatif/service.py
#
#   run_whatif     question or spec -> verified scenario -> baseline vs
#                  scenario totals, each lever's own effect, the impact by
#                  segment, a sensitivity curve and (when asked) a forecast
#   run_sweep      one lever swept over a range of values (one query)
#   run_forecast   history per period -> forecast with a 95% band and a
#                  backtest, plus the same forecast under the scenario
#
# Every number comes from read-only SQL through the existing safety gate
# (sql_safety) and executor (query_executor), inside dialects.using().

import calendar
import math
import statistics
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.engine import Engine

from backend import dialects, telemetry
from backend.investigation.engine import InvestigationError, _num, _period_key, _run
from backend.text_to_sql.schema_context import get_agent_schema
from backend.whatif import forecast as fc
from backend.whatif.builder import breakdown_sql, scenario_expression, series_sql, totals_sql
from backend.whatif.planner import plan_whatif
from backend.whatif.spec import Lever, Scenario, WhatIfError, verify_scenario

MAX_SEGMENTS_SHOWN = 12
SWEEP_PERCENTS = (-20, -15, -10, -5, 0, 5, 10, 15, 20)
MAX_SWEEP_VALUES = 25
PERIOD_DAYS = {"day": 1, "week": 7, "month": 30.44, "quarter": 91.31, "year": 365.25}
PARTIAL_PERIOD_RATIO = 0.5
MIN_HISTORY = 3


# ============================================================
# HELPERS
# ============================================================

def _query(sql: str, engine: Engine, max_rows: int = 5000) -> List[Dict[str, Any]]:
    try:
        return _run(sql, engine, max_rows=max_rows)["rows"]
    except InvestigationError as exc:
        raise WhatIfError(str(exc)) from exc


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(value, digits)


def _pct(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return _round((new - old) / abs(old) * 100.0, 2)


def _scenario(spec: Optional[Dict[str, Any]], question: str, engine: Engine, schema: Optional[Dict[str, Any]],
              model, need_time: bool = False):
    """(verified scenario, the spec it came from, raw LLM output)."""
    schema = schema or get_agent_schema(engine)
    raw_plan = ""
    if spec is None:
        started = time.perf_counter()
        spec, raw_plan = plan_whatif(question, schema, model=model)
        telemetry.record_stage("whatif_plan", time.perf_counter() - started)
    return verify_scenario(spec, schema, engine, need_time=need_time), raw_plan


def _totals(scenario: Scenario, expressions: Sequence[str], engine: Engine) -> Dict[str, Any]:
    sql = totals_sql(scenario, expressions)
    row = _query(sql, engine, max_rows=1)[0]
    return {"baseline": _num(row.get("b")), "values": [_num(row.get(f"s{i}")) for i in range(len(expressions))],
            "rows": int(_num(row.get("n")) or 0), "sql": sql}


# ============================================================
# WHAT-IF
# ============================================================

def run_whatif(question: str = "", engine: Engine = None, spec: Optional[Dict[str, Any]] = None, model=None,
               schema: Optional[Dict[str, Any]] = None, sensitivity: bool = True) -> Dict[str, Any]:
    with dialects.using(engine):
        return _run_whatif(question, engine, spec, model, schema, sensitivity)


def _run_whatif(question, engine, spec, model, schema, sensitivity) -> Dict[str, Any]:
    scenario, raw_plan = _scenario(spec, question, engine, schema, model)
    if not scenario.levers:
        if scenario.forecast_periods:
            report = _run_forecast(scenario, engine, scenario.forecast_periods)
            report.update({"question": question, "raw_plan": raw_plan})
            return report
        raise WhatIfError("No change was found in the question. Say what changes, for example "
                          "'what if prices rise 10%' or 'what if São Paulo orders disappear'.")

    started = time.perf_counter()
    full = scenario_expression(scenario)
    singles = [scenario_expression(scenario, [lever]) for lever in scenario.levers] if len(scenario.levers) > 1 else []
    totals = _totals(scenario, [full, *singles], engine)
    baseline, value = totals["baseline"], totals["values"][0]
    delta = None if baseline is None or value is None else value - baseline

    effects = []
    for lever, single in zip(scenario.levers, totals["values"][1:] if singles else totals["values"]):
        lever_delta = None if baseline is None or single is None else single - baseline
        effects.append({"label": lever.label(scenario.metric.name), "scenario": _round(single),
                        "delta": _round(lever_delta), "delta_pct": _pct(single, baseline)})
    known = [effect["delta"] for effect in effects if effect["delta"] is not None]
    interaction = _round(delta - sum(known)) if delta is not None and len(known) == len(effects) and len(effects) > 1 else None

    report: Dict[str, Any] = {
        "status": "SUCCESS",
        "kind": "whatif",
        "question": question,
        "spec": scenario.to_spec(),
        "metric": {"name": scenario.metric.name, "expression": scenario.to_spec()["metric_expression"],
                   "kind": scenario.metric.kind},
        "levers": [{"label": lever.label(scenario.metric.name), **lever.to_spec()} for lever in scenario.levers],
        "baseline": _round(baseline),
        "scenario": _round(value),
        "delta": _round(delta),
        "delta_pct": _pct(value, baseline),
        "rows": totals["rows"],
        "lever_effects": effects,
        "interaction": interaction,
        "breakdown": _breakdown(scenario, full, delta, engine) if scenario.breakdown else None,
        "assumptions": scenario.assumptions(),
        "notes": list(scenario.notes),
        "sql": {"totals": totals["sql"]},
        "raw_plan": raw_plan,
    }
    if report["breakdown"]:
        report["sql"]["breakdown"] = report["breakdown"].pop("sql")
    telemetry.record_stage("whatif_analysis", time.perf_counter() - started)

    index = next((i for i, lever in enumerate(scenario.levers) if lever.parameter), None)
    if sensitivity and index is not None:
        report["sensitivity"] = _sweep(scenario, index, None, engine)
    if scenario.forecast_periods:
        if scenario.time is None:
            report["notes"].append("No date column was found, so no forecast was made.")
        else:
            try:
                report["forecast"] = _run_forecast(scenario, engine, scenario.forecast_periods)
            except WhatIfError as exc:
                report["notes"].append(f"Forecast skipped: {exc}")
    return report


def _breakdown(scenario: Scenario, expression: str, total_delta: Optional[float], engine: Engine) -> Dict[str, Any]:
    sql = breakdown_sql(scenario, expression)
    rows = []
    for row in _query(sql, engine):
        base, value = _num(row.get("b")), _num(row.get("s"))
        change = None if base is None and value is None else (value or 0.0) - (base or 0.0)
        rows.append({
            "segment": "(empty)" if row.get("k") is None else str(row.get("k")),
            "baseline": _round(base), "scenario": _round(value), "delta": _round(change),
            "delta_pct": _pct(value, base), "n": int(_num(row.get("n")) or 0),
            "share_of_change_pct": _round(change / total_delta * 100.0, 1)
            if change is not None and total_delta and scenario.metric.kind in ("sum", "count", "count_star") else None,
        })
    rows.sort(key=lambda item: abs(item["delta"] or 0.0), reverse=True)
    table, column = scenario.breakdown
    return {"dimension": f"{table}.{column}", "segments": len(rows), "rows": rows[:MAX_SEGMENTS_SHOWN],
            "unchanged_segments": sum(1 for item in rows if not item["delta"]), "sql": sql}


# ============================================================
# SENSITIVITY
# ============================================================

def _sweep_values(lever: Lever) -> List[float]:
    if lever.type in ("scale", "volume"):
        values = list(SWEEP_PERCENTS)
    else:
        current = getattr(lever, lever.parameter)
        span = abs(current) or 1.0
        values = [current - span + span * step / 4 for step in range(9)] if lever.type == "shift" else \
                 [current + span * (step - 4) / 8 for step in range(9)]
    current = getattr(lever, lever.parameter)
    return sorted(set(round(v, 6) for v in values) | {round(current, 6)})


def _sweep(scenario: Scenario, index: int, values: Optional[Sequence[float]], engine: Engine) -> Dict[str, Any]:
    if not 0 <= index < len(scenario.levers):
        raise WhatIfError(f"There is no change number {index + 1} in this scenario.")
    lever = scenario.levers[index]
    if not lever.parameter:
        raise WhatIfError(f"'{lever.label()}' has no number to vary.")
    values = sorted(set(float(v) for v in (values or _sweep_values(lever))))[:MAX_SWEEP_VALUES]
    if lever.parameter == "percent":
        values = [v for v in values if -100.0 <= v <= 1000.0]
    expressions = []
    for value in values:
        levers = list(scenario.levers)
        levers[index] = lever.with_parameter(value)
        expressions.append(scenario_expression(scenario, levers))
    totals = _totals(scenario, expressions, engine)
    baseline = totals["baseline"]
    points = [{"value": value, "scenario": _round(result), "delta": _round(None if result is None or baseline is None
                                                                         else result - baseline),
               "delta_pct": _pct(result, baseline)} for value, result in zip(values, totals["values"])]
    return {"lever_index": index, "lever": lever.label(scenario.metric.name), "parameter": lever.parameter,
            "current": getattr(lever, lever.parameter), "elasticity": lever.elasticity,
            "baseline": _round(baseline), "points": points, "sql": totals["sql"]}


def run_sweep(spec: Dict[str, Any], engine: Engine, lever_index: int = 0, values: Optional[Sequence[float]] = None,
              elasticity: Optional[float] = None, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with dialects.using(engine):
        scenario, _ = _scenario(spec, "", engine, schema, None)
        if elasticity is not None and 0 <= lever_index < len(scenario.levers):
            lever = scenario.levers[lever_index]
            if lever.type == "scale":
                import dataclasses

                from backend.whatif.spec import ELASTICITY_RANGE, _number

                scenario.levers[lever_index] = dataclasses.replace(
                    lever, elasticity=_number(elasticity, "Elasticity", *ELASTICITY_RANGE))
        report = _sweep(scenario, lever_index, values, engine)
        report["assumptions"] = scenario.assumptions()
        report["status"] = "SUCCESS"
        return report


# ============================================================
# FORECAST
# ============================================================

def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip().replace("T", " ")
    for size, pattern in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M"), (10, "%Y-%m-%d")):
        try:
            return datetime.strptime(text[:size], pattern)
        except ValueError:
            continue
    return None


def _add(start: datetime, grain: str, steps: int = 1) -> datetime:
    if grain == "day":
        return start + timedelta(days=steps)
    if grain == "week":
        return start + timedelta(days=7 * steps)
    months = {"month": 1, "quarter": 3, "year": 12}[grain] * steps
    year, month = divmod(start.month - 1 + months, 12)
    year, month = start.year + year, month + 1
    return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))


def run_forecast(question: str = "", engine: Engine = None, spec: Optional[Dict[str, Any]] = None, horizon: int = 6,
                 grain: Optional[str] = None, model=None, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with dialects.using(engine):
        if spec is not None and grain:
            spec = {**spec, "grain": grain}
        scenario, raw_plan = _scenario(spec, question, engine, schema, model, need_time=True)
        if grain and spec is None:
            scenario.grain = grain
        report = _run_forecast(scenario, engine, horizon or scenario.forecast_periods or 6)
        report.update({"question": question, "raw_plan": raw_plan})
        return report


def _run_forecast(scenario: Scenario, engine: Engine, horizon: int) -> Dict[str, Any]:
    if scenario.time is None:
        raise WhatIfError("A forecast needs a date column.")
    horizon = max(1, min(int(horizon), 36))
    started = time.perf_counter()
    expression = scenario_expression(scenario) if scenario.levers else None
    sql = series_sql(scenario, expression)
    rows = _query(sql, engine)
    notes: List[str] = list(scenario.notes)

    points = []
    for row in rows:
        start = _as_datetime(row.get("p"))
        if start is None:
            continue
        points.append({"start": start, "last": _as_datetime(row.get("last_seen")),
                       "b": _num(row.get("b")), "s": _num(row.get("s")) if expression else None})
    if len(points) > 1:
        # A period the data only partly covers (usually the current one) would look like a drop:
        # the last period is partial when its data stops far earlier in the period than usual.
        def covered(point):
            return ((point["last"] - point["start"]).days + 1) / PERIOD_DAYS[scenario.grain] if point["last"] else None

        usual = [c for c in (covered(point) for point in points[:-1]) if c is not None]
        last = covered(points[-1])
        if usual and last is not None and last < PARTIAL_PERIOD_RATIO * statistics.median(usual):
            notes.append(f"The last {scenario.grain} ({_period_key(points[-1]['start'])}) is only partly in the data "
                         "and was left out.")
            points = points[:-1]

    additive = scenario.metric.kind in ("sum", "count", "count_star")
    filled: List[Dict[str, Any]] = []
    for point in points:
        while filled and _add(filled[-1]["start"], scenario.grain) < point["start"]:
            gap = _add(filled[-1]["start"], scenario.grain)
            filled.append({"start": gap, "b": 0.0 if additive else filled[-1]["b"],
                           "s": (0.0 if additive else filled[-1]["s"]) if expression else None, "gap": True})
        filled.append(point)
    gaps = sum(1 for point in filled if point.get("gap"))
    if gaps:
        notes.append(f"{gaps} {scenario.grain}(s) had no rows and were counted as "
                     f"{'0' if additive else 'the previous value'}.")
    filled = [point for point in filled if point["b"] is not None]
    if len(filled) < MIN_HISTORY:
        raise WhatIfError(f"At least {MIN_HISTORY} full {scenario.grain}s of history are needed to forecast "
                          f"(found {len(filled)}).")

    baseline_series = [point["b"] for point in filled]
    season = fc.SEASONS.get(scenario.grain, 0)
    result = fc.forecast(baseline_series, horizon, season)

    ratio = None
    if expression:
        scenario_series = [point["s"] or 0.0 for point in filled]
        ratio = fc.ratio(scenario_series, baseline_series, season or 12)

    future = []
    start = filled[-1]["start"]
    for point in result["points"]:
        start = _add(start, scenario.grain)
        item = {"period": _period_key(start), **{k: point[k] for k in ("yhat", "lower", "upper")}}
        if ratio is not None:
            item.update({"scenario": _round(point["yhat"] * ratio), "scenario_lower": _round(point["lower"] * ratio),
                         "scenario_upper": _round(point["upper"] * ratio)})
        future.append(item)

    history = [{"period": _period_key(point["start"]), "baseline": _round(point["b"]),
                "scenario": _round(point["s"]) if expression else None,
                "fitted": result["fitted"][index]} for index, point in enumerate(filled)]
    combine = sum if additive else (lambda values: sum(values) / len(values))
    summary = {"aggregate": "total" if additive else "average",
               "baseline": _round(combine([p["yhat"] for p in future]))}
    if ratio is not None:
        summary["scenario"] = _round(combine([p["scenario"] for p in future]))
        summary["delta_pct"] = _pct(summary["scenario"], summary["baseline"])

    assumptions = ["Assumes the past trend and seasonality continue; it cannot know about events that have not "
                   "happened yet (new competitors, promotions, holidays that move)."]
    if ratio is not None:
        assumptions.append(f"The scenario forecast applies the changes' measured effect on recent history "
                           f"({(ratio - 1) * 100:+.1f}%) to every future {scenario.grain}.")
    assumptions += scenario.assumptions()
    telemetry.record_stage("whatif_forecast", time.perf_counter() - started)
    return {
        "status": "SUCCESS",
        "kind": "forecast",
        "spec": scenario.to_spec(),
        "metric": {"name": scenario.metric.name, "kind": scenario.metric.kind},
        "levers": [{"label": lever.label(scenario.metric.name), **lever.to_spec()} for lever in scenario.levers],
        "grain": scenario.grain,
        "horizon": horizon,
        "history": history,
        "forecast": future,
        "model": result["model"],
        "params": result["params"],
        "season_length": result["season_length"],
        "backtest": result["backtest"],
        "scenario_ratio": _round(ratio),
        "summary": summary,
        "assumptions": list(dict.fromkeys(assumptions)),
        "notes": notes,
        "sql": {"series": sql},
    }
