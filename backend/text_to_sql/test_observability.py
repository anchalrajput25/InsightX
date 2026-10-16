from backend.text_to_sql.observability import (
    QueryTrace,
    generate_query_id,
    log_event,
    log_query_trace,
)


print("=" * 70)
print("OBSERVABILITY TEST")
print("=" * 70)


# ---------------------------------------------------------
# TEST 1: QUERY ID
# ---------------------------------------------------------

query_id = generate_query_id()

print("\nTEST 1: QUERY ID")
print("-" * 70)
print("QUERY ID:", query_id)


# ---------------------------------------------------------
# TEST 2: CREATE QUERY TRACE
# ---------------------------------------------------------

trace = QueryTrace(
    question="Which cities have the highest number of customers?"
)

print("\nTEST 2: CREATE QUERY TRACE")
print("-" * 70)
print("QUERY ID:", trace.query_id)
print("QUESTION:", trace.data["question"])


# ---------------------------------------------------------
# TEST 3: RECORD PIPELINE STAGES
# ---------------------------------------------------------

trace.record_stage(
    "query_rewriting",
    {
        "rewritten_query": (
            "Find cities with the highest customer count."
        )
    },
)

trace.record_stage(
    "retrieval",
    {
        "relevant_tables": [
            "olist_customers_dataset"
        ]
    },
)

trace.record_stage(
    "schema_linking",
    {
        "relevant_columns": [
            "customer_city",
            "customer_id",
        ]
    },
)

trace.record_stage(
    "sql_generation",
    {
        "sql": """
            SELECT
                customer_city,
                COUNT(customer_id) AS customer_count
            FROM olist_customers_dataset
            GROUP BY customer_city
            ORDER BY customer_count DESC
            LIMIT 10;
        """
    },
)

trace.record_stage(
    "sql_validation",
    {
        "valid": True,
        "status": "VALID",
    },
)

trace.record_stage(
    "sql_safety",
    {
        "safe": True,
        "status": "SAFE",
    },
)

trace.record_stage(
    "query_execution",
    {
        "success": True,
        "row_count": 10,
        "execution_time_ms": 78.25,
    },
)

trace.record_stage(
    "result_validation",
    {
        "valid": True,
        "status": "VALID",
    },
)

trace.record_stage(
    "answer_generation",
    {
        "success": True,
        "answer": (
            "São Paulo has the highest number of customers."
        ),
    },
)


# ---------------------------------------------------------
# TEST 4: ADD METADATA
# ---------------------------------------------------------

trace.add_metadata(
    "database",
    "data reliability",
)

trace.add_metadata(
    "model",
    "openai/gpt-oss-120b",
)


# ---------------------------------------------------------
# TEST 5: RECORD ERROR
# ---------------------------------------------------------

trace.record_error(
    stage="test_stage",
    error="This is a test error.",
)


# ---------------------------------------------------------
# TEST 6: FINALIZE TRACE
# ---------------------------------------------------------

final_data = trace.finalize()

print("\nTEST 6: FINALIZED TRACE")
print("-" * 70)

print("QUERY ID:")
print(final_data["query_id"])

print("\nQUESTION:")
print(final_data["question"])

print("\nTOTAL EXECUTION TIME (ms):")
print(final_data["total_execution_time_ms"])

print("\nRECORDED STAGES:")

for stage_name, stage_data in final_data.items():
    if stage_data is not None and stage_name not in {
        "query_id",
        "timestamp",
        "question",
        "errors",
        "metadata",
        "total_execution_time_ms",
    }:
        print(" -", stage_name)

print("\nERRORS:")
print(final_data["errors"])

print("\nMETADATA:")
print(final_data["metadata"])


# ---------------------------------------------------------
# TEST 7: WRITE TRACE TO LOG FILE
# ---------------------------------------------------------

log_result = log_query_trace(trace)

print("\nTEST 7: WRITE QUERY TRACE")
print("-" * 70)
print("SUCCESS:", log_result["success"])
print("QUERY ID:", log_result["query_id"])
print("LOG FILE:", log_result["log_file"])

if log_result["error"]:
    print("ERROR:", log_result["error"])


# ---------------------------------------------------------
# TEST 8: LOG INDIVIDUAL EVENT
# ---------------------------------------------------------

event_result = log_event(
    event_name="test_event",
    data={
        "message": "Observability event test successful.",
        "component": "text_to_sql",
    },
)

print("\nTEST 8: LOG EVENT")
print("-" * 70)
print("SUCCESS:", event_result["success"])

if event_result["error"]:
    print("ERROR:", event_result["error"])


print("\n" + "=" * 70)
print("OBSERVABILITY TEST COMPLETED")
print("=" * 70)