import re

from langchain_core.prompts import ChatPromptTemplate
from backend.llm_service import get_llm


# ============================================================
# LLM
# ============================================================
llm = get_llm(job="sql_generate")


# ============================================================
# PROMPT
# ============================================================

prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
You are the SQL Generation Engine of an advanced,
production-grade Text-to-SQL system.

Generate ONE READ-ONLY PostgreSQL SQL statement for
arbitrary PostgreSQL databases.

The Schema Linking context is the source of truth.
Never invent tables, columns, relationships, keys,
data types, or business definitions.

Use the original question, analytical plan, and linked
schema to generate correct SQL.

============================================================
COMPLEX DATABASE SUPPORT
============================================================

Support:

- multiple JOINs
- multi-hop JOIN paths
- INNER / LEFT / RIGHT / FULL / CROSS JOIN
- CTEs and multiple CTEs
- nested subqueries
- correlated subqueries
- EXISTS / NOT EXISTS
- IN / NOT IN
- GROUP BY / HAVING
- DISTINCT
- CASE / COALESCE / NULLIF
- conditional aggregation
- window functions
- ROW_NUMBER / RANK / DENSE_RANK
- LAG / LEAD
- running totals
- partitioned calculations
- top-K per group
- YoY / MoM / WoW analysis
- growth calculations
- contribution analysis
- cohort / segmentation analysis
- UNION / UNION ALL / INTERSECT / EXCEPT
- PostgreSQL date/time operations

For complex analytical questions, use CTEs or subqueries
to represent dependent analytical stages clearly.

Do not create one independent SQL query per sub-question.
Combine compatible stages into one SQL statement.

============================================================
LARGE DATABASE RULES
============================================================

The database may contain hundreds of tables.

Use ONLY the relevant tables, columns, keys and join paths
provided by Schema Linking.

Do not use the entire database schema.

If a required table, column, or relationship is missing,
return NEEDS_CLARIFICATION.

If multiple competing join paths exist and no path is
selected by Schema Linking, return NEEDS_CLARIFICATION.

Never guess.

============================================================
JOIN AND AGGREGATION CORRECTNESS
============================================================

Follow the supplied join path exactly.

Do not skip intermediate relationship tables.

Before aggregating, consider one-to-many fan-out.

Prevent accidental metric multiplication by:

- pre-aggregating at the correct grain
- using appropriate CTEs
- joining aggregated results where necessary
- using DISTINCT only when logically justified

Do not use DISTINCT merely to hide a bad join.

Every non-aggregated selected column must be compatible
with GROUP BY.

Use WHERE for row-level filtering and HAVING for
post-aggregation filtering.

============================================================
METRICS
============================================================

Do not invent business formulas.

If a metric is directly supported by the schema and
query analysis, calculate it.

If the metric requires a business definition that is
not available, return NEEDS_CLARIFICATION.

For derived metrics, expose the calculation logically.

Example:

absolute_growth =
current_value - previous_value

percentage_growth =
(current_value - previous_value)
/
NULLIF(previous_value, 0) * 100

Only choose an interpretation explicitly supported by
the query analysis.

============================================================
TIME
============================================================

Use the temporal interpretation supplied by the query
analysis.

If "last year", "previous year", rolling periods, etc.
remain ambiguous, return NEEDS_CLARIFICATION.

Do not silently invent a date interpretation.

============================================================
RANKING
============================================================

Use:

ROW_NUMBER() for unique row positions.

RANK() when tied values should share rank and gaps are
acceptable.

DENSE_RANK() when tied values should share rank without
gaps.

For top-N per group, use a partitioned ranking strategy
when appropriate.

When a question asks for the top/most/least/highest item, SELECT
both the item and the measure used to rank it (e.g. the COUNT or
SUM), so the answer can report the number.

============================================================
FILTER VALUES
============================================================

Text comparisons in PostgreSQL are exact (case and accents
matter). Use filter values exactly as stored; when the context
shows a stored value (e.g. a previous database result), copy it
verbatim.

