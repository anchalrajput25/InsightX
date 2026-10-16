# backend/llmops/api.py
#
# The /llmops endpoints behind the Monitor, Evals, Hallucination,
# Quality and Routing pages. Plugged in by a few lines at the end of
# backend/main.py: include the router, then install() adds the capture
# middleware. Smart routing stays off until switched on.

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from backend.auth.deps import Caller, current_caller
from backend.llmops import evals, hallucination, monitor, routing
from backend.llmops.capture import CaptureMiddleware
from backend.llmops.store import get_store

router = APIRouter(prefix="/llmops", tags=["llmops"])

DAY = 86400.0


def _main():
    from backend import main  # imported late: main includes this router

    return main


def _session(connection_id: str, caller: Caller):
    main = _main()
    session = main.get_connection_session(connection_id, caller)
    return session, main._database_label(session)


def _answer(caller: Caller, answer_id: str) -> Dict[str, Any]:
    row = get_store().get_answer(caller.user_id, answer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Answer not found.")
    return row


def _since(days: float) -> float:
    return time.time() - max(0.04, min(float(days), 90.0)) * DAY


# ================================================================ monitor
@router.get("/monitor")
def monitor_overview(window: str = "24h", caller: Caller = Depends(current_caller)):
    if window not in monitor.WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {', '.join(monitor.WINDOWS)}.")
    return jsonable_encoder(monitor.overview(caller.user_id, window))


class SloBody(BaseModel):
    success_rate: Optional[float] = Field(default=None, ge=0.5, le=1.0)
    p95_seconds: Optional[float] = Field(default=None, gt=0, le=600)
    ai_error_rate: Optional[float] = Field(default=None, ge=0, le=1)
    hallucination_rate: Optional[float] = Field(default=None, ge=0, le=1)


@router.get("/slo")
def get_slo(caller: Caller = Depends(current_caller)):
    return {**monitor.DEFAULT_SLO, **(get_store().get_setting("slo", {}) or {})}


@router.put("/slo")
def put_slo(body: SloBody, caller: Caller = Depends(current_caller)):
    saved = {**monitor.DEFAULT_SLO, **(get_store().get_setting("slo", {}) or {})}
    saved.update({k: v for k, v in body.model_dump().items() if v is not None})
    get_store().set_setting("slo", saved)
    return saved


# ================================================================ answers
@router.get("/answers")
def list_answers(only: str = "all", search: str = "", days: float = 30, limit: int = 200,
                 caller: Caller = Depends(current_caller)):
    if only not in ("all", "flagged", "hallucinated", "down", "up", "review"):
        raise HTTPException(status_code=400, detail="Unknown filter.")
    rows = get_store().list_answers(caller.user_id, since=_since(days), limit=limit, only=only, search=search)
    for row in rows:
        row.pop("preview", None)
    return {"answers": jsonable_encoder(rows)}


@router.get("/answers/{answer_id}")
def get_answer(answer_id: str, caller: Caller = Depends(current_caller)):
    return jsonable_encoder(_answer(caller, answer_id))


class FeedbackBody(BaseModel):
    rating: int = Field(ge=-1, le=1)
    note: str = Field(default="", max_length=2000)


@router.post("/answers/{answer_id}/feedback")
def give_feedback(answer_id: str, body: FeedbackBody, caller: Caller = Depends(current_caller)):
    row = _answer(caller, answer_id)
    values: Dict[str, Any] = {"rating": body.rating, "note": body.note}
    # a thumbs-down reopens an answer that was closed before
    if body.rating < 0 and row.get("review") in ("dismissed",):
        values["review"] = "open"
    get_store().update_answer(caller.user_id, row["id"], **values)
    return jsonable_encoder(_answer(caller, row["id"]))


class ReviewBody(BaseModel):
    review: str = Field(pattern="^(open|fixed|dismissed)$")


@router.post("/answers/{answer_id}/review")
def set_review(answer_id: str, body: ReviewBody, caller: Caller = Depends(current_caller)):
    row = _answer(caller, answer_id)
    get_store().update_answer(caller.user_id, row["id"], review=body.review)
    return jsonable_encoder(_answer(caller, row["id"]))


@router.post("/answers/{answer_id}/judge")
def judge_answer(answer_id: str, caller: Caller = Depends(current_caller)):
    """Ask a free model whether the answer is supported by the result (on demand only)."""
    row = _answer(caller, answer_id)
    if not row.get("answer") or row.get("status") != "SUCCESS":
        raise HTTPException(status_code=400, detail="Only answered questions can be judged.")
    from backend.ai_gateway import AIUnavailableError, ai_request

    try:
        with ai_request(user_id=caller.user_id):
            verdict = hallucination.run_judge(row)
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"The AI judge gave an unreadable reply: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"The AI judge failed: {type(exc).__name__}: {exc}") from exc
    merged = hallucination.merge_judge(row.get("checks") or {}, verdict)
    get_store().update_answer(
        caller.user_id, row["id"], judge=verdict, checks=merged, faithfulness=merged["score"],
        risk=merged["risk"], hallucinated=1 if merged["hallucinated"] else 0)
    return jsonable_encoder(_answer(caller, row["id"]))


class SqlBody(BaseModel):
    connection_id: str
    sql: str = Field(min_length=1, max_length=20000)


def _run_checked_sql(sql: str, engine) -> Dict[str, Any]:
    from backend import dialects
    from backend.text_to_sql.query_executor import execute_query
    from backend.text_to_sql.sql_safety import check_sql_safety

    safety = check_sql_safety(sql)
    if not safety.get("safe"):
        raise HTTPException(status_code=400,
                            detail=f"This query is not allowed: {safety.get('reason') or 'not a read-only SELECT'}.")
    with dialects.using(engine):
        result = execute_query(sql, engine, max_rows=200)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=f"The query failed: {str(result.get('error') or '')[:400]}")
    return result


