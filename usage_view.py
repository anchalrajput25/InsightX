# usage_view.py
#
# "📊 AI usage" page: how much AI the app uses, on which free models,
# for whom, and whether anything is running out. Reads the backend's
# /ai/usage and /ai/routes; never calls an AI model itself.
#
# Layout follows the Benchmark page: one plain-English summary, four
# score cards, the live status of every free model, then tabs with the
# details. Builders return HTML or Altair charts and do not depend on
# Streamlit, so they can be tested on their own.

import html
from typing import Any, Dict, List, Optional

import altair as alt
import requests

from evaluation_view import SERIES, _grade
from investigation_view import FONT_HTML, LIGHT, Theme, _style, detect_dark, theme_for

TIER_NAMES = {"light": "Light jobs", "sql": "SQL writing", "heavy": "Heavy jobs"}
TIER_HELP = {
    "light": "rewriting the question, splitting it up, picking tables, writing the answer",
    "sql": "writing the SQL query",
    "heavy": "fixing SQL that failed, planning and explaining investigations",
}
PROVIDER_NAMES = {"gemini": "Google Gemini", "groq": "Groq", "openrouter": "OpenRouter", "vllm": "Self-hosted",
                  "cache": "Saved answers"}
JOB_NAMES = {
    "rewrite": "Rewrite question", "decompose": "Split question", "schema_link": "Pick tables",
    "context": "Follow-up context", "answer": "Write answer", "sql_generate": "Write SQL",
    "sql_candidate": "Extra SQL attempts", "sql_repair": "Fix failed SQL",
    "investigation_plan": "Plan investigation", "investigation_narrative": "Explain investigation",
    "benchmark_generate": "Benchmark questions", "small": "Light job", "sql": "SQL job",
}
FAILURE_NAMES = {
    "rate_limit": ("Too many requests at once", "The free per-minute limit was hit; the next model answered instead."),
    "quota": ("Daily free limit used up", "That model is skipped until its daily limit resets."),
    "unavailable": ("Provider busy or down", "Timeout, overload or network error; the next model answered instead."),
    "not_found": ("Model retired", "The provider no longer offers this model; it is skipped."),
    "auth": ("API key problem", "The key is missing or wrong. Check .env."),
    "context_too_long": ("Question too large for model", "A model with a bigger context was used instead."),
    "bad_request": ("Rejected request", "The provider rejected the request itself."),
}
PERIODS = {"Today": 1, "Last 7 days": 7, "Last 30 days": 30}


def provider_colors(t: Theme) -> Dict[str, str]:
    palette = SERIES[t.dark]
    return {"gemini": palette[0], "groq": palette[1], "openrouter": palette[2], "vllm": palette[3],
            "cache": t.muted}


def _num(value: Optional[float]) -> str:
    if value is None:
        return "–"
    if value >= 1e6:
        return f"{value / 1e6:.1f}M"
    if value >= 1e4:
        return f"{value / 1e3:.0f}K"
    return f"{value:,.0f}"


def _pct(value: Optional[float]) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


# ============================================================
# QUOTA (from /ai/routes)
# ============================================================

