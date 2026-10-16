"""
Pick a chart for a query result and describe what it shows.

No AI calls: the choice comes from the shape of the result
(how many rows, which columns are dates, numbers or labels)
and a few words in the question ("share", "trend", ...).
"""

import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

import pandas as pd

# Chart kinds, in the order the switcher shows them.
METRIC = "metric"
BAR = "bar"
LINE = "line"
AREA = "area"
PIE = "pie"
SCATTER = "scatter"

LABELS = {
    METRIC: "Number",
    BAR: "Bar",
    LINE: "Line",
    AREA: "Area",
    PIE: "Pie",
    SCATTER: "Scatter",
}

MAX_BARS = 25
MAX_PIE_SLICES = 8
MAX_SERIES = 8
MAX_MEASURES = 3
MAX_METRICS = 4

_SHARE_WORDS = re.compile(
    r"\b(share|shares|percent|percentage|proportion|split|breakdown|distribution|mix|composition)\b",
    re.I,
)
_TREND_WORDS = re.compile(
    r"\b(trend|over time|by (day|week|month|quarter|year)|daily|weekly|monthly|quarterly|yearly|growth)\b",
    re.I,
)
_TIME_NAME = re.compile(
    r"(date|time|day|week|month|quarter|year|period|_at$|_on$|^dt$)", re.I
)
_ID_NAME = re.compile(r"(^id$|_id$|^id_|code$|zip|postal|phone)", re.I)
_YEAR_NAME = re.compile(r"(^|_)(year|yr)($|_)", re.I)


@dataclass
class ChartPlan:
    kind: str
    x: Optional[str] = None
    y: List[str] = field(default_factory=list)
    color: Optional[str] = None
    options: List[str] = field(default_factory=list)
    horizontal: bool = False
    note: Optional[str] = None
    # Labels that are the actual answer, drawn in a stronger colour.
    highlight: List = field(default_factory=list)


# ------------------------------------------------------------
# Result -> typed DataFrame
# ------------------------------------------------------------

def to_frame(rows: List[Dict], columns: Optional[List[str]] = None) -> pd.DataFrame:
    frame = pd.DataFrame(rows or [])
    if columns:
        ordered = [c for c in columns if c in frame.columns]
        frame = frame[ordered + [c for c in frame.columns if c not in ordered]]
    # first_name + last_name -> one readable label.
    lower = {str(c).lower(): c for c in frame.columns}
    if "first_name" in lower and "last_name" in lower and "name" not in lower:
        first, last = lower["first_name"], lower["last_name"]
        frame.insert(
            list(frame.columns).index(first),
            "name",
            (frame[first].fillna("").astype(str) + " " + frame[last].fillna("").astype(str)).str.strip(),
        )
        frame = frame.drop(columns=[first, last])

    for name in frame.columns:
        series = frame[name]
        if pd.api.types.is_bool_dtype(series):
            continue
        if _is_text(series):
            numbers = pd.to_numeric(series, errors="coerce")
            if series.notna().any() and numbers.notna().sum() == series.notna().sum():
                frame[name] = numbers
                continue
            if _looks_like_dates(series):
                frame[name] = pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)
    return frame


def _is_text(series: pd.Series) -> bool:
    return series.dtype == object or pd.api.types.is_string_dtype(series)


def _looks_like_dates(series: pd.Series) -> bool:
    values = series.dropna().astype(str)
    if values.empty:
        return False
    sample = values.head(50)
    if not sample.str.match(r"^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2}.*)?$").all():
        return False
    parsed = pd.to_datetime(sample, errors="coerce", utc=True)
    return parsed.notna().all()


def _is_numeric(frame: pd.DataFrame, name: str) -> bool:
    return pd.api.types.is_numeric_dtype(frame[name]) and not pd.api.types.is_bool_dtype(frame[name])


def _is_year(frame: pd.DataFrame, name: str) -> bool:
    if not _is_numeric(frame, name) or not _YEAR_NAME.search(name):
        return False
    values = frame[name].dropna()
    return not values.empty and values.between(1900, 2100).all() and (values % 1 == 0).all()


