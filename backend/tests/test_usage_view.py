# AI usage page: ledger summary and page builders (no backend, no AI).

import time

from backend.ai_gateway.report import usage_summary
from backend.ai_gateway.usage_store import get_store


def seed():
    store = get_store()
    now = time.time()
    rows = [
        ("asha", "r1", "rewrite", "light", "gemini/flash", "gemini", "error", "rate_limit"),
        ("asha", "r1", "rewrite", "light", "groq/oss", "groq", "ok", ""),
        ("asha", "r1", "sql_generate", "sql", "groq/oss", "groq", "ok", ""),
        ("ravi", "r2", "rewrite", "light", "cache", "cache", "cache", ""),
        ("ravi", "r2", "sql_generate", "sql", "groq/oss", "groq", "ok", ""),
    ]
    for user, rid, job, tier, route, provider, status, kind in rows:
        store.record(ts=now, user_id=user, request_id=rid, job=job, tier=tier, route=route, provider=provider,
                     status=status, error_kind=kind, prompt_tokens=100 if status == "ok" else 0,
                     completion_tokens=10 if status == "ok" else 0, latency_ms=1000)


def test_usage_summary_counts_questions_requests_cache_and_failures():
    seed()
    usage = usage_summary(days=7)
    totals = usage["totals"]
    assert totals["questions"] == 2 and totals["requests"] == 4 and totals["failed"] == 1
    assert totals["cache_hits"] == 1 and totals["cache_hit_rate"] == 0.25
    assert totals["calls_per_question"] == 2.0 and totals["cost_usd"] == 0.0
    assert {r["route"] for r in usage["by_route"]} == {"gemini/flash", "groq/oss"}
    assert {u["user_id"]: u["questions"] for u in usage["by_user"]} == {"asha": 1, "ravi": 1}
    assert usage["failures"] == [{"kind": "rate_limit", "count": 1}]
    assert usage_summary(days=7, user_id="ravi")["totals"]["questions"] == 1


def test_page_builders_render_in_both_themes():
    import usage_view as view
    from investigation_view import DARK, LIGHT

    seed()
    usage = usage_summary(days=7)
    routes = [
        {"tier": "light", "position": 1, "route": "gemini/flash", "provider": "gemini", "model": "flash", "free": True,
         "rpm_limit": 10, "rpd_limit": 100, "key_configured": True, "blocked_reason": "", "requests_today": 40,
         "cooldown_seconds": 0},
        {"tier": "sql", "position": 1, "route": "groq/oss", "provider": "groq", "model": "oss", "free": True,
         "rpm_limit": 30, "rpd_limit": 100, "key_configured": True, "blocked_reason": "daily free limit reached",
         "requests_today": 100, "cooldown_seconds": 0},
        {"tier": "heavy", "position": 1, "route": "groq/oss", "provider": "groq", "model": "oss", "free": True,
         "rpm_limit": 30, "rpd_limit": 100, "key_configured": True, "blocked_reason": "daily free limit reached",
         "requests_today": 100, "cooldown_seconds": 0},
        {"tier": "heavy", "position": 2, "route": "openrouter/x:free", "provider": "openrouter", "model": "x:free",
         "free": True, "rpm_limit": None, "rpd_limit": None, "key_configured": False, "blocked_reason": "",
         "requests_today": 0, "cooldown_seconds": 0},
    ]
    assert round(view.free_quota_left(routes), 6) == 0.3  # 60 of 200 left (no-key model ignored)
    assert view.route_state(routes[1]) == ("Used up today", "bad")
    assert view.route_state(routes[3]) == ("No API key", "off")
    for theme in (LIGHT, DARK):
        table = view.models_table_html(routes, theme)
        assert table.count("<tr>") == 3  # one row per model
        assert "SQL writing · 1st choice<br>Heavy jobs · 1st choice" in table
        assert "2 questions answered with 4 AI requests" in view.summary_html(usage, "Last 7 days", theme)
        assert "Free capacity left today" in view.scorecards_html(usage, routes, theme)
        assert view.daily_chart(usage, theme).to_dict() and view.jobs_chart(usage, theme).to_dict()
    assert view.failure_rows(usage)[0]["Problem"] == "Too many requests at once"
    empty = usage_summary(days=1, user_id="nobody")
    assert view.daily_chart(empty) is None and "No questions asked today" in view.summary_text(empty, "Today")
