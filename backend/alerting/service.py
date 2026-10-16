# backend/alerting/service.py
#
# One check of one alert, and the background checker that runs the
# checks that are due (every 30 seconds it looks for alerts whose time
# has come). A check: run the metric's SQL on the live database, judge
# the latest value (detect.py), then
#   unusual  -> raise an alert (or update the open one for the same
#               period; a warning that turns critical is sent again)
#   normal   -> close the alert's open alerts ("back to normal")
# Snoozed alerts are still recorded, just not sent.
#
# INSIGHTX_ALERTS_SCHEDULER=0 turns the background checker off.

import logging
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.engine import Engine

from backend.alerting import detect, metrics, notify
from backend.alerting.store import AlertStore, get_store

logger = logging.getLogger(__name__)

TICK_SECONDS = float(os.getenv("INSIGHTX_ALERTS_TICK_SECONDS", "30"))
SINGLE_GRAIN = "reading"   # one-number alerts: each check is one reading


def owner_key(account_id: Optional[int]) -> str:
    """Same owner names as the chat list: account:<id>, or 'local' with sign-in off."""
    return f"account:{account_id}" if account_id is not None else "local"


def points_for(alert: Dict[str, Any], data: Dict[str, Any], store: AlertStore) -> List[Dict[str, Any]]:
    if not data["single"]:
        return data["points"]
    return [{"t": datetime.fromtimestamp(r["ts"]), "v": r["value"]} for r in store.readings(alert["id"])]


def evaluate_alert(alert: Dict[str, Any], points: List[Dict[str, Any]], single: bool,
                   grain: Optional[str], now: Optional[datetime] = None) -> Dict[str, Any]:
    rule = dict(alert["rule"])
    if single:
        rule["skip_unfinished"] = False
        grain = SINGLE_GRAIN
    return detect.evaluate(rule, points, alert["name"], grain or alert.get("grain"), now or datetime.now())


def check_alert(alert: Dict[str, Any], engine: Engine, store: Optional[AlertStore] = None,
                send: bool = True, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Check one alert now. Never raises: problems are saved on the alert."""
    store = store or get_store()
    checked = time.time()
    try:
        data = metrics.read_series(alert["sql"], engine, fill=bool(alert.get("fill_gaps")))
        if data["single"]:
            if not data["points"]:
                raise metrics.MetricError("The query gave no number.")
            store.add_reading(alert["id"], data["points"][0]["v"], checked)
        result = evaluate_alert(alert, points_for(alert, data, store), data["single"], data["grain"], now)
    except Exception as exc:  # the database is down, the table was renamed...
        message = str(exc).strip()[:500] or exc.__class__.__name__
        store.update_alert(alert["id"], state="error", last_error=message, last_checked=checked)
        return detect._result("error", message=message)

    if result["fired"]:
        _raise(alert, result, store, send)
    elif result["status"] == "ok":
        store.resolve_open(alert["id"])
    store.update_alert(alert["id"], state=result["status"], last_value=result.get("value"),
                       last_message=result.get("message") or "", last_error="", last_checked=checked)
    return result


def _raise(alert: Dict[str, Any], result: Dict[str, Any], store: AlertStore, send: bool) -> None:
    snoozed = float(alert.get("snooze_until") or 0) > time.time()
    # one open alert per period; a one-number alert keeps one open alert until it is back to normal
    existing = store.open_event(alert["id"], None if alert.get("single") else str(result.get("period") or ""))
    if existing:
        worse = existing["severity"] == "warning" and result["severity"] == "critical"
        fields = {key: result.get(key) for key in ("severity", "title", "message", "value", "expected", "change_pct")}
        fields["period"] = str(result.get("period") or "")[:100]
        if worse and send and not snoozed:
            fields["notified"] = "; ".join(notify.send(alert, result))
        store.update_event(existing["id"], **{k: v for k, v in fields.items() if v is not None or k == "expected"})
        return
    lines = notify.send(alert, result) if send and not snoozed else []
    store.add_event(alert, result, "; ".join(lines) or ("snoozed, not sent" if snoozed else ""))


# ------------------------------------------------------------
# the background checker
# ------------------------------------------------------------

def _open_engine(alert: Dict[str, Any], store: AlertStore) -> Engine:
    """The alert's database: its own connection, or another live one to the same database."""
    from backend import main

    try:
        session = main.get_connection_session(alert["connection_id"])
        if owner_key(session.get("owner")) == alert["owner"]:
            return session["engine"]
    except Exception:
        pass
    for connection_id, session in list(main.POSTGRES_SESSIONS.items()):
        if owner_key(session.get("owner")) == alert["owner"] and main._database_label(session) == alert["database"]:
            store.update_alert(alert["id"], connection_id=connection_id)
            return session["engine"]
    raise RuntimeError("The database is not connected. Connect it again on the 💬 page and the alert carries on.")


def tick(store: Optional[AlertStore] = None, open_engine: Optional[Callable] = None,
         now: Optional[float] = None) -> int:
    """Check every alert that is due. Returns how many were checked."""
    store = store or get_store()
    now = time.time() if now is None else now
    done = 0
    for alert in store.due(now):
        if not store.claim(alert["id"], alert["next_check"], now + int(alert["check_minutes"]) * 60):
            continue  # another worker took it
        try:
            engine = (open_engine or _open_engine)(alert, store)
        except Exception as exc:
            store.update_alert(alert["id"], state="error", last_error=str(exc)[:500], last_checked=now)
            continue
        check_alert(alert, engine, store)
        done += 1
    return done


_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:
            logger.exception("Alert check round failed")
        time.sleep(TICK_SECONDS)


def scheduler_enabled() -> bool:
    if os.getenv("INSIGHTX_ALERTS_SCHEDULER", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return "pytest" not in sys.modules


def start_scheduler() -> bool:
    """Start the background checker once per process. True when it is running."""
    global _thread
    if not scheduler_enabled():
        return False
    with _thread_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="insightx-alerts", daemon=True)
            _thread.start()
    return True


def scheduler_running() -> bool:
    return _thread is not None and _thread.is_alive()