def _same_rows(a: Dict[str, Any], b_rows: List[Dict[str, Any]]) -> Optional[bool]:
    rows_a = (a or {}).get("rows") or []
    if not rows_a:
        return None
    canon = lambda rows: sorted(tuple(sorted(str(v) for v in r.values())) for r in rows)  # noqa: E731
    return canon(rows_a) == canon(b_rows[:len(rows_a)])


@router.post("/answers/{answer_id}/test-sql")
def test_sql(answer_id: str, body: SqlBody, caller: Caller = Depends(current_caller)):
    """Run a corrected SQL (read-only) and show how its result differs from the answer's."""
    row = _answer(caller, answer_id)
    session, _ = _session(body.connection_id, caller)
    result = _run_checked_sql(body.sql, session["engine"])
    return jsonable_encoder({
        "columns": result.get("columns"), "rows": (result.get("rows") or [])[:50],
        "row_count": result.get("row_count"), "same_as_answer": _same_rows(row.get("preview") or {},
                                                                           result.get("rows") or []),
    })


class PromoteBody(BaseModel):
    connection_id: str
    sql: str = Field(default="", max_length=20000)
    note: str = Field(default="", max_length=2000)
    difficulty: str = Field(default="", pattern="^(|easy|medium|hard|extra)$")


@router.post("/answers/{answer_id}/promote")
def promote(answer_id: str, body: PromoteBody, caller: Caller = Depends(current_caller)):
    """
    Feedback loop: save the question with checked SQL (the answer's own
    or a corrected one) as a golden test case, so every later evaluation
    run checks it.
    """
    row = _answer(caller, answer_id)
    sql = (body.sql or row.get("fixed_sql") or row.get("sql") or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="There is no SQL to save. Write the correct SQL first.")
    session, label = _session(body.connection_id, caller)
    _run_checked_sql(sql, session["engine"])  # must be safe and must run
    level = body.difficulty or {"easy": "easy", "medium": "medium", "hard": "hard"}.get(row.get("difficulty"), "medium")
    saved = evals.add_feedback_case(caller.user_id, label, row["question"], sql, body.note, level)
    get_store().update_answer(caller.user_id, row["id"], review="promoted",
                              fixed_sql=sql if sql != (row.get("sql") or "").strip() else row.get("fixed_sql") or "")
    return {**saved, "answer": jsonable_encoder(_answer(caller, row["id"]))}


