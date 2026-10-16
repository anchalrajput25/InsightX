from backend.text_to_sql.answer_generator import generate_answer


print("=" * 70)
print("ANSWER GENERATOR TEST")
print("=" * 70)


# ---------------------------------------------------------
# TEST 1: VALID RESULT
# ---------------------------------------------------------

valid_result = {
    "success": True,
    "status": "SUCCESS",
    "rows": [
        {
            "customer_city": "sao paulo",
            "customer_count": 1250,
        },
        {
            "customer_city": "rio de janeiro",
            "customer_count": 980,
        },
        {
            "customer_city": "belo horizonte",
            "customer_count": 640,
        },
    ],
    "columns": [
        "customer_city",
        "customer_count",
    ],
    "row_count": 3,
    "execution_time_ms": 42.5,
    "error": None,
}

question = "Which cities have the highest number of customers?"

result = generate_answer(
    question=question,
    result=valid_result,
)

print("\nTEST 1: VALID RESULT")
print("-" * 70)
print("SUCCESS:", result["success"])
print("ANSWER :", result["answer"])

if result["error"]:
    print("ERROR  :", result["error"])


# ---------------------------------------------------------
# TEST 2: EMPTY RESULT
# ---------------------------------------------------------

empty_result = {
    "success": True,
    "status": "SUCCESS",
    "rows": [],
    "columns": [
        "customer_city",
        "customer_count",
    ],
    "row_count": 0,
    "execution_time_ms": 20.3,
    "error": None,
}

question = "Which cities have customers?"

result = generate_answer(
    question=question,
    result=empty_result,
)

print("\nTEST 2: EMPTY RESULT")
print("-" * 70)
print("SUCCESS:", result["success"])
print("ANSWER :", result["answer"])

if result["error"]:
    print("ERROR  :", result["error"])


# ---------------------------------------------------------
# TEST 3: QUERY EXECUTION FAILURE
# ---------------------------------------------------------

failed_result = {
    "success": False,
    "status": "FAILED",
    "rows": [],
    "columns": [],
    "row_count": 0,
    "execution_time_ms": 15.8,
    "error": "column customer_count does not exist",
}

question = "Which city has the most customers?"

result = generate_answer(
    question=question,
    result=failed_result,
)

print("\nTEST 3: EXECUTION FAILURE")
print("-" * 70)
print("SUCCESS:", result["success"])
print("ANSWER :", result["answer"])

if result["error"]:
    print("ERROR  :", result["error"])