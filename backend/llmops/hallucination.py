# backend/llmops/hallucination.py
#
# Hallucination checks for one answered question.
#
# 1. Deterministic grounding (always, no AI): every claim in the answer
#    is compared with the result rows the answer was written from.
#      - numbers and quoted values not found in the result
#        (reuses text_to_sql.trust.verify_claims)
#      - claims made about an empty result
#      - "top 10" when the result has fewer rows
#      - "highest / lowest X" that does not match the first row
#      - row counts stated in the answer that differ from the result
#      - join fan-out warnings from the sanity check
#      - low trust from the trust layer
#      - capitalised names that appear nowhere in the result (weak)
#    Each finding has a weight; the weights give a faithfulness score
#    (0..1) and a risk level.
#
# 2. AI judge (only when someone asks for it on the Hallucination page):
#    a free model reads the question, SQL, a result preview and the
#    answer and lists unsupported claims. Uses the existing gateway.

import json
import re
from typing import Any, Dict, List, Optional

from backend.text_to_sql.trust import _fold, extract_numbers, verify_claims

WEIGHTS = {
    "unverified_number": 0.30,
    "unverified_value": 0.25,
    "empty_result_claim": 0.45,
    "top_n_mismatch": 0.20,
    "superlative_mismatch": 0.35,
    "row_count_mismatch": 0.25,
    "fanout": 0.20,
    "low_trust": 0.15,
    "unknown_name": 0.08,
    "judge_unsupported": 0.30,
}

_TOP_N_RE = re.compile(r"\b(?:top|first|best|largest|biggest)\s+(\d{1,4})\b", re.IGNORECASE)
_SUPERLATIVE_RE = re.compile(
    r"\b(highest|lowest|most|least|largest|smallest|biggest|top|best|worst|maximum|minimum)\b",
    re.IGNORECASE)
_COUNT_RE = re.compile(r"\b(?:there (?:are|were)|found|returned|total of|shows?)\s+(\d[\d,]*)\s+"
                       r"(?:rows?|records?|results?|items?|entries)\b", re.IGNORECASE)
_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b")
_EMPTY_WORDS = ("no data", "no rows", "no results", "no records", "none", "not found", "nothing",
                "no matching", "zero", "empty", "could not find", "did not find", "there are no",
                "there were no", "no ")

# Common words that start sentences or name things that are not data.
_STOP_NAMES = {
    "the", "this", "that", "these", "those", "there", "here", "based", "according", "overall",
    "total", "average", "top", "sql", "the result", "result", "results", "query", "data", "note",
    "however", "also", "each", "all", "in", "on", "for", "from", "with", "between", "and", "or",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "highest", "lowest", "most", "least", "yes", "no", "it", "they", "we",
    "showing", "sales", "revenue", "orders", "customers", "products", "count", "sum", "insightx",
}


def _finding(kind: str, detail: str, text: str = "") -> Dict[str, Any]:
    return {"kind": kind, "weight": WEIGHTS.get(kind, 0.1), "detail": detail, "text": text}


def _first_number(row: Dict[str, Any], columns: List[str]) -> Optional[str]:
    for column in columns:
        value = row.get(column)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return column
    return None


def _label_column(row: Dict[str, Any], columns: List[str]) -> Optional[str]:
    for column in columns:
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            return column
    return None


def risk_level(score: float, findings: List[Dict[str, Any]]) -> str:
    if not findings:
        return "none"
    if score < 0.55:
        return "high"
    if score < 0.8:
        return "medium"
    return "low"


