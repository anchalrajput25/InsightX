# investigation_view.py
#
# Streamlit view of an investigation report ("why did the metric
# change?"). Chart builders return Altair charts (Vega-Lite, bundled
# with Streamlit) and do not depend on Streamlit, so they can be
# rendered and checked on their own.
#
# Visual system: the validated reference palette, with SELECTED light
# and dark steps (not an automatic flip). Thin marks, hairline grid,
# rounded data ends, tooltips on every mark, one unit per axis,
# direction shown by sign/icon as well as colour. No yOffset or other
# encodings that older Vega-Lite builds ignore.

import html
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import altair as alt

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
# Inside HTML style="..." attributes the font stack must not use double quotes.
FONT_HTML = FONT.replace('"', "'")


@dataclass(frozen=True)
class Theme:
    dark: bool
    surface: str
    card: str
    ink: str
    ink_2: str
    muted: str
    grid: str
    axis: str
    border: str
    increase: str
    decrease: str
    neutral: str
    total: str
    midpoint: str
    accent: str
    good: str
    warning: str
    critical: str


LIGHT = Theme(
    dark=False, surface="#fcfcfb", card="#ffffff", ink="#0b0b0b", ink_2="#52514e", muted="#898781",
    grid="#e1e0d9", axis="#c3c2b7", border="rgba(11,11,11,0.10)",
    increase="#2a78d6", decrease="#e34948", neutral="#898781", total="#c3c2b7", midpoint="#f0efec",
    accent="#eb6834", good="#0ca30c", warning="#fab219", critical="#d03b3b",
)
DARK = Theme(
    dark=True, surface="#1a1a19", card="#222220", ink="#ffffff", ink_2="#c3c2b7", muted="#898781",
    grid="#2c2c2a", axis="#383835", border="rgba(255,255,255,0.10)",
    increase="#3987e5", decrease="#e66767", neutral="#898781", total="#5e5d59", midpoint="#383835",
    accent="#d95926", good="#0ca30c", warning="#fab219", critical="#d03b3b",
)


def theme_for(dark: bool) -> Theme:
    return DARK if dark else LIGHT


def detect_dark(st) -> bool:
    """
    Dark if either signal says so: the browser-reported theme
    (st.context.theme, Streamlit >= 1.46) or the configured base theme.
    Both are needed: with theme.base = "dark" in config.toml, Streamlit
    still reports st.context.theme.type == "light".
    """
    try:
        theme_type = getattr(getattr(st.context, "theme", None), "type", None)
        if str(theme_type or "").lower() == "dark":
            return True
    except Exception:
        pass
    try:
        return str(st.get_option("theme.base") or "").lower() == "dark"
    except Exception:
        return False


def _selection_point(**kwargs):
    if hasattr(alt, "selection_point"):  # Altair 5+
        return alt.selection_point(**kwargs)
    kwargs.pop("nearest", None)  # pragma: no cover - Altair 4
    return alt.selection_single(nearest=True, **kwargs)


def _style(chart, t: Theme, height: int, title: str = "", subtitle: str = "", grid: bool = True):
    title_spec = alt.TitleParams(
        text=title, subtitle=subtitle, anchor="start", fontSize=14, fontWeight=600, color=t.ink,
        subtitleFontSize=12, subtitleColor=t.ink_2, offset=12, font=FONT, subtitleFont=FONT,
    ) if title else alt.Undefined
    return (
        chart.properties(height=height, width="container", title=title_spec, background="transparent")
        .configure_view(strokeWidth=0)
        .configure_axis(grid=grid, gridColor=t.grid, gridWidth=1, domainColor=t.axis, tickColor=t.axis,
                        labelColor=t.ink_2, titleColor=t.ink_2, labelFont=FONT, titleFont=FONT,
                        labelFontSize=11, titleFontSize=11, titleFontWeight=500)
        .configure_legend(labelFont=FONT, titleFont=FONT, labelColor=t.ink_2, titleColor=t.ink_2,
                          orient="top", symbolType="circle")
        .configure_title(font=FONT, color=t.ink)
    )