def unique_routes(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each model once (a model can serve several tiers), first-listed tier kept."""
    seen, result = set(), []
    for row in routes:
        if row["route"] not in seen:
            seen.add(row["route"])
            result.append(row)
    return result


def free_quota_left(routes: List[Dict[str, Any]]) -> Optional[float]:
    """Share of today's known free daily requests still unused, over models that have a key."""
    usable = [r for r in unique_routes(routes) if r["key_configured"] and r.get("rpd_limit")]
    total = sum(r["rpd_limit"] for r in usable)
    if not total:
        return None
    used = sum(min(r["requests_today"], r["rpd_limit"]) for r in usable)
    return max(0.0, 1 - used / total)


def route_state(row: Dict[str, Any]):
    """(label, kind) where kind is ok | warn | bad | off."""
    if not row["key_configured"]:
        return "No API key", "off"
    reason = row.get("blocked_reason") or ""
    if reason.startswith("not found"):
        return "Retired", "bad"
    if reason.startswith("auth"):
        return "Key rejected", "bad"
    if "daily" in reason or reason.startswith("quota"):
        return "Used up today", "bad"
    if reason:
        wait = row.get("cooldown_seconds") or 0
        return (f"Paused {wait}s" if wait else "Busy this minute"), "warn"
    return "Ready", "ok"


# ============================================================
# HTML
# ============================================================

def summary_text(usage: Dict[str, Any], period: str) -> str:
    totals = usage["totals"]
    if not totals["questions"]:
        return f"No questions asked {('today' if period == 'Today' else 'in this period')} yet."
    parts = [f"{totals['questions']} questions answered with {totals['requests']} AI requests"
             f" ({totals['calls_per_question']} per question)."]
    cost = totals["cost_usd"]
    parts.append("All on free models, $0.00 spent." if not cost else f"${cost:.4f} spent on paid models.")
    if totals["cache_hits"]:
        parts.append(f"{totals['cache_hits']} AI steps were answered from saved results for free.")
    if totals["failed"]:
        parts.append(f"{totals['failed']} provider errors were handled by switching to another model.")
    return " ".join(parts)


def summary_html(usage: Dict[str, Any], period: str, t: Theme = LIGHT) -> str:
    return (f'<div style="font-family:{FONT_HTML};background:{t.card};border:1px solid {t.border};'
            f'border-left:5px solid {SERIES[t.dark][0]};border-radius:10px;padding:14px 18px;margin:4px 0 10px 0">'
            f'<div style="font-size:12px;font-weight:600;letter-spacing:.04em;color:{t.ink_2}">SUMMARY</div>'
            f'<div style="font-size:16px;color:{t.ink};margin-top:4px;line-height:1.5">'
            f'{html.escape(summary_text(usage, period))}</div></div>')


def _card(title: str, value: str, detail: str, meaning: str, t: Theme, rate: Optional[float] = None,
          graded: bool = True) -> str:
    """Score card like the Benchmark page; informational cards get no grade chip."""
    if graded:
        grade, colour, icon = _grade(rate, t)
        chip = (f'<div style="display:inline-block;margin-top:10px;padding:2px 10px;border-radius:999px;'
                f'font-size:12px;font-weight:600;color:{colour};border:1px solid {colour}">{icon} {html.escape(grade)}</div>')
    else:
        colour, chip = SERIES[t.dark][0], ""
    return (
        f'<div style="flex:1;min-width:210px;background:{t.card};border:1px solid {t.border};border-radius:12px;'
        f'padding:16px 18px;border-top:4px solid {colour}">'
        f'<div style="font-size:13px;font-weight:600;color:{t.ink_2}">{html.escape(title)}</div>'
        f'<div style="font-size:34px;font-weight:650;color:{t.ink};margin-top:6px;line-height:1.1">{html.escape(value)}</div>'
        f'<div style="font-size:13px;color:{t.ink_2};margin-top:2px">{html.escape(detail)}</div>{chip}'
        f'<div style="font-size:12px;color:{t.muted};margin-top:10px;line-height:1.4">{html.escape(meaning)}</div></div>'
    )


def scorecards_html(usage: Dict[str, Any], routes: List[Dict[str, Any]], t: Theme = LIGHT) -> str:
    totals = usage["totals"]
    requests_n, failed = totals["requests"], totals["failed"]
    reliability = None if not requests_n else totals["successful"] / requests_n
    quota = free_quota_left(routes)
    hit_rate = totals.get("cache_hit_rate")
    ready = sum(1 for r in unique_routes(routes) if route_state(r)[1] == "ok")
    total_models = len(unique_routes(routes))
    cards = [
        _card("Free capacity left today", _pct(quota), f"{ready} of {total_models} models ready now",
              "How much of today's free daily requests are still unused, across models with an API key.",
              t, quota, graded=quota is not None),
        _card("Questions answered", _num(totals["questions"]),
              f"{_num(requests_n)} AI requests · {totals['calls_per_question'] or '–'} per question",
              "Every question uses a few AI steps; fewer requests per question means more free capacity.",
              t, graded=False),
        _card("Provider reliability", _pct(reliability),
              f"{failed} errors, each retried on another model" if failed else "no provider errors",
              "Share of AI requests that worked first time. Errors never reach users: another free model answers.",
              t, reliability, graded=reliability is not None),
        _card("Saved answers", _pct(hit_rate), f"{totals['cache_hits']} AI steps skipped",
              "Repeated work answered from saved results: free, instant, and no free quota used.", t, graded=False),
    ]
    return (f'<div style="font-family:{FONT_HTML};display:flex;flex-wrap:wrap;gap:12px;margin:6px 0 4px 0">'
            f'{"".join(cards)}</div>')


ORDINALS = {1: "1st choice", 2: "2nd choice", 3: "3rd choice"}


def models_table_html(routes: List[Dict[str, Any]], t: Theme = LIGHT) -> str:
    """One row per model: what it is used for (and in which order), status, today's use of its free limit."""
    colours = {"ok": t.good, "warn": t.warning, "bad": t.critical, "off": t.muted}
    roles: Dict[str, List[str]] = {}
    for row in routes:
        roles.setdefault(row["route"], []).append(
            f'{TIER_NAMES.get(row["tier"], row["tier"])} · {ORDINALS.get(row["position"], "backup")}')
    body = []
    for row in unique_routes(routes):
        label, kind = route_state(row)
        colour = colours[kind]
        limit = row.get("rpd_limit")
        used = row["requests_today"]
        share = min(1.0, used / limit) if limit else 0
        bar_colour = t.good if share < 0.7 else t.warning if share < 0.95 else t.critical
        usage_cell = (
            f'<div style="background:{t.grid};border-radius:4px;height:8px;overflow:hidden">'
            f'<div style="width:{share * 100:.0f}%;height:8px;background:{bar_colour}"></div></div>'
            f'<div style="font-size:11px;color:{t.ink_2};margin-top:3px">{used} of {limit} today</div>'
            if limit else f'<div style="font-size:12px;color:{t.ink_2}">{used} today · limit set by provider</div>'
        )
        used_for = "<br>".join(html.escape(role) for role in roles[row["route"]])
        body.append(
            f'<tr>'
            f'<td style="padding:7px 10px;color:{t.ink};border-bottom:1px solid {t.border}">'
            f'<b>{html.escape(row["model"])}</b><div style="font-size:11px;color:{t.ink_2}">'
            f'{html.escape(PROVIDER_NAMES.get(row["provider"], row["provider"]))}'
            f'{"" if row.get("free", True) else " · paid"}</div></td>'
            f'<td style="padding:7px 10px;color:{t.ink_2};font-size:12px;border-bottom:1px solid {t.border}">{used_for}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid {t.border};white-space:nowrap">'
            f'<span style="display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600;'
            f'color:{colour};border:1px solid {colour}">{html.escape(label)}</span></td>'
            f'<td style="padding:7px 10px;width:28%;border-bottom:1px solid {t.border}">{usage_cell}</td></tr>')
    head = "".join(
        f'<th style="text-align:left;padding:8px 10px;color:{t.ink_2};font-weight:600">{name}</th>'
        for name in ("Model", "Used for", "Status", "Free requests today"))
    return (f'<div style="font-family:{FONT_HTML};border:1px solid {t.border};border-radius:10px;overflow:hidden">'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<thead><tr style="background:{t.card}">{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


# ============================================================
# CHARTS
# ============================================================

def daily_chart(usage: Dict[str, Any], t: Theme = LIGHT):
    rows = []
    for day in usage.get("by_day") or []:
        for provider, count in day.items():
            if provider != "day" and count:
                rows.append({"day": day["day"], "source": PROVIDER_NAMES.get(provider, provider),
                             "provider": provider, "requests": count})
    if not rows:
        return None
    colours = provider_colors(t)
    present = [p for p in colours if any(r["provider"] == p for r in rows)]
    days = sorted({r["day"] for r in rows})
    for r in rows:
        r["label"] = f"{int(r['day'][8:10])} {('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec').split()[int(r['day'][5:7]) - 1]}"
    labels = [next(r["label"] for r in rows if r["day"] == d) for d in days]
    chart = alt.Chart(alt.Data(values=rows)).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3,
                                                      width={"band": 0.7}).encode(
        x=alt.X("label:O", sort=labels, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("sum(requests):Q", title="AI steps"),
        color=alt.Color("source:N", title=None,
                        scale=alt.Scale(domain=[PROVIDER_NAMES[p] for p in present],
                                        range=[colours[p] for p in present])),
        tooltip=[alt.Tooltip("label:O", title="Day"), alt.Tooltip("source:N", title="Answered by"),
                 alt.Tooltip("sum(requests):Q", title="AI steps")],
    )
    return _style(chart, t, 260, "AI steps per day", "Coloured by who answered: each free provider, or saved results")


def jobs_chart(usage: Dict[str, Any], t: Theme = LIGHT):
    rows = [{"job": JOB_NAMES.get(item["job"], item["job"]), "requests": item["requests"],
             "saved": item["cache_hits"], "failed": item["failed"]}
            for item in usage.get("by_job") or [] if item["requests"] or item["cache_hits"]]
    if not rows:
        return None
    rows.sort(key=lambda r: -(r["requests"] + r["saved"]))
    long_rows = ([{"job": r["job"], "kind": "Sent to a model", "count": r["requests"]} for r in rows] +
                 [{"job": r["job"], "kind": "Saved answer", "count": r["saved"]} for r in rows])
    chart = alt.Chart(alt.Data(values=long_rows)).mark_bar(cornerRadiusEnd=3, height=14).encode(
        y=alt.Y("job:N", sort=[r["job"] for r in rows], title=None, axis=alt.Axis(labelLimit=200)),
        x=alt.X("sum(count):Q", title="AI steps", axis=alt.Axis(tickCount=6)),
        color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=["Sent to a model", "Saved answer"],
                                                               range=[SERIES[t.dark][0], t.muted])),
        tooltip=[alt.Tooltip("job:N", title="Step"), alt.Tooltip("kind:N", title=""),
                 alt.Tooltip("sum(count):Q", title="Count")],
    )
    return _style(chart, t, 30 * len(rows) + 60, "Where the AI requests go", "Each step of answering a question")


# ============================================================
# TABLE ROWS
# ============================================================

def model_rows(usage: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for item in usage.get("by_route") or []:
        provider, _, model = item["route"].partition("/")
        rows.append({
            "Model": model, "Provider": PROVIDER_NAMES.get(provider, provider),
            "Requests": item["requests"], "Worked": item["ok"], "Errors": item["failed"],
            "Typical time (s)": None if item["p50_latency_ms"] is None else round(item["p50_latency_ms"] / 1000, 1),
            "Tokens": item["tokens"], "Cost ($)": item["cost_usd"],
        })
    return rows


def user_rows(usage: Dict[str, Any], daily_limit: Optional[int]) -> List[Dict[str, Any]]:
    rows = []
    for item in usage.get("by_user") or []:
        rows.append({"User": item["user_id"], "Questions": item["questions"], "AI requests": item["requests"],
                     "Saved answers": item["cache_hits"], "Tokens": item["tokens"], "Cost ($)": item["cost_usd"]})
    return rows


def failure_rows(usage: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"Problem": FAILURE_NAMES.get(item["kind"], (item["kind"], ""))[0], "Times": item["count"],
             "What happened": FAILURE_NAMES.get(item["kind"], ("", "Handled by switching to another model."))[1]}
            for item in usage.get("failures") or []]


# ============================================================
# PAGE
# ============================================================

def _get(backend_url: str, path: str, params: Optional[Dict[str, Any]] = None,
         headers: Optional[Dict[str, str]] = None):
    response = requests.get(f"{backend_url}{path}", params=params, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def render_usage_page(st, backend_url: str, current_user: str = "local",
                      headers: Optional[Dict[str, str]] = None, signed_in: bool = False) -> None:
    """signed_in: the backend shows each person only their own usage."""
    t = theme_for(detect_dark(st))

    def chart(c):
        if c is not None:
            st.altair_chart(c, use_container_width=True, theme=None)

    head, button = st.columns([5, 1])
    with head:
        st.subheader("📊 AI usage")
        st.caption("Which free AI models InsightX uses, how much free capacity is left, and who is using it.")
    with button:
        st.write("")
        if st.button("🔄 Refresh", key="usage_refresh", use_container_width=True):
            st.rerun()

    left, right = st.columns([2, 3])
    with left:
        period = st.radio("Period", list(PERIODS), index=1, horizontal=True, key="usage_period")
    with right:
        if signed_in:
            scope = "me"
            st.caption(f"Your usage ({current_user}). The model status below is shared by everyone.")
        else:
            scope = st.radio("Show", ["Everyone", f"Only me ({current_user})"], horizontal=True, key="usage_scope")

    try:
        usage = _get(backend_url, "/ai/usage",
                     {"days": PERIODS[period], **({"user": current_user} if scope != "Everyone" else {})}, headers)
        status = _get(backend_url, "/ai/routes", headers=headers)
    except requests.exceptions.ConnectionError:
        st.error(f"Backend is not running at {backend_url}. Start FastAPI first.")
        return
    except Exception as exc:
        st.error(f"Could not load AI usage: {exc}")
        return

    routes = status.get("routes") or []
    settings = status.get("settings") or {}

    if not any(r["key_configured"] for r in routes):
        st.warning("No AI provider key is set. Add at least one free key to `.env`: "
                   "`GEMINI_API_KEY` (aistudio.google.com), `GROQ_API_KEY` (console.groq.com) or "
                   "`OPENROUTER_API_KEY` (openrouter.ai), then restart the backend.")

    st.markdown(summary_html(usage, period, t), unsafe_allow_html=True)
    st.markdown(scorecards_html(usage, routes, t), unsafe_allow_html=True)

    st.markdown("#### Free models")
    st.caption("Light jobs: " + TIER_HELP["light"] + ". SQL writing: " + TIER_HELP["sql"] + ". Heavy jobs: "
               + TIER_HELP["heavy"] + ". Each job tries its 1st choice, then the next. A model that is busy, "
               "used up or retired is skipped automatically, so users never see the error.")
    st.markdown(models_table_html(routes, t), unsafe_allow_html=True)
    notes = []
    if settings.get("user_daily_questions"):
        notes.append(f"Daily limit: {settings['user_daily_questions']} questions per person.")
    if settings.get("cache_enabled"):
        notes.append(f"Saved answers kept for {settings.get('cache_ttl_hours', 24):.0f} hours.")
    router = status.get("openrouter") or {}
    if router.get("configured") and not router.get("error"):
        notes.append("OpenRouter account: free tier." if router.get("is_free_tier")
                     else f"OpenRouter account: ${router.get('usage_usd') or 0:.2f} used"
                          + (f" of ${router['limit_usd']:.2f}." if router.get("limit_usd") else "."))
    if notes:
        st.caption(" ".join(notes))

    if signed_in:
        overview, models_tab, problems_tab = st.tabs(["Overview", "Models", "Problems"])
        users_tab = None
    else:
        overview, models_tab, users_tab, problems_tab = st.tabs(["Overview", "Models", "Users", "Problems"])
    with overview:
        if not usage["totals"]["questions"]:
            st.info("No AI usage in this period yet. Ask a question, then press Refresh.")
        else:
            chart(daily_chart(usage, t))
            chart(jobs_chart(usage, t))
    with models_tab:
        rows = model_rows(usage)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
            st.caption("Typical time is the median answer time of that model.")
        else:
            st.info("No requests sent to models in this period.")
    if users_tab is not None:
        with users_tab:
            rows = user_rows(usage, settings.get("user_daily_questions"))
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.info("No users in this period.")
    with problems_tab:
        rows = failure_rows(usage)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.success("No provider problems in this period.")
