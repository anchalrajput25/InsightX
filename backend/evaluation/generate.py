# backend/evaluation/generate.py
#
# Propose candidate golden cases for ANY connected database.
#
# The SQL model sees the profiled schema (tables, columns, types,
# relationships, example values of low-cardinality text columns) and
# proposes question + SQL pairs across difficulty categories. Every
# candidate is then checked deterministically (safety gate, EXPLAIN,
# execution, non-empty, not huge); only passing candidates are kept,
# marked verified: false.
#
# A person must review candidates (is the SQL really the right answer
# to the question?) and set verified: true. Generated SQL is never
# trusted as ground truth on its own.

import re
from typing import Any, Dict, List

import yaml
from sqlalchemy.engine import Engine

from backend.evaluation.golden import GoldenCase, GoldenSet
from backend.evaluation.runner import EVAL_MAX_ROWS
from backend.schema_profiler import get_database_schema
from backend.text_to_sql.query_executor import execute_query
from backend.text_to_sql.sql_safety import check_sql_safety
from backend.text_to_sql.sql_validator import validate_sql

CATEGORIES = ["simple", "filter", "aggregation", "join", "temporal", "ranking", "multi_join"]

# Text columns with at most this many distinct values get example
# values in the prompt, so filters use real stored values.
EXAMPLE_VALUE_MAX_DISTINCT = 25
EXAMPLE_VALUES_PER_COLUMN = 8
MAX_SCHEMA_CHARS = 14_000

PROMPT = """
You are creating an evaluation set for a natural-language-to-SQL
system on a PostgreSQL database. Only the schema below exists.

Write {count} realistic questions a business user could ask about this
data, each with ONE correct read-only PostgreSQL query that answers it.

Cover these categories, spread evenly: {categories}
- simple: count/list from one table
- filter: a condition on a stored value (use exact example values)
- aggregation: SUM/AVG/COUNT grouped by a column
- join: needs two tables joined on a listed relationship
- temporal: uses a date/timestamp column (year, month, ranges)
- ranking: top/bottom N or the highest/lowest item
- multi_join: needs three or more tables

Rules:
- Use only the tables, columns and relationships listed.
- Questions must be unambiguous: say exactly what is counted/summed.
- Return small results (one value or at most 20 rows); use LIMIT for
  rankings and ORDER BY when order matters.
- Do not use ROUND or formatting; return raw values.
- When a question asks for the top/highest item, select the item and
  the measure.

Return ONLY YAML, no prose:
cases:
  - question: <question>
    expected_sql: |
      <SQL>
    tags: [<category>]
    order_matters: <true if the question asks for an ordered list>

SCHEMA:
{schema}
"""


def describe_schema(engine: Engine) -> str:
    """Compact, generic schema description with example values."""
    profile = get_database_schema(engine)
    lines: List[str] = []
    for table in profile.get("tables", []):
        if table.get("error"):
            continue
        lines.append(f"TABLE {table['table_name']} ({table.get('row_count', '?')} rows)")
        for column in table.get("columns", []):
            entry = f"  - {column['column_name']} {column.get('data_type', '')}"
            distinct = column.get("distinct_count")
            sample_values = _example_values(table, column, distinct)
            if sample_values:
                entry += f"  values: {sample_values}"
            lines.append(entry)
    relationships = profile.get("relationships", [])
    if relationships:
        lines.append("RELATIONSHIPS")
        lines += [
            f"  - {r['child_table']}.{r['child_column']} = {r['parent_table']}.{r['parent_column']}"
            for r in relationships
        ]
    return "\n".join(lines)[:MAX_SCHEMA_CHARS]


def _example_values(table: Dict[str, Any], column: Dict[str, Any], distinct: Any) -> List[str]:
    data_type = str(column.get("data_type", "")).lower()
    if "char" not in data_type and "text" not in data_type:
        return []
    if not isinstance(distinct, int) or distinct == 0 or distinct > EXAMPLE_VALUE_MAX_DISTINCT:
        return []
    if column.get("top_values"):
        return list(column["top_values"])[:EXAMPLE_VALUES_PER_COLUMN]
    values = []
    for row in table.get("sample_rows", []):
        value = row.get(column["column_name"])
        if value not in (None, "") and value not in values:
            values.append(value)
    return values[:EXAMPLE_VALUES_PER_COLUMN]


def _parse_cases(raw: str) -> List[Dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL | re.IGNORECASE)
    fenced = re.search(r"```(?:yaml)?\s*\n(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return []
    cases = data.get("cases") if isinstance(data, dict) else data
    return [case for case in cases or [] if isinstance(case, dict)]


def _slug(text: str, taken: set) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:50] or "case"
    candidate, counter = slug, 2
    while candidate in taken:
        candidate, counter = f"{slug}_{counter}", counter + 1
    taken.add(candidate)
    return candidate


def check_candidate(sql: str, engine: Engine) -> str:
    """Return '' when the SQL is usable as ground truth, else why not."""
    safety = check_sql_safety(sql)
    if not safety["safe"]:
        return "unsafe: " + "; ".join(safety["errors"])
    validation = validate_sql(sql, engine=engine, check_values=True)
    if not validation["valid"]:
        return "invalid: " + "; ".join(validation["errors"])
    result = execute_query(sql, engine, max_rows=EVAL_MAX_ROWS)
    if not result["success"]:
        return "execution failed: " + str(result["error"])
    if result["row_count"] == 0:
        return "returns no rows"
    if result["truncated"] or result["row_count"] > 100:
        return "result too large for a golden case"
    return ""


def generate_candidates(engine: Engine, name: str, count: int = 30, llm=None) -> Dict[str, Any]:
    """
    Ask the SQL model for candidate cases and keep those that pass the
    deterministic checks. Returns {"golden": GoldenSet, "rejected": [...]}.
    """
    if llm is None:
        from backend.llm_service import get_llm
        llm = get_llm(job="benchmark_generate", max_tokens=8000)

    prompt = PROMPT.format(count=count, categories=", ".join(CATEGORIES), schema=describe_schema(engine))
    raw = llm.invoke(prompt)
    proposals = _parse_cases(getattr(raw, "content", raw))

    kept: List[GoldenCase] = []
    rejected: List[Dict[str, str]] = []
    taken: set = set()
    seen_sql: set = set()

    for proposal in proposals:
        question = str(proposal.get("question") or "").strip()
        sql = str(proposal.get("expected_sql") or "").strip().rstrip(";")
        if not question or not sql:
            continue
        normalized = " ".join(sql.lower().split())
        if normalized in seen_sql:
            continue
        seen_sql.add(normalized)

        problem = check_candidate(sql, engine)
        if problem:
            rejected.append({"question": question, "sql": sql, "reason": problem})
            continue

        tags = [str(tag) for tag in proposal.get("tags") or [] if str(tag) in CATEGORIES] or ["untagged"]
        kept.append(GoldenCase(
            id=_slug(question, taken),
            question=question,
            expected_sql=sql,
            tags=tags,
            order_matters=bool(proposal.get("order_matters", False)),
            verified=False,
            notes="Generated candidate: review the SQL, then set verified: true.",
        ))

    return {
        "golden": GoldenSet(
            name=name,
            description="Generated candidates. Review each case before trusting it.",
            cases=kept,
        ),
        "rejected": rejected,
    }
