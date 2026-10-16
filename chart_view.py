"""
Draw the chart chosen by chart_builder with Altair (ships with Streamlit).

Charts are interactive: hover for values, scroll to zoom, drag to pan,
double-click to reset, and the "..." menu saves PNG or SVG.
"""

import altair as alt
import pandas as pd

from chart_builder import (
    AREA,
    BAR,
    LABELS,
    LINE,
    METRIC,
    PIE,
    SCATTER,
    bar_frame,
    column_roles,
    compact,
    plan_chart,
    summarize,
    switch,
    to_frame,
    with_context,
)

HEIGHT = 360


def _title(name):
    return str(name).replace("_", " ").capitalize()


def _x_type(frame, name):
    if pd.api.types.is_datetime64_any_dtype(frame[name]):
        return "temporal"
    if pd.api.types.is_numeric_dtype(frame[name]):
        return "ordinal" if frame[name].nunique() <= 40 else "quantitative"
    return "ordinal"


def _time_x(frame, name, title, **extra):
    """X axis for dates: whole months or years get one tidy label each."""
    values = frame[name].dropna()
    if pd.api.types.is_datetime64_any_dtype(frame[name]) and not values.empty and values.nunique() <= 40:
        if (values.dt.month == 1).all() and (values.dt.day == 1).all():
            return alt.X(name, type="ordinal", timeUnit="year", title=title,
                         axis=alt.Axis(labelAngle=0), **extra)
        if (values.dt.day == 1).all():
            return alt.X(name, type="ordinal", timeUnit="yearmonth", title=title,
                         axis=alt.Axis(format="%b %Y", labelAngle=-30), **extra)
    angle = 0 if pd.api.types.is_numeric_dtype(frame[name]) else -30
    return alt.X(name, type=_x_type(frame, name), title=title, axis=alt.Axis(labelAngle=angle), **extra)


def _tooltips(frame, columns):
    tips = []
    for name in columns:
        if not name:
            continue
        if pd.api.types.is_datetime64_any_dtype(frame[name]):
            tips.append(alt.Tooltip(name, type="temporal", title=_title(name)))
        elif pd.api.types.is_numeric_dtype(frame[name]):
            tips.append(alt.Tooltip(name, type="quantitative", title=_title(name), format=",.2~f"))
        else:
            tips.append(alt.Tooltip(name, type="nominal", title=_title(name)))
    return tips


def _long(frame, plan):
    """Several measures -> one 'measure'/'value' pair per row, for colour."""
    keep = [plan.x] + list(plan.y)
    data = frame[keep].melt(id_vars=[plan.x], var_name="measure", value_name="value")
    data["measure"] = data["measure"].map(_title)
    return data


