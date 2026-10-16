# backend/llmops/monitor.py
#
# Live monitoring for the Monitor page. Reads the existing stores only:
#   - AI usage ledger (every model call: route, status, tokens, latency)
#   - traces (every question: status, seconds, stage timings)
#   - answer checks (hallucination risk, trust, feedback)
#   - the gateway's live route health (cooldowns, free limits)
#
# Gives KPIs, time series, per-model health, stage latency, SLOs with an
# error budget, simple anomaly incidents and recent failures.
#
# Privacy: with sign-in on, questions, tokens and cost are the caller's
# own. Model health (which free model is up) is shared, as on /ai/routes.

import json
import math
import time
from collections import defaultdict
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from backend.llmops.store import get_store

DEFAULT_SLO = {
    "success_rate": 0.95,      # questions answered without an error
    "p95_seconds": 30.0,       # 95% of questions faster than this
    "ai_error_rate": 0.10,     # model calls that failed
    "hallucination_rate": 0.05,
}

WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


def percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    low, high = math.floor(k), math.ceil(k)
    if low == high:
        return round(ordered[int(k)], 3)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (k - low), 3)


def _bucket_size(seconds: float) -> float:
    if seconds <= 3600:
        return 300          # 5 minutes
    if seconds <= 86400:
        return 3600         # 1 hour
    if seconds <= 7 * 86400:
        return 6 * 3600     # 6 hours
    return 86400            # 1 day


def _traces(user_id: str, since: float, with_detail: bool = False) -> List[Dict[str, Any]]:
    from backend import tracing

    store = tracing.get_store()
    table = tracing.traces
    columns = [c for c in table.c if with_detail or c.name != "detail"]
    with store.engine.connect() as connection:
        rows = connection.execute(
            select(*columns).where(table.c.user_id == user_id, table.c.ts >= since)
            .order_by(table.c.ts.desc()).limit(5000)
        ).all()
    items = []
    for row in rows:
        item = dict(row._mapping)
        if with_detail:
            try:
                item["detail"] = json.loads(item.get("detail") or "{}")
            except ValueError:
                item["detail"] = {}
        items.append(item)
    return items