def check_answer(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic grounding checks for one /query response.
    Returns {score, risk, hallucinated, findings, claims, applicable}.
    """
    answer = str(response.get("answer") or "")
    question = str(response.get("question") or "")
    result = response.get("execution_result") or {}
    rows = list(result.get("rows") or [])
    columns = list(result.get("columns") or [])
    row_count = result.get("row_count")
    if row_count is None:
        row_count = len(rows)
    status = str(response.get("status") or "")

    findings: List[Dict[str, Any]] = []
    if not answer or status not in ("SUCCESS", "success"):
        return {"score": None, "risk": "none", "hallucinated": False, "findings": [],
                "claims": {"numbers": [], "values": []}, "applicable": False}

    # 1. Numbers and quoted values (reuse the trust layer's checker).
    # Always re-check the final answer text (the stored claim_check can
    # describe an earlier draft of the answer).
    claims = verify_claims(answer, question, result)
    for text in claims.get("unverified_numbers") or []:
        findings.append(_finding("unverified_number", f"The number {text} is not in the result.", text))
    for text in claims.get("unverified_values") or []:
        findings.append(_finding("unverified_value", f'"{text}" is not a value in the result.', text))

    lowered = answer.lower()

    # 2. Claims about an empty result.
    if row_count == 0:
        numbers = [n for n in extract_numbers(answer) if n["value"] != 0]
        says_empty = any(word in lowered for word in _EMPTY_WORDS)
        if numbers and not says_empty:
            findings.append(_finding(
                "empty_result_claim",
                "The query returned no rows, but the answer states figures.",
                ", ".join(n["text"] for n in numbers[:5])))

    # 3. "Top N" larger than what came back.
    for match in _TOP_N_RE.finditer(answer):
        wanted = int(match.group(1))
        if rows and wanted > int(row_count) and not result.get("truncated"):
            findings.append(_finding(
                "top_n_mismatch",
                f"The answer mentions {match.group(0)!r} but the result has {row_count} row(s).",
                match.group(0)))
            break

    # 4. "Highest X is Y" should name the first row's label when the
    #    result is sorted (top pick queries put the winner first).
    if rows and _SUPERLATIVE_RE.search(answer):
        label = _label_column(rows[0], columns)
        sql = str(response.get("sql") or "").lower()
        if label and "order by" in sql:
            first = _fold(rows[0].get(label))
            others = {_fold(row.get(label)) for row in rows[1:6] if row.get(label)}
            mentioned_first = first and first in _fold(answer)
            mentioned_other = [o for o in others if o and len(o) >= 3 and o in _fold(answer)]
            sentence = _SUPERLATIVE_RE.search(answer)
            head = _fold(answer[max(0, sentence.start() - 120): sentence.end() + 160]) if sentence else ""
            number = _first_number(rows[0], columns)
            if number:
                # rows tied with the first one are equally "the highest"
                tied = {_fold(row.get(label)) for row in rows[1:6] if row.get(number) == rows[0].get(number)}
                mentioned_other = [o for o in mentioned_other if o not in tied]
            if not mentioned_first and mentioned_other and any(o in head for o in mentioned_other):
                findings.append(_finding(
                    "superlative_mismatch",
                    f'The answer picks "{mentioned_other[0]}" as the {sentence.group(1).lower()}, '
                    f'but the first row of the sorted result is "{rows[0].get(label)}".',
                    mentioned_other[0]))

    # 5. Row counts stated in words.
    for match in _COUNT_RE.finditer(answer):
        stated = int(match.group(1).replace(",", ""))
        if row_count and stated != int(row_count) and not result.get("truncated"):
            numbers_in_rows = {
                float(v) for row in rows for v in row.values()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if float(stated) not in numbers_in_rows:
                findings.append(_finding(
                    "row_count_mismatch",
                    f"The answer says {stated} but the result has {row_count} row(s).",
                    match.group(0)))
                break

    # 6. Fan-out found by the sanity check (totals may be inflated).
    sanity = response.get("sanity") or {}
    for item in (sanity.get("fanout") or [])[:2]:
        detail = item.get("message") if isinstance(item, dict) else str(item)
        findings.append(_finding("fanout", detail or "A join may count rows more than once.", ""))

    # 7. Low trust.
    trust = response.get("trust") or {}
    if str(trust.get("level") or "").lower() == "low":
        findings.append(_finding("low_trust", "The trust layer rated this answer low.", ""))

    # 8. Capitalised names that appear nowhere (weak signal).
    known = " ".join(_fold(v) for row in rows[:200] for v in row.values() if v is not None)
    known += " " + " ".join(_fold(c) for c in columns) + " " + _fold(question)
    unknown = []
    reported = {_fold(text) for text in claims.get("unverified_values") or []}
    for match in _NAME_RE.finditer(answer):
        name = match.group(1)
        folded = _fold(name)
        if folded in reported or folded in _STOP_NAMES or folded.split(" ")[0] in _STOP_NAMES:
            continue
        start = match.start()
        if start == 0 or answer[max(0, start - 2):start].strip() in (".", "!", "?", ":", "-", "*"):
            continue  # sentence start
        if folded not in known and not any(part in known for part in folded.split(" ") if len(part) > 3):
            unknown.append(name)
    for name in list(dict.fromkeys(unknown))[:3]:
        findings.append(_finding("unknown_name", f'"{name}" does not appear in the result.', name))

    return summarise(findings, claims)


def summarise(findings: List[Dict[str, Any]], claims: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    penalty = 0.0
    for item in findings:
        penalty += item["weight"]
    score = round(max(0.0, 1.0 - min(penalty, 1.0)), 3)
    risk = risk_level(score, findings)
    hard = {"unverified_number", "unverified_value", "empty_result_claim", "superlative_mismatch",
            "row_count_mismatch", "judge_unsupported"}
    hallucinated = any(item["kind"] in hard for item in findings) and risk in ("medium", "high")
    claims = claims or {}
    return {
        "score": score,
        "risk": risk,
        "hallucinated": hallucinated,
        "findings": findings,
        "claims": {"numbers": claims.get("numbers") or [], "values": claims.get("values") or []},
        "applicable": True,
    }


# ------------------------------------------------------------ AI judge
JUDGE_PROMPT = """You check whether an answer is fully supported by a SQL result.
Question: {question}
SQL: {sql}
Result columns: {columns}
Result rows (first {shown} of {total}):
{rows}

Answer to check:
{answer}

List every claim in the answer that the rows do not support (numbers, names,
rankings, trends, causes). Simple rounding is fine. Reply with JSON only:
{{"faithful": true|false, "score": 0.0-1.0, "unsupported_claims": ["..."], "explanation": "one sentence"}}"""


def judge_prompt(row: Dict[str, Any]) -> str:
    preview = row.get("preview") or {}
    rows = preview.get("rows") or []
    return JUDGE_PROMPT.format(
        question=row.get("question", ""),
        sql=(row.get("sql") or "")[:2000],
        columns=", ".join(map(str, preview.get("columns") or [])),
        shown=len(rows),
        total=row.get("row_count") if row.get("row_count") is not None else len(rows),
        rows="\n".join(json.dumps(r, default=str)[:400] for r in rows) or "(no rows)",
        answer=(row.get("answer") or "")[:3000],
    )


def parse_judge(text: str) -> Dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", flags=re.DOTALL)
    if not match:
        raise ValueError("The judge did not reply with JSON.")
    data = json.loads(match.group(0))
    claims = [str(c)[:300] for c in (data.get("unsupported_claims") or []) if str(c).strip()][:10]
    try:
        score = float(data.get("score"))
    except (TypeError, ValueError):
        score = 0.0 if claims else 1.0
    return {
        "faithful": bool(data.get("faithful", not claims)) and not claims,
        "score": round(max(0.0, min(1.0, score)), 3),
        "unsupported_claims": claims,
        "explanation": str(data.get("explanation") or "")[:500],
    }


def run_judge(row: Dict[str, Any]) -> Dict[str, Any]:
    """Ask a free model through the existing gateway. Raises on failure."""
    import time
    from backend.ai_gateway.gateway import get_gateway

    started = time.time()
    text = get_gateway().complete(judge_prompt(row), job="answer", max_tokens=800)
    verdict = parse_judge(text)
    verdict["seconds"] = round(time.time() - started, 2)
    verdict["at"] = time.time()
    return verdict


def merge_judge(checks: Dict[str, Any], verdict: Dict[str, Any]) -> Dict[str, Any]:
    """Fold the judge's unsupported claims into the deterministic findings."""
    findings = [f for f in (checks.get("findings") or []) if f.get("kind") != "judge_unsupported"]
    for claim in verdict.get("unsupported_claims") or []:
        findings.append(_finding("judge_unsupported", f"AI judge: {claim}", claim))
    merged = summarise(findings, checks.get("claims") or {})
    # Blend: the judge's own score counts for half when it ran.
    if checks.get("score") is not None and verdict.get("score") is not None:
        merged["score"] = round(min(merged["score"], 0.5 * merged["score"] + 0.5 * verdict["score"]), 3)
        merged["risk"] = risk_level(merged["score"], findings)
    return merged
