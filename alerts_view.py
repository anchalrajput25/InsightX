# alerts_view.py
#
# "🚨 Alerts" page: InsightX watches numbers in your database and warns
# you when something unusual happens (backend/alerting/ does the work).
#
#   🚨 Alert feed   what was raised: acknowledge, resolve, snooze, and
#                   "Find out why" (opens an investigation on the Ask page)
#   📋 My alerts    each alert with its live chart, normal-range band and
#                   past alerts; check now, pause, snooze, edit, delete
#   ➕ New alert    pick or describe a number, choose when to be warned,
#                   preview it on the current data (with a back-test of how
#                   often it would have fired), then create it
#
# render_alert_badge() is the small "🚨 2 open alerts" line in the sidebar.

import html
import time
from typing import Any, Callable, Dict, List, Optional

import altair as alt
import pandas as pd
import requests

from evaluation_view import _tile
from investigation_view import FONT_HTML, LIGHT, Theme, detect_dark, theme_for

RULE_LABELS = {
    "smart": "🧠 Smart: warn me when anything looks unusual",
    "limit": "📏 Goes above or below a limit",
    "change": "⚡ Sudden jump or drop",
    "trend": "📉 Keeps falling or rising",
    "stale": "⏳ Data stops arriving",
}
RULE_HELP = {
    "smart": "Learns the normal level from the past (trend, weekday habits and usual ups and downs) and warns "
             "when the latest value is far outside it. No limits to set.",
    "limit": "Warns when the number crosses a fixed value you choose.",
    "change": "Warns when the number moves a lot compared with the period before, the same day last week, "
              "or the same time last year.",
    "trend": "Warns when the number keeps going the same way several periods in a row.",
    "stale": "Warns when no new rows have arrived for a while: a broken data feed or a stopped process.",
}
DIRECTIONS = {"both": "Rises and drops", "up": "Only rises", "down": "Only drops"}
COMPARE = {"previous": "the period before", "week": "the same day last week", "year": "the same time last year"}
SEVERITY = {"auto": "Auto (by how far off it is)", "warning": "Always warning", "critical": "Always critical"}
FREQUENCY = {5: "Every 5 minutes", 15: "Every 15 minutes", 30: "Every 30 minutes", 60: "Every hour",
             180: "Every 3 hours", 360: "Every 6 hours", 720: "Every 12 hours", 1440: "Once a day"}
STATE = {
    "ok": ("✅", "Normal"), "fired": ("🔴", "Alert"), "new": ("🕓", "Waiting for first check"),
    "error": ("⚠️", "Problem"), "not_enough_data": ("🕓", "Learning"), "no_data": ("⚪", "No data"),
}
EXAMPLES = ("e.g. number of orders per day · total payment value per week · orders not delivered yet")


# ------------------------------------------------------------
# small helpers
# ------------------------------------------------------------

def _call(method: str, backend_url: str, path: str, headers: Optional[Dict[str, str]] = None,
          params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None, timeout: int = 120):
    return requests.request(method, f"{backend_url}{path}", params=params, json=body, headers=headers,
                            timeout=timeout)


def _error(response) -> str:
    try:
        detail = response.json().get("detail") or response.text
    except ValueError:
        detail = response.text
    detail = str(detail)
    if "connection session not found" in detail.lower():
        return "the backend restarted and forgot the connection. Connect the database again on the 💬 page."
    return detail


def ago(ts: Optional[float], now: Optional[float] = None) -> str:
    if not ts:
        return "never"
    seconds = (now or time.time()) - float(ts)
    future = seconds < 0
    seconds = abs(seconds)
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = int(seconds // size)
            text = f"{count} {unit}{'s' if count != 1 else ''}"
            return f"in {text}" if future else f"{text} ago"
    return "in under a minute" if future else "just now"


def fmt(value: Optional[float]) -> str:
    if value is None:
        return "-"
    size = abs(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e4, "K")):
        if size >= limit:
            return f"{value / limit:,.1f}{suffix}"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def describe_rule(rule: Dict[str, Any], grain: Optional[str] = None) -> str:
    """The alert's rule in one plain sentence."""
    unit = grain if grain in ("day", "week", "month", "quarter", "year") else "check"
    kind = rule.get("type", "smart")
    direction = rule.get("direction", "both")
    if kind == "smart":
        way = {"both": "", "up": ", only rises", "down": ", only drops"}[direction]
        return f"Warn when it looks unusual ({rule.get('sensitivity', 'medium')} sensitivity{way})"
    if kind == "limit":
        return f"Warn when it goes {rule.get('op', 'above')} {fmt(float(rule.get('value', 0)))}"
    if kind == "change":
        verb = {"both": "jumps or drops", "up": "jumps", "down": "drops"}[direction]
        return f"Warn when it {verb} {fmt(float(rule.get('percent', 30)))}% vs {COMPARE[rule.get('compare_to', 'previous')]}"
    if kind == "trend":
        verb = {"both": "falls or rises", "up": "rises", "down": "falls"}[direction]
        return f"Warn when it {verb} {rule.get('periods', 3)} {unit}s in a row"
    return f"Warn when there is no new data for {rule.get('periods', 2)} {unit}s"


