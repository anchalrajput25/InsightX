# history_view.py
#
# "🕘 History": every question asked, from every chat and database,
# with its answer, SQL and (for "why" questions) the real reason.
# Search it, open any entry, ask it again or delete it.

import html
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import altair as alt
import requests

from evaluation_view import SERIES, _tile
from investigation_view import FONT_HTML, LIGHT, Theme, _style, detect_dark, theme_for

PAGE_SIZE = 25
KIND_LABELS = {"": "All", "question": "Questions", "investigation": "Why investigations"}
OK_STATUSES = {"SUCCESS"}


def when(ts: Optional[float]) -> str:
    if not ts:
        return ""
    moment = datetime.fromtimestamp(float(ts))
    today = datetime.now().date()
    if moment.date() == today:
        return "Today " + moment.strftime("%H:%M")
    if moment.date() == today - timedelta(days=1):
        return "Yesterday " + moment.strftime("%H:%M")
    return moment.strftime("%d %b %Y, %H:%M")


def day_group(ts: Optional[float]) -> str:
    return when(ts).rsplit(" ", 1)[0].rstrip(",") if ts else ""


def tiles_html(data: Dict[str, Any], t: Theme = LIGHT) -> str:
    activity = data.get("activity") or []
    total = len(activity)
    why = sum(1 for item in activity if item.get("kind") == "investigation")
    week_ago = datetime.now().timestamp() - 7 * 86400
    week = sum(1 for item in activity if (item.get("ts") or 0) >= week_ago)
    days = {datetime.fromtimestamp(item["ts"]).date() for item in activity if item.get("ts")}
    tiles = [
        _tile("Questions asked", f"{total:,}", "since you started", t, "💬", t.accent),
        _tile("Why investigations", f"{why:,}", "root-cause answers", t, "🧠", t.increase),
        _tile("Last 7 days", f"{week:,}", "questions", t, "📅", t.good),
        _tile("Active days", f"{len(days):,}", "days with questions", t, "🔥", t.warning),
    ]
    return f'<div style="font-family:{FONT_HTML};display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 12px">{"".join(tiles)}</div>'


def activity_rows(activity: List[Dict[str, Any]], days: int = 30) -> List[Dict[str, Any]]:
    start = datetime.now().date() - timedelta(days=days - 1)
    counts: Dict[tuple, int] = {}
    for item in activity:
        if not item.get("ts"):
            continue
        day = datetime.fromtimestamp(item["ts"]).date()
        if day >= start:
            key = (day, KIND_LABELS.get(item.get("kind"), "Questions"))
            counts[key] = counts.get(key, 0) + 1
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        for kind in ("Questions", "Why investigations"):
            rows.append({"day": day.isoformat(), "kind": kind, "count": counts.get((day, kind), 0)})
    return rows


def activity_chart(activity: List[Dict[str, Any]], t: Theme = LIGHT):
    rows = activity_rows(activity)
    if not any(row["count"] for row in rows):
        return None
    colors = SERIES[t.dark]
    chart = alt.Chart(alt.Data(values=rows)).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("yearmonthdate(day):T", title=None, axis=alt.Axis(format="%d %b", labelAngle=0, tickCount=8)),
        y=alt.Y("count:Q", title="Questions", stack=True, axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=["Questions", "Why investigations"],
                                                               range=[colors[0], colors[1]]),
                        legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("yearmonthdate(day):T", title="Day", format="%d %b %Y"), alt.Tooltip("kind:N", title="Type"),
                 alt.Tooltip("count:Q", title="Questions")],
    )
    return _style(chart, t, 190, "Your activity", "Questions per day, last 30 days")


def status_badge(status: str, t: Theme = LIGHT) -> str:
    status = (status or "").upper()
    if status in OK_STATUSES:
        color, text = t.good, "Answered"
    elif status == "NEEDS_CLARIFICATION":
        color, text = t.warning, "Needed more detail"
    else:
        color, text = t.critical, "Failed"
    return (f'<span style="font-family:{FONT_HTML};font-size:11px;font-weight:700;color:#fff;background:{color};'
            f'border-radius:99px;padding:2px 9px">{text}</span>')


def investigation_html(detail: Dict[str, Any], t: Theme = LIGHT) -> str:
    parts = []
    if detail.get("metric") and detail.get("change_pct") is not None:
        arrow = "▼" if detail["change_pct"] < 0 else "▲"
        color = t.decrease if detail["change_pct"] < 0 else t.increase
        parts.append(
            f'<div style="font-size:14px;color:{t.ink}"><b>{html.escape(str(detail["metric"]))}</b> '
            f'{html.escape(str(detail.get("baseline_label") or ""))} → {html.escape(str(detail.get("current_label") or ""))}: '
            f'<span style="color:{color};font-weight:700">{arrow} {abs(detail["change_pct"]):.1f}%</span></div>'
        )
    for title, items, icon in (("Reasons found", detail.get("reasons"), "🧠"), ("How to improve", detail.get("actions"), "✅")):
        items = [str(item) for item in items or [] if item]
        if items:
            parts.append(f'<div style="font-size:12px;font-weight:700;color:{t.muted};text-transform:uppercase;'
                         f'letter-spacing:.05em;margin-top:8px">{icon} {title}</div>'
                         + "".join(f'<div style="font-size:13px;color:{t.ink_2}">• {html.escape(item)}</div>' for item in items))
    return f'<div style="font-family:{FONT_HTML};margin:4px 0 8px">{"".join(parts)}</div>' if parts else ""


