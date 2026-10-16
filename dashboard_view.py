# dashboard_view.py
#
# "📈 Dashboard": a basic picture of the whole connected database
# (backend/dashboard.py builds it from the schema, no AI), and a box to
# ask for any chart in your own words. Asked-for charts are kept and
# re-run on the current data each time the page opens.
#
# Charts are drawn with the same chart code as the answers
# (chart_builder.py / chart_view.py); nothing there is changed.

import html
from typing import Any, Dict, List, Optional

import requests

from chart_builder import LABELS, METRIC, plan_chart, summarize, switch, to_frame
from chart_view import build_chart, render_metrics
from evaluation_view import _tile
from investigation_view import FONT_HTML, LIGHT, Theme, detect_dark, theme_for

EXAMPLES = "e.g. a graph of revenue by month · a pie chart of orders by payment type · customers by state"


def tiles_html(data: Dict[str, Any], t: Theme = LIGHT) -> str:
    tiles = [
        _tile("Tables", f"{int(data.get('tables') or 0):,}", "in this database", t, "🗂", t.accent),
        _tile("Rows", f"{int(data.get('rows') or 0):,}", "records in all tables", t, "🧾", t.increase),
        _tile("Columns", f"{int(data.get('columns') or 0):,}", "fields", t, "🔢", t.good),
        _tile("Links", f"{int(data.get('links') or 0):,}", "between tables", t, "🔗", t.warning),
    ]
    return f'<div style="font-family:{FONT_HTML};display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 12px">{"".join(tiles)}</div>'


def chart_plan(rows: List[Dict[str, Any]], columns: List[str], text: str = "", kind: str = ""):
    """(frame, plan) for some rows; `kind` is the chart type the person asked for, if any."""
    if not rows:
        return None, None
    frame = to_frame(rows, columns or None)
    plan = plan_chart(frame, text)
    if plan is not None and kind and kind in plan.options:
        plan = switch(plan, kind)
    return frame, plan


def draw(st, rows: List[Dict[str, Any]], columns: List[str], text: str = "", kind: str = "",
         key: str = "dash") -> bool:
    try:
        frame, plan = chart_plan(rows, columns, text, kind)
    except Exception:
        return False
    if plan is None:
        return False
    if plan.kind == METRIC:
        render_metrics(st, frame, plan)
        return True
    if len(plan.options) > 1:
        chosen = st.radio("Chart type", plan.options, index=plan.options.index(plan.kind),
                          format_func=lambda option: LABELS[option], horizontal=True,
                          key=f"{key}_type", label_visibility="collapsed")
        plan = switch(plan, chosen)
    try:
        st.altair_chart(build_chart(frame, plan), use_container_width=True)
    except Exception as exc:
        st.caption(f"Chart not available ({exc}).")
        return False
    summary = summarize(frame, plan)
    if summary:
        st.caption(summary)
    return True


def _call(method: str, backend_url: str, path: str, headers: Optional[Dict[str, str]] = None,
          params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None, timeout: int = 120):
    return requests.request(method, f"{backend_url}{path}", params=params, json=body, headers=headers,
                            timeout=timeout)


def _error(response) -> str:
    try:
        detail = str(response.json().get("detail") or response.text)
    except ValueError:
        detail = response.text
    if "connection session not found" in detail.lower():
        return "the backend restarted and forgot the connection. Connect the database again on the 💬 page."
    return detail