============================================================
READ-ONLY SECURITY
============================================================

Generate exactly ONE SQL statement.

Never generate:

INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
CREATE
GRANT
REVOKE
CALL
DO
EXECUTE

Do not generate multiple statements separated by semicolons.

Use explicit columns instead of SELECT * unless explicitly
required.

============================================================
OUTPUT
============================================================

Return exactly:

STATUS:
READY / NEEDS_CLARIFICATION / FAILED

SQL:
<one PostgreSQL statement>

QUERY_PLAN:
1. ...
2. ...
3. ...

USED_TABLES:
- ...

USED_COLUMNS:
- ...

JOIN_PATH:
- ...

SQL_FEATURES:
- JOIN: Yes/No
- MULTI_HOP_JOIN: Yes/No
- CTE: Yes/No
- MULTIPLE_CTES: Yes/No
- SUBQUERY: Yes/No
- CORRELATED_SUBQUERY: Yes/No
- AGGREGATION: Yes/No
- CONDITIONAL_AGGREGATION: Yes/No
- WINDOW_FUNCTION: Yes/No
- RANKING: Yes/No
- TIME_ANALYSIS: Yes/No
- UNION: Yes/No
- DISTINCT: Yes/No

ASSUMPTIONS:
- ...

VALIDATION_NOTES:
- ...

If clarification is required:

STATUS:
NEEDS_CLARIFICATION

SQL:
NOT GENERATED

CLARIFICATION_REQUIRED:
...

REASON:
...

Do not answer the user in natural language.
"""
    ),
    (
        "human",
        """
ORIGINAL QUESTION:
{question}

ANALYTICAL PLAN:
{analytical_plan}

