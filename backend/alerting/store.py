# backend/alerting/store.py
#
# Saved alerts, the alerts they raised ("events"), and past readings of
# one-number alerts. Same storage as the other stores: one Postgres for
# everything when INSIGHTX_APP_DB_URL is set, else logs/alerts.db.
# INSIGHTX_ALERTS_DB_URL points it somewhere else.

import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import (Boolean, Column, Float, Index, Integer, MetaData, String, Table, Text, and_, delete, func,
                        insert, select, update)
from sqlalchemy.engine import Engine

from backend import app_db

MAX_ALERTS = 50          # per person and database
MAX_EVENTS = 1000        # per person
MAX_OBSERVATIONS = 500   # per one-number alert

metadata = MetaData()

alerts = Table(
    "insightx_alerts", metadata,
    Column("id", String(32), primary_key=True),
    Column("owner", String(200), nullable=False),
    Column("database", String(300), nullable=False, default=""),
    Column("connection_id", String(100), nullable=False),
    Column("name", String(300), nullable=False),
    Column("metric", Text, nullable=False, default=""),
    Column("sql", Text, nullable=False),
    Column("grain", String(20)),
    Column("fill_gaps", Boolean, nullable=False, default=False),
    Column("single", Boolean, nullable=False, default=False),
    Column("rule", Text, nullable=False),
    Column("check_minutes", Integer, nullable=False, default=60),
    Column("channels", Text, nullable=False, default="{}"),
    Column("status", String(20), nullable=False, default="active"),
    Column("created", Float, nullable=False),
    Column("next_check", Float, nullable=False, default=0.0),
    Column("last_checked", Float),
    Column("state", String(30), nullable=False, default="new"),
    Column("last_value", Float),
    Column("last_message", Text, nullable=False, default=""),
    Column("last_error", Text, nullable=False, default=""),
    Column("snooze_until", Float, nullable=False, default=0.0),
    Index("ix_insightx_alerts_owner", "owner", "database"),
    Index("ix_insightx_alerts_due", "status", "next_check"),
)

events = Table(
    "insightx_alert_events", metadata,
    Column("id", String(32), primary_key=True),
    Column("alert_id", String(32), nullable=False),
    Column("owner", String(200), nullable=False),
    Column("database", String(300), nullable=False, default=""),
    Column("alert_name", String(300), nullable=False, default=""),
    Column("ts", Float, nullable=False),
    Column("updated", Float, nullable=False),
    Column("period", String(100), nullable=False, default=""),
    Column("severity", String(20), nullable=False, default="warning"),
    Column("title", String(500), nullable=False, default=""),
    Column("message", Text, nullable=False, default=""),
    Column("value", Float),
    Column("expected", Float),
    Column("change_pct", Float),
    Column("status", String(20), nullable=False, default="open"),
    Column("resolved_ts", Float),
    Column("notified", Text, nullable=False, default=""),
    Index("ix_insightx_alert_events_owner", "owner", "ts"),
    Index("ix_insightx_alert_events_alert", "alert_id", "status"),
)

observations = Table(
    "insightx_alert_readings", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("alert_id", String(32), nullable=False),
    Column("ts", Float, nullable=False),
    Column("value", Float, nullable=False),
    Index("ix_insightx_alert_readings_alert", "alert_id", "ts"),
)

OPEN = ("open", "acknowledged")


def _alert_row(row) -> Dict[str, Any]:
    data = dict(row._mapping)
    data["rule"] = json.loads(data.get("rule") or "{}")
    data["channels"] = json.loads(data.get("channels") or "{}")
    return data


class AlertStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_ALERTS_DB_URL", "alerts.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    # ---------------- alerts ----------------

    def count(self, owner: str, database: str) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(alerts).where(
                alerts.c.owner == owner, alerts.c.database == database)).scalar() or 0)

    def add_alert(self, owner: str, database: str, connection_id: str, name: str, metric: str, sql: str,
                  grain: Optional[str], fill_gaps: bool, single: bool, rule: Dict[str, Any], check_minutes: int,
                  channels: Dict[str, Any]) -> str:
        alert_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self.engine.begin() as connection:
            connection.execute(insert(alerts).values(
                id=alert_id, owner=owner, database=database, connection_id=connection_id, name=name[:300],
                metric=metric[:2000], sql=sql, grain=grain, fill_gaps=bool(fill_gaps), single=bool(single),
                rule=json.dumps(rule), check_minutes=int(check_minutes), channels=json.dumps(channels or {}),
                status="active", created=now, next_check=now + int(check_minutes) * 60, state="new",
                last_message="", last_error="", snooze_until=0.0))
        return alert_id

    def list_alerts(self, owner: str, database: Optional[str] = None) -> List[Dict[str, Any]]:
        query = select(alerts).where(alerts.c.owner == owner)
        if database is not None:
            query = query.where(alerts.c.database == database)
        with self.engine.connect() as connection:
            rows = connection.execute(query.order_by(alerts.c.created.desc())).fetchall()
        return [_alert_row(row) for row in rows]

    def get_alert(self, alert_id: str, owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
        query = select(alerts).where(alerts.c.id == alert_id)
        if owner is not None:
            query = query.where(alerts.c.owner == owner)
        with self.engine.connect() as connection:
            row = connection.execute(query).fetchone()
        return _alert_row(row) if row else None

    def update_alert(self, alert_id: str, **fields) -> None:
        if "rule" in fields:
            fields["rule"] = json.dumps(fields["rule"])
        if "channels" in fields:
            fields["channels"] = json.dumps(fields["channels"] or {})
        with self._lock, self.engine.begin() as connection:
            connection.execute(update(alerts).where(alerts.c.id == alert_id).values(**fields))

    def delete_alert(self, owner: str, alert_id: str) -> bool:
        with self._lock, self.engine.begin() as connection:
            result = connection.execute(delete(alerts).where(alerts.c.owner == owner, alerts.c.id == alert_id))
            if result.rowcount:
                connection.execute(delete(events).where(events.c.alert_id == alert_id))
                connection.execute(delete(observations).where(observations.c.alert_id == alert_id))
        return bool(result.rowcount)

    def due(self, now: float, limit: int = 20) -> List[Dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(alerts).where(alerts.c.status == "active", alerts.c.next_check <= now)
                                      .order_by(alerts.c.next_check).limit(limit)).fetchall()
        return [_alert_row(row) for row in rows]

    def claim(self, alert_id: str, seen_next_check: float, new_next_check: float) -> bool:
        """Take an alert for checking; False when another worker already took it."""
        with self._lock, self.engine.begin() as connection:
            result = connection.execute(update(alerts).where(
                alerts.c.id == alert_id, alerts.c.next_check == seen_next_check).values(next_check=new_next_check))
        return bool(result.rowcount)

    # ---------------- readings of one-number alerts ----------------

    def add_reading(self, alert_id: str, value: float, ts: Optional[float] = None) -> None:
        with self._lock, self.engine.begin() as connection:
            connection.execute(insert(observations).values(alert_id=alert_id, ts=ts or time.time(), value=value))
            stale = connection.execute(select(observations.c.ts).where(observations.c.alert_id == alert_id)
                                       .order_by(observations.c.ts.desc()).offset(MAX_OBSERVATIONS).limit(1)).scalar()
            if stale is not None:
                connection.execute(delete(observations).where(observations.c.alert_id == alert_id,
                                                              observations.c.ts <= stale))

    def readings(self, alert_id: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(observations.c.ts, observations.c.value)
                                      .where(observations.c.alert_id == alert_id)
                                      .order_by(observations.c.ts)).fetchall()
        return [{"ts": row.ts, "value": row.value} for row in rows]

    # ---------------- events ----------------

    def open_event(self, alert_id: str, period: Optional[str] = None) -> Optional[Dict[str, Any]]:
        query = select(events).where(events.c.alert_id == alert_id, events.c.status.in_(OPEN))
        if period is not None:
            query = query.where(events.c.period == period)
        with self.engine.connect() as connection:
            row = connection.execute(query.order_by(events.c.ts.desc())).fetchone()
        return dict(row._mapping) if row else None

    def add_event(self, alert: Dict[str, Any], result: Dict[str, Any], notified: str = "") -> str:
        event_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self.engine.begin() as connection:
            connection.execute(insert(events).values(
                id=event_id, alert_id=alert["id"], owner=alert["owner"], database=alert.get("database", ""),
                alert_name=alert["name"], ts=now, updated=now, period=str(result.get("period") or "")[:100],
                severity=result.get("severity") or "warning", title=str(result.get("title") or "")[:500],
                message=str(result.get("message") or ""), value=result.get("value"),
                expected=result.get("expected"), change_pct=result.get("change_pct"), status="open",
                notified=notified))
            stale = connection.execute(select(events.c.ts).where(events.c.owner == alert["owner"])
                                       .order_by(events.c.ts.desc()).offset(MAX_EVENTS).limit(1)).scalar()
            if stale is not None:
                connection.execute(delete(events).where(events.c.owner == alert["owner"], events.c.ts <= stale))
        return event_id

    def update_event(self, event_id: str, **fields) -> None:
        fields.setdefault("updated", time.time())
        with self._lock, self.engine.begin() as connection:
            connection.execute(update(events).where(events.c.id == event_id).values(**fields))

    def resolve_open(self, alert_id: str, message: str = "") -> int:
        """The number is back to normal: close its open alerts."""
        now = time.time()
        values = {"status": "resolved", "resolved_ts": now, "updated": now}
        with self._lock, self.engine.begin() as connection:
            result = connection.execute(update(events).where(
                events.c.alert_id == alert_id, events.c.status.in_(OPEN)).values(**values))
        return int(result.rowcount or 0)

    def list_events(self, owner: str, status: str = "open", database: Optional[str] = None,
                    alert_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        query = select(events).where(events.c.owner == owner)
        if status == "open":
            query = query.where(events.c.status.in_(OPEN))
        elif status in ("acknowledged", "resolved"):
            query = query.where(events.c.status == status)
        if database is not None:
            query = query.where(events.c.database == database)
        if alert_id is not None:
            query = query.where(events.c.alert_id == alert_id)
        with self.engine.connect() as connection:
            rows = connection.execute(query.order_by(events.c.ts.desc()).limit(max(1, min(limit, 500)))).fetchall()
        return [dict(row._mapping) for row in rows]

    def set_event_status(self, owner: str, event_id: str, status: str) -> bool:
        now = time.time()
        values: Dict[str, Any] = {"status": status, "updated": now,
                                  "resolved_ts": now if status == "resolved" else None}
        with self._lock, self.engine.begin() as connection:
            result = connection.execute(update(events).where(
                events.c.owner == owner, events.c.id == event_id).values(**values))
        return bool(result.rowcount)

    def summary(self, owner: str, database: Optional[str] = None) -> Dict[str, Any]:
        event_filter = and_(events.c.owner == owner, events.c.status.in_(OPEN))
        alert_filter = alerts.c.owner == owner
        if database is not None:
            event_filter = and_(event_filter, events.c.database == database)
            alert_filter = and_(alert_filter, alerts.c.database == database)
        with self.engine.connect() as connection:
            by_severity = dict(connection.execute(select(events.c.severity, func.count()).where(event_filter)
                                                  .group_by(events.c.severity)).fetchall())
            by_status = dict(connection.execute(select(alerts.c.status, func.count()).where(alert_filter)
                                                .group_by(alerts.c.status)).fetchall())
            last_check = connection.execute(select(func.max(alerts.c.last_checked)).where(alert_filter)).scalar()
            day_ago = time.time() - 86400
            raised_today = connection.execute(select(func.count()).select_from(events).where(
                events.c.owner == owner, events.c.ts >= day_ago,
                *( [events.c.database == database] if database is not None else []))).scalar()
        critical, warning = int(by_severity.get("critical", 0)), int(by_severity.get("warning", 0))
        return {"open": critical + warning, "critical": critical, "warning": warning,
                "active": int(by_status.get("active", 0)), "paused": int(by_status.get("paused", 0)),
                "last_check": last_check, "raised_24h": int(raised_today or 0)}


_store: Optional[AlertStore] = None
_store_lock = threading.Lock()


def get_store() -> AlertStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = AlertStore()
        return _store


def set_store(store: Optional[AlertStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store