def fmt(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"{value / 1e9:,.2f}B"
    if magnitude >= 1e6:
        return f"{value / 1e6:,.2f}M"
    if magnitude >= 1e4:
        return f"{value / 1e3:,.1f}K"
    if magnitude >= 100:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def axis_format(values) -> str:
    """SI suffixes for large numbers, plain decimals for small ones (never 'm' for milli)."""
    largest = max((abs(v) for v in values if v is not None), default=0)
    return "~s" if largest >= 1000 else ",.2f" if largest < 10 else ",.0f"


def short(label: str, limit: int = 28) -> str:
    text = str(label)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def column_name(dimension: str) -> str:
    return dimension.split(".", 1)[-1]


# ============================================================
# CHARTS
# ============================================================

def trend_chart(report: Dict[str, Any], t: Theme = LIGHT):
    """Metric over time; normal range around the baseline; compared periods marked."""
    h = report["headline"]
    points = [
        {
            "period": p["period"], "value": p["value"], "n": p["n"],
            "state": "partial period" if p.get("partial") else "complete",
            "role": "current" if p["period"] == h["current_period"]
            else "baseline" if p["period"] == h["baseline_period"] else "",
        }
        for p in report["trend"] if p["value"] is not None
    ]
    data = alt.Data(values=points)
    fmt_y = axis_format([p["value"] for p in points])
    x = alt.X("period:T", title=None, axis=alt.Axis(format="%b %Y", labelAngle=0, tickCount=8))
    y = alt.Y("value:Q", title=h["metric"], axis=alt.Axis(format=fmt_y))

    layers = []
    if h.get("typical_change_pct") is not None and h.get("baseline") is not None:
        spread = abs(h["baseline"]) * h["typical_change_pct"] / 100
        layers.append(alt.Chart(alt.Data(values=[{
            "start": h["baseline_period"], "end": h["current_period"],
            "low": h["baseline"] - spread, "high": h["baseline"] + spread,
        }])).mark_rect(color=t.increase, opacity=0.14 if t.dark else 0.10).encode(
            x="start:T", x2="end:T", y="low:Q", y2="high:Q",
            tooltip=[alt.Tooltip("low:Q", title="Normal range from", format=",.2f"),
                     alt.Tooltip("high:Q", title="to", format=",.2f")],
        ))

    line = alt.Chart(data).transform_filter(alt.datum.state == "complete").mark_line(
        color=t.increase, strokeWidth=2).encode(x=x, y=y)
    partial = alt.Chart(data).transform_filter(alt.datum.state == "partial period").mark_point(
        color=t.muted, filled=False, size=50, strokeWidth=1.5).encode(x=x, y=y)
    hover = _selection_point(fields=["period"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    rule = alt.Chart(data).mark_rule(color=t.axis, strokeWidth=1).encode(
        x=x, opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("period:T", title="Period", format="%b %Y"),
                 alt.Tooltip("value:Q", title=h["metric"], format=",.2f"),
                 alt.Tooltip("n:Q", title="Records", format=","),
                 alt.Tooltip("state:N", title="Data")],
    ).add_params(hover)
    dots = alt.Chart(data).mark_circle(color=t.increase, size=70).encode(
        x=x, y=y, opacity=alt.condition(hover, alt.value(1), alt.value(0)))
    current_color = t.decrease if h["direction"] == "decrease" else t.increase
    compared = alt.Chart(data).transform_filter(alt.datum.role != "").mark_point(
        filled=True, size=120, stroke=t.surface, strokeWidth=2).encode(
        x=x, y=y,
        color=alt.Color("role:N", scale=alt.Scale(domain=["baseline", "current"], range=[t.neutral, current_color]),
                        legend=alt.Legend(title=None, orient="top-left", direction="horizontal")),
    )
    labels = alt.Chart(data).transform_filter(alt.datum.role != "").mark_text(
        dy=-15, fontSize=11, fontWeight=600, color=t.ink, font=FONT).encode(
        x=x, y=y, text=alt.Text("value:Q", format=".3~s" if fmt_y == "~s" else ",.2f"))
    chart = alt.layer(*layers, line, partial, rule, dots, compared, labels)
    return _style(chart, t, 300, f"{h['metric']} over time",
                  "Shaded: normal size of a period-to-period change. Hollow points: incomplete periods.")


def waterfall_chart(report: Dict[str, Any], driver: Dict[str, Any], t: Theme = LIGHT):
    """Baseline -> each segment's contribution -> current."""
    h = report["headline"]
    steps = [{"label": h.get("baseline_label", h["baseline_period"]), "delta": None, "kind": "total",
              "start": 0.0, "end": h["baseline"]}]
    running = h["baseline"]
    shown = [item for item in driver["top"] if item.get("contribution") is not None][:7]
    segments = list(shown)
    rest = (h["current"] - h["baseline"]) - sum(item["contribution"] for item in shown)
    hidden = driver["segments_total"] - len(shown)
    if hidden > 0 or abs(rest) > 1e-9 * max(abs(h["baseline"]), 1):
        total = h["current"] - h["baseline"]
        segments.append({"segment": f"Other ({hidden})" if hidden > 0 else "Other", "contribution": rest,
                         "share_of_change_pct": rest / total * 100 if total else None})
    for item in segments:
        start, end = running, running + item["contribution"]
        steps.append({"label": short(item["segment"], 22), "full": item["segment"], "delta": item["contribution"],
                      "share": item.get("share_of_change_pct"),
                      "kind": "increase" if item["contribution"] >= 0 else "decrease", "start": start, "end": end})
        running = end
    steps.append({"label": h.get("current_label", h["current_period"]), "delta": None, "kind": "total",
                  "start": 0.0, "end": h["current"]})
    for order, step in enumerate(steps):
        step["order"] = order
        step.setdefault("full", step["label"])

    data = alt.Data(values=steps)
    middle = steps[1:-1] or steps
    low = min(min(s["start"], s["end"]) for s in middle)
    high = max(max(s["start"], s["end"]) for s in steps)
    pad = (high - low) * 0.25 or abs(high) * 0.05 or 1
    y_domain = [max(0.0, low - pad) if low > 0 else low - pad, high + pad * 0.4]
    big = abs(h["baseline"] or 0) >= 1000
    x = alt.X("label:N", sort=[s["label"] for s in steps], title=None,
              axis=alt.Axis(labelAngle=-28, labelLimit=150, labelPadding=6))
    bars = alt.Chart(data).mark_bar(cornerRadius=4, size=32).encode(
        x=x,
        y=alt.Y("start:Q", title=h["metric"], scale=alt.Scale(domain=y_domain, clamp=True),
                axis=alt.Axis(format=axis_format([h["baseline"], h["current"]]))),
        y2="end:Q",
        color=alt.Color("kind:N", scale=alt.Scale(domain=["total", "increase", "decrease"],
                                                  range=[t.total, t.increase, t.decrease]),
                        legend=alt.Legend(title=None, orient="top", direction="horizontal")),
        tooltip=[alt.Tooltip("full:N", title="Step"),
                 alt.Tooltip("delta:Q", title="Contribution", format="+,.2f"),
                 alt.Tooltip("share:Q", title="Share of change (%)", format=".1f"),
                 alt.Tooltip("end:Q", title="Running total", format=",.2f")],
    )
    text = alt.Chart(data).transform_filter(alt.datum.delta != None).transform_calculate(  # noqa: E711
        top="max(datum.start, datum.end)",
        label_text="(datum.delta >= 0 ? '+' : '−') + format(abs(datum.delta), abs(datum.delta) >= 1000 ? '.3~s' : '.3~f')",
    ).mark_text(dy=-8, fontSize=11, color=t.ink_2, font=FONT).encode(x=x, y="top:Q", text="label_text:N")
    totals = alt.Chart(data).transform_filter(alt.datum.kind == "total").mark_text(
        dy=-8, fontSize=11, fontWeight=600, color=t.ink, font=FONT).encode(
        x=x, y="end:Q", text=alt.Text("end:Q", format=".3~s" if big else ".3~f"))
    return _style(alt.layer(bars, text, totals), t, 320,
                  f"Where the change came from: by {column_name(driver['dimension'])}",
                  "Each bar is one segment's contribution to the change.")


def contribution_chart(driver: Dict[str, Any], metric: str, t: Theme = LIGHT):
    """Diverging bars: actual contribution; tick: what proportional change would have given."""
    total = driver.get("total_change") or 0
    rows = []
    for item in driver["top"]:
        if item.get("contribution") is None:
            continue
        lead = item["segment"] == driver.get("leading_segment")
        rows.append({
            "segment": short(item["segment"], 30) + ("  ★" if lead else ""), "full": item["segment"],
            "contribution": item["contribution"],
            "expected": total * (item.get("baseline_share_pct") or 0) / 100,
            "direction": "increase" if item["contribution"] >= 0 else "decrease",
            "baseline": item.get("baseline"), "current": item.get("current"),
            "change_pct": item.get("change_pct"), "size": item.get("baseline_share_pct"),
        })
    # Explicit order: computed sorts are dropped when layer domains merge.
    rows.sort(key=lambda row: row["contribution"], reverse=total >= 0)
    data = alt.Data(values=rows)
    y = alt.Y("segment:N", sort=[row["segment"] for row in rows], title=None, axis=alt.Axis(labelLimit=200))
    number_format = axis_format([row["contribution"] for row in rows] + [row["expected"] for row in rows])
    tooltip = [alt.Tooltip("full:N", title="Segment"),
               alt.Tooltip("contribution:Q", title="Contribution", format="+,.2f"),
               alt.Tooltip("expected:Q", title="If it had moved in proportion", format="+,.2f"),
               alt.Tooltip("size:Q", title="Share of baseline (%)", format=".1f"),
               alt.Tooltip("baseline:Q", title="Baseline", format=",.2f"),
               alt.Tooltip("current:Q", title="Current", format=",.2f"),
               alt.Tooltip("change_pct:Q", title="Change (%)", format="+.1f")]
    bars = alt.Chart(data).mark_bar(cornerRadius=4, height=14).encode(
        y=y, x=alt.X("contribution:Q", title=f"Contribution to the change in {metric}",
                     axis=alt.Axis(format=number_format)),
        color=alt.Color("direction:N", scale=alt.Scale(domain=["increase", "decrease"], range=[t.increase, t.decrease]),
                        legend=None),
        tooltip=tooltip,
    )
    ticks = alt.Chart(data).mark_tick(color=t.ink, thickness=2, size=22).encode(y=y, x="expected:Q", tooltip=tooltip)
    zero = alt.Chart(alt.Data(values=[{"x": 0}])).mark_rule(color=t.axis).encode(x="x:Q")
    return _style(alt.layer(zero, bars, ticks), t, max(170, 30 * len(rows) + 50),
                  f"Contribution by {column_name(driver['dimension'])}",
                  "Bar: actual contribution. Tick: if the segment had moved in proportion to its size. ★ leading driver.")


def heatmap_chart(drill: Dict[str, Any], metric: str, t: Theme = LIGHT):
    cells = [{**cell, "row_short": short(cell["row"], 22), "column_short": short(cell["column"], 18)}
             for cell in drill["cells"]]
    extent = max((abs(cell["contribution"] or 0) for cell in cells), default=1) or 1
    rows_dim, cols_dim = column_name(drill["rows_dimension"]), column_name(drill["columns_dimension"])
    chart = alt.Chart(alt.Data(values=cells)).mark_rect(cornerRadius=4, stroke=t.surface, strokeWidth=3).encode(
        x=alt.X("column_short:N", sort=[short(c, 18) for c in drill["columns"]], title=cols_dim,
                axis=alt.Axis(labelAngle=-30, labelLimit=140, orient="top")),
        y=alt.Y("row_short:N", sort=[short(r, 22) for r in drill["rows"]], title=rows_dim,
                axis=alt.Axis(labelLimit=170)),
        color=alt.Color("contribution:Q", title=f"Δ {metric}",
                        scale=alt.Scale(domain=[-extent, 0, extent], range=[t.decrease, t.midpoint, t.increase]),
                        legend=alt.Legend(orient="right", format=axis_format([extent]), gradientLength=140)),
        tooltip=[alt.Tooltip("row:N", title=rows_dim), alt.Tooltip("column:N", title=cols_dim),
                 alt.Tooltip("contribution:Q", title="Contribution", format="+,.2f"),
                 alt.Tooltip("baseline:Q", title="Baseline", format=",.2f"),
                 alt.Tooltip("current:Q", title="Current", format=",.2f")],
    )
    return _style(chart, t, max(180, 40 * len(drill["rows"]) + 70), f"Drill-down: {rows_dim} × {cols_dim}",
                  "Red: lost, blue: gained, grey: no change. Hover a cell for values.", grid=False)


SIGNAL_GROUPS = {
    "average": ("Averages that moved", "Change in %"),
    "time_gap": ("Time gaps that moved", "Change in days"),
    "share": ("Status mix shifts", "Change in percentage points"),
}


def signal_rows(report: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    rows, seen = [], {}
    for signal in report.get("signals", []):
        if signal["kind"] != kind:
            continue
        label = short(signal["signal"], 64)
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:  # never let two signals share a label
            label = f"{label} ({seen[label]})"
        rows.append({
            "signal": label, "full": signal["signal"], "table": signal.get("table") or "",
            "overall": signal.get("overall_change"), "segment": signal.get("segment_change"),
            "overall_baseline": signal.get("overall_baseline"), "overall_current": signal.get("overall_current"),
            "segment_baseline": signal.get("segment_baseline"), "segment_current": signal.get("segment_current"),
            "scope": signal.get("scope", ""),
        })
    return rows


def signals_chart(report: Dict[str, Any], kind: str = "average", t: Theme = LIGHT):
    """Dumbbell per signal: ● overall change → ● change inside the leading segment."""
    rows = signal_rows(report, kind)
    if not rows:
        return None
    title, unit = SIGNAL_GROUPS[kind]
    order = [row["signal"] for row in rows]
    data = alt.Data(values=rows)
    y = alt.Y("signal:N", sort=order, title=None, axis=alt.Axis(labelLimit=360, labelPadding=8))
    tooltip = [alt.Tooltip("full:N", title="Signal"), alt.Tooltip("table:N", title="Table"),
               alt.Tooltip("overall:Q", title=f"Overall ({unit.lower()})", format="+.2f"),
               alt.Tooltip("overall_baseline:Q", title="Overall baseline", format=",.2f"),
               alt.Tooltip("overall_current:Q", title="Overall current", format=",.2f"),
               alt.Tooltip("scope:N", title="Leading segment"),
               alt.Tooltip("segment:Q", title=f"In segment ({unit.lower()})", format="+.2f"),
               alt.Tooltip("segment_baseline:Q", title="Segment baseline", format=",.2f"),
               alt.Tooltip("segment_current:Q", title="Segment current", format=",.2f")]
    zero = alt.Chart(alt.Data(values=[{"x": 0}])).mark_rule(color=t.axis).encode(x="x:Q")
    link = alt.Chart(data).transform_filter("isValid(datum.segment) && isValid(datum.overall)").mark_rule(
        color=t.axis, strokeWidth=3).encode(y=y, x="overall:Q", x2="segment:Q", tooltip=tooltip)
    folded = alt.Chart(data).transform_fold(["overall", "segment"], as_=["scope_key", "change"]).transform_filter(
        "isValid(datum.change)").transform_calculate(
        where="datum.scope_key == 'overall' ? 'overall' : 'in leading segment'")
    dots = folded.mark_circle(size=130, opacity=1, stroke=t.surface, strokeWidth=1.5).encode(
        y=y, x=alt.X("change:Q", title=unit),
        color=alt.Color("where:N", scale=alt.Scale(domain=["overall", "in leading segment"], range=[t.neutral, t.accent]),
                        legend=alt.Legend(title=None, orient="top", direction="horizontal")),
        tooltip=tooltip,
    )
    subtitle = "Grey: all records. Orange: inside the leading segment. Correlation, not proven cause."
    return _style(alt.layer(zero, link, dots), t, max(120, 36 * len(order) + 60), title, subtitle)


# ============================================================
# HTML BLOCKS
# ============================================================

def hero_html(report: Dict[str, Any], t: Theme = LIGHT) -> str:
    h = report["headline"]
    down = h["direction"] == "decrease"
    color = t.decrease if down else t.increase if h["direction"] == "increase" else t.neutral
    arrow = "▼" if down else "▲" if h["direction"] == "increase" else "■"
    pct = f"{h['change_pct']:+.1f}%" if h.get("change_pct") is not None else "n/a"
    unusual = {
        "unusual": ("Unusual change", t.critical, "⚠"),
        "notable": ("Notable change", t.warning, "!"),
        "normal": ("Within normal variation", t.good, "✓"),
        "unknown": ("Not enough history", t.neutral, "?"),
    }[h.get("unusual", "unknown")]
    typical = f"typical change ±{h['typical_change_pct']:.1f}%" if h.get("typical_change_pct") is not None else ""
    tiles = [
        ("Baseline", fmt(h["baseline"]), h.get("baseline_label", h["baseline_period"])),
        ("Current", fmt(h["current"]), h.get("current_label", h["current_period"])),
        ("Change", f"{'+' if (h['change'] or 0) >= 0 else '−'}{fmt(abs(h['change'] or 0))}", pct),
    ]
    vv = report.get("volume_value")
    if vv and vv.get("records_change_pct") is not None:
        tiles.append(("Records", f"{vv['records_change_pct']:+.1f}%", f"{vv['records_baseline']:,} → {vv['records_current']:,}"))
        tiles.append(("Value per record", f"{vv['value_per_record_change_pct']:+.1f}%",
                      f"{fmt(vv['value_per_record_baseline'])} → {fmt(vv['value_per_record_current'])}"))
    tile_html = "".join(
        f'<div style="flex:1;min-width:120px;padding:10px 14px;border-left:1px solid {t.border}">'
        f'<div style="font-size:12px;color:{t.ink_2}">{html.escape(label)}</div>'
        f'<div style="font-size:20px;font-weight:650;color:{t.ink};line-height:1.25">{html.escape(value)}</div>'
        f'<div style="font-size:12px;color:{t.ink_2}">{html.escape(sub)}</div></div>'
        for label, value, sub in tiles
    )
    return f"""
<div style="font-family:{FONT_HTML};border:1px solid {t.border};border-radius:16px;background:{t.card};padding:18px 18px 14px;margin:6px 0 14px">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap">
    <div>
      <div style="font-size:13px;color:{t.ink_2};text-transform:uppercase;letter-spacing:.04em">{html.escape(h['metric'])}</div>
      <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
        <span style="font-size:38px;font-weight:700;color:{t.ink};line-height:1.1">{html.escape(fmt(h['current']))}</span>
        <span style="font-size:18px;font-weight:650;color:{color}">{arrow} {html.escape(pct)}</span>
      </div>
      <div style="font-size:13px;color:{t.ink_2}">{html.escape(h.get('current_label', ''))} vs {html.escape(h.get('baseline_label', ''))}
        · {html.escape(h.get('period_choice', ''))}</div>
    </div>
    <div style="display:inline-flex;align-items:center;gap:8px;border:1px solid {unusual[1]}66;border-radius:999px;padding:6px 12px;background:{t.surface}">
      <span style="color:{unusual[1]};font-weight:700">{unusual[2]}</span>
      <span style="font-size:13px;font-weight:600;color:{t.ink}">{unusual[0]}</span>
      <span style="font-size:12px;color:{t.ink_2}">{html.escape(typical)}</span>
    </div>
  </div>
  <div style="display:flex;flex-wrap:wrap;margin-top:14px;border-top:1px solid {t.border};padding-top:8px">{tile_html}</div>
</div>"""


def trust_html(trust: Dict[str, Any], t: Theme = LIGHT) -> str:
    level = trust.get("level", "medium")
    color = {"high": t.good, "medium": t.warning, "low": t.critical}.get(level, t.neutral)
    icon = {"high": "✓", "medium": "!", "low": "⚠"}.get(level, "?")
    score = int(trust.get("score") or 0)
    return f"""
<div style="font-family:{FONT_HTML};display:flex;align-items:center;gap:14px;flex-wrap:wrap;border:1px solid {color}66;border-radius:12px;padding:10px 14px;background:{t.card};margin:4px 0 10px">
  <span style="font-weight:700;color:{t.ink}"><span style="color:{color}">{icon}</span> {level.capitalize()} confidence</span>
  <div style="flex:1;min-width:160px;max-width:300px;height:8px;background:{t.midpoint};border-radius:99px;overflow:hidden">
    <div style="width:{score}%;height:100%;background:{color}"></div></div>
  <span style="font-size:12px;color:{t.ink_2}">{score}/100 · numbers in the explanation are checked against the computed facts</span>
</div>"""


def hypotheses_html(narrative: Dict[str, Any], t: Theme = LIGHT) -> str:
    cards = []
    for item in narrative.get("hypotheses") or []:
        hypothesis, evidence = (item.get("hypothesis", ""), item.get("evidence", "")) if isinstance(item, dict) else (str(item), "")
        cards.append(
            f'<div style="flex:1;min-width:240px;border:1px solid {t.border};border-radius:12px;padding:12px 14px;background:{t.card}">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:.05em;color:{t.accent};text-transform:uppercase">Hypothesis to test</div>'
            f'<div style="font-size:14px;color:{t.ink};margin:4px 0 6px">{html.escape(hypothesis)}</div>'
            f'<div style="font-size:12px;color:{t.ink_2}"><b>Evidence:</b> {html.escape(evidence)}</div></div>'
        )
    return (f'<div style="font-family:{FONT_HTML};display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 4px">{"".join(cards)}</div>'
            if cards else "")


def actions_html(narrative: Dict[str, Any], t: Theme = LIGHT) -> str:
    """'How to improve': numbered action cards."""
    cards = []
    for number, item in enumerate(narrative.get("actions") or [], start=1):
        action, why = (item.get("action", ""), item.get("why", "")) if isinstance(item, dict) else (str(item), "")
        if not action:
            continue
        cards.append(
            f'<div style="display:flex;gap:12px;align-items:flex-start;border:1px solid {t.border};border-left:4px solid {t.good};'
            f'border-radius:12px;padding:12px 14px;background:{t.card}">'
            f'<div style="flex:none;width:26px;height:26px;border-radius:50%;background:{t.good};color:#fff;font-weight:700;'
            f'font-size:13px;display:flex;align-items:center;justify-content:center">{number}</div>'
            f'<div><div style="font-size:14px;font-weight:600;color:{t.ink}">{html.escape(action)}</div>'
            + (f'<div style="font-size:12px;color:{t.ink_2};margin-top:3px"><b>Why:</b> {html.escape(why)}</div>' if why else "")
            + "</div></div>"
        )
    return (f'<div style="font-family:{FONT_HTML};display:flex;flex-direction:column;gap:8px;margin:6px 0 4px">{"".join(cards)}</div>'
            if cards else "")


def reasons_html(report: Dict[str, Any], t: Theme = LIGHT) -> str:
    """'Real reason': the checked causes, strongest first, plus what was ruled out."""
    colors = {"Most likely": t.accent, "Likely": t.warning, "Possible": t.neutral}
    cards = []
    for item in report.get("reasons") or []:
        likelihood = item.get("likelihood", "Possible")
        color = colors.get(likelihood, t.neutral)
        strength = max(0, min(100, int(item.get("strength") or 0)))
        cards.append(
            f'<div style="border:1px solid {t.border};border-left:4px solid {color};border-radius:12px;padding:12px 14px;background:{t.card}">'
            f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
            f'<span style="font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#fff;'
            f'background:{color};border-radius:99px;padding:2px 9px">{html.escape(likelihood)}</span>'
            f'<span style="font-size:15px;font-weight:650;color:{t.ink}">{html.escape(str(item.get("title", "")))}</span></div>'
            f'<div style="font-size:13px;color:{t.ink_2};margin:6px 0 8px">{html.escape(str(item.get("detail", "")))}</div>'
            f'<div style="display:flex;align-items:center;gap:8px"><div style="flex:1;max-width:260px;height:6px;'
            f'background:{t.midpoint};border-radius:99px;overflow:hidden"><div style="width:{strength}%;height:100%;'
            f'background:{color}"></div></div><span style="font-size:11px;color:{t.muted}">evidence strength {strength}/100</span>'
            f'</div></div>'
        )
    ruled = []
    for item in report.get("ruled_out") or []:
        ruled.append(
            f'<div style="font-size:12px;color:{t.ink_2};padding:3px 0"><span style="color:{t.good};font-weight:700">✗</span> '
            f'<b style="color:{t.ink}">{html.escape(str(item.get("title", "")))}</b>: {html.escape(str(item.get("detail", "")))}</div>'
        )
    if not cards and not ruled:
        return ""
    ruled_html = (f'<div style="border:1px dashed {t.border};border-radius:12px;padding:8px 14px;background:{t.card}">'
                  f'<div style="font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:{t.muted};'
                  f'margin-bottom:2px">Checked, not the reason</div>{"".join(ruled)}</div>') if ruled else ""
    return (f'<div style="font-family:{FONT_HTML};display:flex;flex-direction:column;gap:8px;margin:6px 0 4px">'
            f'{"".join(cards)}{ruled_html}</div>')


# ============================================================
# STREAMLIT VIEW
# ============================================================

def render_investigation(st, report: Dict[str, Any], ask=None) -> None:
    """ask(question): optional; makes the next questions clickable."""
    t = theme_for(detect_dark(st))

    def chart(c):
        if c is not None:
            st.altair_chart(c, use_container_width=True, theme=None)

    st.divider()
    st.subheader("🔎 Investigation")
    st.caption(report.get("question", ""))

    st.markdown(hero_html(report, t), unsafe_allow_html=True)
    trust = report.get("trust") or {}
    if trust:
        st.markdown(trust_html(trust, t), unsafe_allow_html=True)

    narrative = report.get("narrative") or {}
    if narrative.get("real_reason") or report.get("reasons") or report.get("ruled_out"):
        st.markdown("#### 🧠 Real reason")
        if narrative.get("real_reason"):
            st.markdown(narrative["real_reason"])
        st.markdown(reasons_html(report, t), unsafe_allow_html=True)
        st.caption("Each reason was checked with SQL on your data: the period before, the same time last year, "
                   "new vs returning customers, and other numbers that changed at the same time.")

    st.markdown("#### What happened")
    if report.get("narrative_source") == "computed":
        st.caption("AI explanation unavailable" + (f" ({report['narrative_error']})" if report.get("narrative_error") else "")
                   + ", so this answer is built directly from the computed facts.")
    if narrative.get("summary"):
        st.markdown(narrative["summary"])
        claims = report.get("claim_check") or {}
        if claims.get("unverified_numbers"):
            st.warning("Not found in the computed facts, treat as unverified: " + ", ".join(claims["unverified_numbers"]))
    else:
        st.info("The written explanation is unavailable; the key facts below come straight from the data.")

    if narrative.get("drivers"):
        st.markdown("#### Why: main drivers")
        for line in narrative["drivers"]:
            st.markdown(f"- {line}")

    chart(trend_chart(report, t))

    drivers = report.get("drivers") or []
    if drivers:
        st.markdown("#### Where it came from")
        options = [column_name(d["dimension"]) for d in drivers]
        choice = st.radio("Break down by", options, horizontal=True, key=f"inv_dim_{hash(report.get('question'))}",
                          help="Ranked by how much of the change their segments explain beyond their size.")
        driver = drivers[options.index(choice)]
        h = report["headline"]
        if driver.get("total_change") is not None and h.get("baseline") is not None and h.get("current") is not None:
            left, right = st.columns([1.1, 1])
            with left:
                chart(waterfall_chart(report, driver, t))
            with right:
                chart(contribution_chart(driver, report["headline"]["metric"], t))
            if driver.get("mix_effect") is not None:
                st.caption(f"Mix effect {driver['mix_effect']:+.4g} (segments' weights shifted) · "
                           f"rate effect {driver['rate_effect']:+.4g} (values within segments changed)")
        else:
            st.dataframe(driver["top"], use_container_width=True)

    drill = report.get("drilldown")
    if drill and drill.get("cells"):
        chart(heatmap_chart(drill, report["headline"]["metric"], t))

    signal_charts = [signals_chart(report, kind, t) for kind in ("average", "time_gap", "share")]
    if any(c is not None for c in signal_charts):
        st.markdown("#### What moved with it")
        for c in signal_charts:
            chart(c)

    if narrative.get("actions"):
        st.markdown("#### ✅ How to improve")
        st.markdown(actions_html(narrative, t), unsafe_allow_html=True)
        st.caption("Suggested from the drivers above; check them against what you know about the business.")

    if narrative.get("hypotheses"):
        st.markdown("#### What could explain it, and what to try")
        st.markdown(hypotheses_html(narrative, t), unsafe_allow_html=True)
        st.caption("Hypotheses come from patterns in the data; confirm them before acting.")
    if narrative.get("next_steps"):
        st.markdown("#### Next questions")
        if ask is None:
            for line in narrative["next_steps"]:
                st.markdown(f"- {line}")
        else:
            st.caption("Click a question to ask it.")
            for number, line in enumerate(narrative["next_steps"]):
                if st.button(f"💬 {line}", key=f"inv_next_{hash(report.get('question'))}_{number}",
                             use_container_width=True):
                    ask(str(line))

    with st.expander("Key facts (computed directly from the data)"):
        for fact in report.get("facts") or []:
            st.markdown(f"- {fact}")
    with st.expander("Why this confidence level?"):
        for reason in trust.get("reasons") or []:
            effect = reason.get("effect", 0)
            st.markdown(f"- **{effect:+d}** {reason['detail']}" if effect else f"- {reason['detail']}")
    with st.expander(f"Evidence: {len(report.get('queries') or [])} read-only SQL queries"):
        plan = report.get("plan") or {}
        st.caption(f"Metric: {plan.get('expression')} ({plan.get('metric_kind')}) · time: {plan.get('time_column')} "
                   f"by {plan.get('grain')}" + (f" · filter: {plan['filters']}" if plan.get("filters") else ""))
        for note in plan.get("notes") or []:
            st.caption(note)
        for query in report.get("queries") or []:
            st.markdown(f"**{query['purpose']}**")
            st.code(query["sql"], language="sql")