def why_question(event: Dict[str, Any]) -> str:
    """The question 'Find out why' asks on the Ask page."""
    value, expected = event.get("value"), event.get("expected")
    pct = event.get("change_pct")
    went_down = (value is not None and expected is not None and value < expected) or (pct is not None and pct < 0)
    name = str(event.get("alert_name") or "this number")
    name = name[:1].lower() + name[1:]
    period = str(event.get("period") or "").strip()
    if not period or period == "latest reading" or period.startswith("the check"):
        when = ""
    elif period[:1].isdigit():  # a day: "27 Jul 2022"
        when = f" on {period}"
    else:  # "Jul 2022", "Q3 2022", "week of ..."
        when = f" in {period}"
    return f"Why did {name} {'drop' if went_down else 'go up'}{when}?"


# ------------------------------------------------------------
# chart: value, normal-range band, expected line, past alerts
# ------------------------------------------------------------

def series_frame(backtest: Dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(backtest.get("series") or [])
    if frame.empty:
        return frame
    frame["t"] = pd.to_datetime(frame["t"], errors="coerce")
    for column in ("v", "expected", "low", "high"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["severity"] = frame.get("severity", pd.Series([None] * len(frame))).fillna("")
    return frame.dropna(subset=["t", "v"])


def alert_chart(backtest: Dict[str, Any], rule: Dict[str, Any], t: Theme = LIGHT, height: int = 230):
    frame = series_frame(backtest)
    if len(frame) < 2:
        return None
    x = alt.X("t:T", title=None, axis=alt.Axis(grid=False, labelColor=t.ink_2, domainColor=t.axis,
                                                 tickColor=t.axis, labelFlush=True))
    y_axis = alt.Axis(gridColor=t.grid, labelColor=t.ink_2, domain=False, ticks=False, format="~s")
    tooltip = [alt.Tooltip("t:T", title="Period"), alt.Tooltip("v:Q", title="Value", format=",.2f"),
               alt.Tooltip("expected:Q", title="Expected", format=",.2f"),
               alt.Tooltip("low:Q", title="Normal from", format=",.2f"),
               alt.Tooltip("high:Q", title="Normal to", format=",.2f")]
    layers = []
    kind = rule.get("type", "smart")
    if kind == "smart" and frame["low"].notna().any():
        band = frame.dropna(subset=["low", "high"])
        layers.append(alt.Chart(band).mark_area(color=t.increase, opacity=0.13, interpolate="monotone").encode(
            x=x, y=alt.Y("low:Q", title=None, axis=y_axis), y2="high:Q"))
        layers.append(alt.Chart(band).mark_line(color=t.increase, strokeDash=[4, 4], strokeWidth=1.2,
                                                opacity=0.8, interpolate="monotone").encode(x=x, y="expected:Q"))
    if kind == "limit":
        limit = float(rule.get("value", 0))
        layers.append(alt.Chart(pd.DataFrame({"limit": [limit]})).mark_rule(
            color=t.critical, strokeDash=[6, 4], strokeWidth=1.5).encode(y="limit:Q"))
    layers.append(alt.Chart(frame).mark_line(color=t.ink if not t.dark else t.ink_2, strokeWidth=1.8,
                                             interpolate="monotone").encode(
        x=x, y=alt.Y("v:Q", title=None, axis=y_axis), tooltip=tooltip))
    fired = frame[frame["fired"] == True]  # noqa: E712 - pandas column compare
    if not fired.empty:
        layers.append(alt.Chart(fired).mark_point(filled=True, size=70, opacity=1, stroke=t.card,
                                                  strokeWidth=1.5).encode(
            x=x, y="v:Q", tooltip=tooltip,
            color=alt.Color("severity:N", legend=None,
                            scale=alt.Scale(domain=["critical", "warning"], range=[t.critical, t.warning]))))
    last = frame.tail(1)
    layers.append(alt.Chart(last).mark_point(filled=True, size=110, color=t.accent, stroke=t.card,
                                             strokeWidth=2).encode(x=x, y="v:Q", tooltip=tooltip))
    return (alt.layer(*layers).properties(height=height, background="transparent")
            .configure_view(strokeWidth=0).configure(font=FONT_HTML.replace("'", "")))


def verdict_html(result: Dict[str, Any], t: Theme = LIGHT) -> str:
    status = result.get("status")
    if result.get("fired"):
        color = t.critical if result.get("severity") == "critical" else t.warning
        head = f"{'🔴' if result.get('severity') == 'critical' else '🟠'} {result.get('severity', '').upper()}: " \
               f"{result.get('title', '')}"
    elif status == "ok":
        color, head = t.good, "✅ Normal right now"
    else:
        color, head = t.muted, "🕓 " + {"not_enough_data": "Still learning", "no_data": "No data yet",
                                        "error": "Problem"}.get(status, "Checked")
    return (f'<div style="font-family:{FONT_HTML};border-left:4px solid {color};background:{t.card};'
            f'border:1px solid {t.border};border-left-width:4px;border-left-color:{color};border-radius:10px;'
            f'padding:10px 14px;margin:6px 0">'
            f'<div style="font-weight:600;color:{t.ink}">{html.escape(head)}</div>'
            f'<div style="color:{t.ink_2};font-size:14px;margin-top:2px">{html.escape(result.get("message") or "")}'
            f'</div></div>')


def event_html(event: Dict[str, Any], t: Theme = LIGHT, now: Optional[float] = None) -> str:
    severity = event.get("severity") or "warning"
    status = event.get("status") or "open"
    color = {"critical": t.critical, "warning": t.warning}.get(severity, t.muted)
    if status == "resolved":
        color = t.good
    chip = {"open": ("Open", color), "acknowledged": ("Seen", t.increase), "resolved": ("Resolved", t.good)}[status]
    icon = "✅" if status == "resolved" else ("🔴" if severity == "critical" else "🟠")
    pct = event.get("change_pct")
    change = (f'<span style="color:{t.critical if pct < 0 else t.increase};font-weight:600;margin-left:8px">'
              f'{pct:+.0f}%</span>') if pct is not None else ""
    sent = event.get("notified") or ""
    meta = " · ".join(x for x in [html.escape(str(event.get("alert_name") or "")),
                                   html.escape(str(event.get("period") or "")),
                                   f"raised {ago(event.get('ts'), now)}",
                                   html.escape(sent)] if x)
    return (
        f'<div style="font-family:{FONT_HTML};background:{t.card};border:1px solid {t.border};'
        f'border-left:5px solid {color};border-radius:10px;padding:12px 14px;margin:8px 0 4px">'
        f'<div style="display:flex;justify-content:space-between;gap:10px;align-items:baseline">'
        f'<div style="font-weight:600;color:{t.ink};font-size:15px">{icon} {html.escape(event.get("title") or "")}'
        f'{change}</div>'
        f'<span style="font-size:11px;font-weight:600;color:{chip[1]};border:1px solid {chip[1]};border-radius:999px;'
        f'padding:1px 8px;white-space:nowrap">{chip[0]} · {severity.upper()}</span></div>'
        f'<div style="color:{t.ink_2};font-size:14px;margin-top:4px">{html.escape(event.get("message") or "")}</div>'
        f'<div style="color:{t.muted};font-size:12px;margin-top:6px">{meta}</div></div>')


def tiles_html(summary: Dict[str, Any], checker: bool, t: Theme = LIGHT) -> str:
    tiles = [
        _tile("Watching", f"{summary.get('active', 0):,}", f"{summary.get('paused', 0)} paused", t, "👁", t.increase),
        _tile("Critical", f"{summary.get('critical', 0):,}", "open alerts", t, "●", t.critical),
        _tile("Warnings", f"{summary.get('warning', 0):,}", "open alerts", t, "●", t.warning),
        _tile("Last 24 hours", f"{summary.get('raised_24h', 0):,}", "alerts raised", t, "🕘", t.accent),
        _tile("Background checks", "On" if checker else "Off",
              f"last check {ago(summary.get('last_check'))}", t, "✓" if checker else "!",
              t.good if checker else t.warning),
    ]
    return f'<div style="font-family:{FONT_HTML};display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 12px">{"".join(tiles)}</div>'


# ------------------------------------------------------------
# sidebar badge
# ------------------------------------------------------------

def render_alert_badge(st, backend_url: str, connection_id: Optional[str] = None,
                       headers: Optional[Dict[str, str]] = None) -> None:
    """'🚨 2 open alerts (1 critical)' in the sidebar; quiet when there are none or the backend is away."""
    cached = st.session_state.get("_alert_badge")
    if cached and time.time() - cached[0] < 30:
        summary = cached[1]
    else:
        try:
            response = _call("GET", backend_url, "/alerts/summary", headers,
                             params={"connection_id": connection_id or ""}, timeout=3)
            summary = response.json() if response.ok else {}
        except (requests.exceptions.RequestException, ValueError):
            summary = {}
        st.session_state["_alert_badge"] = (time.time(), summary)
    count = int(summary.get("open") or 0)
    if count:
        critical = int(summary.get("critical") or 0)
        extra = f" ({critical} critical)" if critical else ""
        st.sidebar.markdown(f"🚨 **{count} open alert{'s' if count != 1 else ''}**{extra}: see the 🚨 Alerts page")


# ------------------------------------------------------------
# the page
# ------------------------------------------------------------

def render_alerts_page(st, backend_url: str, connection_id: Optional[str],
                       headers: Optional[Dict[str, str]] = None,
                       ask: Optional[Callable[[str], None]] = None) -> None:
    t = theme_for(detect_dark(st))
    st.session_state.pop("_alert_badge", None)  # the sidebar count refreshes after changes here

    head, button = st.columns([5, 1])
    with head:
        st.subheader("🚨 Alerts")
        st.caption("InsightX watches your numbers in the background and warns you when something unusual happens.")
    if not connection_id:
        st.info("Connect a database on the 💬 page first, then come back to set up alerts.")
        _feed(st, backend_url, headers, t, ask, None)
        return

    try:
        response = _call("GET", backend_url, "/alerts", headers, params={"connection_id": connection_id})
    except requests.exceptions.ConnectionError:
        st.error(f"Backend is not running at {backend_url}. Start FastAPI first.")
        return
    if not response.ok:
        st.warning(f"Could not load your alerts: {_error(response)}")
        return
    data = response.json()
    alerts: List[Dict[str, Any]] = data.get("alerts") or []
    summary = data.get("summary") or {}

    with button:
        st.write("")
        if st.button("🔄 Check all now", key="alerts_check_all", use_container_width=True,
                     disabled=not alerts):
            with st.spinner("Checking every alert on the current data..."):
                for alert in alerts:
                    _call("POST", backend_url, f"/alerts/{alert['id']}/check", headers, timeout=300)
            st.rerun()

    if st.session_state.get("alerts_notice"):
        st.success(st.session_state.pop("alerts_notice"))
    st.markdown(tiles_html(summary, bool(data.get("checker_running")), t), unsafe_allow_html=True)

    feed_tab, mine_tab, new_tab = st.tabs([f"🚨 Alert feed ({summary.get('open', 0)})",
                                           f"📋 My alerts ({len(alerts)})", "➕ New alert"])
    with feed_tab:
        _feed(st, backend_url, headers, t, ask, connection_id)
    with mine_tab:
        _my_alerts(st, backend_url, headers, t, alerts, data.get("check_minutes") or list(FREQUENCY))
    with new_tab:
        _new_alert(st, backend_url, connection_id, headers, t, bool(data.get("email_ready")))


def _feed(st, backend_url, headers, t, ask, connection_id) -> None:
    show = st.radio("Show", ["Open", "All", "Resolved"], horizontal=True, key="alerts_feed_filter",
                    label_visibility="collapsed")
    status = {"Open": "open", "All": "all", "Resolved": "resolved"}[show]
    try:
        response = _call("GET", backend_url, "/alerts/events", headers,
                         params={"status": status, "connection_id": connection_id or "", "limit": 100})
    except requests.exceptions.RequestException as exc:
        st.error(f"Could not load alerts: {exc}")
        return
    if not response.ok:
        st.warning(f"Could not load alerts: {_error(response)}")
        return
    found = response.json().get("events") or []
    if not found:
        st.info("🎉 Nothing needs your attention." if status == "open" else "No alerts here yet.")
        return
    now = time.time()
    for event in found:
        st.markdown(event_html(event, t, now), unsafe_allow_html=True)
        cols = st.columns(4)
        if event["status"] == "open" and cols[0].button("👁 Mark as seen", key=f"ev_ack_{event['id']}",
                                                        use_container_width=True):
            _call("POST", backend_url, f"/alerts/events/{event['id']}", headers, body={"action": "acknowledge"})
            st.rerun()
        if event["status"] != "resolved" and cols[1].button("✅ Resolve", key=f"ev_res_{event['id']}",
                                                            use_container_width=True):
            _call("POST", backend_url, f"/alerts/events/{event['id']}", headers, body={"action": "resolve"})
            st.rerun()
        if event["status"] == "resolved" and cols[1].button("↩ Reopen", key=f"ev_reo_{event['id']}",
                                                            use_container_width=True):
            _call("POST", backend_url, f"/alerts/events/{event['id']}", headers, body={"action": "reopen"})
            st.rerun()
        if ask is not None and cols[2].button("🔍 Find out why", key=f"ev_why_{event['id']}",
                                              use_container_width=True, help=why_question(event)):
            ask(why_question(event))
        if event["status"] != "resolved" and cols[3].button("😴 Snooze 1 day", key=f"ev_snz_{event['id']}",
                                                            use_container_width=True,
                                                            help="Keep checking, but don't send messages for a day"):
            _call("PATCH", backend_url, f"/alerts/{event['alert_id']}", headers, body={"snooze_hours": 24})
            _call("POST", backend_url, f"/alerts/events/{event['id']}", headers, body={"action": "acknowledge"})
            st.session_state["alerts_notice"] = "Snoozed for a day: still checked, no messages sent."
            st.rerun()


def _my_alerts(st, backend_url, headers, t, alerts, minutes_options) -> None:
    if not alerts:
        st.info("No alerts yet. Make one in the ➕ New alert tab.")
        return
    now = time.time()
    show_charts = st.toggle("Show charts", value=len(alerts) <= 8, key="alerts_show_charts")
    for alert in alerts:
        icon, word = STATE.get(alert.get("state") or "new", ("•", alert.get("state")))
        if alert.get("status") == "paused":
            icon, word = "⏸", "Paused"
        snoozed = float(alert.get("snooze_until") or 0) > now
        with st.container(border=True):
            top, right = st.columns([4, 1])
            with top:
                st.markdown(f"**{icon} {html.escape(alert['name'])}** · {word}"
                            + (f" · 😴 snoozed until {time.strftime('%d %b %H:%M', time.localtime(alert['snooze_until']))}"
                               if snoozed else ""))
                st.caption(describe_rule(alert.get("rule") or {}, alert.get("grain")))
            with right:
                if alert.get("last_value") is not None and alert.get("state") != "error" \
                        and (alert.get("rule") or {}).get("type") != "stale":
                    st.metric("Latest", fmt(alert["last_value"]))
            channels = alert.get("channels") or {}
            sends = ", ".join(x for x in ["web hook" if channels.get("webhook") else "",
                                          "e-mail" if channels.get("email") else ""] if x) or "this page only"
            next_check = "paused" if alert.get("status") == "paused" else f"next {ago(alert.get('next_check'), now)}"
            st.caption(f"{FREQUENCY.get(int(alert.get('check_minutes') or 60), 'Checked regularly')} · "
                       f"last checked {ago(alert.get('last_checked'), now)} · {next_check} · tells: {sends}")
            if alert.get("state") == "error" and alert.get("last_error"):
                st.warning(alert["last_error"])
            elif alert.get("last_message"):
                st.markdown(f"<div style='font-size:14px;color:{t.ink_2}'>{html.escape(alert['last_message'])}</div>",
                            unsafe_allow_html=True)
            if show_charts:
                _alert_chart(st, backend_url, headers, t, alert)

            cols = st.columns(4)
            if cols[0].button("🔄 Check now", key=f"al_chk_{alert['id']}", use_container_width=True):
                with st.spinner("Checking..."):
                    response = _call("POST", backend_url, f"/alerts/{alert['id']}/check", headers, timeout=300)
                if response.ok:
                    result = response.json().get("result") or {}
                    st.session_state["alerts_notice"] = f"{alert['name']}: {result.get('message') or 'checked.'}"
                st.rerun()
            paused = alert.get("status") == "paused"
            if cols[1].button("▶ Resume" if paused else "⏸ Pause", key=f"al_pause_{alert['id']}",
                              use_container_width=True):
                _call("PATCH", backend_url, f"/alerts/{alert['id']}", headers,
                      body={"status": "active" if paused else "paused"})
                st.rerun()
            if cols[2].button("🔔 Unsnooze" if snoozed else "😴 Snooze 1 day", key=f"al_snz_{alert['id']}",
                              use_container_width=True):
                _call("PATCH", backend_url, f"/alerts/{alert['id']}", headers,
                      body={"snooze_hours": 0 if snoozed else 24})
                st.rerun()
            if cols[3].button("🗑 Delete", key=f"al_del_{alert['id']}", use_container_width=True):
                _call("DELETE", backend_url, f"/alerts/{alert['id']}", headers)
                st.rerun()
            with st.expander("⚙️ Edit"):
                _edit(st, backend_url, headers, alert, minutes_options)
            with st.expander("SQL"):
                st.code(alert.get("sql") or "", language="sql")


def _alert_chart(st, backend_url, headers, t, alert) -> None:
    try:
        response = _call("GET", backend_url, f"/alerts/{alert['id']}/chart", headers, timeout=120)
    except requests.exceptions.RequestException:
        return
    if not response.ok:
        st.caption(f"Chart not available: {_error(response)}")
        return
    data = response.json()
    chart = alert_chart(data.get("backtest") or {}, alert.get("rule") or {}, t)
    if chart is None:
        st.caption("The chart appears after a few checks." if data.get("single") else "No data to draw yet.")
        return
    st.altair_chart(chart, use_container_width=True)
    history = data.get("backtest") or {}
    if history.get("fired_count"):
        st.caption(f"Red/orange dots: {history['fired_count']} time(s) this rule found something unusual "
                   f"in the last {history.get('checked', 0)} periods. Shaded area: the normal range.")


def _rule_inputs(st, prefix: str, rule: Optional[Dict[str, Any]] = None, grain: str = "day",
                 single: bool = False) -> Dict[str, Any]:
    """The 'when to warn me' controls. Returns the rule."""
    rule = rule or {}
    kinds = [k for k in RULE_LABELS if not (single and k == "stale")]
    kind = st.selectbox("When should I warn you?", kinds, format_func=RULE_LABELS.get,
                        index=kinds.index(rule.get("type", "smart")) if rule.get("type", "smart") in kinds else 0,
                        key=f"{prefix}_type")
    st.caption(RULE_HELP[kind])
    out: Dict[str, Any] = {"type": kind}
    unit = grain if grain in ("day", "week", "month", "quarter", "year") else "check"
    left, right = st.columns(2)
    if kind == "smart":
        with left:
            out["sensitivity"] = st.select_slider(
                "Sensitivity", ["low", "medium", "high"], value=rule.get("sensitivity", "medium"),
                format_func=lambda s: {"low": "Low (fewer alerts)", "medium": "Medium",
                                       "high": "High (more alerts)"}[s], key=f"{prefix}_sens")
        with right:
            out["direction"] = st.radio("Warn about", list(DIRECTIONS), format_func=DIRECTIONS.get,
                                        index=list(DIRECTIONS).index(rule.get("direction", "both")),
                                        horizontal=True, key=f"{prefix}_dir")
    elif kind == "limit":
        with left:
            out["op"] = st.radio("When it goes", ["above", "below"], horizontal=True,
                                 index=0 if rule.get("op", "above") == "above" else 1, key=f"{prefix}_op")
        with right:
            out["value"] = st.number_input("Limit", value=float(rule.get("value", 0.0)), key=f"{prefix}_val")
    elif kind == "change":
        with left:
            out["percent"] = st.number_input("By more than (%)", min_value=1.0, max_value=10000.0,
                                             value=float(rule.get("percent", 30.0)), step=5.0, key=f"{prefix}_pct")
            out["direction"] = st.radio("Warn about", list(DIRECTIONS), format_func=DIRECTIONS.get,
                                        index=list(DIRECTIONS).index(rule.get("direction", "both")),
                                        horizontal=True, key=f"{prefix}_cdir")
        with right:
            options = ["previous"] if single else list(COMPARE)
            out["compare_to"] = st.selectbox("Compared with", options, format_func=COMPARE.get,
                                             index=options.index(rule.get("compare_to", "previous"))
                                             if rule.get("compare_to", "previous") in options else 0,
                                             key=f"{prefix}_cmp")
    elif kind == "trend":
        with left:
            out["periods"] = int(st.number_input(f"How many {unit}s in a row", min_value=2, max_value=60,
                                                 value=int(rule.get("periods", 3)), key=f"{prefix}_n"))
        with right:
            out["direction"] = st.radio("Direction", ["down", "up", "both"],
                                        format_func={"down": "Falling", "up": "Rising", "both": "Either"}.get,
                                        index=["down", "up", "both"].index(rule.get("direction", "down")
                                                                           if rule.get("type") == "trend" else "down"),
                                        horizontal=True, key=f"{prefix}_tdir")
    else:
        with left:
            out["periods"] = int(st.number_input(f"No new data for how many {unit}s", min_value=1, max_value=365,
                                                 value=int(rule.get("periods", 2)), key=f"{prefix}_gap"))
    left, right = st.columns(2)
    with left:
        out["severity"] = st.selectbox("How serious", list(SEVERITY), format_func=SEVERITY.get,
                                       index=list(SEVERITY).index(rule.get("severity", "auto")), key=f"{prefix}_sev")
    with right:
        if kind != "stale" and not single:
            out["skip_unfinished"] = st.checkbox(
                "Ignore the period that is not over yet", value=bool(rule.get("skip_unfinished", True)),
                key=f"{prefix}_skip", help="Today's (or this month's) number is still growing, so it would look low.")
    return out


def _edit(st, backend_url, headers, alert, minutes_options) -> None:
    prefix = f"edit_{alert['id']}"
    rule = _rule_inputs(st, prefix, alert.get("rule") or {}, alert.get("grain") or "day", bool(alert.get("single")))
    options = [m for m in minutes_options if m in FREQUENCY]
    current = int(alert.get("check_minutes") or 60)
    minutes = st.selectbox("Check", options, format_func=FREQUENCY.get,
                           index=options.index(current) if current in options else 0, key=f"{prefix}_freq")
    name = st.text_input("Name", value=alert.get("name") or "", key=f"{prefix}_name")
    channels = alert.get("channels") or {}
    webhook = st.text_input("Web hook (Slack / Discord / Teams)", value=channels.get("webhook", ""),
                            key=f"{prefix}_hook")
    email = st.text_input("E-mail", value=channels.get("email", ""), key=f"{prefix}_mail")
    if st.button("💾 Save changes", key=f"{prefix}_save", type="primary"):
        response = _call("PATCH", backend_url, f"/alerts/{alert['id']}", headers,
                         body={"rule": rule, "check_minutes": minutes, "name": name, "webhook": webhook,
                               "email": email})
        if response.ok:
            st.session_state["alerts_notice"] = f"Saved: {name}"
            st.rerun()
        st.warning(_error(response))


def _new_alert(st, backend_url, connection_id, headers, t, email_ready) -> None:
    st.markdown("##### 1. What should I watch?")
    source = st.radio("Number", ["📋 Pick a suggested number", "✨ Describe it in words"], horizontal=True,
                      key="new_alert_source", label_visibility="collapsed")
    metric: Dict[str, Any] = {}
    grain = "day"
    if source.startswith("📋"):
        grain = st.radio("Per", ["day", "week", "month"], horizontal=True, key="new_alert_grain",
                         format_func=lambda g: f"Per {g}")
        cache_key = f"_alert_suggestions|{connection_id}|{grain}"
        if cache_key not in st.session_state:
            try:
                with st.spinner("Finding numbers worth watching..."):
                    response = _call("GET", backend_url, "/alerts/suggestions", headers,
                                     params={"connection_id": connection_id, "grain": grain}, timeout=300)
                st.session_state[cache_key] = response.json().get("suggestions") or [] if response.ok else []
                if not response.ok:
                    st.warning(_error(response))
            except requests.exceptions.RequestException as exc:
                st.warning(f"Could not get suggestions: {exc}")
                st.session_state[cache_key] = []
        found = st.session_state.get(cache_key) or []
        if not found:
            st.info("No dated tables found to suggest from. Describe the number in words instead.")
            return
        names = [s["name"] for s in found]
        picked = st.selectbox("Number", names, key="new_alert_pick", label_visibility="collapsed")
        suggestion = found[names.index(picked)]
        st.caption(suggestion.get("description") or "")
        metric = {"sql": suggestion["sql"], "name": suggestion["name"], "fill_gaps": suggestion.get("fill_gaps", False),
                  "metric_text": suggestion["name"]}
    else:
        words = st.text_input("Number", placeholder=EXAMPLES, key="new_alert_words", label_visibility="collapsed")
        st.caption("Say \"per day\", \"per week\" or \"per month\" for a number over time, or ask for one number "
                   "(like orders waiting to ship): InsightX then learns from each check.")
        if not words.strip():
            st.session_state.pop("new_alert_preview", None)
            return
        metric = {"metric_text": words.strip(), "fill_gaps": True}
        known = st.session_state.get("new_alert_preview")
        if known and known.get("metric_text") == words.strip():
            metric["sql"] = known["sql"]  # already turned into SQL: no second AI call
            grain = known.get("grain") or "day"

    preview = st.session_state.get("new_alert_preview")
    same_metric = preview and preview.get("metric_text") == metric.get("metric_text")
    single = bool(same_metric and preview.get("single"))

    st.markdown("##### 2. When should I warn you?")
    rule = _rule_inputs(st, "new_alert", grain=(preview.get("grain") if same_metric else grain) or grain,
                        single=single)

    st.markdown("##### 3. How should I tell you?")
    left, right = st.columns(2)
    with left:
        minutes = st.selectbox("Check", list(FREQUENCY), index=list(FREQUENCY).index(60), format_func=FREQUENCY.get,
                               key="new_alert_freq")
        name = st.text_input("Name", value=(preview.get("name") if same_metric else metric.get("name")) or "",
                             key=f"new_alert_name_{metric.get('metric_text', '')}", placeholder="Name of this alert")
    with right:
        webhook = st.text_input("Web hook (optional)", placeholder="Slack, Discord or Teams incoming web hook URL",
                                key="new_alert_hook")
        email = st.text_input("E-mail (optional)", placeholder="you@example.com", key="new_alert_mail",
                              help=None if email_ready else "E-mail needs INSIGHTX_SMTP_HOST set on the backend.")
        if email.strip() and not email_ready:
            st.caption("⚠️ E-mail is not set up on the backend yet (INSIGHTX_SMTP_HOST); the alert still shows here.")
        if (webhook.strip() or email.strip()) and st.button("📨 Send a test message", key="new_alert_test"):
            response = _call("POST", backend_url, "/alerts/test-notification", headers,
                             body={"webhook": webhook, "email": email})
            if response.ok:
                for line in response.json().get("results") or []:
                    (st.success if line.endswith("sent") else st.warning)(line)
            else:
                st.warning(_error(response))

    body = {"connection_id": connection_id, **metric, "name": name.strip() or metric.get("name", ""), "rule": rule}
    left, right = st.columns(2)
    if left.button("👀 Preview on current data", key="new_alert_preview_btn", use_container_width=True):
        with st.spinner("Getting the data and testing the rule on the past..."):
            response = _call("POST", backend_url, "/alerts/preview", headers, body=body, timeout=600)
        if response.ok:
            st.session_state["new_alert_preview"] = {**response.json(), "metric_text": metric.get("metric_text")}
            st.rerun()
        st.warning(f"Could not preview: {_error(response)}")
    if right.button("✅ Create alert", key="new_alert_create", type="primary", use_container_width=True):
        with st.spinner("Creating the alert and running its first check..."):
            response = _call("POST", backend_url, "/alerts", headers,
                             body={**body, "check_minutes": minutes, "webhook": webhook, "email": email}, timeout=600)
        if response.ok:
            made = response.json()
            first = made.get("first_check") or {}
            st.session_state.pop("new_alert_preview", None)
            st.session_state["alerts_notice"] = (f"Alert created: {made['alert']['name']}. First check: "
                                                 f"{first.get('message') or 'done'}")
            st.rerun()
        st.warning(f"Could not create the alert: {_error(response)}")

    if same_metric:
        _preview(st, t, preview, rule)


def _preview(st, t, preview, rule) -> None:
    st.markdown("##### 👀 Preview")
    if preview.get("rule", {}).get("type") != rule.get("type"):
        st.caption("You changed the rule: press Preview again to see it on your data.")
    st.markdown(verdict_html(preview.get("evaluation") or {}, t), unsafe_allow_html=True)
    history = preview.get("backtest") or {}
    chart = alert_chart(history, preview.get("rule") or rule, t, height=260)
    if chart is not None:
        st.altair_chart(chart, use_container_width=True)
    if preview.get("single"):
        st.caption("This is one number, so InsightX learns what is normal from each check after you create it.")
    elif history.get("checked"):
        unit = history.get("grain") or "period"
        count = history.get("fired_count", 0)
        st.markdown(f"**Back-test:** in the last {history['checked']} {unit}s this alert would have fired "
                    f"**{count}** time{'s' if count != 1 else ''}.")
        if count:
            chips = " ".join(f"`{f['period']}`" for f in history.get("fired", [])[-12:])
            st.caption(f"Most recent: {chips}")
        if count > max(5, history["checked"] // 10):
            st.caption("💡 That is a lot. Try Low sensitivity, a bigger %, or only one direction.")
    with st.expander("SQL"):
        st.code(preview.get("sql") or "", language="sql")