def _calls(user_id: Optional[str], since: float) -> List[Dict[str, Any]]:
    from backend.ai_gateway.usage_store import get_store as usage_store

    days = max(1, int((time.time() - since) // 86400) + 1)
    return [row for row in usage_store().calls(days=days, user_id=user_id) if row["ts"] >= since]


def _is_ok(status: str) -> bool:
    return str(status or "").upper() in ("SUCCESS", "CLARIFICATION", "NEEDS_CLARIFICATION")


def kpis(traces: List[Dict[str, Any]], calls: List[Dict[str, Any]], answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    questions = [t for t in traces if t.get("kind", "question") == "question"]
    seconds = [float(t["seconds"]) for t in questions if t.get("seconds") is not None]
    ok = sum(1 for t in questions if _is_ok(t.get("status")))
    sent = [c for c in calls if c["status"] != "cache"]
    errors = sum(1 for c in sent if c["status"] == "error")
    cache_hits = sum(1 for c in calls if c["status"] == "cache")
    checked = [a for a in answers if a.get("faithfulness") is not None]
    hallucinated = sum(1 for a in checked if a.get("hallucinated"))
    rated = [a for a in answers if a.get("rating")]
    return {
        "questions": len(questions),
        "success_rate": round(ok / len(questions), 4) if questions else None,
        "p50_seconds": percentile(seconds, 0.5),
        "p95_seconds": percentile(seconds, 0.95),
        "ai_calls": len(sent),
        "ai_error_rate": round(errors / len(sent), 4) if sent else None,
        "cache_hit_rate": round(cache_hits / len(calls), 4) if calls else None,
        "tokens": int(sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0) for c in calls)),
        "cost_usd": round(sum(float(c.get("cost_usd") or 0) for c in calls), 6),
        "checked_answers": len(checked),
        "hallucination_rate": round(hallucinated / len(checked), 4) if checked else None,
        "mean_faithfulness": round(sum(a["faithfulness"] for a in checked) / len(checked), 4) if checked else None,
        "thumbs_up_rate": round(sum(1 for a in rated if a["rating"] > 0) / len(rated), 4) if rated else None,
        "rated_answers": len(rated),
    }


def series(traces, calls, answers, since: float, now: float) -> List[Dict[str, Any]]:
    size = _bucket_size(now - since)
    start = math.floor(since / size) * size
    buckets: Dict[float, Dict[str, Any]] = {}
    t = start
    while t <= now:
        buckets[t] = {"t": t, "questions": 0, "errors": 0, "seconds": [], "ai_calls": 0, "ai_errors": 0,
                      "tokens": 0, "cache": 0, "flagged": 0}
        t += size

    def slot(ts: float) -> Optional[Dict[str, Any]]:
        return buckets.get(math.floor(ts / size) * size)

    for item in traces:
        if item.get("kind", "question") != "question":
            continue
        b = slot(item["ts"])
        if b is None:
            continue
        b["questions"] += 1
        b["seconds"].append(float(item.get("seconds") or 0))
        if not _is_ok(item.get("status")):
            b["errors"] += 1
    for call in calls:
        b = slot(call["ts"])
        if b is None:
            continue
        if call["status"] == "cache":
            b["cache"] += 1
        else:
            b["ai_calls"] += 1
            b["ai_errors"] += call["status"] == "error"
        b["tokens"] += int((call.get("prompt_tokens") or 0) + (call.get("completion_tokens") or 0))
    for answer in answers:
        b = slot(answer["ts"])
        if b is not None and answer.get("risk") in ("medium", "high"):
            b["flagged"] += 1

    out = []
    for b in buckets.values():
        out.append({
            "t": b["t"], "questions": b["questions"], "errors": b["errors"],
            "p50_seconds": percentile(b["seconds"], 0.5), "p95_seconds": percentile(b["seconds"], 0.95),
            "ai_calls": b["ai_calls"], "ai_errors": b["ai_errors"], "tokens": b["tokens"],
            "cache_hits": b["cache"], "flagged": b["flagged"],
        })
    return out


def route_table(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-model numbers from the ledger, merged with live gateway health."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for call in calls:
        if call["status"] != "cache":
            grouped[call["route"]].append(call)
    live: Dict[str, Dict[str, Any]] = {}
    try:
        from backend.ai_gateway.gateway import get_gateway

        for row in get_gateway().status():
            live.setdefault(row["route"], row)
    except Exception:
        pass
    rows = []
    for route in sorted(set(grouped) | set(live)):
        items = grouped.get(route, [])
        ok = [c for c in items if c["status"] == "ok"]
        latency = [float(c.get("latency_ms") or 0) / 1000 for c in ok]
        errors: Dict[str, int] = defaultdict(int)
        for c in items:
            if c["status"] == "error":
                errors[c.get("error_kind") or "error"] += 1
        status = live.get(route, {})
        state = "idle"
        if status.get("blocked_reason"):
            state = "blocked"
        elif status and not status.get("key_configured", True):
            state = "no_key"
        elif items:
            rate = len(ok) / len(items)
            state = "healthy" if rate >= 0.9 else "degraded" if rate >= 0.6 else "failing"
        rows.append({
            "route": route,
            "provider": status.get("provider") or route.split("/", 1)[0],
            "tier": status.get("tier", ""),
            "calls": len(items),
            "success_rate": round(len(ok) / len(items), 4) if items else None,
            "p50_seconds": percentile(latency, 0.5),
            "p95_seconds": percentile(latency, 0.95),
            "tokens": int(sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0) for c in items)),
            "errors": dict(errors),
            "state": state,
            "blocked_reason": status.get("blocked_reason", ""),
            "requests_today": status.get("requests_today"),
            "rpd_limit": status.get("rpd_limit"),
            "key_configured": status.get("key_configured"),
        })
    rows.sort(key=lambda r: (-r["calls"], r["route"]))
    return rows