def _is_time(frame: pd.DataFrame, name: str) -> bool:
    if pd.api.types.is_datetime64_any_dtype(frame[name]):
        return True
    if _is_year(frame, name):
        return True
    # Text periods such as "2024-03" or "2024-Q1" named like a time column.
    if _is_text(frame[name]) and _TIME_NAME.search(name):
        values = frame[name].dropna().astype(str)
        return not values.empty and values.str.match(r"^\d{4}([-/ ]?(Q\d|\d{1,2}))?").all()
    return False


def column_roles(frame: pd.DataFrame) -> Dict[str, List[str]]:
    time, measures, labels, ids = [], [], [], []
    for name in frame.columns:
        if _is_time(frame, name):
            time.append(name)
        elif _ID_NAME.search(name):
            ids.append(name)
        elif _is_numeric(frame, name):
            measures.append(name)
        else:
            labels.append(name)
    # Readable labels first; ids only when there is nothing else.
    return {"time": time, "measures": measures, "labels": labels + ids, "ids": ids}


# ------------------------------------------------------------
# Choosing the chart
# ------------------------------------------------------------

def plan_chart(frame: pd.DataFrame, question: str = "") -> Optional[ChartPlan]:
    if frame is None or frame.empty or len(frame.columns) == 0:
        return None

    roles = column_roles(frame)
    time, measures, labels = roles["time"], roles["measures"], roles["labels"]
    rows = len(frame)
    question = question or ""

    if not measures:
        return None

    # One row of numbers: show them as big number cards.
    if rows == 1:
        return ChartPlan(kind=METRIC, y=measures[:MAX_METRICS], options=[METRIC])

    # Something over time.
    if time:
        x = time[0]
        series = next(
            (c for c in labels if c not in roles["ids"] and 1 < frame[c].nunique() <= MAX_SERIES),
            None,
        )
        y = measures[:1] if series else measures[:MAX_MEASURES]
        return ChartPlan(
            kind=LINE,
            x=x,
            y=y,
            color=series,
            options=[LINE, AREA, BAR],
        )

    # Labels with numbers: bar or pie.
    if labels:
        x = labels[0]
        y = measures[:MAX_MEASURES]
        color = None
        # Two label columns and one number: second label becomes the series.
        if (
            len(labels) > 1 and len(y) == 1 and labels[1] not in roles["ids"]
            and 1 < frame[labels[1]].nunique() <= MAX_SERIES
        ):
            color = labels[1]

        pie_ok = (
            len(y) == 1
            and color is None
            and frame[x].nunique() == rows
            and 2 <= rows <= MAX_PIE_SLICES
            and (frame[y[0]].dropna() >= 0).all()
            and frame[y[0]].sum() > 0
        )
        options = [BAR] + ([PIE] if pie_ok else [])
        kind = PIE if pie_ok and _SHARE_WORDS.search(question) else BAR

        longest = frame[x].astype(str).str.len().max()
        note = None
        if kind == BAR and frame[x].nunique() > MAX_BARS:
            note = f"Showing the top {MAX_BARS} of {frame[x].nunique()} by {y[0]}."
        return ChartPlan(
            kind=kind,
            x=x,
            y=y,
            color=color,
            options=options,
            horizontal=bool(longest and longest > 12) or rows > 10,
            note=note,
        )

    # Only numbers: compare the first two as a scatter.
    if len(measures) >= 2 and rows >= 3:
        return ChartPlan(kind=SCATTER, x=measures[0], y=[measures[1]], options=[SCATTER])

    return None


def switch(plan: ChartPlan, kind: str) -> ChartPlan:
    if kind not in plan.options or kind == plan.kind:
        return plan
    return replace(plan, kind=kind)


def with_context(answer_rows: List[Dict], context: Dict, question: str = ""):
    """
    Plan a chart on the wider context rows and highlight the answer's rows.
    Returns (frame, plan) or (None, None) when the context is not usable.
    """
    frame = to_frame(context.get("rows") or [], context.get("columns"))
    plan = plan_chart(frame, question)
    if context.get("kind") in ("trend", "split"):
        # A single total, shown again split by date or by label.
        if plan is None or plan.kind == METRIC or not plan.x:
            return None, None
        plan.note = context.get("note")
        return frame, plan
    if plan is None or plan.kind not in (BAR, PIE) or not plan.x:
        return None, None
    # Same ORDER BY, so the answer's rows are the first rows here.
    count = int(context.get("picked") or len(answer_rows or []))
    plan.highlight = list(dict.fromkeys(frame[plan.x].head(count).tolist()))
    if not plan.highlight:
        return None, None
    if plan.kind == PIE:
        plan = replace(plan, kind=BAR)
    shown = f"the top {len(frame)}" if context.get("truncated") else f"all {len(frame)}"
    plan.note = f"Red: your answer. The chart shows {shown} {_readable(plan.x)} results so you can compare."
    return frame, plan


