import re
from typing import Any, Dict, Optional

from backend.llm_service import get_llm


# ============================================================
# LLM
# ============================================================

llm = get_llm(job="sql_repair")


# ============================================================
# SQL CLEANING
# ============================================================

def clean_sql(sql: str) -> str:
    """
    Remove markdown code fences and surrounding whitespace.
    """

    if not isinstance(sql, str):
        return ""

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
        flags=re.IGNORECASE,
    )

    return sql.strip()


# ============================================================
# RESPONSE EXTRACTION
# ============================================================

def extract_sql(response: str) -> str:
    """
    Extract SQL from the LLM response.
    """

    response = response.strip()

    # Prefer SQL inside markdown code block
    code_match = re.search(
        r"```(?:sql|postgresql)?\s*(.*?)```",
        response,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if code_match:
        return clean_sql(code_match.group(1))

    # Otherwise use the full response
    return clean_sql(response)


# ============================================================
# SCHEMA COMPACTION
# ============================================================

def compact_schema(schema: Any, max_chars: int = 12000) -> str:
    """
    Keep schema context within a reasonable LLM input size.
    """

    if schema is None:
        return ""

    if isinstance(schema, dict) and isinstance(schema.get("tables"), list):
        # Agent schema: {"tables": [{table_name, columns: [...]}],
        #                "relationships": [{source_table, ...}]}
        lines = []
        for table in schema["tables"]:
            columns = ", ".join(
                f"{column.get('column_name')} {column.get('data_type', '')}".strip()
                for column in table.get("columns", [])
            )
            lines.append(f"TABLE {table.get('table_name')}: {columns}")
        relationships = schema.get("relationships") or []
        if relationships:
            lines.append("RELATIONSHIPS:")
            lines.extend(
                f"{rel.get('source_table')}.{rel.get('source_column')} = "
                f"{rel.get('target_table')}.{rel.get('target_column')}"
                for rel in relationships
            )
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[schema truncated for context size]..."
        return text

    if isinstance(schema, str):
        text = schema

    elif isinstance(schema, dict):
        lines = []

        for table_name, table_info in schema.items():

            lines.append(
                f"TABLE: {table_name}"
            )

            if isinstance(table_info, dict):

                columns = table_info.get("columns", [])

                if columns:
                    lines.append(
                        "COLUMNS: "
                        + ", ".join(
                            str(column)
                            for column in columns
                        )
                    )

                relationships = table_info.get(
                    "relationships",
                    []
                )

                if relationships:
                    lines.append(
                        "RELATIONSHIPS: "
                        + ", ".join(
                            str(r)
                            for r in relationships
                        )
                    )

            else:
                lines.append(
                    str(table_info)
                )

        text = "\n".join(lines)

    else:
        text = str(schema)

    if len(text) > max_chars:
        text = text[:max_chars]

        text += (
            "\n...[schema truncated for context size]..."
        )

    return text


# ============================================================
# PROMPT
# ============================================================

def build_correction_prompt(
    question: str,
    failed_sql: str,
    error_message: str,
    schema: Any = None,
    previous_attempts: Optional[list[str]] = None,
) -> str:
    """
    Build the SQL correction prompt.
    """

    schema_text = compact_schema(schema)

    attempts_text = ""

    if previous_attempts:

        attempts_text = "\n\nPREVIOUS FAILED ATTEMPTS:\n"

        for index, attempt in enumerate(
            previous_attempts,
            start=1,
        ):
            attempts_text += (
                f"\nAttempt {index}:\n"
                f"{attempt}\n"
            )

    prompt = f"""
You are a PostgreSQL SQL correction engine.

Your task is to correct a failed SQL query.

You MUST follow these rules:

1. Return exactly ONE corrected PostgreSQL SQL query.
2. The query must be READ-ONLY.
3. Only SELECT-style queries are allowed.
4. Do NOT use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE,
   TRUNCATE, MERGE, GRANT, REVOKE, COPY, CALL, DO, or other
   write/destructive operations.
5. Use only tables and columns supported by the provided schema.
6. Preserve the original user's intent.
7. Do not invent tables or columns.
8. Fix syntax errors, invalid columns, invalid tables,
   incorrect aliases, JOIN problems, aggregation problems,
   GROUP BY problems, and PostgreSQL-specific errors when
   the error information allows you to do so.
9. If the error says a filter value does not occur in the data
   and lists stored values, replace the literal with the matching
   stored value exactly (same spelling, case and accents).
10. If the SQL is already correct, return it unchanged.
11. Do not explain your answer.
12. Do not use markdown.
13. Return SQL only.

USER QUESTION:
{question}

FAILED SQL:
{failed_sql}

DATABASE ERROR:
{error_message}

DATABASE SCHEMA:
{schema_text}
{attempts_text}

Return ONLY the corrected PostgreSQL SQL query.
"""

    return prompt.strip()


# ============================================================
# SQL CORRECTION
# ============================================================

def correct_sql(
    question: str,
    failed_sql: str,
    error_message: str,
    schema: Any = None,
    previous_attempts: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """
    Ask the LLM to correct failed SQL.
    """

    if not question.strip():

        return {
            "success": False,
            "sql": "",
            "error": "Question is empty.",
        }

    if not failed_sql.strip():

        return {
            "success": False,
            "sql": "",
            "error": "Failed SQL is empty.",
        }

    if not error_message.strip():

        return {
            "success": False,
            "sql": "",
            "error": "Database error message is empty.",
        }

    prompt = build_correction_prompt(
        question=question,
        failed_sql=failed_sql,
        error_message=error_message,
        schema=schema,
        previous_attempts=previous_attempts,
    )

    try:

        response = llm.invoke(prompt)

        corrected_sql = extract_sql(
            response.content
        )

    except Exception as exc:

        return {
            "success": False,
            "sql": "",
            "error": str(exc),
        }

    if not corrected_sql:

        return {
            "success": False,
            "sql": "",
            "error": "LLM returned empty SQL.",
        }

    # --------------------------------------------------------
    # BASIC SAFETY CHECK
    # --------------------------------------------------------

    # Safety is enforced by the sql_safety gate, which the agent runs
    # on every corrected query before validation and execution.

    without_trailing_semicolon = (
        corrected_sql.rstrip(";").strip()
    )

    if ";" in without_trailing_semicolon:

        return {
            "success": False,
            "sql": corrected_sql,
            "error": (
                "Corrected SQL contains multiple "
                "SQL statements."
            ),
        }

    return {
        "success": True,
        "sql": corrected_sql,
        "error": None,
    }


# ============================================================
# SIMPLE HELPER
# ============================================================

def get_corrected_sql(
    question: str,
    failed_sql: str,
    error_message: str,
    schema: Any = None,
) -> str:
    """
    Return corrected SQL directly.

    Raises ValueError if correction fails.
    """

    result = correct_sql(
        question=question,
        failed_sql=failed_sql,
        error_message=error_message,
        schema=schema,
    )

    if not result["success"]:

        raise ValueError(
            result["error"]
        )

    return result["sql"]


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    question = """
    Show the top 5 customers by number of orders.
    """

    failed_sql = """
    SELECT
        c.customer_id,
        COUNT(o.order_id) AS order_count
    FROM olist_customers_dataset c
    JOIN olist_orders_dataset o
        ON c.customer_id = o.customer_id
    GROUP BY c.customer_id
    ORDER BY order_count DESC
    LIMITT 5;
    """

    error_message = """
    PostgreSQL syntax error:
    syntax error at or near "LIMITT"
    """

    schema = {
        "olist_customers_dataset": {
            "columns": [
                "customer_id",
                "customer_unique_id",
                "customer_zip_code_prefix",
                "customer_city",
                "customer_state",
            ]
        },
        "olist_orders_dataset": {
            "columns": [
                "order_id",
                "customer_id",
                "order_status",
                "order_purchase_timestamp",
            ]
        },
    }

    print("=" * 70)
    print("SQL ERROR CORRECTION TEST")
    print("=" * 70)

    result = correct_sql(
        question=question,
        failed_sql=failed_sql,
        error_message=error_message,
        schema=schema,
    )

    print("\nSUCCESS:", result["success"])
    print("\nCORRECTED SQL:")
    print(result["sql"])

    if result["error"]:
        print("\nERROR:")
        print(result["error"])