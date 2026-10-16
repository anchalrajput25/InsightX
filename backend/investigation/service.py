# backend/investigation/service.py
#
# run_investigation(): question -> verified plan -> deterministic
# analysis -> checked narrative -> trust assessment.

import time
from typing import Any, Dict, Optional

from sqlalchemy.engine import Engine

from backend import dialects, telemetry
from backend.investigation.engine import Investigation, InvestigationError
from backend.investigation.narrative import fallback_narrative, fallback_real_reason, facts_summary, narrate
from backend.investigation.planner import plan_investigation
from backend.investigation.reasons import find_reasons
from backend.text_to_sql.schema_context import get_agent_schema

MIN_RECORDS = 30


def investigation_trust(report: Dict[str, Any], claim_check: Dict[str, Any]) -> Dict[str, Any]:
    score, reasons = 100, []

    def note(signal: str, effect: int, detail: str) -> None:
        nonlocal score
        score += effect
        reasons.append({"signal": signal, "effect": effect, "detail": detail})

    headline = report["headline"]
    if headline.get("unusual") == "normal":
        note("change", -20, "The change is within normal period-to-period variation; it may be noise.")
    elif headline.get("unusual") == "unknown":
        note("change", -10, "Too little history to say whether this change is unusual.")
    else:
        note("change", 0, f"The change is {headline['unusual']} compared with typical variation.")

    trend = {point["period"]: point for point in report["trend"]}
    smallest = min(trend[headline["baseline_period"]]["n"], trend[headline["current_period"]]["n"])
    if smallest < MIN_RECORDS:
        note("volume", -25, f"Only {smallest} records in one of the compared periods.")

    drivers = report.get("drivers") or []
    if not drivers:
        note("drivers", -20, "No breakdown dimension could be analysed.")
    else:
        top = drivers[0]["top"][0] if drivers[0]["top"] else {}
        share = top.get("share_of_change_pct")
        if share is not None and abs(share) < 25:
            note("drivers", -10, "The change is spread across many segments; no single segment dominates.")
        elif share is not None:
            note("drivers", 0, f"'{top['segment']}' explains {share:.0f}% of the change by {drivers[0]['dimension']}.")

    if claim_check.get("unverified_numbers"):
        note("narrative", -35, "The explanation states numbers not found in the computed facts: "
             + ", ".join(claim_check["unverified_numbers"]))
    elif claim_check.get("status") == "verified":
        note("narrative", 0, "Every number in the explanation was found in the computed facts.")

    note("method", 0, "Signals show what changed together with the metric (correlation, not proven cause).")
    score = max(0, min(100, score))
    level = "high" if score >= 80 else "medium" if score >= 55 else "low"
    return {"level": level, "score": score, "reasons": reasons}


def run_investigation(
    question: str,
    engine: Engine,
    planner_model=None,
    narrative_model=None,
    schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    with dialects.using(engine):
        return _run_investigation(question, engine, planner_model, narrative_model, schema)


def _run_investigation(question, engine, planner_model, narrative_model, schema) -> Dict[str, Any]:
    question = (question or "").strip()
    if not question:
        raise InvestigationError("Question cannot be empty.")
    schema = schema or get_agent_schema(engine)

    started = time.perf_counter()
    plan, raw_plan = plan_investigation(question, schema, engine, model=planner_model)
    telemetry.record_stage("investigation_plan", time.perf_counter() - started)
    started = time.perf_counter()
    investigation = Investigation(plan, schema, engine)
    report = investigation.run()
    try:
        report.update(find_reasons(investigation, report))  # "reasons", "ruled_out"
    except Exception as exc:
        report.update({"reasons": [], "ruled_out": []})
        report["plan"]["notes"].append(f"The real-reason checks were skipped: {str(exc)[:160]}")
    telemetry.record_stage("investigation_analysis", time.perf_counter() - started)
    report["question"] = question
    report["facts"] = facts_summary(report)

    started = time.perf_counter()
    story = narrate(report, question, model=narrative_model)
    telemetry.record_stage("investigation_narrative", time.perf_counter() - started)
    report["narrative"] = story["narrative"]
    report["narrative_source"] = "ai"
    if not report["narrative"]:
        report["narrative"] = fallback_narrative(report)
        report["narrative_source"] = "computed"
        report["narrative_error"] = story.get("error", "")
    if not str(report["narrative"].get("real_reason") or "").strip():
        report["narrative"]["real_reason"] = fallback_real_reason(report)
    report["claim_check"] = story["claim_check"]
    report["trust"] = investigation_trust(report, story["claim_check"])
    report["raw_plan"] = raw_plan
    report["status"] = "SUCCESS"
    return report
