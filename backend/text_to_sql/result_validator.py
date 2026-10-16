from typing import Any, Dict, List, Optional


def validation_result(
    valid: bool,
    status: str,
    reason: str,
    errors: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "valid": valid,
        "status": status,
        "reason": reason,
        "errors": errors or [],
        "warnings": warnings or [],
        "details": details or {},
    }


def validate_result(
    result: Dict[str, Any],
    expected_columns: Optional[List[str]] = None,
    allow_empty: bool = False,
) -> Dict[str, Any]:
    """
    Validate the result returned by Query Executor.

    This is database-independent.
    It validates the returned result structure and quality,
    not the business meaning of the answer.
    """

    if not isinstance(result, dict):
        return validation_result(
            valid=False,
            status="INVALID",
            reason="Invalid executor result format.",
            errors=["Executor result must be a dictionary."],
        )

    if not result.get("success", False):
        return validation_result(
            valid=False,
            status="INVALID",
            reason="Query execution failed.",
            errors=[
                result.get("error") or "Unknown execution error."
            ],
        )

    rows = result.get("rows", [])
    columns = result.get("columns", [])
    row_count = result.get("row_count", 0)

    if not isinstance(rows, list):
        return validation_result(
            valid=False,
            status="INVALID",
            reason="Invalid rows format.",
            errors=["Rows must be a list."],
        )

    if not isinstance(columns, list):
        return validation_result(
            valid=False,
            status="INVALID",
            reason="Invalid columns format.",
            errors=["Columns must be a list."],
        )

    warnings = []

    # Check row count consistency
    if row_count != len(rows):
        warnings.append(
            f"Reported row_count ({row_count}) "
            f"does not match actual rows ({len(rows)})."
        )

    # Empty result
    if len(rows) == 0:
        if allow_empty:
            warnings.append(
                "Query returned zero rows."
            )
        else:
            return validation_result(
                valid=False,
                status="INVALID",
                reason="Query returned no rows.",
                errors=[
                    "Empty result set. "
                    "The query may not have matched any records."
                ],
                warnings=warnings,
                details={
                    "columns": columns,
                    "row_count": row_count,
                },
            )

    # Expected column validation
    if expected_columns:
        missing_columns = [
            column
            for column in expected_columns
            if column not in columns
        ]

        if missing_columns:
            return validation_result(
                valid=False,
                status="INVALID",
                reason="Expected columns are missing from the result.",
                errors=[
                    "Missing columns: "
                    + ", ".join(missing_columns)
                ],
                warnings=warnings,
                details={
                    "columns": columns,
                    "row_count": row_count,
                    "missing_columns": missing_columns,
                },
            )

    # NULL analysis
    null_counts = {}

    for column in columns:
        null_count = sum(
            1
            for row in rows
            if isinstance(row, dict)
            and row.get(column) is None
        )

        if null_count > 0:
            null_counts[column] = null_count

    if null_counts:
        warnings.append(
            "NULL values detected in result columns."
        )

    # Duplicate row detection
    duplicate_count = 0

    try:
        serialized_rows = [
            tuple(sorted(row.items()))
            for row in rows
            if isinstance(row, dict)
        ]

        duplicate_count = len(serialized_rows) - len(
            set(serialized_rows)
        )

        if duplicate_count > 0:
            warnings.append(
                f"{duplicate_count} duplicate row(s) detected."
            )

    except TypeError:
        warnings.append(
            "Duplicate-row analysis was skipped "
            "because some values are not hashable."
        )

    details = {
        "columns": columns,
        "row_count": row_count,
        "actual_row_count": len(rows),
        "null_counts": null_counts,
        "duplicate_count": duplicate_count,
    }

    return validation_result(
        valid=True,
        status="VALID",
        reason="Query result passed structural validation.",
        errors=[],
        warnings=warnings,
        details=details,
    )


def is_valid_result(
    result: Dict[str, Any],
    expected_columns: Optional[List[str]] = None,
    allow_empty: bool = False,
) -> bool:

    validation = validate_result(
        result=result,
        expected_columns=expected_columns,
        allow_empty=allow_empty,
    )

    return validation["valid"]