LINKED SCHEMA:
{linked_schema}
"""
    )
])


# ============================================================
# CONTEXT COMPACTION
# ============================================================

def compact_text(text: str, max_chars: int) -> str:
    """
    Prevent very large intermediate agent outputs from
    exceeding the LLM input-token limit.
    """

    if not text:
        return ""

    text = text.strip()

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + "\n...[context truncated]..."


def extract_relevant_schema(linked_schema: str) -> str:
    """
    Keep the most useful Schema Linker sections for SQL
    generation instead of sending the entire verbose output.
    """

    important_sections = [
        "Core Tables",
        "Supporting Tables",
        "Table-Concept Mapping",
        "Column Mapping",
        "Required Join Paths",
        "Actual Constraints Used",
        "Inferred Relationships Used",
        "Derived Metrics",
        "Filters",
        "Time Fields",
        "Grouping Fields",
        "Ranking Fields",
        "Ambiguities",
        "Missing Information",
        "Final Relevant Schema",
    ]

    lines = linked_schema.splitlines()

    selected = []
    current_section = None
    include = False

    normalized_sections = {
        section.lower()
        for section in important_sections
    }

    for line in lines:
        stripped = line.strip()

        # Detect markdown headings / named sections.
        heading = stripped.lstrip("#").strip().rstrip(":")
        normalized_heading = heading.lower()

        matched = None

        for section in important_sections:
            if normalized_heading == section.lower():
                matched = section
                break

        if matched:
            current_section = matched
            include = True
            selected.append(line)
            continue

        # Stop when a new major section begins.
        if stripped.startswith("## ") or stripped.startswith("### "):
            if current_section is not None:
                possible_heading = (
                    stripped.lstrip("#").strip().rstrip(":")
                )

                if possible_heading.lower() not in normalized_sections:
                    include = False
                    current_section = None

        if include:
            selected.append(line)

    result = "\n".join(selected).strip()

    # If the format did not contain recognizable headings,
    # fall back to a bounded version of the full output.
    if not result:
        result = linked_schema

    return compact_text(result, 10000)


def build_analytical_plan(
    rewritten_query: str,
    decomposed_query: str,
) -> str:
    """
    Keep the useful reasoning context while avoiding the
    verbose repeated formatting produced by earlier agents.
    """

    rewritten = compact_text(rewritten_query, 4000)
    decomposed = compact_text(decomposed_query, 5000)

    return (
        "QUERY REWRITER:\n"
        f"{rewritten}\n\n"
        "QUERY DECOMPOSER:\n"
        f"{decomposed}"
    )


# ============================================================
# SQL CLEANER
# ============================================================

def clean_sql(sql: str) -> str:
    """
    Remove accidental Markdown code fences.
    """

    sql = sql.strip()

    sql = re.sub(
        r"^```(?:sql|postgresql)?\s*",
        "",
        sql,
        flags=re.IGNORECASE,
    )

    sql = re.sub(
        r"\s*```$",
        "",
        sql,
    )

    return sql.strip()


# ============================================================
# READ-ONLY CHECK
# ============================================================

def is_read_only_sql(sql: str) -> bool:
    """
    Preliminary deterministic check.

    Final SQL safety will be handled by the dedicated
    sql_safety.py component later.
    """

    sql = clean_sql(sql)

    normalized = sql.lower().strip()

    if not (
        normalized.startswith("select ")
        or normalized.startswith("with ")
    ):
        return False

    forbidden = [
        r"\binsert\b",
        r"\bupdate\b",
        r"\bdelete\b",
        r"\bdrop\b",
        r"\balter\b",
        r"\btruncate\b",
        r"\bcreate\b",
        r"\bgrant\b",
        r"\brevoke\b",
        r"\bcall\b",
        r"\bdo\b",
        r"\bexecute\b",
    ]

    for pattern in forbidden:
        if re.search(pattern, normalized):
            return False

    return True


def has_multiple_statements(sql: str) -> bool:
    """
    Detect multiple SQL statements.

    A single trailing semicolon is allowed.
    """

    sql = clean_sql(sql)

    without_trailing = sql.rstrip(";").strip()

    return ";" in without_trailing


# ============================================================
# MAIN SQL GENERATOR
# ============================================================

# Extra candidates use a sampling LLM and one of these strategies, so
# they explore different (but valid) formulations of the same query.
CANDIDATE_STRATEGIES = [
    "",
    "Write the query step by step with CTEs: first compute each needed intermediate result, then combine them.",
    "Write the most direct query possible: plain JOINs, WHERE and GROUP BY, without CTEs unless they are required.",
    "Re-read the question carefully, decide exactly what one result row represents, then write the query.",
]

_candidate_llms = {}


def candidate_llm(temperature: float):
    """Sampling LLM for extra SQL candidates (cached per temperature)."""
    if temperature not in _candidate_llms:
        _candidate_llms[temperature] = get_llm(job="sql_candidate", temperature=temperature)
    return _candidate_llms[temperature]


def generate_sql(
    question: str,
    rewritten_query: str,
    decomposed_query: str,
    linked_schema: str,
    prepared_schema: bool = False,
    model=None,
    strategy: str = "",
) -> str:
    """
    prepared_schema: linked_schema is already the verified, rendered
        schema (schema_verifier.render_linked_schema) and is passed
        through unchanged instead of being section-filtered.
    model: LLM to use (default: the module's deterministic SQL LLM).
    strategy: extra instruction for diversified candidates.
    """

    if not question.strip():
        raise ValueError("Question cannot be empty.")

    if not linked_schema.strip():
        raise ValueError(
            "Schema linking output cannot be empty."
        )

    analytical_plan = build_analytical_plan(
        rewritten_query,
        decomposed_query,
    )
    if strategy:
        analytical_plan += f"\n\nAPPROACH FOR THIS ATTEMPT:\n{strategy}"

    relevant_schema = (
        compact_text(linked_schema, 16000)
        if prepared_schema
        else extract_relevant_schema(linked_schema)
    )

    messages = prompt.format_messages(
        question=compact_text(
            question.strip(),
            3000,
        ),
        analytical_plan=compact_text(
            analytical_plan,
            9000,
        ),
        linked_schema=compact_text(
            relevant_schema,
            16000,
        ),
    )

    response = (model or llm).invoke(messages)

    result = response.content.strip()

    return result