def build_chart(frame, plan):
    if plan.kind == SCATTER:
        y = plan.y[0]
        return (
            alt.Chart(frame)
            .mark_circle(size=70, opacity=0.75)
            .encode(
                x=alt.X(plan.x, type="quantitative", title=_title(plan.x), scale=alt.Scale(zero=False)),
                y=alt.Y(y, type="quantitative", title=_title(y), scale=alt.Scale(zero=False)),
                tooltip=_tooltips(frame, list(frame.columns[:6])),
            )
            .properties(height=HEIGHT)
            .interactive()
        )

    if plan.kind == PIE:
        y = plan.y[0]
        base = alt.Chart(frame).encode(
            theta=alt.Theta(y, type="quantitative", stack=True),
            color=alt.Color(plan.x, type="nominal", title=_title(plan.x),
                            sort=alt.EncodingSortField(field=y, order="descending")),
            order=alt.Order(y, type="quantitative", sort="descending"),
            tooltip=_tooltips(frame, [plan.x, y]),
        )
        return (base.mark_arc(innerRadius=70, outerRadius=150)).properties(height=HEIGHT)

    many = len(plan.y) > 1
    data = _long(frame, plan) if many else frame
    y_field = "value" if many else plan.y[0]
    y_title = "Value" if many else _title(plan.y[0])
    color_field = "measure" if many else plan.color
    tips = _tooltips(data, [plan.x, color_field, y_field])
    if color_field:
        color = alt.Color(color_field, type="nominal", title=_title(color_field))
    elif plan.highlight:
        color = alt.condition(
            alt.FieldOneOfPredicate(field=plan.x, oneOf=list(plan.highlight)),
            alt.value("#E45756"),
            alt.value("#A9BCD0"),
        )
    else:
        color = alt.value("#4C78A8")

    if plan.kind == BAR:
        data = bar_frame(data, plan) if not many else data
        is_time = plan.x in column_roles(frame)["time"]
        sort = None if is_time else alt.EncodingSortField(field=y_field, op="sum", order="descending")
        label_type = _x_type(data, plan.x) if is_time else "nominal"
        if plan.horizontal and not is_time:
            encoding = dict(
                y=alt.Y(plan.x, type=label_type, sort=sort, title=_title(plan.x)),
                x=alt.X(y_field, type="quantitative", title=y_title),
            )
            if color_field:
                if hasattr(alt, "YOffset"):
                    encoding["yOffset"] = alt.YOffset(color_field, type="nominal")
            height = max(HEIGHT, 26 * data[plan.x].nunique() * (len(plan.y) if many else 1))
        else:
            encoding = dict(
                x=(_time_x(data, plan.x, _title(plan.x)) if is_time else
                   alt.X(plan.x, type=label_type, sort=sort, title=_title(plan.x),
                         axis=alt.Axis(labelAngle=-30))),
                y=alt.Y(y_field, type="quantitative", title=y_title),
            )
            if color_field and hasattr(alt, "XOffset"):
                encoding["xOffset"] = alt.XOffset(color_field, type="nominal")
            height = HEIGHT
        return (
            alt.Chart(data)
            .mark_bar(cornerRadius=3)
            .encode(color=color, tooltip=tips, **encoding)
            .properties(height=min(height, 900))
        )

    # LINE / AREA
    encoding = dict(
        x=_time_x(data, plan.x, _title(plan.x)),
        y=alt.Y(y_field, type="quantitative", title=y_title, stack=None),
        color=color,
        tooltip=tips,
    )
    if plan.kind == AREA:
        mark = alt.Chart(data).mark_area(opacity=0.45, line=True)
    else:
        mark = alt.Chart(data).mark_line(point=True)
    return mark.encode(**encoding).properties(height=HEIGHT).interactive(bind_y=False)


def render_metrics(st, frame, plan):
    labels = [c for c in frame.columns if c not in plan.y]
    names = ", ".join(str(frame[c].iloc[0]) for c in labels[:2] if pd.notna(frame[c].iloc[0]))
    columns = st.columns(len(plan.y))
    for column, name in zip(columns, plan.y):
        title = f"{_title(name)} ({names})" if names else _title(name)
        column.metric(title, compact(frame[name].iloc[0]))


def render_chart(st, execution, question="", key="chart", context=None):
    """
    Show the auto chart for a query result. Returns True if a chart was drawn.

    context: extra rows from the backend (backend/text_to_sql/chart_context.py):
    "top" answers are highlighted among the other rows; single totals are
    shown again split by date ("trend") or by label ("split").
    """
    rows = (execution or {}).get("rows") or []
    if not rows:
        return False
    try:
        answer = to_frame(rows, execution.get("columns"))
        answer_plan = plan_chart(answer, question)
        frame, plan = (None, None)
        if context and context.get("rows"):
            frame, plan = with_context(rows, context, question)
        if plan is None:
            frame, plan = answer, answer_plan
    except Exception:
        return False
    if plan is None:
        return False

    # A single number: show it big, then (if available) the same total
    # split by month or by label underneath.
    if answer_plan is not None and answer_plan.kind == METRIC:
        render_metrics(st, answer, answer_plan)
        if plan.kind == METRIC:
            return True

    if len(plan.options) > 1:
        chosen = st.radio(
            "Chart type",
            plan.options,
            index=plan.options.index(plan.kind),
            format_func=lambda kind: LABELS[kind],
            horizontal=True,
            key=f"{key}_type",
            label_visibility="collapsed",
        )
        plan = switch(plan, chosen)

    try:
        chart = build_chart(frame, plan)
    except Exception as error:
        st.caption(f"Chart not available for this result ({error}).")
        return False

    st.altair_chart(chart, use_container_width=True)

    summary = summarize(frame, plan)
    if summary:
        st.markdown(f"**What it shows:** {summary}")
    if plan.note:
        st.caption(plan.note)
    st.caption("Hover for values. Scroll to zoom, double-click to reset. Use the ⋯ menu to save as PNG or SVG.")
    return True
