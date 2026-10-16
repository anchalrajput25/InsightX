# backend/evaluation/compare.py
#
# Execution accuracy: does the predicted SQL return the same data as
# the expected SQL? (The standard Text-to-SQL metric used by Spider and
# BIRD; the SQL text itself is irrelevant.)
#
# Two levels:
#   strict  - same number of columns and the same rows (as a multiset,
#             or as a sequence when order matters); column names and
#             column order are ignored.
#   lenient - every expected column is found among the predicted
#             columns and the projected rows match. Accepts answers
#             that add helpful extra columns (e.g. the count behind a
#             "top city" answer), which strict comparison would reject.
#
# Values are normalized so equivalent representations compare equal
# (1 == 1.0 == Decimal('1.00'), timestamps vs ISO strings, ...).

import math
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Numbers are compared after rounding to this many decimals, so
# float noise (0.1 + 0.2) does not fail a match.
NUMERIC_DECIMALS = 4

# Above this many candidate column assignments the lenient search
# stops (protects against pathological wide results).
MAX_ASSIGNMENTS = 5000


def normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if math.isnan(number):
            return "NaN"
        rounded = round(number, NUMERIC_DECIMALS)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return round(value.total_seconds(), NUMERIC_DECIMALS)
    if isinstance(value, bytes):
        return value.hex()
    return str(value).strip()


def _rows(result_rows: Sequence[Dict[str, Any]], columns: Sequence[str]) -> List[Tuple]:
    return [tuple(normalize_value(row.get(column)) for column in columns) for row in result_rows]


def _same_rows(expected: List[Tuple], predicted: List[Tuple], order_matters: bool) -> bool:
    if order_matters:
        return expected == predicted
    return Counter(expected) == Counter(predicted)


def compare_results(
    expected_rows: Sequence[Dict[str, Any]],
    expected_columns: Sequence[str],
    predicted_rows: Sequence[Dict[str, Any]],
    predicted_columns: Sequence[str],
    order_matters: bool = False,
) -> Dict[str, Any]:
    """
    Compare two query results. Returns
    {"match": bool, "strict": bool, "lenient": bool, "reason": str,
     "column_mapping": {expected_index: predicted_index} | None}.
    """
    expected = _rows(expected_rows, expected_columns)
    predicted = _rows(predicted_rows, predicted_columns)

    if len(expected) != len(predicted):
        return _result(False, False, f"row count differs: expected {len(expected)}, got {len(predicted)}")

    # Strict: same width, any column order.
    if len(expected_columns) == len(predicted_columns):
        mapping = _find_mapping(expected, predicted, len(expected_columns), len(predicted_columns), order_matters)
        if mapping is not None:
            return _result(True, True, "same rows", mapping)

    # Lenient: expected columns are a subset of predicted columns.
    if len(predicted_columns) > len(expected_columns):
        mapping = _find_mapping(expected, predicted, len(expected_columns), len(predicted_columns), order_matters)
        if mapping is not None:
            return _result(False, True, "same rows plus extra columns", mapping)

    if len(predicted_columns) < len(expected_columns):
        return _result(False, False, f"missing columns: expected {len(expected_columns)}, got {len(predicted_columns)}")
    return _result(False, False, "values differ")


def _result(strict: bool, lenient: bool, reason: str, mapping: Optional[Dict[int, int]] = None) -> Dict[str, Any]:
    return {
        "match": strict or lenient,
        "strict": strict,
        "lenient": lenient,
        "reason": reason,
        "column_mapping": mapping,
    }


def _find_mapping(
    expected: List[Tuple],
    predicted: List[Tuple],
    expected_width: int,
    predicted_width: int,
    order_matters: bool,
) -> Optional[Dict[int, int]]:
    """
    Find an injective mapping expected column -> predicted column such
    that the projected predicted rows equal the expected rows.

    Candidate columns are pruned by comparing each column's value
    multiset first, which makes the search cheap in practice.
    """
    if expected_width == 0:
        return {} if len(expected) == len(predicted) else None

    def column(rows, index):
        return Counter(row[index] for row in rows)

    candidates = []
    for e in range(expected_width):
        expected_values = column(expected, e)
        options = [p for p in range(predicted_width) if column(predicted, p) == expected_values]
        if not options:
            return None
        candidates.append(options)

    tried = 0
    for choice in _assignments(candidates):
        tried += 1
        if tried > MAX_ASSIGNMENTS:
            return None
        projected = [tuple(row[p] for p in choice) for row in predicted]
        if _same_rows(expected, projected, order_matters):
            return {e: p for e, p in enumerate(choice)}
    return None


def _assignments(candidates: List[List[int]]):
    """Injective choices, one predicted column per expected column."""
    def walk(index, used, chosen):
        if index == len(candidates):
            yield tuple(chosen)
            return
        for option in candidates[index]:
            if option not in used:
                used.add(option)
                chosen.append(option)
                yield from walk(index + 1, used, chosen)
                chosen.pop()
                used.discard(option)
    yield from walk(0, set(), [])
