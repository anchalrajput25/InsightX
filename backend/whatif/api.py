# backend/whatif/api.py
#
#   POST /whatif          question or edited spec -> scenario report
#   POST /whatif/sweep    one lever over a range of values
#   POST /forecast        next periods, optionally under the scenario
#   GET  /whatif/route    does a question look like a what-if? (UI hint)
#
# Same rules as /query and /investigate: sign-in (current_caller), the
# connection must belong to the caller, and a question that needs the AI
# counts against the caller's daily question limit. A spec edited in the
# browser runs without the AI, so it does not count.

import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from backend import telemetry, tracing
from backend.ai_gateway import AIUnavailableError, ai_request
from backend.auth.deps import Caller, current_caller
from backend.investigation.engine import InvestigationError
from backend.whatif.planner import looks_like_whatif
from backend.whatif.service import run_forecast, run_sweep, run_whatif
from backend.whatif.spec import WhatIfError

logger = logging.getLogger("insightx.whatif")

router = APIRouter()


class WhatIfRequest(BaseModel):
    connection_id: str
    question: Optional[str] = Field(default=None, max_length=4000)
    # A scenario returned by an earlier call, possibly edited (levers, numbers).
    spec: Optional[Dict[str, Any]] = None


class SweepRequest(BaseModel):
    connection_id: str
    spec: Dict[str, Any]
    lever_index: int = Field(default=0, ge=0, le=10)
    values: Optional[List[float]] = Field(default=None, max_length=25)
    elasticity: Optional[float] = Field(default=None, ge=-10, le=10)


class ForecastRequest(BaseModel):
    connection_id: str
    question: Optional[str] = Field(default=None, max_length=4000)
    spec: Optional[Dict[str, Any]] = None
    horizon: int = Field(default=6, ge=1, le=36)
    grain: Optional[str] = Field(default=None, pattern="^(day|week|month|quarter|year)$")


def _engine(connection_id: str, caller: Caller):
    # Imported here: backend.main includes this router.
    from backend.main import get_connection_session

    return get_connection_session(connection_id, caller)["engine"]


def _run(caller: Caller, question: str, needs_ai: bool, kind: str, work: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    from backend.main import _start_question

    user_id = _start_question(caller) if needs_ai else caller.user_id
    with telemetry.collect() as stats:
        try:
            with ai_request(user_id=user_id):
                report = work()
        except Exception as exc:
            tracing.save(stats, user_id, question, kind=kind, error=f"{type(exc).__name__}: {exc}")
            if isinstance(exc, AIUnavailableError):
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if isinstance(exc, (WhatIfError, InvestigationError)):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if isinstance(exc, HTTPException):
                raise
            logger.exception("What-if failed")
            raise HTTPException(status_code=500, detail=f"What-if failed: {str(exc).splitlines()[0][:300]}") from exc
    report["trace"] = tracing.save(stats, user_id, question, kind=kind, result={"status": report.get("status")}) or {}
    return jsonable_encoder(report)


def _question_or_spec(question: Optional[str], spec: Optional[Dict[str, Any]]) -> str:
    if spec is None and not (question or "").strip():
        raise HTTPException(status_code=422, detail="Send a question or a scenario spec.")
    return (question or "").strip() or "(edited scenario)"


@router.post("/whatif")
def whatif(request: WhatIfRequest, caller: Caller = Depends(current_caller)):
    """What-if scenario: baseline vs scenario from read-only SQL, by segment, with sensitivity."""
    question = _question_or_spec(request.question, request.spec)
    engine = _engine(request.connection_id, caller)
    return _run(caller, question, request.spec is None, "whatif",
                lambda: run_whatif(request.question or "", engine, spec=request.spec))


@router.post("/whatif/sweep")
def whatif_sweep(request: SweepRequest, caller: Caller = Depends(current_caller)):
    """One lever of a scenario swept over a range of values."""
    engine = _engine(request.connection_id, caller)
    return _run(caller, "(sensitivity)", False, "whatif",
                lambda: run_sweep(request.spec, engine, request.lever_index, request.values, request.elasticity))


@router.post("/forecast")
def forecast(request: ForecastRequest, caller: Caller = Depends(current_caller)):
    """Forecast of a metric with a 95% band and a backtest, optionally under a scenario."""
    question = _question_or_spec(request.question, request.spec)
    engine = _engine(request.connection_id, caller)
    return _run(caller, question, request.spec is None, "forecast",
                lambda: run_forecast(request.question or "", engine, spec=request.spec,
                                     horizon=request.horizon, grain=request.grain))


@router.get("/whatif/route")
def whatif_route(question: str, caller: Caller = Depends(current_caller)):
    """Whether a question looks like a what-if or a forecast (UI routing hint)."""
    return {"whatif": looks_like_whatif(question)}
