# backend/investigation/narrative.py
#
# Words for the computed facts. Two layers:
#
#   facts_summary()  deterministic sentences built by code (always
#                    correct, used as fallback and shown as "key facts")
#   narrate()        LLM explanation + hypotheses + next steps, given
#                    ONLY the computed facts; every number it states is
#                    checked against those facts (trust.verify_claims),
#                    with one regeneration when something is invented.
#
# Recommendations are hypotheses: the data shows where a change is
# concentrated and what moved with it, not what caused it.

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from backend.llm_service import get_llm
from backend.text_to_sql.trust import verify_claims

llm = get_llm(job="investigation_narrative", max_tokens=3000)


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 10:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


def facts_summary(report: Dict[str, Any]) -> List[str]:
    h = report["headline"]
    facts = []
    verb = {"decrease": "fell", "increase": "rose"}.get(h["direction"], "did not change")
    pct = f" ({abs(h['change_pct']):.1f}%)" if h.get("change_pct") is not None else ""
    facts.append(
        f"{h['metric']} {verb} from {_fmt(h['baseline'])} in {h.get('baseline_label', h['baseline_period'])} "
        f"to {_fmt(h['current'])} in {h.get('current_label', h['current_period'])}{pct}."
    )
    if h.get("unusual") in ("unusual", "notable", "normal") and h.get("typical_change_pct") is not None:
        label = {"unusual": "an unusual change", "notable": "a notable change",
                 "normal": "within normal variation"}[h["unusual"]]
        facts.append(f"That is {label}: a typical period-to-period change is about {h['typical_change_pct']:.1f}%.")
    vv = report.get("volume_value")
    if vv and vv.get("records_change_pct") is not None:
        main = "the number of records" if vv["main_factor"] == "records" else "the value per record"
        other = "value per record" if vv["main_factor"] == "records" else "the number of records"
        sentence = (f"Number of records changed {vv['records_change_pct']:+.1f}% and value per record "
                    f"{vv['value_per_record_change_pct']:+.1f}%: the change comes mainly from {main}")
        sentence += f" ({other} moved the other way)." if vv["factors_opposed"] else "."
        facts.append(sentence)
    for driver in report.get("drivers", [])[:3]:
        lead = next((item for item in driver["top"] if item["segment"] == driver.get("leading_segment")), None)
        lead = lead or (driver["top"][0] if driver["top"] else None)
        if lead and lead.get("share_of_change_pct") is not None and abs(lead["share_of_change_pct"]) < 1:
            continue  # explains nothing: not a driver
        if lead and lead.get("share_of_change_pct") is not None:
            facts.append(
                f"By {driver['dimension'].split('.', 1)[-1]}: '{lead['segment']}' accounts for {lead['share_of_change_pct']:.0f}% "
                f"of the change while holding {lead.get('baseline_share_pct', 0):.0f}% of the {h['metric']} "
                f"({_fmt(lead['baseline'])} to {_fmt(lead['current'])})."
            )
        elif lead and lead.get("change_pct") is not None:
            facts.append(f"By {driver['dimension'].split('.', 1)[-1]}: '{lead['segment']}' changed {lead['change_pct']:+.1f}%.")
    drivers = report.get("drivers") or []
    if drivers and drivers[0].get("score") is not None and drivers[0]["score"] < 0.15 and report["plan"]["metric_kind"] != "ratio":
        facts.append("No breakdown concentrates the change: every segment moved roughly in proportion to its size.")
    if report.get("drivers") and report["drivers"][0].get("mix_effect") is not None:
        d = report["drivers"][0]
        facts.append(f"Mix effect {d['mix_effect']:+.4g}, rate effect {d['rate_effect']:+.4g} "
                     f"(by {d['dimension'].split('.', 1)[-1]}).")
    for signal in report.get("signals", [])[:3]:
        scope_key = "segment" if signal.get("segment_change") is not None else "overall"
        change = signal.get(f"{scope_key}_change")
        if change is None:
            continue
        where = f" within {signal['scope']}" if scope_key == "segment" else ""
        unit = {"points": " points", "days": " days"}.get(signal["unit"], "%")
        facts.append(
            f"{signal['signal']}{where} moved {change:+.1f}{unit} "
            f"({_fmt(signal.get(scope_key + '_baseline'))} to {_fmt(signal.get(scope_key + '_current'))})."
        )
    return facts


