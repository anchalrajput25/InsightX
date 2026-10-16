# backend/alerting/api.py
#
# The /alerts endpoints. Plugged in by a few lines at the end of
# backend/main.py (include_router + start the background checker).

import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from backend import telemetry
from backend.alerting import detect, metrics, notify, service
from backend.alerting.store import MAX_ALERTS, get_store
from backend.auth.deps import Caller, current_caller

router = APIRouter(prefix="/alerts", tags=["alerts"])

CHECK_MINUTES = (5, 15, 30, 60, 180, 360, 720, 1440)


def _main():
    from backend import main  # imported late: main includes this router

    return main


def _session(connection_id: str, caller: Caller):
    main = _main()
    session = main.get_connection_session(connection_id, caller)
    return session, main._database_label(session)


class MetricBody(BaseModel):
    connection_id: str
    metric_text: str = Field(default="", max_length=1000)
    sql: str = Field(default="", max_length=20000)
    name: str = Field(default="", max_length=300)
    fill_gaps: bool = False
    rule: Dict[str, Any] = Field(default_factory=dict)


class CreateBody(MetricBody):
    check_minutes: int = 60
    webhook: str = Field(default="", max_length=1000)
    email: str = Field(default="", max_length=300)


class ChangeBody(BaseModel):
    status: Optional[str] = None
    snooze_hours: Optional[float] = None
    rule: Optional[Dict[str, Any]] = None
    check_minutes: Optional[int] = None
    name: Optional[str] = Field(default=None, max_length=300)
    webhook: Optional[str] = Field(default=None, max_length=1000)
    email: Optional[str] = Field(default=None, max_length=300)


class EventAction(BaseModel):
    action: str


class TestNotification(BaseModel):
    webhook: str = ""
    email: str = ""


def _rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return detect.clean_rule(rule)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _channels(webhook: str, email: str) -> Dict[str, str]:
    channels = {}
    if webhook.strip():
        if not notify.valid_webhook(webhook):
            raise HTTPException(status_code=422, detail="The web hook must be an http(s) URL.")
        channels["webhook"] = webhook.strip()
    if email.strip():
        if not notify.valid_email(email):
            raise HTTPException(status_code=422, detail="That e-mail address does not look right.")
        channels["email"] = email.strip()
    return channels


def _resolve_metric(body: MetricBody, caller: Caller, engine) -> Dict[str, Any]:
    """The metric's SQL: given (a suggestion, or an earlier preview), or made from words by the AI."""
    sql, used_ai = body.sql.strip(), False
    if not sql:
        text = body.metric_text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="Say what number to watch, or pick a suggested one.")
        main = _main()
        user_id = main._start_question(caller)
        with telemetry.collect():
            with main.ai_request(user_id=user_id):
                result = main.run_text_to_sql(question=text, thread_id=f"alert-{uuid.uuid4()}", engine=engine)
        sql = str(result.get("generated_sql") or "").strip()
        if str(result.get("status") or "").upper() != "SUCCESS" or not sql:
            detail = result.get("clarification") or result.get("error") or "Could not get data for this number."
            raise HTTPException(status_code=422, detail=str(detail)[:500])
        used_ai = True
    try:
        data = metrics.read_series(sql, engine, fill=body.fill_gaps)
    except metrics.MetricError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not data["points"]:
        raise HTTPException(status_code=422, detail="The query gave no data to watch.")
    name = body.name.strip() or body.metric_text.strip()[:1].upper() + body.metric_text.strip()[1:] or "My number"
    return {**data, "sql": sql, "used_ai": used_ai, "name": name[:300]}


def _picture(name: str, rule: Dict[str, Any], data: Dict[str, Any], points=None) -> Dict[str, Any]:
    """The verdict on the latest value, and how the rule would have behaved in the past."""
    points = data["points"] if points is None else points
    alert = {"name": name, "rule": rule, "grain": data["grain"]}
    verdict = service.evaluate_alert(alert, points, data["single"], data["grain"])
    if data["single"]:
        rule = {**rule, "skip_unfinished": False}
    history = detect.backtest(rule, points, name, service.SINGLE_GRAIN if data["single"] else data["grain"],
                              datetime.now())
    return {"evaluation": verdict, "backtest": history}


