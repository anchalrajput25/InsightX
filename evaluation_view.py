# evaluation_view.py
#
# Benchmark page: explores the reports written by
#   python -m backend.evaluation ... run
# (logs/evaluation/*.json). It only reads files, it never calls the
# LLM or the database.
#
# Layout, top to bottom:
#   hero        execution accuracy with its 95% confidence interval,
#               plus tiles for the other behaviours (declining
#               unanswerable questions, refusing destructive ones),
#               trust precision, latency and cost
#   variants    (ablation runs) accuracy with CI per variant, the
#               accuracy-vs-cost trade-off, paired significance tests
#   accuracy    by difficulty and by question type, with CIs
#   trust       does the trust score mean what it says? (reliability
#               diagram + accuracy per trust level)
#   failures    why cases failed (automatic diagnosis)
#   speed       where the time goes (pipeline stages) and latency spread
#   cases       filterable table; one case in detail (expected vs
#               generated SQL, diagnosis, cost)
#
# Chart builders are pure functions (report dict in, Altair chart out)
# so they can be rendered and tested without Streamlit. Same visual
# system as the investigation view (theme-aware, validated palette).

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import altair as alt

from investigation_view import FONT_HTML, LIGHT, Theme, _style, detect_dark, theme_for

# Categorical slots of the validated palette, fixed order (light, dark).
SERIES = {
    False: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    True: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}
DIFFICULTY_ORDER = ["easy", "medium", "hard", "extra"]
DIFFICULTY_HELP = {
    "easy": "one table, at most one filter or aggregate",
    "medium": "a join or grouping",
    "hard": "several joins, nested query or temporal logic",
    "extra": "window functions, ratios over joins, multi-step",
}
CAUSES = {
    "pipeline_error": "Pipeline stopped with an error",
    "clarification": "Asked for clarification",
    "timeout": "Timed out",
    "retrieval_miss": "Retrieval missed a needed table",
    "missing_table": "SQL skipped a needed table",
    "wrong_join": "Joined extra tables",
    "wrong_filter": "Wrong filter value or condition",
    "wrong_aggregation": "Wrong aggregate (SUM/AVG/COUNT…)",
    "wrong_grouping": "Wrong grouping",
    "wrong_limit": "Wrong LIMIT / OFFSET",
    "wrong_order": "Right rows, wrong order",
    "missing_columns": "Missing result columns",
    "wrong_calculation": "Different calculation in result",
    "wrong_values": "Values differ (cause unclear)",
    "hallucinated_answer": "Answered a question the data can't answer",
    "data_modified": "Data was modified",
}
STAGES = {
    "rewrite": "Question rewriting", "decompose": "Decomposition", "retrieve": "Schema retrieval",
    "schema_link": "Schema linking", "generate_sql": "SQL generation", "select_sql": "Candidate selection",
    "clarification": "Clarification", "safety": "Safety check", "validate_sql": "SQL validation",
    "correction": "SQL correction", "execute": "Execution", "validate_result": "Result checks",
    "generate_answer": "Answer writing",
}


# ============================================================
# REPORT ACCESS
# ============================================================

# A run's progress file counts as "running" while it was written to recently.
RUNNING_WINDOW_S = 10 * 60


def list_reports(directory: Path) -> List[Path]:
    """
    Finished reports (*.json) and the progress files of runs that are
    still going or were stopped (*.partial.jsonl). A progress file is
    hidden once a finished report of the same question set is newer.
    """
    directory = Path(directory)
    if not directory.exists():
        return []
    finished = [p for p in directory.glob("*.json")]
    partials = []
    for p in directory.glob("*.partial.jsonl"):
        golden = p.name.split("-")[0]
        newer = [f for f in finished if f.name.split("-")[0] == golden and f.stat().st_mtime >= p.stat().st_mtime]
        if not newer and p.stat().st_size:
            partials.append(p)
    return sorted(finished + partials, key=lambda p: p.stat().st_mtime, reverse=True)


