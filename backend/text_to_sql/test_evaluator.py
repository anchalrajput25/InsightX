from backend.text_to_sql.evaluator import (
    calculate_exact_match,
    calculate_execution_accuracy,
    calculate_schema_linking_accuracy,
    calculate_retrieval_recall,
    calculate_correction_success,
    calculate_answer_faithfulness,
    evaluate_text_to_sql,
    evaluation_summary,
)


print("=" * 70)
print("TEXT-TO-SQL EVALUATOR TEST")
print("=" * 70)


# ---------------------------------------------------------
# TEST 1: SQL EXACT MATCH
# ---------------------------------------------------------

predicted_sql = """
SELECT customer_id
FROM customers
LIMIT 10;
"""

expected_sql = """
SELECT customer_id
FROM customers
LIMIT 10
"""

result = calculate_exact_match(
    predicted_sql=predicted_sql,
    expected_sql=expected_sql,
)

print("\nTEST 1: SQL EXACT MATCH")
print("-" * 70)
print("RESULT:", result)


# ---------------------------------------------------------
# TEST 2: EXECUTION ACCURACY
# ---------------------------------------------------------

predicted_result = [
    {"customer_id": "C001", "customer_count": 10},
    {"customer_id": "C002", "customer_count": 8},
]

expected_result = [
    {"customer_id": "C001", "customer_count": 10},
    {"customer_id": "C002", "customer_count": 8},
]

result = calculate_execution_accuracy(
    predicted_result=predicted_result,
    expected_result=expected_result,
)

print("\nTEST 2: EXECUTION ACCURACY")
print("-" * 70)
print("RESULT:", result)


# ---------------------------------------------------------
# TEST 3: SCHEMA LINKING ACCURACY
# ---------------------------------------------------------

predicted_tables = [
    "customers",
    "orders",
    "products",
]

expected_tables = [
    "customers",
    "orders",
]

result = calculate_schema_linking_accuracy(
    predicted_tables=predicted_tables,
    expected_tables=expected_tables,
)

print("\nTEST 3: SCHEMA LINKING ACCURACY")
print("-" * 70)
print("RESULT:", result)


# ---------------------------------------------------------
# TEST 4: RETRIEVAL RECALL
# ---------------------------------------------------------

retrieved_tables = [
    "customers",
    "orders",
    "products",
]

relevant_tables = [
    "customers",
    "orders",
]

result = calculate_retrieval_recall(
    retrieved_tables=retrieved_tables,
    relevant_tables=relevant_tables,
)

print("\nTEST 4: RETRIEVAL RECALL")
print("-" * 70)
print("RESULT:", result)


# ---------------------------------------------------------
# TEST 5: CORRECTION SUCCESS
# ---------------------------------------------------------

corrected_sql = """
SELECT customer_id
FROM customers
LIMIT 10;
"""

validation_result = {
    "valid": True,
    "status": "VALID",
}

result = calculate_correction_success(
    corrected_sql=corrected_sql,
    validation_result=validation_result,
)

print("\nTEST 5: CORRECTION SUCCESS")
print("-" * 70)
print("RESULT:", result)


# ---------------------------------------------------------
# TEST 6: ANSWER FAITHFULNESS
# ---------------------------------------------------------

answer = (
    "C001 has 10 customers and C002 has 8 customers."
)

result_rows = [
    {"customer_id": "C001", "customer_count": 10},
    {"customer_id": "C002", "customer_count": 8},
]

result = calculate_answer_faithfulness(
    answer=answer,
    result_rows=result_rows,
)

print("\nTEST 6: ANSWER FAITHFULNESS")
print("-" * 70)
print("SCORE:", result["score"])
print("FAITHFUL:", result["faithful"])
print("REASON:", result["reason"])


# ---------------------------------------------------------
# TEST 7: COMPLETE EVALUATION
# ---------------------------------------------------------

evaluation = evaluate_text_to_sql(
    predicted_sql=predicted_sql,
    expected_sql=expected_sql,
    predicted_result=predicted_result,
    expected_result=expected_result,
    predicted_tables=predicted_tables,
    expected_tables=expected_tables,
    retrieved_tables=retrieved_tables,
    relevant_tables=relevant_tables,
    corrected_sql=corrected_sql,
    correction_validation=validation_result,
    answer=answer,
)

print("\nTEST 7: COMPLETE EVALUATION")
print("-" * 70)

print("SUCCESS:", evaluation["success"])

print("\nMETRICS:")

for metric, value in evaluation["metrics"].items():
    print(f"{metric}: {value}")


# ---------------------------------------------------------
# TEST 8: EVALUATION SUMMARY
# ---------------------------------------------------------

summary = evaluation_summary(
    evaluation
)

print("\nTEST 8: EVALUATION SUMMARY")
print("-" * 70)

print("METRICS AVAILABLE:")
for metric in summary["metrics_available"]:
    print(" -", metric)

print("\nMETRIC COUNT:")
print(summary["metric_count"])


print("\n" + "=" * 70)
print("EVALUATOR TEST COMPLETED")
print("=" * 70)