def _compact(report: Dict[str, Any]) -> Dict[str, Any]:
    drivers = []
    for driver in report.get("drivers", [])[:3]:
        drivers.append({
            "dimension": driver["dimension"],
            "top_segments": [
                {k: v for k, v in item.items() if k in ("segment", "baseline", "current", "change_pct",
                                                         "contribution", "share_of_change_pct")}
                for item in driver["top"][:5]
            ],
            **({"mix_effect": driver["mix_effect"], "rate_effect": driver["rate_effect"]} if "mix_effect" in driver else {}),
        })
    drill = report.get("drilldown")
    return {
        "headline": report["headline"],
        "volume_value": report.get("volume_value"),
        "drivers": drivers,
        "drilldown_inside_top_segment": (drill or {}).get("top_inside"),
        "related_signals": report.get("signals", [])[:5],
        "real_reasons": [{k: reason.get(k) for k in ("likelihood", "title", "detail", "evidence")}
                         for reason in report.get("reasons") or []],
        "checked_not_the_reason": [{k: reason.get(k) for k in ("title", "detail", "evidence")}
                                   for reason in report.get("ruled_out") or []],
    }


PROMPT = """
You are a senior business analyst. Explain why the metric changed using
ONLY the computed facts below (they come from SQL on the user's data).

Rules:
- Use only numbers that appear in the facts (you may round them).
  Do not calculate new numbers.
- Say where the change is concentrated (segments) and what moved with it
  (signals). Signals are correlations, not proven causes.
- Recommendations must be framed as hypotheses to test, each tied to a
  fact. Do not invent business context.
- "actions" answers "how to improve": 2-4 practical things the business
  can do, most important first, each aimed at a driver or segment named
  in the facts (e.g. "Win back customers in <segment>"), with the fact
  that supports it. Plain words, no jargon.
- If the change is within normal variation, say so first.
- "real_reason" answers "why" in 2-3 plain sentences: start from the
  strongest item in real_reasons (e.g. the earlier period was a one-off
  spike, a seasonal pattern, fewer new customers, slower delivery), then
  where it happened (drivers). Say which causes were checked and ruled
  out when useful. If real_reasons is empty, use the drivers and signals.

Return ONLY JSON:
{{"real_reason": "<2-3 sentences: the most likely real cause>",
  "summary": "<2-3 sentences>",
  "drivers": ["<one sentence per main driver>", ...],
  "hypotheses": [{{"hypothesis": "<what may explain it / what to try>", "evidence": "<the fact it rests on>"}}],
  "actions": [{{"action": "<what to do>", "why": "<the fact it rests on>"}}],
  "next_steps": ["<a follow-up question the user can ask about this data, written as a question>", ...]}}

QUESTION: {question}

FACTS:
{facts}
{correction}"""


def _numbers_and_labels(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _numbers_and_labels(item)
    elif isinstance(value, list):
        for item in value:
            yield from _numbers_and_labels(item)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)):
        yield value
        yield abs(value)
    else:
        yield str(value)


def fact_table(report: Dict[str, Any]) -> Dict[str, Any]:
    """All computed numbers and labels as a pseudo query result for claim checking."""
    values = list(_numbers_and_labels(_compact(report)))
    return {"success": True, "columns": ["fact"], "rows": [{"fact": value} for value in values], "row_count": len(values)}


def _parse(raw: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL | re.IGNORECASE)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    decoder = json.JSONDecoder()
    for start in [m.start() for m in re.finditer(r"\{", text)]:
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "summary" in value:
            return value
    plain = text.strip()
    if len(plain) > 40 and "{" not in plain:
        return {"summary": plain}  # model answered in prose instead of JSON
    return None


