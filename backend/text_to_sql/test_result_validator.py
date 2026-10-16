from backend.text_to_sql.result_validator import validate_result


print("=" * 70)
print("RESULT VALIDATOR TEST")
print("=" * 70)


# ---------------------------------------------------------
# TEST 1: VALID RESULT
# ---------------------------------------------------------

valid_result = {
    "success": True,
    "status": "SUCCESS",
    "rows": [
        {
            "customer_id": "C001",
            "customer_city": "Delhi",
        },
        {
            "customer_id": "C002",
            "customer_city": "Mumbai",
        },
    ],
    "columns": [
        "customer_id",
        "customer_city",
    ],
    "row_count": 2,
    "execution_time_ms": 25.4,
    "error": None,
}

result = validate_result(
    result=valid_result,
    expected_columns=[
        "customer_id",
        "customer_city",
    ],
)

print("\nTEST 1: VALID RESULT")
print("-" * 70)
print("STATUS :", result["status"])
print("VALID  :", result["valid"])
print("REASON :", result["reason"])
print("DETAILS:", result["details"])


# ---------------------------------------------------------
# TEST 2: EMPTY RESULT
# ---------------------------------------------------------

empty_result = {
    "success": True,
    "status": "SUCCESS",
    "rows": [],
    "columns": [
        "customer_id",
        "customer_city",
    ],
    "row_count": 0,
    "execution_time_ms": 18.2,
    "error": None,
}

result = validate_result(
    result=empty_result,
    expected_columns=[
        "customer_id",
        "customer_city",
    ],
)

print("\nTEST 2: EMPTY RESULT")
print("-" * 70)
print("STATUS :", result["status"])
print("VALID  :", result["valid"])
print("REASON :", result["reason"])

if result["errors"]:
    print("ERRORS:")
    for error in result["errors"]:
        print(" -", error)


# ---------------------------------------------------------
# TEST 3: MISSING EXPECTED COLUMN
# ---------------------------------------------------------

missing_column_result = {
    "success": True,
    "status": "SUCCESS",
    "rows": [
        {
            "customer_id": "C001",
        },
    ],
    "columns": [
        "customer_id",
    ],
    "row_count": 1,
    "execution_time_ms": 20.1,
    "error": None,
}

result = validate_result(
    result=missing_column_result,
    expected_columns=[
        "customer_id",
        "customer_city",
    ],
)

print("\nTEST 3: MISSING EXPECTED COLUMN")
print("-" * 70)
print("STATUS :", result["status"])
print("VALID  :", result["valid"])
print("REASON :", result["reason"])

if result["errors"]:
    print("ERRORS:")
    for error in result["errors"]:
        print(" -", error)


# ---------------------------------------------------------
# TEST 4: NULL VALUES
# ---------------------------------------------------------

null_result = {
    "success": True,
    "status": "SUCCESS",
    "rows": [
        {
            "customer_id": "C001",
            "customer_city": None,
        },
        {
            "customer_id": "C002",
            "customer_city": "Delhi",
        },
    ],
    "columns": [
        "customer_id",
        "customer_city",
    ],
    "row_count": 2,
    "execution_time_ms": 22.7,
    "error": None,
}

result = validate_result(
    result=null_result,
    expected_columns=[
        "customer_id",
        "customer_city",
    ],
)

print("\nTEST 4: NULL VALUES")
print("-" * 70)
print("STATUS :", result["status"])
print("VALID  :", result["valid"])
print("REASON :", result["reason"])

if result["warnings"]:
    print("WARNINGS:")
    for warning in result["warnings"]:
        print(" -", warning)

print("NULL COUNTS:", result["details"]["null_counts"])


# ---------------------------------------------------------
# TEST 5: DUPLICATE ROWS
# ---------------------------------------------------------

duplicate_result = {
    "success": True,
    "status": "SUCCESS",
    "rows": [
        {
            "customer_id": "C001",
            "customer_city": "Delhi",
        },
        {
            "customer_id": "C001",
            "customer_city": "Delhi",
        },
    ],
    "columns": [
        "customer_id",
        "customer_city",
    ],
    "row_count": 2,
    "execution_time_ms": 19.5,
    "error": None,
}

result = validate_result(
    result=duplicate_result,
    expected_columns=[
        "customer_id",
        "customer_city",
    ],
)

print("\nTEST 5: DUPLICATE ROWS")
print("-" * 70)
print("STATUS :", result["status"])
print("VALID  :", result["valid"])
print("REASON :", result["reason"])

if result["warnings"]:
    print("WARNINGS:")
    for warning in result["warnings"]:
        print(" -", warning)

print(
    "DUPLICATE COUNT:",
    result["details"]["duplicate_count"],
)


# ---------------------------------------------------------
# TEST 6: EXECUTION FAILURE
# ---------------------------------------------------------

failed_result = {
    "success": False,
    "status": "FAILED",
    "rows": [],
    "columns": [],
    "row_count": 0,
    "execution_time_ms": 15.3,
    "error": "column does not exist",
}

result = validate_result(
    result=failed_result,
)

print("\nTEST 6: EXECUTION FAILURE")
print("-" * 70)
print("STATUS :", result["status"])
print("VALID  :", result["valid"])
print("REASON :", result["reason"])

if result["errors"]:
    print("ERRORS:")
    for error in result["errors"]:
        print(" -", error)