def load_report(path: Path, golden_dir: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(path)
    if path.name.endswith(".partial.jsonl"):
        return load_partial_report(path, golden_dir)
    return json.loads(path.read_text(encoding="utf-8"))


def load_partial_report(path: Path, golden_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Live report from a run's progress file (one JSON row per answered question)."""
    import time
    from dataclasses import asdict
    from datetime import datetime, timezone

    from backend.evaluation.runner import CaseResult, summarize

    allowed = set(CaseResult.__dataclass_fields__)
    latest: Dict[tuple, Dict[str, Any]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        latest[(row.get("variant", "baseline"), int(row.get("repeat", 1)), row.get("id"))] = {
            k: v for k, v in row.items() if k in allowed}
    by_variant: Dict[str, List[Any]] = {}
    for (variant, _repeat, _id), row in latest.items():
        by_variant.setdefault(variant, []).append(CaseResult(**row))

    golden_name = path.name.split("-")[0]
    total = None
    if golden_dir is not None and (Path(golden_dir) / f"{golden_name}.yaml").exists():
        try:
            from backend.evaluation.golden import load_golden_set

            total = len(load_golden_set(Path(golden_dir) / f"{golden_name}.yaml").selected())
        except Exception:
            total = None
    modified = path.stat().st_mtime
    running = time.time() - modified < RUNNING_WINDOW_S
    variants = [{"name": name, "label": name, "changes": {}, "settings": {}, "summary": summarize(results),
                 "cases": [asdict(r) for r in results]} for name, results in by_variant.items()]
    answered = len(latest)
    return {
        "golden_set": golden_name,
        "run_at": datetime.fromtimestamp(modified, timezone.utc).isoformat(timespec="seconds"),
        "case_count": total,
        "repeats": max((key[1] for key in latest), default=1),
        "variants": variants if len(variants) > 1 else [],
        "summary": variants[0]["summary"] if variants else {},
        "cases": variants[0]["cases"] if variants else [],
        "live": {"running": running, "answered": answered, "total": total, "file": str(path)},
    }


def variants_of(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Benchmark reports carry variants; single-run reports are one variant."""
    if report.get("variants"):
        return report["variants"]
    return [{"name": "run", "label": "run", "changes": {}, "settings": {},
             "summary": report.get("summary") or {}, "cases": report.get("cases") or []}]


def report_label(path: Path, report: Dict[str, Any]) -> str:
    live = report.get("live")
    if live:
        progress = f"{live['answered']}/{live['total']}" if live.get("total") else f"{live['answered']}"
        state = "⏳ running" if live["running"] else "⏸ stopped"
        return f"{report.get('golden_set')} · {state} · {progress} questions answered"
    variants = variants_of(report)
    when = str(report.get("run_at", ""))[:16].replace("T", " ")
    count = report.get("case_count") or variants[0]["summary"].get("cases")
    extra = f" · {len(variants)} variants" if len(variants) > 1 else ""
    repeats = f" · ×{report['repeats']}" if (report.get("repeats") or 1) > 1 else ""
    return f"{report.get('golden_set', path.stem)} · {when} UTC · {count} cases{extra}{repeats}"


def _pct(value: Optional[float], digits: int = 0) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _secs(value: Optional[float]) -> str:
    return "n/a" if value is None else (f"{value:.1f} s" if value < 100 else f"{value:.0f} s")


def _money(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"${value:.4f}" if value < 0.1 else f"${value:.2f}"


# ============================================================
# HTML: hero + tiles
# ============================================================

def _tile(label: str, value: str, sub: str, t: Theme, icon: str = "", icon_color: str = "") -> str:
    mark = (f'<span style="color:{icon_color};font-weight:700;margin-right:6px">{html.escape(icon)}</span>'
            if icon else "")
    return (
        f'<div style="background:{t.card};border:1px solid {t.border};border-radius:10px;padding:12px 14px;'
        f'min-width:150px;flex:1">'
        f'<div style="font-size:12px;color:{t.ink_2}">{html.escape(label)}</div>'
        f'<div style="font-size:22px;font-weight:600;color:{t.ink};margin-top:4px">{mark}{html.escape(value)}</div>'
        f'<div style="font-size:12px;color:{t.ink_2};margin-top:2px">{html.escape(sub)}</div></div>'
    )


def _status(rate: Optional[float], t: Theme):
    if rate is None:
        return "", ""
    if rate >= 0.999:
        return "✓", t.good
    if rate >= 0.8:
        return "!", t.warning
    return "✕", t.critical


def hero_html(variant: Dict[str, Any], report: Dict[str, Any], t: Theme = LIGHT) -> str:
    s = variant["summary"]
    ci = s.get("execution_accuracy_ci") or [None, None]
    accuracy = s.get("execution_accuracy")
    scored = s.get("scored_cases", 0)
    correct = round((accuracy or 0) * scored)
    ci_text = (f"95% confidence interval {_pct(ci[0])} – {_pct(ci[1])}" if ci[0] is not None else "")
    reps = report.get("repeats") or 1
    basis = f"{correct} of {scored} answerable questions" if reps == 1 else f"{scored} answers over {reps} repeats"

    abstain_icon, abstain_color = _status(s.get("abstention_rate"), t)
    safety_icon, safety_color = _status(s.get("safety_pass_rate"), t)
    tiles = [
        _tile("Declines unanswerable questions", _pct(s.get("abstention_rate")),
              f"{s.get('abstention_cases', 0)} questions the data can't answer", t, abstain_icon, abstain_color),
        _tile("Destructive requests handled safely", _pct(s.get("safety_pass_rate")),
              f"{s.get('safety_cases', 0)} requests · {s.get('write_sql_blocked', 0)} write SQL blocked",
              t, safety_icon, safety_color),
        _tile("Accuracy when trust is high", _pct(s.get("high_trust_accuracy")),
              f"trust was high on {_pct(s.get('high_trust_coverage'))} of questions", t),
        _tile("Typical answer time", _secs(s.get("latency_p50_s")),
              f"p95 {_secs(s.get('latency_p95_s'))} · "
              + (f"{s['llm_calls_per_question']:.1f} LLM calls" if s.get("llm_calls_per_question") is not None else ""), t),
        _tile("Cost per question", _money(s.get("cost_per_question_usd")),
              (f"{_money(s.get('cost_per_correct_answer_usd'))} per correct answer"
               + ("" if s.get("cost_complete", True) else " · some calls unpriced")), t),
    ]
    meta = " · ".join(filter(None, [
        report.get("database") or "", f"{report.get('duration_s')} s run" if report.get("duration_s") else "",
        f"workers {report.get('workers')}" if (report.get("workers") or 1) > 1 else "",
    ]))
    stopped = (f'<div style="margin-top:10px;color:{t.critical};font-size:13px">⚠ Stopped early: '
               f'{html.escape(report["stopped"])}</div>' if report.get("stopped") else "")
    if s.get("infrastructure_errors"):
        stopped += (f'<div style="margin-top:6px;color:{t.warning};font-size:13px">! {s["infrastructure_errors"]} '
                    f'question(s) crashed (network or provider error) and count as wrong.</div>')
    return (
        f'<div style="font-family:{FONT_HTML};background:{t.surface};border:1px solid {t.border};'
        f'border-radius:14px;padding:18px 20px">'
        f'<div style="display:flex;flex-wrap:wrap;gap:24px;align-items:flex-end">'
        f'<div style="min-width:220px">'
        f'<div style="font-size:13px;color:{t.ink_2}">Execution accuracy · {html.escape(variant["label"])}</div>'
        f'<div style="font-size:52px;font-weight:600;line-height:1.05;color:{t.ink}">{_pct(accuracy, 1)}</div>'
        f'<div style="font-size:13px;color:{t.ink_2}">{html.escape(basis)}</div>'
        f'<div style="font-size:12px;color:{t.muted}">{html.escape(ci_text)}</div></div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:10px;flex:1">{"".join(tiles)}</div></div>'
        f'<div style="font-size:12px;color:{t.muted};margin-top:10px">{html.escape(meta)}</div>{stopped}</div>'
    )


def stability_html(summary: Dict[str, Any], t: Theme = LIGHT) -> str:
    if summary.get("stable_rate") is None:
        return ""
    tiles = [
        _tile("Same outcome on every repeat", _pct(summary["stable_rate"]), "share of questions", t),
        _tile("Right at least once (pass@k)", _pct(summary["pass_at_k"]), "ceiling with retries", t),
        _tile("Right every time (pass^k)", _pct(summary["pass_all_k"]), "what users can rely on", t),
        _tile("Accuracy spread across repeats",
              "±" + _pct(summary.get("repeat_accuracy_std"), 1) if summary.get("repeat_accuracy_std") is not None else "n/a",
              "standard deviation", t),
    ]
    return (f'<div style="font-family:{FONT_HTML};display:flex;flex-wrap:wrap;gap:10px">{"".join(tiles)}</div>')


# ============================================================
# CHARTS
# ============================================================

def _interval_rows(groups: Dict[str, Dict[str, Any]], order: Optional[List[str]] = None,
                   labels: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    names = [n for n in (order or []) if n in groups] or list(groups)
    rows = []
    for name in names:
        g = groups[name]
        if g.get("accuracy") is None:
            continue
        rows.append({
            "group": (labels or {}).get(name, name), "key": name, "cases": g["cases"],
            "accuracy": g["accuracy"] * 100,
            "low": (g["ci_low"] if g.get("ci_low") is not None else g["accuracy"]) * 100,
            "high": (g["ci_high"] if g.get("ci_high") is not None else g["accuracy"]) * 100,
            "label": f"{g['accuracy'] * 100:.0f}%  ·  n={g['cases']}",
        })
    return rows


def _interval_chart(rows, t: Theme, title: str, subtitle: str, color: str, label_limit: int = 220):
    """Horizontal bars from 0 with a 95% CI whisker and a direct value label."""
    if not rows:
        return None
    order = [row["group"] for row in rows]
    data = alt.Data(values=rows)
    y = alt.Y("group:N", sort=order, title=None, axis=alt.Axis(labelLimit=label_limit, labelPadding=8))
    x_scale = alt.Scale(domain=[0, 100], nice=False)
    tooltip = [alt.Tooltip("group:N", title="Group"), alt.Tooltip("cases:Q", title="Questions"),
               alt.Tooltip("accuracy:Q", title="Accuracy %", format=".1f"),
               alt.Tooltip("low:Q", title="95% CI low", format=".1f"),
               alt.Tooltip("high:Q", title="95% CI high", format=".1f")]
    bars = alt.Chart(data).mark_bar(color=color, cornerRadiusEnd=4, height=16).encode(
        y=y, x=alt.X("accuracy:Q", scale=x_scale, title="Accuracy (%)"), tooltip=tooltip)
    whisker = alt.Chart(data).mark_rule(color=t.ink_2, strokeWidth=1.5).encode(
        y=y, x="low:Q", x2="high:Q", tooltip=tooltip)
    caps = alt.Chart(data).transform_fold(["low", "high"], as_=["end", "value"]).mark_tick(
        color=t.ink_2, thickness=1.5, size=10, orient="vertical").encode(y=y, x="value:Q")
    text = alt.Chart(data).mark_text(align="left", dx=6, color=t.ink_2, fontSize=11).encode(
        y=y, x="high:Q", text="label:N")
    return _style(alt.layer(bars, whisker, caps, text), t, max(110, 34 * len(rows) + 50), title, subtitle)


def difficulty_chart(summary: Dict[str, Any], t: Theme = LIGHT):
    rows = _interval_rows(summary.get("by_difficulty") or {}, DIFFICULTY_ORDER)
    for row in rows:
        row["group"] = f"{row['key']} – {DIFFICULTY_HELP.get(row['key'], '')}"
    return _interval_chart(rows, t, "Accuracy by difficulty",
                           ["Whiskers: 95% confidence interval.", "Few questions in a group = wide interval."],
                           SERIES[t.dark][0], label_limit=320)


def category_chart(summary: Dict[str, Any], t: Theme = LIGHT, minimum_cases: int = 2, limit: int = 8):
    groups = {k: v for k, v in (summary.get("by_tag_ci") or {}).items() if v.get("cases", 0) >= minimum_cases}
    order = sorted(groups, key=lambda k: (groups[k]["accuracy"] or 0, -groups[k]["cases"]))
    shown = order[:limit]
    subtitle = [f"Types with at least {minimum_cases} questions, weakest first."]
    if len(order) > limit:
        subtitle.append(f"All {len(order)} types are listed under 'Accuracy for every question type'.")
    return _interval_chart(_interval_rows(groups, shown), t, "Weakest question types", subtitle, SERIES[t.dark][0])


def category_rows(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    groups = summary.get("by_tag_ci") or {}
    rows = [{"Question type": k, "Questions": v["cases"], "Accuracy": _pct(v["accuracy"], 1),
             "95% CI": f"{_pct(v.get('ci_low'))} – {_pct(v.get('ci_high'))}", "_sort": v["accuracy"] or 0}
            for k, v in groups.items()]
    rows.sort(key=lambda r: (r["_sort"], -r["Questions"]))
    for row in rows:
        row.pop("_sort")
    return rows


def failures_chart(summary: Dict[str, Any], t: Theme = LIGHT):
    causes = summary.get("diagnoses") or {}
    if not causes:
        return None
    total = sum(causes.values())
    rows = [{"cause": CAUSES.get(k, k), "key": k, "count": v, "share": v / total * 100,
             "label": f"{v}  ({v / total * 100:.0f}%)"} for k, v in causes.items()]
    rows.sort(key=lambda r: -r["count"])
    order = [r["cause"] for r in rows]
    data = alt.Data(values=rows)
    y = alt.Y("cause:N", sort=order, title=None, axis=alt.Axis(labelLimit=280, labelPadding=8))
    tooltip = [alt.Tooltip("cause:N", title="Likely cause"), alt.Tooltip("count:Q", title="Failed questions"),
               alt.Tooltip("share:Q", title="% of failures", format=".0f")]
    bars = alt.Chart(data).mark_bar(color=t.decrease, cornerRadiusEnd=4, height=16).encode(
        y=y, x=alt.X("count:Q", title="Failed questions", axis=alt.Axis(tickMinStep=1, format="d")), tooltip=tooltip)
    text = alt.Chart(data).mark_text(align="left", dx=6, color=t.ink_2, fontSize=11).encode(
        y=y, x="count:Q", text="label:N")
    return _style(alt.layer(bars, text), t, max(110, 30 * len(rows) + 50), "Why questions failed",
                  [f"{total} failures, diagnosed by comparing generated and expected SQL.",
                   "A starting point to confirm, not proof."])


def failure_stage_chart(summary: Dict[str, Any], t: Theme = LIGHT):
    stages = summary.get("failure_stages") or {}
    if not stages:
        return None
    rows = sorted(({"stage": STAGES.get(k, k), "count": v} for k, v in stages.items()), key=lambda r: -r["count"])
    data = alt.Data(values=rows)
    y = alt.Y("stage:N", sort=[r["stage"] for r in rows], title=None, axis=alt.Axis(labelPadding=8))
    bars = alt.Chart(data).mark_bar(color=t.decrease, cornerRadiusEnd=4, height=14).encode(
        y=y, x=alt.X("count:Q", title="Questions", axis=alt.Axis(tickMinStep=1, format="d")),
        tooltip=[alt.Tooltip("stage:N", title="Stage"), alt.Tooltip("count:Q", title="Questions stopped here")])
    text = alt.Chart(data).mark_text(align="left", dx=6, color=t.ink_2, fontSize=11).encode(y=y, x="count:Q", text="count:Q")
    return _style(alt.layer(bars, text), t, max(150, 34 * len(rows) + 90), "Where the pipeline stopped",
                  "Failed questions that ended in an error, by stage.")


def calibration_chart(summary: Dict[str, Any], t: Theme = LIGHT):
    calibration = summary.get("trust_calibration") or {}
    bins = calibration.get("bins") or []
    if not bins:
        return None
    rows = [{"confidence": b["confidence"] * 100, "accuracy": b["accuracy"] * 100, "count": b["count"],
             "range": f"{b['low'] * 100:.0f}–{b['high'] * 100:.0f}"} for b in bins]
    data = alt.Data(values=rows)
    scale = alt.Scale(domain=[0, 100], nice=False)
    diagonal = alt.Chart(alt.Data(values=[{"x": 0, "y": 0}, {"x": 100, "y": 100}])).mark_line(
        color=t.axis, strokeDash=[4, 4], strokeWidth=1.5).encode(x=alt.X("x:Q", scale=scale), y=alt.Y("y:Q", scale=scale))
    tooltip = [alt.Tooltip("range:N", title="Trust score"), alt.Tooltip("count:Q", title="Questions"),
               alt.Tooltip("confidence:Q", title="Mean trust score", format=".0f"),
               alt.Tooltip("accuracy:Q", title="Actually correct %", format=".0f")]
    line = alt.Chart(data).mark_line(color=SERIES[t.dark][0], strokeWidth=2).encode(
        x=alt.X("confidence:Q", scale=scale, title="Trust score given (0–100)"),
        y=alt.Y("accuracy:Q", scale=scale, title="Actually correct (%)"))
    points = alt.Chart(data).mark_circle(color=SERIES[t.dark][0], opacity=1, stroke=t.surface, strokeWidth=2).encode(
        x="confidence:Q", y="accuracy:Q",
        size=alt.Size("count:Q", scale=alt.Scale(range=[80, 600]), legend=None), tooltip=tooltip)
    label = alt.Chart(alt.Data(values=[{"x": 4, "y": 12, "t": "perfectly calibrated"}])).mark_text(
        color=t.muted, fontSize=11, align="left", baseline="bottom").encode(x="x:Q", y="y:Q", text="t:N")
    ece = calibration.get("ece")
    covered = (f" Covers {calibration['scored']} of {calibration['total']} questions (the rest got no score)."
               if calibration.get("total") and calibration.get("scored") != calibration.get("total") else "")
    subtitle = ["Dashed line = the score means what it says; below it = over-confident.",
                f"Calibration error {ece:.2f} (0 is perfect). Dot size = number of questions.{covered}"]
    return _style(alt.layer(diagonal, line, points, label), t, 300, "Does the trust score mean what it says?", subtitle)


def trust_levels_chart(summary: Dict[str, Any], t: Theme = LIGHT):
    by_trust, counts = summary.get("accuracy_by_trust") or {}, summary.get("trust_counts") or {}
    styles = {"high": ("✓ high", t.good), "medium": ("! medium", t.warning), "low": ("✕ low", t.critical)}
    rows = [{"level": styles[k][0], "accuracy": by_trust[k] * 100, "count": counts.get(k, 0),
             "label": f"{by_trust[k] * 100:.0f}% correct  ·  n={counts.get(k, 0)}"}
            for k in ("high", "medium", "low") if by_trust.get(k) is not None]
    if not rows:
        return None
    data = alt.Data(values=rows)
    order = [r["level"] for r in rows]
    y = alt.Y("level:N", sort=order, title=None, axis=alt.Axis(labelPadding=8))
    color = alt.Color("level:N", scale=alt.Scale(domain=[styles[k][0] for k in styles],
                                                 range=[styles[k][1] for k in styles]), legend=None)
    tooltip = [alt.Tooltip("level:N", title="Trust level"), alt.Tooltip("count:Q", title="Questions"),
               alt.Tooltip("accuracy:Q", title="Correct %", format=".0f")]
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=4, height=16).encode(
        y=y, x=alt.X("accuracy:Q", scale=alt.Scale(domain=[0, 100]), title="Correct (%)"), color=color, tooltip=tooltip)
    text = alt.Chart(data).mark_text(align="left", dx=6, color=t.ink_2, fontSize=11).encode(
        y=y, x="accuracy:Q", text="label:N")
    return _style(alt.layer(bars, text), t, 150, "Accuracy per trust level",
                  "A useful trust signal: high ≫ medium ≫ low.")


def stage_chart(summary: Dict[str, Any], t: Theme = LIGHT):
    stages = summary.get("stage_mean_s") or {}
    rows = [{"stage": STAGES.get(k, k), "seconds": v, "label": f"{v:.2f} s"} for k, v in stages.items() if v]
    if not rows:
        return None
    rows.sort(key=lambda r: -r["seconds"])
    total = sum(r["seconds"] for r in rows)
    for r in rows:
        r["share"] = r["seconds"] / total * 100
    data = alt.Data(values=rows)
    y = alt.Y("stage:N", sort=[r["stage"] for r in rows], title=None, axis=alt.Axis(labelPadding=8))
    tooltip = [alt.Tooltip("stage:N", title="Stage"), alt.Tooltip("seconds:Q", title="Mean seconds", format=".2f"),
               alt.Tooltip("share:Q", title="% of pipeline time", format=".0f")]
    bars = alt.Chart(data).mark_bar(color=SERIES[t.dark][0], cornerRadiusEnd=4, height=14).encode(
        y=y, x=alt.X("seconds:Q", title="Mean seconds per question"), tooltip=tooltip)
    text = alt.Chart(data).mark_text(align="left", dx=6, color=t.ink_2, fontSize=11).encode(
        y=y, x="seconds:Q", text="label:N")
    return _style(alt.layer(bars, text), t, max(120, 26 * len(rows) + 50), "Where the time goes",
                  "Mean time per pipeline stage (stages that ran).")


def latency_chart(cases: List[Dict[str, Any]], summary: Dict[str, Any], t: Theme = LIGHT):
    rows = [{"latency": c["latency_s"], "outcome": "correct" if c.get("passed") else "wrong", "id": c["id"],
             "question": c["question"][:90]} for c in cases if c.get("latency_s")]
    if not rows:
        return None
    data = alt.Data(values=rows)
    colors = SERIES[t.dark]
    bars = alt.Chart(data).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, binSpacing=2).encode(
        x=alt.X("latency:Q", bin=alt.Bin(maxbins=24), title="Seconds per question"),
        y=alt.Y("count():Q", title="Questions", stack=True, axis=alt.Axis(tickMinStep=1, format="d")),
        color=alt.Color("outcome:N", scale=alt.Scale(domain=["correct", "wrong"], range=[colors[0], colors[1]]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("outcome:N", title="Outcome"), alt.Tooltip("count():Q", title="Questions")],
    )
    marks = [{"x": summary.get("latency_p50_s"), "t": "p50"}, {"x": summary.get("latency_p95_s"), "t": "p95"}]
    marks = [m for m in marks if m["x"] is not None]
    rules = alt.Chart(alt.Data(values=marks)).mark_rule(color=t.ink_2, strokeDash=[3, 3]).encode(x="x:Q")
    labels = alt.Chart(alt.Data(values=marks)).mark_text(align="left", dx=4, dy=-4, color=t.ink_2, fontSize=11,
                                                          baseline="top").encode(x="x:Q", y=alt.value(4), text="t:N")
    return _style(alt.layer(bars, rules, labels), t, 220, "How long answers take",
                  ["Questions per time range, split by outcome.", "Dashed lines: median (p50) and 95th percentile (p95)."])


def variants_chart(report: Dict[str, Any], t: Theme = LIGHT):
    variants = variants_of(report)
    if len(variants) < 2:
        return None
    colors = SERIES[t.dark]
    rows = []
    for index, v in enumerate(variants):
        s = v["summary"]
        ci = s.get("execution_accuracy_ci") or [None, None]
        if s.get("execution_accuracy") is None:
            continue
        rows.append({"variant": v["label"], "accuracy": s["execution_accuracy"] * 100,
                     "low": (ci[0] if ci[0] is not None else s["execution_accuracy"]) * 100,
                     "high": (ci[1] if ci[1] is not None else s["execution_accuracy"]) * 100,
                     "label": f"{s['execution_accuracy'] * 100:.1f}%", "slot": index})
    data = alt.Data(values=rows)
    order = [r["variant"] for r in rows]
    y = alt.Y("variant:N", sort=order, title=None, axis=alt.Axis(labelLimit=320, labelPadding=8))
    color = alt.Color("variant:N", scale=alt.Scale(domain=order, range=colors[: len(order)]), legend=None)
    tooltip = [alt.Tooltip("variant:N", title="Variant"), alt.Tooltip("accuracy:Q", title="Accuracy %", format=".1f"),
               alt.Tooltip("low:Q", title="95% CI low", format=".1f"), alt.Tooltip("high:Q", title="95% CI high", format=".1f")]
    lows = [r["low"] for r in rows]
    domain = [max(0, (min(lows) // 10) * 10 - 10), 100]
    whisker = alt.Chart(data).mark_rule(strokeWidth=2).encode(
        y=y, x=alt.X("low:Q", scale=alt.Scale(domain=domain, nice=False), title="Execution accuracy (%)"),
        x2="high:Q", color=color, tooltip=tooltip)
    dots = alt.Chart(data).mark_circle(size=160, opacity=1, stroke=t.surface, strokeWidth=2).encode(
        y=y, x="accuracy:Q", color=color, tooltip=tooltip)
    text = alt.Chart(data).mark_text(align="left", dx=8, dy=-11, color=t.ink_2, fontSize=11).encode(
        y=y, x="accuracy:Q", text="label:N")
    return _style(alt.layer(whisker, dots, text), t, 44 * len(rows) + 60, "Accuracy per variant",
                  ["Dot: accuracy. Line: 95% confidence interval.",
                   "Overlapping lines = the difference may be noise; see the paired test below."])


def tradeoff_chart(report: Dict[str, Any], t: Theme = LIGHT):
    variants = variants_of(report)
    if len(variants) < 2:
        return None
    use_cost = all(v["summary"].get("cost_per_question_usd") is not None for v in variants)
    key, title = ("cost_per_question_usd", "Cost per question (USD)") if use_cost else ("latency_p50_s", "Median seconds per question")
    rows = [{"variant": v["label"], "x": v["summary"].get(key), "accuracy": (v["summary"].get("execution_accuracy") or 0) * 100,
             "calls": v["summary"].get("llm_calls_per_question"), "latency": v["summary"].get("latency_p50_s")}
            for v in variants if v["summary"].get(key) is not None]
    if len(rows) < 2:
        return None
    order = [r["variant"] for r in rows]
    data = alt.Data(values=rows)
    color = alt.Color("variant:N", scale=alt.Scale(domain=order, range=SERIES[t.dark][: len(order)]),
                      legend=alt.Legend(title=None, orient="top", direction="vertical", labelLimit=320))
    tooltip = [alt.Tooltip("variant:N", title="Variant"), alt.Tooltip("accuracy:Q", title="Accuracy %", format=".1f"),
               alt.Tooltip("x:Q", title=title, format=".4f" if use_cost else ".1f"),
               alt.Tooltip("calls:Q", title="LLM calls / question", format=".1f"),
               alt.Tooltip("latency:Q", title="Median seconds", format=".1f")]
    points = alt.Chart(data).mark_circle(size=220, opacity=1, stroke=t.surface, strokeWidth=2).encode(
        x=alt.X("x:Q", title=title, scale=alt.Scale(zero=False, padding=30),
                axis=alt.Axis(format="$.4f" if use_cost else ".1f")),
        y=alt.Y("accuracy:Q", title="Execution accuracy (%)", scale=alt.Scale(zero=False, padding=20)),
        color=color, tooltip=tooltip)
    return _style(points, t, 300, "Accuracy vs " + ("cost" if use_cost else "speed"),
                  "Up and to the left is better: more accurate for less " + ("money." if use_cost else "time."))


# ============================================================
# STREAMLIT PAGE
# ============================================================

def _comparison_rows(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for c in report.get("comparisons") or []:
        delta = c.get("accuracy_delta")
        rows.append({
            "Variant": c["variant"], "vs": c["baseline"],
            "Accuracy change": "n/a" if delta is None else f"{delta * 100:+.1f} pts",
            "Questions": c.get("paired_cases"),
            "Baseline better on": c["only_a"], "Variant better on": c["only_b"],
            "p-value": c["p_value"], "Real difference?": "yes (p < 0.05)" if c["significant"] else "not proven",
            "Cost change / question": "n/a" if c.get("cost_per_question_delta") is None else f"{c['cost_per_question_delta']:+.4f} $",
            "Median time change": "n/a" if c.get("latency_p50_delta") is None else f"{c['latency_p50_delta']:+.1f} s",
        })
    return rows


def _case_rows(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for c in cases:
        rows.append({
            "Result": "✓" if c.get("passed") else "✕", "Case": c["id"], "Repeat": c.get("repeat", 1),
            "Difficulty": c.get("difficulty", ""), "Expected": c.get("expect", "answer"),
            "Likely cause": CAUSES.get(c.get("diagnosis") or "", c.get("diagnosis") or ""),
            "Trust": c.get("trust_level") or "", "Seconds": c.get("latency_s"),
            "Cost $": c.get("cost_usd"), "Question": c["question"],
        })
    return rows


def _run_help(st, golden_dir: Path) -> None:
    sets = sorted(p.name for p in Path(golden_dir).glob("*.yaml")) if Path(golden_dir).exists() else []
    golden = f"backend/evaluation/golden/{sets[0]}" if sets else "backend/evaluation/golden/<set>.yaml"
    db = sets[0].rsplit(".", 1)[0] if sets else "<database>"
    st.markdown("Run from the project folder (the backend does not need to be running):")
    st.code(f"python -m backend.evaluation --db {db} check --golden {golden}\n"
            f"python -m backend.evaluation --db {db} run --golden {golden}\n\n"
            f"# compare components (1 vs 3 SQL candidates, with/without correction), 3 repeats, 4 in parallel, $2 cap\n"
            f"python -m backend.evaluation --db {db} run --golden {golden} --ablation --repeats 3 --workers 4 --max-cost 2",
            language="bash")
    if sets:
        st.caption("Question sets available: " + ", ".join(sets) + ". The database must contain the matching data.")


# Plain names for the question-type tags of the golden sets.
TYPE_NAMES = {
    "simple": "Simple counts", "aggregation": "Totals & averages", "filter": "Filtering",
    "ranking": "Top-N & rankings", "temporal": "Dates & time periods", "grouping": "Breakdowns (group by)",
    "join": "Combining 2 tables", "multi_join": "Combining 3+ tables", "follow_up": "Follow-up questions",
    "value_grounding": "Matching stored values", "nulls": "Missing values", "nested": "Multi-step questions",
    "having": "Filtering groups", "ratio": "Percentages & shares", "window": "Running totals & ranks",
    "lookup": "Lookup / translation tables", "anti_join": "'Never' / 'without' questions",
    "unit_conversion": "Unit conversion", "self_join": "Same-table relationships",
    "expression": "Calculated values", "column_comparison": "Comparing two columns", "offset": "Nth-largest",
}


def _grade(rate: Optional[float], t: Theme):
    """(label, colour, icon) for a 0..1 score."""
    if rate is None:
        return "Not tested yet", t.muted, "–"
    if rate >= 0.9:
        return "Excellent", t.good, "✓"
    if rate >= 0.75:
        return "Good", t.warning, "!"
    return "Needs work", t.critical, "✕"


def verdict_text(summary: Dict[str, Any]) -> str:
    """One plain-English sentence summarising the run."""
    scored = summary.get("scored_cases") or 0
    accuracy = summary.get("execution_accuracy")
    parts = []
    if scored and accuracy is not None:
        correct = round(accuracy * scored)
        parts.append(f"InsightX answered {correct} of {scored} test questions correctly ({accuracy * 100:.0f}%).")
    if summary.get("safety_cases"):
        safe = round((summary.get("safety_pass_rate") or 0) * summary["safety_cases"])
        parts.append(f"It refused {safe} of {summary['safety_cases']} requests to change or delete data.")
    if summary.get("abstention_cases"):
        declined = round((summary.get("abstention_rate") or 0) * summary["abstention_cases"])
        parts.append(f"It declined {declined} of {summary['abstention_cases']} questions the data cannot answer "
                     f"instead of guessing.")
    if not summary.get("safety_cases") and not summary.get("abstention_cases"):
        parts.append("Safety and honesty questions come later in the run.")
    return " ".join(parts)


def _score_card(title: str, value: str, detail: str, meaning: str, rate: Optional[float], t: Theme) -> str:
    grade, colour, icon = _grade(rate, t)
    return (
        f'<div style="flex:1;min-width:210px;background:{t.card};border:1px solid {t.border};border-radius:12px;'
        f'padding:16px 18px;border-top:4px solid {colour}">'
        f'<div style="font-size:13px;font-weight:600;color:{t.ink_2}">{html.escape(title)}</div>'
        f'<div style="font-size:34px;font-weight:650;color:{t.ink};margin-top:6px;line-height:1.1">{html.escape(value)}</div>'
        f'<div style="font-size:13px;color:{t.ink_2};margin-top:2px">{html.escape(detail)}</div>'
        f'<div style="display:inline-block;margin-top:10px;padding:2px 10px;border-radius:999px;font-size:12px;'
        f'font-weight:600;color:{colour};border:1px solid {colour}">{icon} {html.escape(grade)}</div>'
        f'<div style="font-size:12px;color:{t.muted};margin-top:10px;line-height:1.4">{html.escape(meaning)}</div>'
        f'</div>'
    )


def scorecards_html(summary: Dict[str, Any], t: Theme = LIGHT) -> str:
    scored = summary.get("scored_cases") or 0
    accuracy = summary.get("execution_accuracy")
    correct = round((accuracy or 0) * scored)
    safety_n, abstain_n = summary.get("safety_cases") or 0, summary.get("abstention_cases") or 0
    safety, abstain = summary.get("safety_pass_rate"), summary.get("abstention_rate")
    seconds = summary.get("latency_p50_s")
    # speed graded on the median answer time
    speed_rate = None if seconds is None else (1.0 if seconds <= 15 else 0.8 if seconds <= 45 else 0.5)
    cards = [
        _score_card("Correct answers", _pct(accuracy), f"{correct} of {scored} questions",
                    "Returned exactly the right data, checked against a known-correct query.", accuracy, t),
        _score_card("Safe with your data", _pct(safety) if safety_n else "–",
                    f"{round((safety or 0) * safety_n)} of {safety_n} requests refused" if safety_n else "not tested yet",
                    "Requests to delete or change data were refused and nothing was modified.", safety if safety_n else None, t),
        _score_card("Admits when it can't answer", _pct(abstain) if abstain_n else "–",
                    f"{round((abstain or 0) * abstain_n)} of {abstain_n} declined" if abstain_n else "not tested yet",
                    "Questions the data can't answer: it should say so instead of making something up.",
                    abstain if abstain_n else None, t),
        _score_card("Speed & cost", _secs(seconds) if seconds is not None else "–",
                    f"per answer · {_money(summary.get('cost_per_question_usd'))} each",
                    "Typical (median) time and AI cost to answer one question.", speed_rate, t),
    ]
    return (f'<div style="font-family:{FONT_HTML};display:flex;flex-wrap:wrap;gap:12px;margin:6px 0 4px 0">'
            f'{"".join(cards)}</div>')


def verdict_html(summary: Dict[str, Any], t: Theme = LIGHT) -> str:
    accuracy = summary.get("execution_accuracy")
    _, colour, icon = _grade(accuracy, t)
    return (f'<div style="font-family:{FONT_HTML};background:{t.card};border:1px solid {t.border};'
            f'border-left:5px solid {colour};border-radius:10px;padding:14px 18px;margin:4px 0 10px 0">'
            f'<div style="font-size:12px;font-weight:600;letter-spacing:.04em;color:{t.ink_2}">SUMMARY</div>'
            f'<div style="font-size:16px;color:{t.ink};margin-top:4px;line-height:1.5">'
            f'<span style="color:{colour};font-weight:700">{icon}</span> {html.escape(verdict_text(summary))}</div></div>')


def simple_difficulty_chart(summary: Dict[str, Any], t: Theme = LIGHT):
    """Accuracy per difficulty as plain bars with 'correct / total' labels (no statistics)."""
    groups = summary.get("by_difficulty") or {}
    rows = []
    for level in DIFFICULTY_ORDER:
        g = groups.get(level)
        if not g or g.get("accuracy") is None:
            continue
        correct = round(g["accuracy"] * g["cases"])
        rows.append({"level": level.capitalize(), "help": DIFFICULTY_HELP[level], "accuracy": g["accuracy"] * 100,
                     "label": f"{g['accuracy'] * 100:.0f}%  ({correct}/{g['cases']})"})
    if not rows:
        return None
    data = alt.Data(values=rows)
    y = alt.Y("level:N", sort=[r["level"] for r in rows], title=None, axis=alt.Axis(labelPadding=8, labelFontSize=12))
    tooltip = [alt.Tooltip("level:N", title="Difficulty"), alt.Tooltip("help:N", title="Means"),
               alt.Tooltip("label:N", title="Correct")]
    track = alt.Chart(data).mark_bar(color=t.grid, cornerRadius=4, height=18).encode(
        y=y, x=alt.X("full:Q", scale=alt.Scale(domain=[0, 100]), title=None, axis=None)
    ).transform_calculate(full="100")
    bars = alt.Chart(data).mark_bar(color=SERIES[t.dark][0], cornerRadius=4, height=18).encode(
        y=y, x=alt.X("accuracy:Q", scale=alt.Scale(domain=[0, 100]), title=None, axis=None), tooltip=tooltip)
    text = alt.Chart(data).mark_text(align="right", dx=-8, color="#ffffff", fontSize=12, fontWeight=600).encode(
        y=y, x="accuracy:Q", text="label:N")
    return _style(alt.layer(track, bars, text), t, 44 * len(rows) + 50, "By difficulty",
                  "Easy = one table · Medium = a join or grouping · Hard / Extra = multi-step questions", grid=False)


def type_rows(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    groups = summary.get("by_tag_ci") or summary.get("by_tag") or {}
    rows = []
    for tag, g in groups.items():
        if tag in ("unanswerable", "safety") or g.get("accuracy") is None:
            continue
        correct = round(g["accuracy"] * g["cases"])
        rows.append({"Question type": TYPE_NAMES.get(tag, tag.replace("_", " ").capitalize()),
                     "Accuracy": round(g["accuracy"] * 100, 1), "Correct": f"{correct} / {g['cases']}"})
    rows.sort(key=lambda r: (r["Accuracy"], r["Question type"]))
    return rows


def type_table_html(rows: List[Dict[str, Any]], t: Theme = LIGHT) -> str:
    """Question types with a bar per row (theme colours; green/amber/red by score)."""
    body = []
    for row in rows:
        rate = row["Accuracy"] / 100
        colour = t.good if rate >= 0.9 else t.warning if rate >= 0.75 else t.critical
        body.append(
            f'<tr><td style="padding:7px 10px;color:{t.ink};border-bottom:1px solid {t.border}">'
            f'{html.escape(row["Question type"])}</td>'
            f'<td style="padding:7px 10px;width:45%;border-bottom:1px solid {t.border}">'
            f'<div style="background:{t.grid};border-radius:4px;height:10px;overflow:hidden">'
            f'<div style="width:{row["Accuracy"]:.0f}%;height:10px;background:{colour};border-radius:4px"></div></div></td>'
            f'<td style="padding:7px 10px;color:{t.ink};text-align:right;white-space:nowrap;border-bottom:1px solid {t.border}">'
            f'<b>{row["Accuracy"]:.0f}%</b> <span style="color:{t.ink_2}">({html.escape(row["Correct"])})</span></td></tr>')
    return (f'<div style="font-family:{FONT_HTML};border:1px solid {t.border};border-radius:10px;overflow:hidden">'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<thead><tr style="background:{t.card}">'
            f'<th style="text-align:left;padding:8px 10px;color:{t.ink_2};font-weight:600">Question type</th>'
            f'<th style="text-align:left;padding:8px 10px;color:{t.ink_2};font-weight:600">Accuracy</th>'
            f'<th style="text-align:right;padding:8px 10px;color:{t.ink_2};font-weight:600">Correct</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def progress_html(answered: int, total: int, t: Theme = LIGHT) -> str:
    share = max(0.0, min(1.0, answered / total)) * 100 if total else 0
    return (f'<div style="font-family:{FONT_HTML};margin:2px 0 10px 0">'
            f'<div style="font-size:13px;color:{t.ink_2};margin-bottom:6px">⏳ Test running: <b style="color:{t.ink}">'
            f'{answered} of {total}</b> questions done · press Refresh for the latest results</div>'
            f'<div style="background:{t.grid};border-radius:999px;height:8px;overflow:hidden">'
            f'<div style="width:{share:.1f}%;height:8px;background:{SERIES[t.dark][0]};border-radius:999px"></div>'
            f'</div></div>')


def _status_line(live: Dict[str, Any], st, t: Theme = LIGHT) -> None:
    total, answered = live.get("total"), live["answered"]
    if live["running"]:
        if total:
            st.markdown(progress_html(answered, total, t), unsafe_allow_html=True)
        else:
            st.info(f"⏳ Test running: {answered} questions done. Press Refresh to update.")
    else:
        st.warning(f"⏸ This test stopped after {answered}" + (f" of {total}" if total else "") +
                   " questions. Continue it by adding "
                   f"`--resume \"{live['file']}\"` to the same command.")


def render_benchmark_page(st, reports_dir: Path, golden_dir: Path) -> None:
    t = theme_for(detect_dark(st))

    def chart(c):
        if c is not None:
            st.altair_chart(c, use_container_width=True, theme=None)

    head, button = st.columns([5, 1])
    with head:
        st.subheader("📏 Benchmark")
        st.caption("InsightX is tested on questions whose correct answers are known, so you can see how often "
                   "it is right, whether it is safe, and how fast it is.")
    with button:
        st.write("")
        if st.button("🔄 Refresh", key="bench_refresh", use_container_width=True):
            st.rerun()

    reports = list_reports(reports_dir)
    if not reports:
        st.info("No test results yet. Run a benchmark from the project folder, then come back here.")
        _run_help(st, golden_dir)
        return
    loaded = {}
    for path in reports[:50]:
        try:
            loaded[path] = load_report(path, golden_dir)
        except (OSError, ValueError):
            continue
    if not loaded:
        st.warning("The result files could not be read.")
        return
    paths = list(loaded)
    if len(paths) > 1:
        path = st.selectbox("Test run", paths, format_func=lambda p: report_label(p, loaded[p]), key="bench_report")
    else:
        path = paths[0]
    report = loaded[path]
    variants = variants_of(report)
    variant = variants[0]
    if len(variants) > 1:
        names = [v["label"] for v in variants]
        variant = variants[names.index(st.radio("Configuration", names, horizontal=True, key="bench_variant",
                                                help="This run compared several pipeline settings."))]
    summary, cases = variant["summary"], variant["cases"]

    if report.get("live"):
        _status_line(report["live"], st, t)
    if not summary.get("cases"):
        st.info("No questions answered yet.")
        return

    st.markdown(verdict_html(summary, t), unsafe_allow_html=True)
    st.markdown(scorecards_html(summary, t), unsafe_allow_html=True)
    if summary.get("infrastructure_errors"):
        st.caption(f"⚠ {summary['infrastructure_errors']} question(s) failed because of a network or AI-provider "
                   f"error (not a wrong answer); they are counted as wrong.")

    failed = [c for c in cases if not c.get("passed")]
    tabs = st.tabs(["Overview", "Test questions", f"Failures ({len(failed)})", "Advanced"])

    with tabs[0]:
        left, right = st.columns([1, 1.1])
        with left:
            chart(simple_difficulty_chart(summary, t))
        with right:
            st.markdown("**By question type** (weakest first)")
            rows = type_rows(summary)
            if rows:
                st.markdown(type_table_html(rows, t), unsafe_allow_html=True)
        with st.expander("What do these numbers mean?"):
            st.markdown(
                "- **Correct answers**: InsightX's SQL result is compared with a hand-checked query. Extra helpful "
                "columns are fine; wrong or missing data is not.\n"
                "- **Safe with your data**: requests like *'delete all canceled orders'* must be refused. Table row "
                "counts are checked before and after.\n"
                "- **Admits when it can't answer**: questions like *'average customer age'* (no such data) must not "
                "get a made-up answer.\n"
                "- **Difficulty**: easy = one table; medium = a join or grouping; hard / extra = multi-step "
                "questions, percentages, running totals.\n"
                "- More detail (confidence ranges, trust-score reliability, time per step) is in **Advanced**.")

    with tabs[1]:
        c1, c2 = st.columns([1, 2])
        with c1:
            outcome = st.radio("Show", ["All", "Correct", "Wrong"], horizontal=True, key="bench_outcome")
        with c2:
            levels = st.multiselect("Difficulty", DIFFICULTY_ORDER, default=[], key="bench_levels",
                                    placeholder="All difficulties")
        shown = [c for c in cases
                 if (outcome == "All" or (outcome == "Wrong") != bool(c.get("passed")))
                 and (not levels or c.get("difficulty") in levels)]
        if not shown:
            st.info("No questions match.")
        else:
            table = [{"Result": "✅" if c.get("passed") else "❌", "Question": c["question"],
                      "Difficulty": c.get("difficulty", "").capitalize(),
                      "Type": {"answer": "Answer", "no_answer": "Should decline",
                               "no_write": "Should refuse"}.get(c.get("expect", "answer"), ""),
                      "Seconds": c.get("latency_s")} for c in shown]
            config = None
            try:
                config = {"Question": st.column_config.TextColumn(width="large"),
                          "Seconds": st.column_config.NumberColumn(format="%.0f")}
            except Exception:
                pass
            st.dataframe(table, use_container_width=True, hide_index=True, column_config=config)
            options = [c["question"] for c in shown]
            picked = st.selectbox("Open a question to see InsightX's SQL and answer", range(len(shown)),
                                  format_func=lambda i: ("✅ " if shown[i].get("passed") else "❌ ") + options[i],
                                  key="bench_case")
            render_case(st, shown[picked], t)

    with tabs[2]:
        if not failed:
            st.success("No failures. Every tested question was answered correctly.")
        else:
            chart(failures_chart(summary, t))
            for index, case in enumerate(failed):
                cause = CAUSES.get(case.get("diagnosis") or "", "Wrong result")
                with st.expander(f"❌ {case['question']}  —  {cause}"):
                    render_case(st, case, t)

    with tabs[3]:
        _advanced(st, report, variant, summary, cases, t, chart)
        with st.expander("Downloads and how to run a benchmark"):
            st.download_button("Download these results (JSON)", data=json.dumps(report, indent=2, default=str),
                               file_name=path.name.replace(".partial.jsonl", ".live.json"), mime="application/json")
            markdown = path.with_suffix(".md")
            if markdown.exists():
                st.download_button("Download the written report (Markdown)",
                                   data=markdown.read_text(encoding="utf-8"),
                                   file_name=markdown.name, mime="text/markdown")
            _run_help(st, golden_dir)


def _advanced(st, report, variant, summary, cases, t, chart) -> None:
    ci = summary.get("execution_accuracy_ci") or [None, None]
    if ci[0] is not None:
        st.markdown(f"**Confidence range.** With {summary.get('scored_cases')} questions, the true accuracy is "
                    f"between **{_pct(ci[0])} and {_pct(ci[1])}** (95% confidence). More questions make this range "
                    f"narrower.")
    variants = variants_of(report)
    if len(variants) > 1:
        st.markdown("##### Comparing configurations")
        left, right = st.columns([1.15, 1])
        with left:
            chart(variants_chart(report, t))
        with right:
            chart(tradeoff_chart(report, t))
        comparison = _comparison_rows(report)
        if comparison:
            st.dataframe(comparison, use_container_width=True, hide_index=True)
            st.caption("p < 0.05 means the difference between two configurations is unlikely to be luck "
                       "(paired exact test on the same questions).")
    if summary.get("stable_rate") is not None:
        st.markdown("##### Consistency across repeats")
        st.markdown(stability_html(summary, t), unsafe_allow_html=True)
    st.markdown("##### Is the trust score reliable?")
    st.caption("Every answer gets a trust score. A reliable score means 'high trust' answers really are right.")
    left, right = st.columns([1.2, 1])
    with left:
        chart(calibration_chart(summary, t))
    with right:
        chart(trust_levels_chart(summary, t))
        if summary.get("answer_faithfulness") is not None:
            st.caption(f"Answer faithfulness {_pct(summary['answer_faithfulness'])}: written answers whose every "
                       f"number was found in the query result.")
    st.markdown("##### Where the time goes")
    left, right = st.columns(2)
    with left:
        chart(stage_chart(summary, t))
    with right:
        chart(latency_chart(cases, summary, t))
    st.markdown("##### Accuracy by question type, with confidence ranges")
    chart(category_chart(summary, t))


def _column_config(st):
    try:
        return {
            "Seconds": st.column_config.NumberColumn(format="%.1f"),
            "Cost $": st.column_config.NumberColumn(format="%.4f"),
            "Question": st.column_config.TextColumn(width="large"),
        }
    except Exception:  # older Streamlit
        return None


def pretty_sql(sql: str) -> str:
    try:
        import sqlglot

        return sqlglot.transpile(sql, read="postgres", write="postgres", pretty=True)[0]
    except Exception:
        return sql


def render_case(st, case: Dict[str, Any], t: Theme = LIGHT) -> None:
    passed = bool(case.get("passed"))
    color = t.good if passed else t.critical
    verdict = "✓ Correct" if passed else "✕ Wrong"
    expect = {"answer": "should answer", "no_answer": "should decline (data can't answer)",
              "no_write": "should refuse to change data"}.get(case.get("expect", "answer"), "")
    cause = CAUSES.get(case.get("diagnosis") or "", case.get("diagnosis") or "")
    detail = case.get("diagnosis_detail") or ""
    reason = case.get("reason") or case.get("error") or ""
    if detail.strip().lower() == reason.strip().lower():
        detail = ""
    st.markdown(
        f'<div style="font-family:{FONT_HTML};border:1px solid {t.border};border-left:4px solid {color};'
        f'border-radius:10px;padding:12px 14px;background:{t.card}">'
        f'<div style="color:{t.ink};font-size:15px;font-weight:600">{html.escape(case["question"])}</div>'
        f'<div style="color:{t.ink_2};font-size:13px;margin-top:4px">'
        f'<span style="color:{color};font-weight:700">{verdict}</span> · {html.escape(case.get("difficulty", ""))} · '
        f'{html.escape(expect)} · status {html.escape(case.get("status", ""))}'
        + (f' at {html.escape(case["error_stage"])}' if case.get("error_stage") else "") + "</div>"
        + (f'<div style="color:{t.ink_2};font-size:13px;margin-top:6px"><b>Likely cause:</b> {html.escape(cause)}'
           + (f' — {html.escape(detail)}' if detail else "")
           + "</div>" if cause else "")
        + f'<div style="color:{t.muted};font-size:12px;margin-top:6px">Result check: {html.escape(reason)}</div>'
        + "</div>",
        unsafe_allow_html=True,
    )
    if case.get("expect", "answer") == "answer":
        left, right = st.columns(2)
        with left:
            st.caption("Expected SQL (known correct)")
            st.code(pretty_sql(case.get("expected_sql") or ""), language="sql")
        with right:
            st.caption("Generated SQL")
            st.code(pretty_sql(case["predicted_sql"]) if case.get("predicted_sql") else "(none)", language="sql")
    elif case.get("predicted_sql"):
        st.caption("Generated SQL")
        st.code(pretty_sql(case["predicted_sql"]), language="sql")
    if case.get("answer"):
        st.caption("Answer shown to the user")
        st.markdown(f"> {case['answer'][:1500]}")
    details = [
        f"Trust: {case.get('trust_level') or 'n/a'} ({case.get('trust_score') if case.get('trust_score') is not None else 'n/a'})",
        f"Time: {_secs(case.get('latency_s'))}",
        f"LLM calls: {case.get('llm_calls', 0)}" + (f" ({case['failed_llm_calls']} failed)" if case.get("failed_llm_calls") else ""),
        f"Tokens: {case.get('prompt_tokens', 0) + case.get('completion_tokens', 0):,}",
        f"Cost: {_money(case.get('cost_usd'))}",
        f"Corrections: {case.get('correction_attempts', 0)}",
    ]
    if case.get("expected_tables"):
        details.append(f"Tables needed: {', '.join(case['expected_tables'])} · retrieved: "
                       f"{', '.join(case.get('retrieved_tables') or []) or 'none'}")
    st.caption(" · ".join(details))
    signals = [s for s in case.get("diagnosis_signals") or [] if s.get("category") != case.get("diagnosis")]
    if signals:
        st.caption("Other differences found: " + "; ".join(
            CAUSES.get(s["category"], s["category"]) + (f" ({s['detail']})" if s.get("detail") else "") for s in signals))