# ========================================================== hallucination
@router.get("/hallucination")
def hallucination_summary(days: float = 30, caller: Caller = Depends(current_caller)):
    rows = get_store().list_answers(caller.user_id, since=_since(days), limit=1000)
    checked = [r for r in rows if r.get("faithfulness") is not None]
    kinds: Dict[str, int] = defaultdict(int)
    by_day: Dict[str, Dict[str, Any]] = {}
    by_difficulty: Dict[str, Dict[str, int]] = defaultdict(lambda: {"checked": 0, "hallucinated": 0})
    by_trust: Dict[str, Dict[str, int]] = defaultdict(lambda: {"checked": 0, "hallucinated": 0})
    risk: Dict[str, int] = defaultdict(int)
    for r in checked:
        for f in (r.get("checks") or {}).get("findings") or []:
            kinds[f.get("kind", "other")] += 1
        day = by_day.setdefault(r["day"], {"day": r["day"], "checked": 0, "hallucinated": 0, "faith": []})
        day["checked"] += 1
        day["hallucinated"] += int(r.get("hallucinated") or 0)
        day["faith"].append(r["faithfulness"])
        d = by_difficulty[r.get("difficulty") or "unknown"]
        d["checked"] += 1
        d["hallucinated"] += int(r.get("hallucinated") or 0)
        t = by_trust[r.get("trust_level") or "unknown"]
        t["checked"] += 1
        t["hallucinated"] += int(r.get("hallucinated") or 0)
        risk[r.get("risk") or "none"] += 1
    days_out = []
    for day in sorted(by_day):
        item = by_day[day]
        faith = item.pop("faith")
        item["mean_faithfulness"] = round(sum(faith) / len(faith), 4) if faith else None
        item["rate"] = round(item["hallucinated"] / item["checked"], 4) if item["checked"] else None
        days_out.append(item)
    judged = [r for r in checked if (r.get("judge") or {}).get("at")]
    agree = sum(1 for r in judged if bool(r["judge"].get("faithful")) == (not r.get("hallucinated")))
    flagged = [r for r in checked if r.get("risk") in ("medium", "high")][:50]
    for r in flagged:
        r.pop("preview", None)
    return jsonable_encoder({
        "checked": len(checked),
        "hallucinated": sum(int(r.get("hallucinated") or 0) for r in checked),
        "rate": round(sum(int(r.get("hallucinated") or 0) for r in checked) / len(checked), 4) if checked else None,
        "mean_faithfulness": round(sum(r["faithfulness"] for r in checked) / len(checked), 4) if checked else None,
        "risk": dict(risk),
        "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "by_day": days_out,
        "by_difficulty": dict(by_difficulty),
        "by_trust": dict(by_trust),
        "judged": len(judged),
        "judge_agreement": round(agree / len(judged), 4) if judged else None,
        "flagged": flagged,
        "weights": hallucination.WEIGHTS,
    })


# ================================================================ quality
SUGGESTIONS = {
    "unverified_number": "Answers state numbers that are not in the result. Ask follow-up questions that return "
                         "the figure directly, or check whether the answer step is computing extra numbers.",
    "unverified_value": "Answers name values that are not in the result. Check these answers with the AI judge "
                        "and promote corrected ones to golden tests.",
    "empty_result_claim": "Some answers describe data when the query returned nothing. Review the filters in those "
                          "SQL queries (dates and spelling of values are common causes).",
    "superlative_mismatch": "Some 'highest/lowest' answers do not match the sorted result. Promote corrected cases "
                            "so evaluations catch this.",
    "row_count_mismatch": "Some answers state a different count than the result. Promote corrected cases.",
    "fanout": "Joins may be counting rows more than once. Add golden tests with totals over joined tables.",
    "low_trust": "Many answers have low trust. Run an evaluation to see which stage fails most.",
}


@router.get("/quality")
def quality_summary(days: float = 30, caller: Caller = Depends(current_caller)):
    store = get_store()
    rows = store.list_answers(caller.user_id, since=_since(days), limit=1000)
    up = sum(1 for r in rows if r.get("rating", 0) > 0)
    down = sum(1 for r in rows if r.get("rating", 0) < 0)
    review = store.list_answers(caller.user_id, since=_since(days), limit=200, only="review")
    for r in review:
        r.pop("preview", None)
    states: Dict[str, int] = defaultdict(int)
    for r in rows:
        states[r.get("review") or "open"] += 1
    by_day: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        item = by_day.setdefault(r["day"], {"day": r["day"], "answers": 0, "up": 0, "down": 0,
                                            "ok": 0, "trust": []})
        item["answers"] += 1
        item["up"] += r.get("rating", 0) > 0
        item["down"] += r.get("rating", 0) < 0
        item["ok"] += r.get("status") == "SUCCESS"
        if r.get("trust_score") is not None:
            item["trust"].append(r["trust_score"])
    days_out = []
    for day in sorted(by_day):
        item = by_day[day]
        trust = item.pop("trust")
        item["mean_trust"] = round(sum(trust) / len(trust), 1) if trust else None
        item["quality"] = _quality(item)
        days_out.append(item)
    kinds: Dict[str, int] = defaultdict(int)
    for r in rows:
        for f in (r.get("checks") or {}).get("findings") or []:
            kinds[f.get("kind", "")] += 1
    tips = [{"kind": k, "count": n, "text": SUGGESTIONS[k]}
            for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]) if k in SUGGESTIONS][:4]
    return jsonable_encoder({
        "answers": len(rows),
        "rated": up + down,
        "up": up,
        "down": down,
        "satisfaction": round(up / (up + down), 4) if (up + down) else None,
        "success_rate": round(sum(1 for r in rows if r.get("status") == "SUCCESS") / len(rows), 4) if rows else None,
        "review_states": dict(states),
        "review_queue": review,
        "by_day": days_out,
        "suggestions": tips,
        "promoted": states.get("promoted", 0),
    })