def bar_frame(frame: pd.DataFrame, plan: ChartPlan) -> pd.DataFrame:
    """Rows for a bar chart: the biggest MAX_BARS labels."""
    if frame[plan.x].nunique() <= MAX_BARS:
        return frame
    top = (
        frame.groupby(plan.x, dropna=False)[plan.y[0]]
        .sum()
        .sort_values(ascending=False)
        .head(MAX_BARS)
        .index
    )
    return frame[frame[plan.x].isin(top)]


# ------------------------------------------------------------
# One-line summary
# ------------------------------------------------------------

def compact(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if pd.isna(number):
        return "n/a"
    size = abs(number)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if size >= limit:
            return f"{number / limit:.2f}".rstrip("0").rstrip(".") + suffix
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _readable(name: str) -> str:
    return str(name).replace("_", " ")


def _when(value) -> str:
    if isinstance(value, pd.Timestamp):
        if value.hour == value.minute == value.second == 0:
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def summarize(frame: pd.DataFrame, plan: ChartPlan) -> str:
    if plan is None or frame.empty:
        return ""
    measure = plan.y[0] if plan.y else None
    what = _readable(measure) if measure else ""

    if plan.kind == METRIC:
        return ""

    if plan.kind in (LINE, AREA) or (plan.kind == BAR and plan.x and _is_time(frame, plan.x)):
        totals = frame.groupby(plan.x, dropna=True)[measure].sum().sort_index()
        if len(totals) < 2:
            return ""
        first, last = totals.iloc[0], totals.iloc[-1]
        text = (
            f"{what.capitalize()} went from {compact(first)} ({_when(totals.index[0])}) "
            f"to {compact(last)} ({_when(totals.index[-1])})"
        )
        if first:
            change = (last - first) / abs(first) * 100
            text += f", {'up' if change >= 0 else 'down'} {abs(change):.0f}%"
        peak = totals.idxmax()
        text += f". Highest: {compact(totals.max())} in {_when(peak)}."
        return text

    if plan.kind in (BAR, PIE):
        totals = frame.groupby(plan.x, dropna=False)[measure].sum().sort_values(ascending=False)
        if totals.empty:
            return ""
        top, top_value = totals.index[0], totals.iloc[0]
        total = totals.sum()
        if len(plan.highlight) == 1 and plan.highlight[0] in totals.index:
            pick = plan.highlight[0]
            rank = list(totals.index).index(pick) + 1
            text = f"{pick}: {compact(totals[pick])}, rank {rank} of {len(totals)}"
            if total > 0 and (totals >= 0).all():
                text += f" ({totals[pick] / total * 100:.0f}% of the total shown)"
            other = totals.index[1] if rank == 1 else totals.index[0]
            label = "Next" if rank == 1 else "Highest"
            return text + f". {label}: {other} ({compact(totals[other])})."
        text = f"{top} has the highest {what} ({compact(top_value)}"
        if total > 0 and (totals >= 0).all() and len(totals) > 1:
            text += f", {top_value / total * 100:.0f}% of the total"
        text += ")"
        if len(totals) > 1:
            text += f". Lowest: {totals.index[-1]} ({compact(totals.iloc[-1])})."
        else:
            text += "."
        return text

    if plan.kind == SCATTER:
        pair = frame[[plan.x, measure]].dropna()
        if len(pair) < 3:
            return ""
        r = pair[plan.x].corr(pair[measure])
        if pd.isna(r):
            return ""
        strength = "strong" if abs(r) >= 0.7 else "moderate" if abs(r) >= 0.4 else "weak"
        direction = "rise together" if r > 0 else "move in opposite directions"
        if strength == "weak":
            return f"{_readable(plan.x).capitalize()} and {what} show little relation (r = {r:.2f})."
        return f"{_readable(plan.x).capitalize()} and {what} {direction} ({strength}, r = {r:.2f})."

    return ""
