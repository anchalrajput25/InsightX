# trace_view.py
#
# "🔍 Traces" page: what happened inside each question, step by step.
# Shows where the time went (a timeline), which AI models were called,
# which database queries ran, and which step failed. Reads the backend's
# /traces endpoints; never calls an AI model itself.
#
# Builders return HTML, rows or Altair charts and do not depend on
# Streamlit, so they can be tested on their own.

import html
from datetime import datetime
from typing import Any, Dict, List, Optional

import altair as alt
import requests

from evaluation_view import SERIES, _tile
from investigation_view import FONT_HTML, LIGHT, Theme, _style, detect_dark, theme_for

STEP_NAMES = {
    "load_schema": "Load database schema",
    "prepare": "Get ready",
    "cache_check": "Check saved answers",
    "cache_check_rewritten": "Check saved answers (follow-up)",
    "rewrite": "Understand the question",
    "decompose": "Split into parts",
    "retrieve": "Find relevant tables",
    "schema_link": "Pick tables and columns",
    "generate_sql": "Write SQL",
    "select_sql": "Compare SQL attempts",
    "clarification": "Ask for clarification",
    "safety": "Safety check",
    "validate_sql": "Check SQL against the database",
    "correction": "Fix the SQL",
    "execute": "Run SQL",
    "validate_result": "Check the result",
    "generate_answer": "Write the answer",
    "chart": "Prepare the chart",
    "follow_ups": "Suggest next questions",
    "investigation_plan": "Plan the investigation",
    "investigation_analysis": "Run the analysis",
    "investigation_narrative": "Explain the findings",
}
AI_JOB_NAMES = {
    "rewrite": "Rewrite question", "decompose": "Split question", "schema_link": "Pick tables",
    "context": "Follow-up context", "answer": "Write answer", "sql_generate": "Write SQL",
    "sql_candidate": "Extra SQL attempt", "sql_repair": "Fix failed SQL",
    "investigation_plan": "Plan investigation", "investigation_narrative": "Explain investigation",
    "follow_ups": "Suggest next questions",
}
KIND_NAMES = {"step": "Step", "llm": "AI call", "db": "Database query"}


def step_name(name: str) -> str:
    return STEP_NAMES.get(name, name.replace("_", " ").capitalize())


def secs(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    value = float(value)
    if value < 1:
        return f"{value * 1000:.0f} ms"
    return f"{value:.1f} s"


def when(ts: Optional[float]) -> str:
    return datetime.fromtimestamp(float(ts)).strftime("%d %b, %H:%M:%S") if ts else ""


def status_mark(status: str, cache: str = "") -> str:
    status = (status or "").upper()
    if cache == "answer":
        return "⚡ Saved answer"
    if status == "SUCCESS":
        return "✅ Answered" + (" (saved SQL)" if cache == "sql" else "")
    if status == "NEEDS_CLARIFICATION":
        return "❓ Asked to clarify"
    return "❌ Failed"


# ------------------------------------------------------------
# Builders
# ------------------------------------------------------------

def list_rows(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "When": when(trace.get("ts")),
            "Question": trace.get("question", ""),
            "Result": status_mark(trace.get("status", ""), trace.get("cache", "")),
            "Time": secs(trace.get("seconds")),
            "AI calls": int(trace.get("llm_calls") or 0),
            "Tokens": int(trace.get("tokens") or 0),
            "DB queries": int(trace.get("db_queries") or 0),
        }
        for trace in traces
    ]