def stage_latency(traces_with_detail: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    times: Dict[str, List[float]] = defaultdict(list)
    for trace in traces_with_detail:
        for event in (trace.get("detail") or {}).get("events") or []:
            if event.get("kind") == "step" and event.get("name"):
                times[event["name"]].append(float(event.get("seconds") or 0))
    rows = [{"stage": name, "count": len(values), "mean_seconds": round(sum(values) / len(values), 3),
             "p95_seconds": percentile(values, 0.95)} for name, values in times.items()]
    rows.sort(key=lambda r: -r["mean_seconds"])
    return rows


def slo_status(k: Dict[str, Any], targets: Dict[str, float], traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []

    def add(key: str, label: str, value: Optional[float], target: float, higher_is_better: bool, unit: str):
        if value is None:
            met = None
        else:
            met = value >= target if higher_is_better else value <= target
        out.append({"key": key, "label": label, "value": value, "target": target, "met": met,
                    "higher_is_better": higher_is_better, "unit": unit})

    add("success_rate", "Questions answered", k.get("success_rate"), targets["success_rate"], True, "ratio")
    add("p95_seconds", "95% of answers faster than", k.get("p95_seconds"), targets["p95_seconds"], False, "seconds")
    add("ai_error_rate", "Model call errors", k.get("ai_error_rate"), targets["ai_error_rate"], False, "ratio")
    add("hallucination_rate", "Hallucination rate", k.get("hallucination_rate"),
        targets["hallucination_rate"], False, "ratio")

    # Error budget for the success SLO: how much of the allowed failure
    # share has been spent in this window.
    questions = [t for t in traces if t.get("kind", "question") == "question"]
    if questions:
        allowed = (1 - targets["success_rate"]) * len(questions)
        failed = sum(1 for t in questions if not _is_ok(t.get("status")))
        out[0]["budget_used"] = round(failed / allowed, 3) if allowed > 0 else (0.0 if failed == 0 else 9.99)
        out[0]["failures"] = failed
        out[0]["allowed_failures"] = round(allowed, 2)
    return out


def incidents(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Spikes in latency or errors: a bucket far above the window's usual
    level (median + 3 x MAD, and at least a real absolute jump).
    """
    found = []

    def scan(key: str, label: str, floor: float):
        values = [p[key] for p in points if p.get(key) is not None]
        if len(values) < 4:
            return
        mid = median(values)
        mad = median([abs(v - mid) for v in values]) or 0.0
        limit = mid + max(3 * 1.4826 * mad, floor)
        for p in points:
            v = p.get(key)
            if v is not None and v > limit:
                found.append({"t": p["t"], "metric": key, "label": label, "value": v,
                              "usual": round(mid, 3), "limit": round(limit, 3)})

    scan("p95_seconds", "Slow answers", 5.0)
    scan("errors", "Failed questions", 2.0)
    scan("ai_errors", "Model errors", 3.0)
    scan("flagged", "Flagged answers", 2.0)
    found.sort(key=lambda i: -i["t"])
    return found[:30]


def failures(traces: List[Dict[str, Any]], limit: int = 15) -> List[Dict[str, Any]]:
    rows = []
    for t in traces:
        if t.get("kind", "question") == "question" and not _is_ok(t.get("status")):
            rows.append({"id": t["id"], "ts": t["ts"], "question": t["question"], "status": t["status"],
                         "error": (t.get("error") or "")[:300], "seconds": t.get("seconds")})
        if len(rows) >= limit:
            break
    return rows


def overview(user_id: str, window: str = "24h", shared_models: bool = True,
             own_calls_only: bool = True) -> Dict[str, Any]:
    now = time.time()
    span = WINDOWS.get(window, WINDOWS["24h"])
    since = now - span
    store = get_store()
    traces = _traces(user_id, since)
    calls = _calls(user_id if own_calls_only else None, since)
    model_calls = _calls(None, since) if shared_models else calls
    answers = store.list_answers(user_id, since=since, limit=1000)
    k = kpis(traces, calls, answers)
    points = series(traces, calls, answers, since, now)
    targets = {**DEFAULT_SLO, **(store.get_setting("slo", {}) or {})}

    # Same KPIs over the previous window, for the arrows on the tiles.
    before = since - span
    prev = kpis(_traces_between(user_id, before, since), [c for c in _calls(user_id if own_calls_only else None, before)
                                                         if c["ts"] < since],
                [a for a in store.list_answers(user_id, since=before, limit=1000) if a["ts"] < since])

    return {
        "window": window,
        "generated_at": now,
        "kpis": k,
        "previous": prev,
        "series": points,
        "routes": route_table(model_calls),
        "stages": stage_latency(_traces(user_id, since, with_detail=True)[:300]),
        "slo": slo_status(k, targets, traces),
        "incidents": incidents(points),
        "failures": failures(traces),
    }


def _traces_between(user_id: str, start: float, end: float) -> List[Dict[str, Any]]:
    return [t for t in _traces(user_id, start) if t["ts"] < end]
