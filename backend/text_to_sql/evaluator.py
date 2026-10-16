from typing import Any, Dict, List, Optional


# ---------------------------------------------------------
# HELPER
# ---------------------------------------------------------

def normalize_sql(sql: str) -> str:
    """
    Normalize SQL for basic comparison.
    """
    if not isinstance(sql, str):
        return ""

    sql = sql.strip().lower()

    # Remove trailing semicolon
    sql = sql.rstrip(";").strip()

    # Normalize whitespace
    sql = " ".join(sql.split())

    return sql


def calculate_exact_match(
    predicted_sql: str,
    expected_sql: str,
) -> bool:
    """
    Basic SQL exact-match evaluation.

    Note:
    Different SQL queries can produce the same result,
    so exact match alone is not sufficient.
    """

    predicted = normalize_sql(predicted_sql)
    expected = normalize_sql(expected_sql)

    return predicted == expected


def calculate_execution_accuracy(
    predicted_result: List[Dict[str, Any]],
    expected_result: List[Dict[str, Any]],
) -> bool:
    """
    Compare predicted and expected database results.
    """

    return predicted_result == expected_result


def calculate_schema_linking_accuracy(
    predicted_tables: List[str],
    expected_tables: List[str],
) -> float:
    """
    Measure overlap between predicted and expected tables.
    """

    predicted = {
        table.lower()
        for table in predicted_tables
    }

    expected = {
        table.lower()
        for table in expected_tables
    }

    if not expected:
        return 0.0

    intersection = predicted.intersection(expected)

    return round(
        len(intersection) / len(expected),
        4,
    )


def calculate_retrieval_recall(
    retrieved_tables: List[str],
    relevant_tables: List[str],
) -> float:
    """
    Measure how many relevant tables were retrieved.
    """

    retrieved = {
        table.lower()
        for table in retrieved_tables
    }

    relevant = {
        table.lower()
        for table in relevant_tables
    }

    if not relevant:
        return 0.0

    intersection = retrieved.intersection(relevant)

    return round(
        len(intersection) / len(relevant),
        4,
    )


# ---------------------------------------------------------
# SQL CORRECTION EVALUATION
# ---------------------------------------------------------

def calculate_correction_success(
    corrected_sql: Optional[str],
    validation_result: Dict[str, Any],
) -> bool:
    """
    Check whether SQL correction produced valid SQL.
    """

    if not corrected_sql:
        return False

    return bool(
        validation_result.get("valid", False)
    )


# ---------------------------------------------------------
# ANSWER FAITHFULNESS
# ---------------------------------------------------------

def calculate_answer_faithfulness(
    answer: str,
    result_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Basic deterministic faithfulness check.

    This does NOT perform semantic LLM evaluation.
    It checks whether the answer exists and whether
    database results were available.

    A stronger LLM-based evaluator can be added later.
    """

    if not answer or not answer.strip():
        return {
            "score": 0.0,
            "faithful": False,
            "reason": "Answer is empty.",
        }

    if not result_rows:
        return {
            "score": 0.0,
            "faithful": False,
            "reason": "No database result available.",
        }

    return {
        "score": 1.0,
        "faithful": True,
        "reason": (
            "Answer was generated from an available "
            "database result."
        ),
    }


# ---------------------------------------------------------
# MAIN EVALUATION
# ---------------------------------------------------------

def evaluate_text_to_sql(
    predicted_sql: str,
    expected_sql: Optional[str] = None,
    predicted_result: Optional[List[Dict[str, Any]]] = None,
    expected_result: Optional[List[Dict[str, Any]]] = None,
    predicted_tables: Optional[List[str]] = None,
    expected_tables: Optional[List[str]] = None,
    retrieved_tables: Optional[List[str]] = None,
    relevant_tables: Optional[List[str]] = None,
    corrected_sql: Optional[str] = None,
    correction_validation: Optional[Dict[str, Any]] = None,
    answer: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluate a complete Text-to-SQL pipeline.

    All metrics are optional so the evaluator can be used
    with different evaluation datasets.
    """

    metrics: Dict[str, Any] = {}

    # -----------------------------------------------------
    # SQL EXACT MATCH
    # -----------------------------------------------------

    if expected_sql is not None:
        metrics["sql_exact_match"] = calculate_exact_match(
            predicted_sql=predicted_sql,
            expected_sql=expected_sql,
        )

    # -----------------------------------------------------
    # EXECUTION ACCURACY
    # -----------------------------------------------------

    if (
        predicted_result is not None
        and expected_result is not None
    ):
        metrics["execution_accuracy"] = (
            calculate_execution_accuracy(
                predicted_result=predicted_result,
                expected_result=expected_result,
            )
        )

    # -----------------------------------------------------
    # SCHEMA LINKING ACCURACY
    # -----------------------------------------------------

    if (
        predicted_tables is not None
        and expected_tables is not None
    ):
        metrics["schema_linking_accuracy"] = (
            calculate_schema_linking_accuracy(
                predicted_tables=predicted_tables,
                expected_tables=expected_tables,
            )
        )

    # -----------------------------------------------------
    # RETRIEVAL RECALL
    # -----------------------------------------------------

    if (
        retrieved_tables is not None
        and relevant_tables is not None
    ):
        metrics["retrieval_recall"] = (
            calculate_retrieval_recall(
                retrieved_tables=retrieved_tables,
                relevant_tables=relevant_tables,
            )
        )

    # -----------------------------------------------------
    # CORRECTION SUCCESS
    # -----------------------------------------------------

    if correction_validation is not None:
        metrics["correction_success"] = (
            calculate_correction_success(
                corrected_sql=corrected_sql,
                validation_result=correction_validation,
            )
        )

    # -----------------------------------------------------
    # ANSWER FAITHFULNESS
    # -----------------------------------------------------

    if answer is not None:
        faithfulness = calculate_answer_faithfulness(
            answer=answer,
            result_rows=predicted_result or [],
        )

        metrics["answer_faithfulness"] = faithfulness

    # -----------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------

    return {
        "success": True,
        "metrics": metrics,
    }


# ---------------------------------------------------------
# SIMPLE EVALUATION SUMMARY
# ---------------------------------------------------------

def evaluation_summary(
    evaluation: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Return a compact evaluation summary.
    """

    metrics = evaluation.get(
        "metrics",
        {},
    )

    return {
        "metrics_available": list(
            metrics.keys()
        ),
        "metric_count": len(metrics),
    }