def timeline_rows(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One bar per event: steps on their own rows, AI calls and queries under them."""
    rows = []
    for index, event in enumerate(events):
        kind = event.get("kind")
        if kind == "step":
            label = step_name(event["name"])
        elif kind == "llm":
            label = "AI: " + AI_JOB_NAMES.get(event["name"], event["name"])
        else:
            label = "Database query"
        start = float(event.get("start") or 0.0)
        seconds = float(event.get("seconds") or 0.0)
        status = event.get("status", "ok")
        rows.append({
            "order": index,
            "label": label,
            "kind": KIND_NAMES.get(kind, kind),
            "start": round(start, 4),
            # Instant events (a saved AI reply) still get a visible sliver.
            "end": round(start + max(seconds, 0.004), 4),
            "seconds": seconds,
            "time": secs(seconds),
            "status": {"ok": "OK", "error": "Failed", "cache": "From saved answers"}.get(status, status),
            "detail": (event.get("model") or event.get("sql") or event.get("error") or "")[:160],
        })
    return rows


def timeline_chart(events: List[Dict[str, Any]], t: Theme = LIGHT):
    rows = timeline_rows(events)
    if not rows:
        return None
    colors = SERIES[t.dark]
    order = list(dict.fromkeys(row["label"] for row in rows))
    color = alt.condition(
        alt.datum.status == "Failed",
        alt.value(t.critical),
        alt.Color("kind:N", title=None,
                  scale=alt.Scale(domain=["Step", "AI call", "Database query"], range=[colors[0], colors[1], colors[2]])),
    )
    chart = alt.Chart(alt.Data(values=rows)).mark_bar(cornerRadius=3, height=14).encode(
        y=alt.Y("label:N", sort=order, title=None, axis=alt.Axis(labelLimit=240)),
        x=alt.X("start:Q", title="Seconds since the question was asked"),
        x2="end:Q",
        color=color,
        tooltip=[alt.Tooltip("label:N", title="What"), alt.Tooltip("time:N", title="Took"),
                 alt.Tooltip("start:Q", title="Started at (s)", format=".2f"),
                 alt.Tooltip("status:N", title="Status"), alt.Tooltip("detail:N", title="Detail")],
    )
    height = max(140, 26 * len(order) + 40)
    return _style(chart, t, height, "Where the time went",
                  "Each bar is one step, AI call or database query. Red means it failed.")


def headline(trace: Dict[str, Any]) -> str:
    """One plain-English sentence on the slowest part."""
    events = (trace.get("detail") or {}).get("events") or []
    steps = [event for event in events if event.get("kind") == "step"]
    total = float(trace.get("seconds") or 0.0)
    failed = [event for event in events if event.get("status") == "error" and event.get("kind") == "step"]
    parts = []
    if failed:
        parts.append(f"It stopped at “{step_name(failed[0]['name'])}”: {failed[0].get('error') or 'error'}.")
    if steps and total > 0:
        slowest = max(steps, key=lambda event: event.get("seconds") or 0)
        share = 100 * float(slowest.get("seconds") or 0) / total
        parts.append(f"The slowest step was “{step_name(slowest['name'])}” ({secs(slowest.get('seconds'))}, "
                     f"{share:.0f}% of the total {secs(total)}).")
    llm = [event for event in events if event.get("kind") == "llm"]
    if llm:
        llm_time = sum(float(event.get("seconds") or 0) for event in llm)
        errors = sum(1 for event in llm if event.get("status") == "error")
        text = f"AI calls took {secs(llm_time)} in total"
        if errors:
            text += f"; {errors} failed and another model was tried"
        parts.append(text + ".")
    elif trace.get("cache") == "answer":
        parts.append("No AI was used: the saved answer was still correct for the current data.")
    return " ".join(parts)


def tiles_html(trace: Dict[str, Any], t: Theme = LIGHT) -> str:
    tiles = [
        _tile("Total time", secs(trace.get("seconds")), when(trace.get("ts")), t),
        _tile("AI calls", str(int(trace.get("llm_calls") or 0)), f"{int(trace.get('tokens') or 0):,} tokens", t),
        _tile("Database queries", str(int(trace.get("db_queries") or 0)), "read-only", t),
        _tile("Result", status_mark(trace.get("status", ""), trace.get("cache", "")),
              f"trust {((trace.get('detail') or {}).get('trust') or {}).get('level') or 'n/a'}", t),
    ]
    return f'<div style="display:flex;gap:10px;flex-wrap:wrap;font-family:{FONT_HTML}">' + "".join(tiles) + "</div>"


def step_rows(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"Step": step_name(event["name"]), "Started at": secs(event.get("start")), "Took": secs(event.get("seconds")),
         "Status": {"ok": "✅", "error": "❌ " + str(event.get("error") or ""), "cache": "⚡ saved answer"}.get(
             event.get("status", "ok"), event.get("status", ""))}
        for event in events if event.get("kind") == "step"
    ]


def ai_rows(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"Job": AI_JOB_NAMES.get(event["name"], event["name"]), "Model": event.get("model", ""),
         "Took": secs(event.get("seconds")),
         "Tokens in / out": f"{event.get('prompt_tokens', 0)} / {event.get('completion_tokens', 0)}",
         "Status": {"ok": "✅", "cache": "⚡ saved reply", "error": "❌ " + str(event.get("error") or "")}.get(
             event.get("status", "ok"), event.get("status", ""))}
        for event in events if event.get("kind") == "llm"
    ]


def db_rows(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"Took": secs(event.get("seconds")), "Rows": event.get("rows", 0),
         "Status": "✅" if event.get("status") == "ok" else "❌ " + str(event.get("error") or ""),
         "SQL": event.get("sql", "")}
        for event in events if event.get("kind") == "db"
    ]


# ------------------------------------------------------------
# Page
# ------------------------------------------------------------

def _get(backend_url: str, path: str, params: Optional[Dict[str, Any]] = None,
         headers: Optional[Dict[str, str]] = None):
    response = requests.get(f"{backend_url}{path}", params=params, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def render_trace_page(st, backend_url: str, headers: Optional[Dict[str, str]] = None) -> None:
    t = theme_for(detect_dark(st))

    head, button = st.columns([5, 1])
    with head:
        st.subheader("🔍 Traces")
        st.caption("Every question you asked, step by step: where the time went, which AI models answered, "
                   "which database queries ran, and where anything failed.")
    with button:
        st.write("")
        if st.button("🔄 Refresh", key="trace_refresh", use_container_width=True):
            st.rerun()

    try:
        traces = _get(backend_url, "/traces", {"limit": 100}, headers).get("traces") or []
    except requests.exceptions.ConnectionError:
        st.error(f"Backend is not running at {backend_url}. Start FastAPI first.")
        return
    except Exception as exc:
        st.error(f"Could not load traces: {exc}")
        return

    if not traces:
        st.info("No traces yet. Ask a question on the 💬 page and it will appear here.")
        return

    st.dataframe(list_rows(traces), use_container_width=True, hide_index=True, height=min(400, 38 + 35 * len(traces)))

    ids = [trace["id"] for trace in traces]
    labels = {trace["id"]: f"{when(trace.get('ts'))} · {trace.get('question', '')[:90]}" for trace in traces}
    wanted = st.session_state.pop("open_trace_id", None)
    index = ids.index(wanted) if wanted in ids else 0
    trace_id = st.selectbox("Open a trace", ids, index=index, format_func=lambda key: labels[key], key="trace_pick")

    try:
        trace = _get(backend_url, f"/traces/{trace_id}", headers=headers)
    except Exception as exc:
        st.error(f"Could not load this trace: {exc}")
        return

    detail = trace.get("detail") or {}
    events = detail.get("events") or []

    st.markdown(f"#### {html.escape(trace.get('question', ''))}")
    st.markdown(tiles_html(trace, t), unsafe_allow_html=True)
    sentence = headline(trace)
    if sentence:
        st.markdown(f"**What happened:** {sentence}")
    if trace.get("error"):
        st.error(trace["error"])

    chart = timeline_chart(events, t)
    if chart is not None:
        st.altair_chart(chart, use_container_width=True, theme=None)

    steps_tab, ai_tab, db_tab, sql_tab = st.tabs(["Steps", "AI calls", "Database queries", "Final SQL"])
    with steps_tab:
        rows = step_rows(events)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption("No steps recorded.")
    with ai_tab:
        rows = ai_rows(events)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption("No AI calls.")
    with db_tab:
        rows = db_rows(events)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption("No database queries.")
    with sql_tab:
        if detail.get("rewritten_query") and detail["rewritten_query"] != trace.get("question"):
            st.caption(f"Understood as: {detail['rewritten_query']}")
        if detail.get("sql"):
            st.code(detail["sql"], language="sql")
        else:
            st.caption("No SQL for this question.")