# ------------------------------------------------------------
# endpoints (fixed paths first, then /{alert_id})
# ------------------------------------------------------------

@router.get("/suggestions")
def suggested_numbers(connection_id: str, grain: str = "day", caller: Caller = Depends(current_caller)):
    """Numbers worth watching in this database (no AI)."""
    session, _ = _session(connection_id, caller)
    from backend import dialects
    from backend.text_to_sql.schema_context import get_agent_schema

    engine = session["engine"]
    with dialects.using(engine):
        schema = get_agent_schema(engine)
    return {"suggestions": metrics.suggestions(engine, schema, grain)}


@router.post("/preview")
def preview(body: MetricBody, caller: Caller = Depends(current_caller)):
    """Try an alert on the current data before saving it."""
    session, _ = _session(body.connection_id, caller)
    rule = _rule(body.rule)
    metric = _resolve_metric(body, caller, session["engine"])
    picture = _picture(metric["name"], rule, metric)
    return jsonable_encoder({"name": metric["name"], "sql": metric["sql"], "grain": metric["grain"],
                             "single": metric["single"], "fill_gaps": body.fill_gaps, "used_ai": metric["used_ai"],
                             "rule": rule, **picture})


@router.get("/summary")
def summary(connection_id: str = "", caller: Caller = Depends(current_caller)):
    """Open alerts, for the sidebar count."""
    database = None
    if connection_id:
        try:
            _, database = _session(connection_id, caller)
        except HTTPException:
            database = None
    return {**get_store().summary(service.owner_key(caller.account_id), database),
            "checker_running": service.scheduler_running()}


@router.get("/events")
def list_events(status: str = "open", connection_id: str = "", alert_id: str = "", limit: int = 100,
                caller: Caller = Depends(current_caller)):
    database = _session(connection_id, caller)[1] if connection_id else None
    found = get_store().list_events(service.owner_key(caller.account_id), status, database, alert_id or None, limit)
    return {"events": jsonable_encoder(found)}


@router.post("/events/{event_id}")
def change_event(event_id: str, body: EventAction, caller: Caller = Depends(current_caller)):
    status = {"acknowledge": "acknowledged", "resolve": "resolved", "reopen": "open"}.get(body.action)
    if status is None:
        raise HTTPException(status_code=422, detail="action must be acknowledge, resolve or reopen.")
    if not get_store().set_event_status(service.owner_key(caller.account_id), event_id, status):
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"status": status}


@router.post("/test-notification")
def test_notification(body: TestNotification, caller: Caller = Depends(current_caller)):
    """Send a test message to a web hook / e-mail before saving the alert."""
    channels = _channels(body.webhook, body.email)
    if not channels:
        raise HTTPException(status_code=422, detail="Give a web hook URL or an e-mail address.")
    alert = {"name": "Test alert", "database": "", "channels": channels}
    result = {"severity": "warning", "title": "Test message from InsightX",
              "message": "If you can read this, InsightX alerts will reach you here."}
    return {"results": notify.send(alert, result)}


@router.get("")
def list_alerts(connection_id: str = "", caller: Caller = Depends(current_caller)):
    """This person's alerts (for the connected database when connection_id is given)."""
    store = get_store()
    owner = service.owner_key(caller.account_id)
    database = None
    if connection_id:
        _, database = _session(connection_id, caller)
        # after reconnecting, the same database has a new connection id: follow it
        for alert in store.list_alerts(owner, database):
            if alert["connection_id"] != connection_id:
                store.update_alert(alert["id"], connection_id=connection_id)
    found = store.list_alerts(owner, database)
    return jsonable_encoder({"alerts": [{k: v for k, v in a.items() if k != "owner"} for a in found],
                             "summary": store.summary(owner, database),
                             "checker_running": service.scheduler_running(),
                             "email_ready": notify.smtp_ready(), "check_minutes": CHECK_MINUTES})


