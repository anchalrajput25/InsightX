# backend/text_to_sql/follow_ups.py
#
# 3-4 related questions to ask next, shown as buttons under an answer.
# A light AI model suggests them from the question, the SQL and a few
# result rows; if that fails, simple questions built from the result's
# columns are used instead, so there is always something to click.

import json
import re
from typing import Any, Dict, List, Optional

from backend.llm_service import get_llm

llm = get_llm(job="follow_ups", max_tokens=400)

MAX_FOLLOW_UPS = 4
TIME_WORDS = re.compile(r"(date|time|month|year|day|week|quarter|period|_at$)", re.IGNORECASE)

PROMPT = """
A user asked a question about their database and got an answer.
Suggest {count} short follow-up questions they would likely ask next
about the SAME data. Each must be answerable with SQL on the tables used,
one line, written as a question, in plain words, under 15 words.
Mix: one that breaks the result down further (by a category or by time),
one that compares (vs last year / vs other groups), and one "why" or
"how to improve" question when the answer is a number that can change.
Do not repeat the original question.

Return ONLY a JSON list of strings.

QUESTION: {question}
SQL: {sql}
COLUMNS: {columns}
FIRST ROWS: {rows}
ANSWER: {answer}
"""


def _clean(items: Any, question: str) -> List[str]:
    seen = {question.strip().lower().rstrip("?")}
    found = []
    for item in items if isinstance(items, list) else []:
        text = re.sub(r"\s+", " ", str(item or "")).strip().strip('"').strip()
        text = re.sub(r"^\d+[.)]\s*", "", text)
        if not text or len(text) > 160:
            continue
        if not text.endswith("?"):
            text += "?"
        key = text.lower().rstrip("?")
        if key in seen:
            continue
        seen.add(key)
        found.append(text)
    return found[:MAX_FOLLOW_UPS]


def _parse(raw: str) -> List[Any]:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL | re.IGNORECASE)
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, list):
                return value
        except json.JSONDecodeError:
            pass
    # one question per line
    return [line.strip(" -*•\t") for line in text.splitlines() if line.strip().endswith("?")]


def simple_follow_ups(question: str, result: Dict[str, Any]) -> List[str]:
    """Follow-ups built without the AI, from the shape of the result."""
    execution = result.get("execution_result") or {}
    columns = [str(c) for c in execution.get("columns") or []]
    rows = execution.get("rows") or []
    has_time = any(TIME_WORDS.search(c) for c in columns)
    found = []
    if len(rows) > 1 and columns:
        found.append(f"Which {columns[0].replace('_', ' ')} is at the bottom of this list?")
    if not has_time:
        found.append("How has this changed month by month?")
    found.append("How does this compare with the same period last year?")
    found.append("Why did this change recently, and how can it improve?")
    return _clean(found, question)


def suggest_follow_ups(question: str, result: Dict[str, Any], model=None) -> List[str]:
    question = (question or "").strip()
    if not question or str(result.get("status") or "").upper() != "SUCCESS":
        return []
    execution = result.get("execution_result") or {}
    prompt = PROMPT.format(
        count=3,
        question=question,
        sql=str(result.get("generated_sql") or "")[:1500],
        columns=", ".join(str(c) for c in execution.get("columns") or [])[:500],
        rows=json.dumps((execution.get("rows") or [])[:5], default=str)[:1500],
        answer=str(result.get("answer") or "")[:800],
    )
    try:
        response = (model or llm).invoke(prompt)
        found = _clean(_parse(str(getattr(response, "content", response))), question)
    except Exception:
        found = []
    if len(found) < 2:
        found = _clean(found + simple_follow_ups(question, result), question)
    return found[:MAX_FOLLOW_UPS]
