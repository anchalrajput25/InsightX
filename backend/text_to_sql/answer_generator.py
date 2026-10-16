from typing import Any, Dict, List

from langchain_core.messages import HumanMessage

from backend.config import query_settings
from backend.llm_service import get_llm


# ---------------------------------------------------------
# LLM
# ---------------------------------------------------------

llm = get_llm(job="answer")

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def compact_rows(
    rows: List[Dict[str, Any]],
    max_rows: int = query_settings.max_answer_rows,
) -> List[Dict[str, Any]]:
    """
    Limit the amount of database output sent to the LLM.
    """

    return rows[:max_rows]


def build_answer_prompt(
    question: str,
    rows: List[Dict[str, Any]],
    columns: List[str],
    row_count: int,
    truncated: bool = False,
) -> str:

    limited_rows = compact_rows(rows)

    if truncated:
        row_count_text = (
            f"More than {row_count} rows (the result was cut off at "
            f"{row_count} rows; the full result is larger)"
        )
    else:
        row_count_text = str(row_count)

    if len(limited_rows) < len(rows):
        row_count_text += (
            f". Only the first {len(limited_rows)} rows are shown below"
        )

    return f"""
You are the final answer generation component of a production
Text-to-SQL system.

Your task is to answer the user's question using ONLY the
validated database result provided below.

USER QUESTION:
{question}

RESULT COLUMNS:
{columns}

TOTAL RESULT ROW COUNT:
{row_count_text}

DATABASE RESULT:
{limited_rows}

STRICT RULES:

1. Answer only from the provided database result.
2. Do NOT invent facts, values, entities, or calculations
   that are not supported by the result.
3. Do NOT assume information that is not present.
4. If the result is insufficient to answer the question,
   clearly say that the available result is insufficient.
5. If the result is empty, do not fabricate an answer.
6. Explain important numbers clearly.
7. Keep the answer concise but useful.
8. Do not mention internal agents, prompts, retrieval,
   LangGraph, SQL generation, or implementation details.
9. Do not output SQL.
10. Do not claim certainty beyond what the result supports.
11. If not all rows are shown, do not present the shown rows as
    the complete result.
12. Write text values (names, cities, categories, statuses) exactly
    as they appear in the result: do not translate, re-case or add
    accents.
13. When the result contains the number behind the answer (a count,
    sum, average, ...), state that number.

Return only the natural-language answer.
""".strip()


# ---------------------------------------------------------
# ANSWER GENERATION
# ---------------------------------------------------------

def generate_answer(
    question: str,
    result: Dict[str, Any],
    correction_note: str = "",
) -> Dict[str, Any]:
    """
    correction_note: feedback from claim verification when a previous
    answer stated numbers/values that are not in the result.
    """

    if not isinstance(result, dict):
        return {
            "success": False,
            "answer": "",
            "error": "Invalid result format.",
        }

    if not result.get("success", False):
        return {
            "success": False,
            "answer": "",
            "error": (
                result.get("error")
                or "Database query execution failed."
            ),
        }

    rows = result.get("rows", [])
    columns = result.get("columns", [])
    row_count = result.get("row_count", len(rows))

    if not rows:
        return {
            "success": True,
            "answer": (
                "The query returned no matching records, "
                "so there is no result to report."
            ),
            "error": None,
        }

    prompt = build_answer_prompt(
        question=question,
        rows=rows,
        columns=columns,
        row_count=row_count,
        truncated=bool(result.get("truncated", False)),
    )
    if correction_note:
        prompt += (
            "\n\nYOUR PREVIOUS ANSWER WAS REJECTED:\n"
            + correction_note
            + "\nWrite the answer again. Every number and value you state must appear in the "
            "DATABASE RESULT above, written the same way (you may round). Do not calculate new "
            "numbers (no differences, totals, shares or percentages that are not in the result)."
        )

    try:
        response = llm.invoke(
            [HumanMessage(content=prompt)]
        )

        answer = response.content.strip()

        if not answer:
            return {
                "success": False,
                "answer": "",
                "error": "LLM returned an empty answer.",
            }

        return {
            "success": True,
            "answer": answer,
            "error": None,
        }

    except Exception as exc:
        return {
            "success": False,
            "answer": "",
            "error": str(exc),
        }


# ---------------------------------------------------------
# SIMPLE HELPER
# ---------------------------------------------------------

def get_answer(
    question: str,
    result: Dict[str, Any],
) -> str:

    response = generate_answer(
        question=question,
        result=result,
    )

    if not response["success"]:
        raise RuntimeError(response["error"])

    return response["answer"]