@router.post("")
def create_alert(body: CreateBody, caller: Caller = Depends(current_caller)):
    session, database = _session(body.connection_id, caller)
    store = get_store()
    owner = service.owner_key(caller.account_id)
    if store.count(owner, database) >= MAX_ALERTS:
        raise HTTPException(status_code=422, detail=f"You can have up to {MAX_ALERTS} alerts per database.")
    rule = _rule(body.rule)
    if body.check_minutes not in CHECK_MINUTES:
        raise HTTPException(status_code=422, detail=f"check_minutes must be one of {CHECK_MINUTES}.")
    channels = _channels(body.webhook, body.email)
    metric = _resolve_metric(body, caller, session["engine"])
    if metric["single"] and rule["type"] == "stale":
        raise HTTPException(status_code=422, detail="'Data stops arriving' needs a number by date, not one number.")
    alert_id = store.add_alert(owner, database, body.connection_id, metric["name"], body.metric_text.strip(),
                               metric["sql"], metric["grain"], body.fill_gaps, metric["single"], rule,
                               body.check_minutes, channels)
    alert = store.get_alert(alert_id)
    first = service.check_alert(alert, session["engine"], store)
    return jsonable_encoder({"alert": {k: v for k, v in store.get_alert(alert_id).items() if k != "owner"},
                             "first_check": first})


def _own(alert_id: str, caller: Caller) -> Dict[str, Any]:
    alert = get_store().get_alert(alert_id, service.owner_key(caller.account_id))
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found.")
    return alert


@router.get("/{alert_id}/chart")
def alert_chart(alert_id: str, caller: Caller = Depends(current_caller)):
    """The live series with its normal-range band and past alerts."""
    alert = _own(alert_id, caller)
    session = _main().get_connection_session(alert["connection_id"], caller)
    store = get_store()
    try:
        data = metrics.read_series(alert["sql"], session["engine"], fill=bool(alert.get("fill_gaps")))
    except metrics.MetricError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    points = service.points_for(alert, data, store)
    return jsonable_encoder({"alert_id": alert_id, "single": data["single"],
                             **_picture(alert["name"], alert["rule"], data, points)})


@router.post("/{alert_id}/check")
def check_now(alert_id: str, caller: Caller = Depends(current_caller)):
    alert = _own(alert_id, caller)
    session = _main().get_connection_session(alert["connection_id"], caller)
    store = get_store()
    result = service.check_alert(alert, session["engine"], store)
    store.update_alert(alert_id, next_check=time.time() + int(alert["check_minutes"]) * 60)
    return jsonable_encoder({"result": result,
                             "alert": {k: v for k, v in store.get_alert(alert_id).items() if k != "owner"}})


@router.patch("/{alert_id}")
def change_alert(alert_id: str, body: ChangeBody, caller: Caller = Depends(current_caller)):
    alert = _own(alert_id, caller)
    fields: Dict[str, Any] = {}
    if body.status is not None:
        if body.status not in ("active", "paused"):
            raise HTTPException(status_code=422, detail="status must be active or paused.")
        fields["status"] = body.status
        if body.status == "active":
            fields["next_check"] = time.time()
    if body.snooze_hours is not None:
        fields["snooze_until"] = time.time() + max(0.0, float(body.snooze_hours)) * 3600 if body.snooze_hours else 0.0
    if body.rule is not None:
        fields["rule"] = _rule({**alert["rule"], **body.rule})
    if body.check_minutes is not None:
        if body.check_minutes not in CHECK_MINUTES:
            raise HTTPException(status_code=422, detail=f"check_minutes must be one of {CHECK_MINUTES}.")
        fields["check_minutes"] = body.check_minutes
        fields["next_check"] = time.time() + body.check_minutes * 60
    if body.name is not None and body.name.strip():
        fields["name"] = body.name.strip()
    if body.webhook is not None or body.email is not None:
        current = alert.get("channels") or {}
        fields["channels"] = _channels(current.get("webhook", "") if body.webhook is None else body.webhook,
                                       current.get("email", "") if body.email is None else body.email)
    if fields:
        get_store().update_alert(alert_id, **fields)
    return jsonable_encoder({k: v for k, v in get_store().get_alert(alert_id).items() if k != "owner"})


@router.delete("/{alert_id}")
def delete_alert(alert_id: str, caller: Caller = Depends(current_caller)):
    if not get_store().delete_alert(service.owner_key(caller.account_id), alert_id):
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"deleted": 1}