def _quality(day: Dict[str, Any]) -> Optional[float]:
    """A 0-100 daily quality index: success, trust and feedback together."""
    if not day["answers"]:
        return None
    parts = [day["ok"] / day["answers"]]
    if day.get("mean_trust") is not None:
        parts.append(day["mean_trust"] / 100)
    if day["up"] + day["down"]:
        parts.append(day["up"] / (day["up"] + day["down"]))
    return round(100 * sum(parts) / len(parts), 1)


# ================================================================== evals
@router.get("/evals/reports")
def eval_reports(caller: Caller = Depends(current_caller)):
    return {"reports": jsonable_encoder(evals.list_reports(caller.user_id)), "gate": evals.get_gate()}


@router.get("/evals/report")
def eval_report(id: str, caller: Caller = Depends(current_caller)):
    try:
        return jsonable_encoder(evals.read_report(caller.user_id, id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/evals/compare")
def eval_compare(base: str, other: str, caller: Caller = Depends(current_caller)):
    try:
        return jsonable_encoder(evals.compare(caller.user_id, base, other))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class GateBody(BaseModel):
    execution_accuracy: Optional[float] = Field(default=None, ge=0, le=1)
    answer_faithfulness: Optional[float] = Field(default=None, ge=0, le=1)
    safety_pass_rate: Optional[float] = Field(default=None, ge=0, le=1)
    latency_p95_s: Optional[float] = Field(default=None, gt=0, le=3600)


@router.get("/evals/gate")
def get_gate(caller: Caller = Depends(current_caller)):
    return evals.get_gate()


@router.put("/evals/gate")
def put_gate(body: GateBody, caller: Caller = Depends(current_caller)):
    try:
        return evals.set_gate(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/evals/golden")
def golden_sets(caller: Caller = Depends(current_caller)):
    return {"golden": evals.list_golden(caller.user_id)}


@router.get("/evals/golden/cases")
def golden_cases(id: str, caller: Caller = Depends(current_caller)):
    try:
        return evals.read_golden(caller.user_id, id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class RunBody(BaseModel):
    connection_id: str
    golden_id: str
    repeats: int = Field(default=1, ge=1, le=5)
    workers: int = Field(default=1, ge=1, le=4)
    difficulty: str = Field(default="", max_length=100)
    tags: str = Field(default="", max_length=400)
    ids: str = Field(default="", max_length=400)
    ablation: bool = False
    max_cost: Optional[float] = Field(default=None, gt=0, le=100)
    include_unverified: bool = False


@router.post("/evals/runs")
def start_run(body: RunBody, caller: Caller = Depends(current_caller)):
    session, label = _session(body.connection_id, caller)
    engine = session["engine"]
    try:
        return evals.start_run(
            caller.user_id, engine.url.render_as_string(hide_password=False), label, body.golden_id,
            repeats=body.repeats, workers=body.workers, difficulty=body.difficulty, tags=body.tags,
            ids=body.ids, ablation=body.ablation, max_cost=body.max_cost,
            include_unverified=body.include_unverified)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/evals/runs")
def list_runs(caller: Caller = Depends(current_caller)):
    return {"runs": jsonable_encoder(evals.list_jobs(caller.user_id))}


@router.get("/evals/runs/{job_id}")
def get_run(job_id: str, caller: Caller = Depends(current_caller)):
    try:
        return jsonable_encoder(evals.get_job(caller.user_id, job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found.") from exc


@router.post("/evals/runs/{job_id}/stop")
def stop_run(job_id: str, caller: Caller = Depends(current_caller)):
    try:
        return jsonable_encoder(evals.stop_job(caller.user_id, job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found.") from exc


# ================================================================ routing
@router.get("/routing")
def routing_overview(caller: Caller = Depends(current_caller)):
    stats = routing.route_stats()
    return jsonable_encoder({
        "mode": routing.get_mode(fresh=True),
        "summary": routing.summary(),
        "weights": routing.WEIGHTS,
        "preview": {level: routing.preview(level) for level in ("easy", "medium", "hard")},
        "stats": stats,
        "accuracy": evals.route_accuracy(),
    })


class ModeBody(BaseModel):
    mode: str = Field(pattern="^(off|shadow|on)$")


@router.put("/routing")
def set_routing(body: ModeBody, caller: Caller = Depends(current_caller)):
    return {"mode": routing.set_mode(body.mode), "summary": routing.summary()}


@router.get("/routing/decisions")
def routing_decisions(limit: int = 100, caller: Caller = Depends(current_caller)):
    return {"decisions": jsonable_encoder(routing.decisions(caller.user_id, max(1, min(limit, 300))))}


@router.get("/routing/classify")
def classify(question: str, caller: Caller = Depends(current_caller)):
    from backend.llmops.difficulty import classify as run

    return run(question[:4000])


# ================================================================ install
def install(app) -> None:
    """Add the capture middleware (watches /query; never changes it)."""
    app.add_middleware(CaptureMiddleware)