def _narrative_text(narrative: Dict[str, Any]) -> str:
    parts = [str(narrative.get("summary") or ""), str(narrative.get("real_reason") or "")]
    parts += [str(item) for item in narrative.get("drivers") or []]
    for item in narrative.get("hypotheses") or []:
        if isinstance(item, dict):
            parts += [str(item.get("hypothesis") or ""), str(item.get("evidence") or "")]
        else:
            parts.append(str(item))
    for item in narrative.get("actions") or []:
        if isinstance(item, dict):
            parts += [str(item.get("action") or ""), str(item.get("why") or "")]
        else:
            parts.append(str(item))
    return "\n".join(parts)


def narrate(report: Dict[str, Any], question: str, model=None) -> Dict[str, Any]:
    """{"narrative": {...} | None, "claim_check": {...}, "raw": str}"""
    facts = json.dumps(_compact(report), default=str, ensure_ascii=False)[:12000]
    table = fact_table(report)
    correction = ""
    best = None
    for attempt in range(2):
        try:
            response = (model or llm).invoke(PROMPT.format(question=question, facts=facts, correction=correction))
        except Exception as exc:
            return {"narrative": None, "claim_check": {"status": "not_applicable"}, "raw": "",
                    "error": f"the language model call failed ({type(exc).__name__})"}
        raw = str(getattr(response, "content", response))
        narrative = _parse(raw)
        if narrative is None:
            continue
        check = verify_claims(_narrative_text(narrative), question, table)
        bad = len(check["unverified_numbers"])
        if best is None or bad < best[0]:
            best = (bad, narrative, check, raw)
        if bad == 0:
            break
        correction = (
            "\nYOUR PREVIOUS ANSWER USED NUMBERS THAT ARE NOT IN THE FACTS: "
            + ", ".join(check["unverified_numbers"])
            + ". Use only numbers from the facts."
        )
    if best is None:
        return {"narrative": None, "claim_check": {"status": "not_applicable"}, "raw": "",
                "error": "the language model's reply could not be read"}
    _, narrative, check, raw = best
    return {"narrative": narrative, "claim_check": check, "raw": raw}


def fallback_narrative(report: Dict[str, Any]) -> Dict[str, Any]:
    """The 'why' answer built only from computed facts, used when the LLM explanation fails."""
    facts = facts_summary(report)
    head = [f for f in facts[:3] if not f.startswith("By ")]
    drivers = [f for f in facts if f.startswith("By ")]
    moved = [f for f in facts if " moved " in f]
    steps = []
    top = (report.get("drivers") or [{}])[0]
    if top.get("leading_segment") is not None and len(report.get("drivers") or []) > 1:
        other = report["drivers"][1]["dimension"].split(".", 1)[-1]
        steps.append(f"Break down '{top['leading_segment']}' by {other} to see where inside it the change sits.")
    return {"summary": " ".join(head), "drivers": drivers + moved, "hypotheses": [],
            "actions": fallback_actions(report), "next_steps": steps,
            "real_reason": fallback_real_reason(report)}


def fallback_real_reason(report: Dict[str, Any]) -> str:
    """The strongest checked reason, in plain words, without the AI."""
    reasons = report.get("reasons") or []
    if not reasons:
        return ""
    top = reasons[0]
    return f"{top['title']}. {top['detail']}"


def fallback_actions(report: Dict[str, Any]) -> List[Dict[str, str]]:
    """'How to improve', built only from the drivers when the AI explanation fails."""
    headline = report.get("headline") or {}
    metric = headline.get("metric") or "the metric"
    fell = (headline.get("change") or 0) < 0
    actions = []
    for driver in (report.get("drivers") or [])[:2]:
        segment, dimension = driver.get("leading_segment"), driver["dimension"].split(".", 1)[-1]
        if segment is None:
            continue
        if fell:
            text = f"Focus first on {dimension} '{segment}': find out what changed there and fix it."
        else:
            text = f"Learn from {dimension} '{segment}' and repeat what worked there elsewhere."
        share = next((item.get("share_of_change_pct") for item in driver.get("top") or []
                      if item.get("segment") == segment), None)
        why = f"It explains the largest part of the change in {metric}" + (
            f" ({share:.0f}% of it)." if isinstance(share, (int, float)) and 0 < share <= 100 else ".")
        actions.append({"action": text, "why": why})
    return actions