def _call(method: str, backend_url: str, path: str, headers: Optional[Dict[str, str]] = None,
          params: Optional[Dict[str, Any]] = None):
    response = requests.request(method, f"{backend_url}{path}", params=params, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def render_history_page(st, backend_url: str, headers: Optional[Dict[str, str]] = None,
                        ask: Optional[Callable[[str, str], None]] = None, connected: bool = True) -> None:
    t = theme_for(detect_dark(st))

    head, button = st.columns([5, 1])
    with head:
        st.subheader("🕘 History")
        st.caption("Every question you asked, from every chat and database, with its answer. "
                   "Search it, open it, or ask it again.")
    with button:
        st.write("")
        if st.button("🔄 Refresh", key="history_refresh", use_container_width=True):
            st.rerun()

    search_col, kind_col = st.columns([3, 2])
    with search_col:
        search = st.text_input("Search", key="history_search", placeholder="e.g. orders, revenue, 2017",
                               label_visibility="collapsed")
    with kind_col:
        kind = st.radio("Show", list(KIND_LABELS), format_func=KIND_LABELS.get, horizontal=True,
                        key="history_kind", label_visibility="collapsed")
    filters = (search.strip(), kind)
    if st.session_state.get("history_filters") != filters:
        st.session_state["history_filters"] = filters
        st.session_state["history_limit"] = PAGE_SIZE
    limit = st.session_state.get("history_limit", PAGE_SIZE)

    try:
        data = _call("GET", backend_url, "/history", headers,
                     {"search": search.strip(), "kind": kind, "limit": limit})
    except requests.exceptions.ConnectionError:
        st.error(f"Backend is not running at {backend_url}. Start FastAPI first.")
        return
    except Exception as exc:
        st.error(f"Could not load the history: {exc}")
        return

    if not data.get("activity"):
        st.info("No questions yet. Ask one on the 💬 page and it will appear here.")
        return

    st.markdown(tiles_html(data, t), unsafe_allow_html=True)
    chart = activity_chart(data.get("activity") or [], t)
    if chart is not None:
        st.altair_chart(chart, use_container_width=True, theme=None)

    items = data.get("items") or []
    total = int(data.get("total") or 0)
    if not items:
        st.info("No questions match this search.")
        return
    st.caption(f"Showing {len(items)} of {total} question{'s' if total != 1 else ''}"
               + ("" if connected else " · connect a database on the 💬 page to ask one again"))

    group = None
    for item in items:
        day = day_group(item.get("ts"))
        if day != group:
            group = day
            st.markdown(f"##### {day}")
        icon = "🧠" if item.get("kind") == "investigation" else "💬"
        mark = "" if str(item.get("status") or "").upper() in OK_STATUSES else " ⚠"
        with st.expander(f"{icon} {item.get('question', '')}{mark}  ·  {when(item.get('ts')).split(' ')[-1]}"):
            meta = [status_badge(item.get("status", ""), t)]
            if item.get("database"):
                meta.append(f'<span style="font-size:12px;color:{t.ink_2}">🗄 {html.escape(item["database"])}</span>')
            meta.append(f'<span style="font-size:12px;color:{t.muted}">{html.escape(when(item.get("ts")))}</span>')
            st.markdown(f'<div style="font-family:{FONT_HTML};display:flex;gap:12px;align-items:center;'
                        f'flex-wrap:wrap;margin-bottom:6px">{"".join(meta)}</div>', unsafe_allow_html=True)
            if item.get("kind") == "investigation":
                st.markdown(investigation_html(item.get("detail") or {}, t), unsafe_allow_html=True)
            if item.get("answer"):
                st.markdown(item["answer"])
            if item.get("sql"):
                st.code(item["sql"], language="sql")
            again, remove = st.columns([3, 1])
            with again:
                if ask is not None and st.button("🔁 Ask again", key=f"history_again_{item['id']}",
                                                 use_container_width=True, disabled=not connected):
                    ask(item.get("question", ""), item.get("kind", "question"))
            with remove:
                if st.button("🗑 Delete", key=f"history_delete_{item['id']}", use_container_width=True):
                    try:
                        _call("DELETE", backend_url, f"/history/{item['id']}", headers)
                    except Exception as exc:
                        st.error(f"Could not delete: {exc}")
                    else:
                        st.rerun()

    if len(items) < total:
        if st.button(f"Show more ({total - len(items)} older)", key="history_more", use_container_width=True):
            st.session_state["history_limit"] = limit + PAGE_SIZE
            st.rerun()

    with st.expander("Clear history"):
        sure = st.checkbox("Yes, delete all my history", key="history_clear_sure")
        if st.button("Delete all", key="history_clear", disabled=not sure):
            try:
                _call("DELETE", backend_url, "/history", headers)
            except Exception as exc:
                st.error(f"Could not clear the history: {exc}")
            else:
                st.session_state.pop("history_clear_sure", None)
                st.rerun()