def render_dashboard_page(st, backend_url: str, connection_id: Optional[str],
                          headers: Optional[Dict[str, str]] = None) -> None:
    t = theme_for(detect_dark(st))

    head, button = st.columns([5, 1])
    with head:
        st.subheader("📈 Dashboard")
        st.caption("A quick picture of your whole database, and any chart you ask for.")
    with button:
        st.write("")
        refresh = st.button("🔄 Refresh", key="dash_refresh", use_container_width=True)

    if not connection_id:
        st.info("Connect a database on the 💬 page first, then come back here.")
        return

    # ---------------- make a chart ----------------
    st.markdown("#### ✨ Make a chart")
    with st.form("dash_make_chart", clear_on_submit=True):
        wanted = st.text_input("What should the chart show?", placeholder=EXAMPLES)
        made = st.form_submit_button("Make chart", type="primary", use_container_width=True)
    if made and wanted.strip():
        try:
            with st.spinner("Getting the data for your chart..."):
                response = _call("POST", backend_url, "/dashboard/charts", headers,
                                 body={"connection_id": connection_id, "request": wanted.strip()}, timeout=600)
        except requests.exceptions.RequestException as exc:
            st.error(f"Could not reach the backend: {exc}")
        else:
            if response.ok:
                st.session_state["dash_new_chart"] = response.json().get("id")
                st.rerun()
            else:
                st.warning(f"Could not make this chart: {_error(response)}")

    try:
        mine = _call("GET", backend_url, "/dashboard/charts", headers, params={"connection_id": connection_id})
    except requests.exceptions.ConnectionError:
        st.error(f"Backend is not running at {backend_url}. Start FastAPI first.")
        return
    charts = mine.json().get("charts") or [] if mine.ok else []
    if not mine.ok:
        st.warning(f"Could not load your charts: {_error(mine)}")
    if charts:
        st.markdown("#### 📌 Your charts")
        st.caption("Charts you asked for. They show the current data each time this page opens.")
        columns = st.columns(2)
        for index, chart in enumerate(charts):
            with columns[index % 2], st.container(border=True):
                new = " 🆕" if chart.get("id") == st.session_state.get("dash_new_chart") else ""
                st.markdown(f"**{html.escape(str(chart.get('request', '')))}**{new}")
                if chart.get("error"):
                    st.warning(chart["error"])
                elif not draw(st, chart.get("rows") or [], chart.get("columns") or [], chart.get("request", ""),
                              chart.get("kind", ""), key=f"dash_mine_{chart['id']}"):
                    st.caption("This result has no numbers to chart.")
                left, right = st.columns([3, 1])
                with left, st.expander("SQL"):
                    st.code(chart.get("sql", ""), language="sql")
                with right:
                    if st.button("🗑 Remove", key=f"dash_remove_{chart['id']}", use_container_width=True):
                        _call("DELETE", backend_url, f"/dashboard/charts/{chart['id']}", headers)
                        st.rerun()

    # ---------------- whole database ----------------
    st.markdown("#### 🗄 Your database at a glance")
    try:
        with st.spinner("Looking at your database..."):
            response = _call("GET", backend_url, "/dashboard/overview", headers,
                             params={"connection_id": connection_id, "refresh": bool(refresh)}, timeout=600)
    except requests.exceptions.RequestException as exc:
        st.error(f"Could not load the overview: {exc}")
        return
    if not response.ok:
        st.warning(f"Could not load the overview: {_error(response)}")
        return
    data = response.json()
    if data.get("database"):
        st.caption(f"🗄 {data['database']}")
    st.markdown(tiles_html(data, t), unsafe_allow_html=True)

    overview = data.get("charts") or []
    sizes = data.get("table_sizes") or []
    if sizes:
        overview = [{"key": "table-sizes", "title": "Biggest tables", "subtitle": "Rows per table",
                     "kind": "bar", "columns": ["table", "rows"], "rows": sizes}] + overview
    if not overview:
        st.info("No charts could be made from this database's tables.")
        return
    columns = st.columns(2)
    for index, chart in enumerate(overview):
        with columns[index % 2], st.container(border=True):
            st.markdown(f"**{html.escape(str(chart.get('title', '')))}**")
            if chart.get("subtitle"):
                st.caption(chart["subtitle"])
            draw(st, chart.get("rows") or [], chart.get("columns") or [], chart.get("title", ""),
                 chart.get("kind", ""), key=f"dash_auto_{index}")
            if chart.get("sql"):
                with st.expander("SQL"):
                    st.code(chart["sql"], language="sql")
