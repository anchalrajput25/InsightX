# backend/text_to_sql/schema_linker.py
#
# Schema linking: map the question to tables, columns, joins, filters
# and metrics of the (focused) schema.
#
# The LLM returns JSON. The JSON is never trusted as-is:
# schema_verifier.verify_linking() checks every table, column, join and
# filter value against the real schema before anything reaches SQL
# generation.

import json
import re
from typing import Any, Dict, Optional

from langchain_core.prompts import ChatPromptTemplate

from backend.llm_service import get_llm


# ============================================================
# LLM
# ============================================================

llm = get_llm(job="schema_link")


# ============================================================
# PROMPT
# ============================================================

prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
You are the schema-linking step of a Text-to-SQL system that works on
ANY PostgreSQL database. Map the user's question to the schema below.

Rules:
- Use ONLY tables and columns that appear in the schema. Never invent
  tables, columns, relationships or business formulas.
- Pick the smallest set of tables that answers the question, plus the
  tables needed to join them.
- Joins must use the listed relationships / join_paths when they exist.
- For filters on text, use the stored value exactly as listed in
  stored_values or value_hints (case and accents matter).
- A metric that must be derived (e.g. a product of two columns) may be
  written as an SQL expression, but only from columns in the schema and
  only when the question or schema supports that definition.
- If the question is ambiguous (several plausible columns, date fields
  or metric definitions), list it under "ambiguities" and still give
  the most likely mapping.
- If something required does not exist in the schema, list it under
  "missing".

Return ONLY one JSON object, no prose, no markdown:
{{
  "tables": ["<table>", ...],
  "columns": [
    {{"table": "<table>", "column": "<column>",
      "role": "measure|dimension|filter|time|join|identifier|sort",
      "reason": "<few words>"}}
  ],
  "joins": [{{"left": "<table>.<column>", "right": "<table>.<column>"}}],
  "filters": [
    {{"table": "<table>", "column": "<column>", "operator": "=",
      "value": "<value>"}}
  ],
  "metrics": [{{"name": "<name>", "expression": "<SQL expression>"}}],
  "time": {{"table": "<table>", "column": "<column>",
            "interpretation": "<e.g. year 2017>"}},
  "ambiguities": ["<text>"],
  "missing": ["<text>"]
}}
Use [] or null for parts that do not apply.
"""
    ),
    (
        "human",
        """
ORIGINAL USER QUESTION:
{question}

QUERY REWRITER OUTPUT:
{rewritten_query}

QUERY DECOMPOSER OUTPUT:
{decomposed_query}

DATABASE SCHEMA:
(Only the tables relevant to this question are shown in full.
 "join_paths" are join conditions taken from the database relationships.
 "value_hints" are stored values that match words in the question.
 "other_tables_not_shown" lists the remaining tables by name only; you
 may use one of them only if it is clearly required.)
{schema}
"""
    ),
])


# ============================================================
# HELPERS
# ============================================================

MAX_SCHEMA_CHARS = 60_000


def _safe_json(data: Any) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False, default=str)


_THINK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", flags=re.DOTALL | re.IGNORECASE)


def parse_linking(raw: str) -> Optional[Dict[str, Any]]:
    """
    Extract the JSON object from an LLM response (tolerates <think>
    blocks, code fences and text around the object). None if absent.
    """
    text = _THINK_RE.sub("", raw or "")
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    decoder = json.JSONDecoder()
    for start in [match.start() for match in re.finditer(r"\{", text)]:
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("tables" in value or "columns" in value):
            return value
    return None


# ============================================================
# MAIN FUNCTIONS
# ============================================================

def link_schema(
    question: str,
    rewritten_query: str,
    decomposed_query: str,
    schema: Dict[str, Any],
) -> str:
    """Raw linker output (JSON text)."""
    if not question or not question.strip():
        raise ValueError("Question cannot be empty.")
    if not schema:
        raise ValueError("Database schema cannot be empty.")

    schema_text = _safe_json(schema)
    if len(schema_text) > MAX_SCHEMA_CHARS:
        schema_text = schema_text[:MAX_SCHEMA_CHARS] + "\n...[schema truncated]..."

    messages = prompt.format_messages(
        question=question.strip(),
        rewritten_query=str(rewritten_query or "").strip(),
        decomposed_query=str(decomposed_query or "").strip(),
        schema=schema_text,
    )
    response = llm.invoke(messages)
    return str(getattr(response, "content", response)).strip()


def link_schema_structured(
    question: str,
    rewritten_query: str,
    decomposed_query: str,
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """{"raw": <LLM text>, "parsed": <dict or None>}"""
    raw = link_schema(question, rewritten_query, decomposed_query, schema)
    return {"raw": raw, "parsed": parse_linking(raw)}
