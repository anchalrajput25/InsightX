# backend/text_to_sql/agent.py

from typing import Any, Dict, Optional, TypedDict, Annotated

import operator
import re

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver

from backend.text_to_sql.retriever import retrieve_relevant_schema
from backend.text_to_sql.graph_rag import (
    retrieve_relationships,
    expand_related_tables,
)
from backend.text_to_sql.query_rewriter import rewrite_query
from backend.text_to_sql.query_decomposer import decompose_query
from backend.text_to_sql.schema_linker import link_schema
from backend.text_to_sql.sql_generator import generate_sql
from backend.text_to_sql.sql_validator import validate_sql
from backend.text_to_sql.sql_safety import check_sql_safety
from backend.text_to_sql.sql_corrector import correct_sql
from backend.text_to_sql.query_executor import execute_query
from backend.text_to_sql.result_validator import validate_result
from backend.text_to_sql.answer_generator import generate_answer


# =========================================================
# STATE
# =========================================================

class TextToSQLState(TypedDict, total=False):

    # -----------------------------------------------------
    # User
    # -----------------------------------------------------

    question: str

    # Persistent conversation history for this LangGraph
    # thread. This is intentionally preserved between turns.
    conversation_history: Annotated[
        list[Dict[str, str]],
        operator.add,
    ]

    # -----------------------------------------------------
    # Dynamic database schema
    # -----------------------------------------------------

    schema: Dict[str, Any]

    # -----------------------------------------------------
    # Query understanding
    # -----------------------------------------------------

    rewritten_query: Any
    decomposition: Any

    # -----------------------------------------------------
    # Retrieval
    # -----------------------------------------------------

    retrieved_schema: Any
    graph_context: Any

    # -----------------------------------------------------
    # Schema linking
    # -----------------------------------------------------

    schema_linking: Any

    # -----------------------------------------------------
    # SQL generation
    # -----------------------------------------------------

    raw_sql_generation: str
    generated_sql: str

    # -----------------------------------------------------
    # SQL validation
    # -----------------------------------------------------

    validation_result: Dict[str, Any]

    # -----------------------------------------------------
    # SQL safety
    # -----------------------------------------------------

    safety_result: Dict[str, Any]

    # -----------------------------------------------------
    # SQL correction
    # -----------------------------------------------------

    corrected_sql: str
    correction_attempts: int

    # -----------------------------------------------------
    # Execution
    # -----------------------------------------------------

    execution_result: Dict[str, Any]

    # -----------------------------------------------------
    # Result validation
    # -----------------------------------------------------

    result_validation: Dict[str, Any]

    # -----------------------------------------------------
    # Final answer
    # -----------------------------------------------------

    answer: str

    # -----------------------------------------------------
    # Pipeline status
    # -----------------------------------------------------

    status: str

    # -----------------------------------------------------
    # Clarification
    # -----------------------------------------------------

    clarification: str

    # -----------------------------------------------------
    # Error
    # -----------------------------------------------------

    error: Optional[str]


# =========================================================
# LOGGING
# =========================================================

def log_stage(message: str):
    """
    Print pipeline progress to the FastAPI/backend terminal.

    flush=True ensures the message appears immediately.
    """

    print(
        f"\n[Text-to-SQL] {message}",
        flush=True,
    )


# =========================================================
# SCHEMA INSPECTION
# =========================================================

def get_database_schema(
    engine: Engine,
) -> Dict[str, Any]:

    log_stage(
        "[0/12] Inspecting database schema..."
    )

    inspector = inspect(engine)

    tables = []
    relationships = []

    # -----------------------------------------------------
    # Inspect every table dynamically
    # -----------------------------------------------------

    for table_name in inspector.get_table_names():

        columns = []

        for column in inspector.get_columns(
            table_name
        ):

            columns.append(
                {
                    "column_name": column.get(
                        "name",
                        "",
                    ),
                    "data_type": str(
                        column.get(
                            "type",
                            "",
                        )
                    ),
                    "description": "",
                    "nullable": bool(
                        column.get(
                            "nullable",
                            True,
                        )
                    ),
                }
            )

        # -------------------------------------------------
        # Primary key
        # -------------------------------------------------

        primary_key = (
            inspector.get_pk_constraint(
                table_name
            )
            or {}
        )

        # -------------------------------------------------
        # Foreign keys
        # -------------------------------------------------

        foreign_keys = (
            inspector.get_foreign_keys(
                table_name
            )
            or []
        )

        tables.append(
            {
                "table_name": table_name,
                "columns": columns,
                "primary_key": (
                    primary_key.get(
                        "constrained_columns",
                        [],
                    )
                    or []
                ),
                "foreign_keys": foreign_keys,
            }
        )

        # -------------------------------------------------
        # Build relationship information
        # -------------------------------------------------

        for fk in foreign_keys:

            source_columns = (
                fk.get(
                    "constrained_columns",
                    [],
                )
                or []
            )

            target_columns = (
                fk.get(
                    "referred_columns",
                    [],
                )
                or []
            )

            target_table = fk.get(
                "referred_table"
            )

            if (
                target_table
                and source_columns
                and target_columns
            ):

                for (
                    source_col,
                    target_col,
                ) in zip(
                    source_columns,
                    target_columns,
                ):

                    relationships.append(
                        {
                            "source_table": table_name,
                            "source_column": source_col,
                            "target_table": target_table,
                            "target_column": target_col,
                            "type": "foreign_key",
                        }
                    )

    log_stage(
        f"[0/12] Schema loaded: "
        f"{len(tables)} tables."
    )

    return {
        "tables": tables,
        "relationships": relationships,
    }


# =========================================================
# RESET TRANSIENT TURN STATE
# =========================================================

def reset_turn_state_node(
    state: TextToSQLState,
):
    """
    Reset all transient pipeline fields for the new question.

    IMPORTANT:
    conversation_history is intentionally NOT modified here.

    This allows the same LangGraph thread to remember previous
    questions/answers while preventing old pipeline results from
    leaking into the current turn.
    """

    log_stage(
        "[0/12] Resetting transient turn state..."
    )

    return {

        # -------------------------------------------------
        # Query understanding
        # -------------------------------------------------

        "rewritten_query": None,
        "decomposition": None,

        # -------------------------------------------------
        # Retrieval
        # -------------------------------------------------

        "retrieved_schema": None,
        "graph_context": None,

        # -------------------------------------------------
        # Schema linking
        # -------------------------------------------------

        "schema_linking": None,

        # -------------------------------------------------
        # SQL generation
        # -------------------------------------------------

        "raw_sql_generation": "",
        "generated_sql": "",

        # -------------------------------------------------
        # SQL validation
        # -------------------------------------------------

        "validation_result": {},

        # -------------------------------------------------
        # SQL safety
        # -------------------------------------------------

        "safety_result": {},

        # -------------------------------------------------
        # SQL correction
        # -------------------------------------------------

        "corrected_sql": "",
        "correction_attempts": 0,

        # -------------------------------------------------
        # Execution
        # -------------------------------------------------

        "execution_result": {},

        # -------------------------------------------------
        # Result validation
        # -------------------------------------------------

        "result_validation": {},

        # -------------------------------------------------
        # Final answer
        # -------------------------------------------------

        "answer": "",

        # -------------------------------------------------
        # Pipeline status
        # -------------------------------------------------

        "status": "RUNNING",

        # -------------------------------------------------
        # Clarification
        # -------------------------------------------------

        "clarification": "",

        # -------------------------------------------------
        # Error
        # -------------------------------------------------

        "error": None,
    }


# =========================================================
# QUERY REWRITING
# =========================================================

def rewrite_node(
    state: TextToSQLState,
):

    log_stage(
        "[1/12] Query rewriting..."
    )

    result = rewrite_query(
        question=state["question"],
        conversation_history=state.get(
            "conversation_history",
            [],
        ),
    )

    log_stage(
        "[1/12] Query rewriting completed."
    )

    return {
        "rewritten_query": result
    }


# =========================================================
# QUERY DECOMPOSITION
# =========================================================

def decomposition_node(
    state: TextToSQLState,
):

    log_stage(
        "[2/12] Query decomposition..."
    )

    result = decompose_query(
        question=state["question"],
        rewritten_query=state[
            "rewritten_query"
        ],
    )

    log_stage(
        "[2/12] Query decomposition completed."
    )

    return {
        "decomposition": result
    }


# =========================================================
# HYBRID RETRIEVAL + GRAPHRAG
# =========================================================

def retrieval_node(
    state: TextToSQLState,
):

    log_stage(
        "[3/12] Hybrid retrieval + GraphRAG..."
    )

    rewritten_query = state[
        "rewritten_query"
    ]

    # Retriever expects text.
    if isinstance(
        rewritten_query,
        str,
    ):

        retrieval_query = rewritten_query

    else:

        retrieval_query = str(
            rewritten_query
        )

    # -----------------------------------------------------
    # Hybrid retrieval
    # -----------------------------------------------------

    result = retrieve_relevant_schema(
        schema=state["schema"],
        query=retrieval_query,
        top_k=8,
    )

    # -----------------------------------------------------
    # Relevant tables
    # -----------------------------------------------------

    relevant_tables = [
        item.get("table")
        for item in result
        if item.get("table")
    ]

    # -----------------------------------------------------
    # GraphRAG relationship retrieval
    # -----------------------------------------------------

    relationships = retrieve_relationships(
        state["schema"],
        relevant_tables,
    )

    # -----------------------------------------------------
    # Graph expansion
    # -----------------------------------------------------

    expanded_tables = expand_related_tables(
        state["schema"],
        relevant_tables,
    )

    log_stage(
        f"[3/12] Retrieval completed: "
        f"{len(relevant_tables)} relevant tables, "
        f"{len(expanded_tables)} expanded tables."
    )

    return {
        "retrieved_schema": result,

        "graph_context": {
            "relevant_tables": relevant_tables,
            "expanded_tables": expanded_tables,
            "relationships": relationships,
        },
    }


# =========================================================
# SCHEMA LINKING
# =========================================================

def schema_linking_node(
    state: TextToSQLState,
):

    log_stage(
        "[4/12] Schema linking..."
    )

    # -----------------------------------------------------
    # Keep the original dynamic schema
    # -----------------------------------------------------

    enriched_schema = dict(
        state["schema"]
    )

    # -----------------------------------------------------
    # Add retrieval evidence
    # -----------------------------------------------------

    enriched_schema[
        "retrieved_schema"
    ] = state.get(
        "retrieved_schema",
        [],
    )

    # -----------------------------------------------------
    # Add GraphRAG evidence
    # -----------------------------------------------------

    enriched_schema[
        "graph_context"
    ] = state.get(
        "graph_context",
        {},
    )

    # -----------------------------------------------------
    # Run schema linker
    # -----------------------------------------------------

    result = link_schema(
        question=state["question"],
        rewritten_query=str(
            state["rewritten_query"]
        ),
        decomposed_query=str(
            state["decomposition"]
        ),
        schema=enriched_schema,
    )

    log_stage(
        "[4/12] Schema linking completed."
    )

    return {
        "schema_linking": result
    }


# =========================================================
# SQL EXTRACTION
# =========================================================

def extract_generated_sql(
    text: str,
) -> str:
    """
    Extract the actual SQL query from the structured
    output returned by sql_generator.py.

    Example input:

        STATUS:
        READY

        SQL:
        SELECT ...

        REASON:
        ...

    Output:

        SELECT ...
    """

    if not isinstance(
        text,
        str,
    ):

        return ""

    text = text.strip()

    # -----------------------------------------------------
    # Look for SQL: section
    # -----------------------------------------------------

    match = re.search(
        r"SQL:\s*(.*?)(?=\n(?:EXPLANATION|REASON|CONFIDENCE|STATUS|CLARIFICATION_REQUIRED):|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if match:

        sql = match.group(
            1
        ).strip()

    else:

        # -------------------------------------------------
        # Fallback: fenced SQL
        # -------------------------------------------------

        fenced = re.search(
            r"```(?:sql|postgresql)?\s*(.*?)```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if fenced:

            sql = fenced.group(
                1
            ).strip()

        else:

            # ---------------------------------------------
            # Last fallback
            # ---------------------------------------------

            sql = text

    # -----------------------------------------------------
    # Remove Markdown fences
    # -----------------------------------------------------

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


# =========================================================
# SQL GENERATION
# =========================================================

def sql_generation_node(
    state: TextToSQLState,
):

    log_stage(
        "[5/12] SQL generation..."
    )

    # -----------------------------------------------------
    # Call SQL generator
    # -----------------------------------------------------

    raw_result = generate_sql(
        question=state["question"],
        rewritten_query=str(
            state["rewritten_query"]
        ),
        decomposed_query=str(
            state["decomposition"]
        ),
        linked_schema=str(
            state["schema_linking"]
        ),
    )

    raw_result = str(
        raw_result
    ).strip()

    # -----------------------------------------------------
    # Save original generator response
    # -----------------------------------------------------

    generated_sql = extract_generated_sql(
        raw_result
    )

    if not generated_sql:

        raise ValueError(
            "SQL generator returned empty SQL."
        )

    log_stage(
        "[5/12] SQL generation completed."
    )

    return {
        "raw_sql_generation": raw_result,
        "generated_sql": generated_sql,
    }


# =========================================================
# SQL GENERATION ROUTER
# =========================================================

def generated_sql_router(
    state: TextToSQLState,
):

    raw_generation = str(
        state.get(
            "raw_sql_generation",
            "",
        )
    ).strip()

    # -----------------------------------------------------
    # Clarification
    # -----------------------------------------------------

    if (
        "NEEDS_CLARIFICATION"
        in raw_generation
    ):

        return "clarification"

    # -----------------------------------------------------
    # Generation failure
    # -----------------------------------------------------

    if (
        "FAILED"
        in raw_generation
    ):

        return "error"

    # -----------------------------------------------------
    # Continue to validation
    # -----------------------------------------------------

    return "validate"


# =========================================================
# CLARIFICATION NODE
# =========================================================

def clarification_node(
    state: TextToSQLState,
):

    generated = str(
        state.get(
            "raw_sql_generation",
            "",
        )
    )

    clarification = ""
    reason = ""

    for line in generated.splitlines():

        if line.startswith(
            "CLARIFICATION_REQUIRED:"
        ):

            clarification = (
                line.split(
                    ":",
                    1,
                )[1]
                .strip()
            )

        elif line.startswith(
            "REASON:"
        ):

            reason = (
                line.split(
                    ":",
                    1,
                )[1]
                .strip()
            )

    if not clarification:

        clarification = generated

    answer = (
        "I need clarification before "
        "generating SQL.\n\n"
        + clarification
    )

    if reason:

        answer += (
            "\n\nReason: "
            + reason
        )

    return {
        "status": "NEEDS_CLARIFICATION",
        "clarification": clarification,
        "answer": answer,
        "conversation_history": [
            {
                "role": "assistant",
                "content": answer,
            }
        ],
    }


# =========================================================
# SQL VALIDATION
# =========================================================

def sql_validation_node(
    state: TextToSQLState,
    engine: Engine,
):

    log_stage(
        "[6/12] SQL validation..."
    )

    result = validate_sql(
        sql=state["generated_sql"],
        engine=engine,
        run_explain=True,
    )

    log_stage(
        "[6/12] SQL validation completed: "
        f"{'VALID' if result.get('valid') else 'INVALID'}."
    )

    return {
        "validation_result": result
    }


# =========================================================
# SQL SAFETY
# =========================================================

def sql_safety_node(
    state: TextToSQLState,
):

    log_stage(
        "[7/12] SQL safety check..."
    )

    result = check_sql_safety(
        state["generated_sql"]
    )

    log_stage(
        "[7/12] SQL safety completed: "
        f"{'SAFE' if result.get('safe') else 'UNSAFE'}."
    )

    return {
        "safety_result": result
    }


# =========================================================
# ROUTING AFTER VALIDATION
# =========================================================

def validation_router(
    state: TextToSQLState,
):

    validation = state.get(
        "validation_result",
        {},
    )

    if validation.get(
        "valid",
        False,
    ):

        return "safety"

    return "correction"


# =========================================================
# ROUTING AFTER SAFETY
# =========================================================

def safety_router(
    state: TextToSQLState,
):

    safety = state.get(
        "safety_result",
        {},
    )

    if safety.get(
        "safe",
        False,
    ):

        return "execute"

    return "correction"


# =========================================================
# SQL CORRECTION
# =========================================================

def correction_node(
    state: TextToSQLState,
):

    log_stage(
        "[8/12] SQL correction..."
    )

    # -----------------------------------------------------
    # Determine why correction is required
    # -----------------------------------------------------

    error_reason = (
        state.get(
            "validation_result",
            {},
        ).get("reason")

        or state.get(
            "safety_result",
            {},
        ).get("reason")

        or state.get(
            "execution_result",
            {},
        ).get("error")

        or "SQL validation or execution failed."
    )

    attempts = (
        state.get(
            "correction_attempts",
            0,
        )
        + 1
    )

    # -----------------------------------------------------
    # Previous SQL attempts
    # -----------------------------------------------------

    previous_attempts = []

    current_sql = str(
        state.get(
            "generated_sql",
            "",
        )
    ).strip()

    if current_sql:

        previous_attempts.append(
            current_sql
        )

    # -----------------------------------------------------
    # Ask SQL corrector
    # -----------------------------------------------------

    correction = correct_sql(
        question=state["question"],
        failed_sql=current_sql,
        error_message=str(
            error_reason
        ),
        schema=state["schema"],
        previous_attempts=previous_attempts,
    )

    # -----------------------------------------------------
    # Correction successful
    # -----------------------------------------------------

    if correction.get(
        "success"
    ):

        corrected_sql = str(
            correction["sql"]
        ).strip()

        if not corrected_sql:

            return {
                "corrected_sql": "",
                "correction_attempts": attempts,
                "error": (
                    "SQL correction returned empty SQL."
                ),
            }

        log_stage(
            "[8/12] SQL correction completed "
            f"(attempt {attempts})."
        )

        return {

            # Keep corrected SQL separately
            "corrected_sql": corrected_sql,

            # Replace generated SQL because all
            # validation/safety/execution stages
            # operate on generated_sql.
            "generated_sql": corrected_sql,

            "correction_attempts": attempts,

            "error": None,
        }

    # -----------------------------------------------------
    # Correction failed
    # -----------------------------------------------------

    log_stage(
        "[8/12] SQL correction failed "
        f"(attempt {attempts})."
    )

    return {
        "corrected_sql": "",
        "correction_attempts": attempts,
        "error": correction.get(
            "error",
            "SQL correction failed.",
        ),
    }


# =========================================================
# CORRECTION ROUTER
# =========================================================

def correction_router(
    state: TextToSQLState,
):

    # -----------------------------------------------------
    # Correction itself failed
    # -----------------------------------------------------

    if state.get(
        "error"
    ):

        return "error"

    # -----------------------------------------------------
    # Maximum two correction attempts
    # -----------------------------------------------------

    attempts = state.get(
        "correction_attempts",
        0,
    )

    if attempts >= 2:

        log_stage(
            "[8/12] Maximum SQL correction "
            "attempts reached."
        )

        return "error"

    # -----------------------------------------------------
    # Revalidate corrected SQL
    # -----------------------------------------------------

    return "validate"


# =========================================================
# ERROR NODE
# =========================================================

def error_node(
    state: TextToSQLState,
):

    answer = (
        "I could not generate a valid and "
        "safe SQL query for this question."
    )

    return {
        "status": "ERROR",
        "answer": answer,
        "conversation_history": [
            {
                "role": "assistant",
                "content": answer,
            }
        ],
    }


# =========================================================
# QUERY EXECUTION
# =========================================================

def execution_node(
    state: TextToSQLState,
    engine: Engine,
):

    log_stage(
        "[9/12] PostgreSQL execution..."
    )

    # generated_sql always contains the latest SQL,
    # including corrected SQL.
    sql = state.get(
        "generated_sql",
        "",
    )

    result = execute_query(
        sql=sql,
        engine=engine,
    )

    if result.get(
        "success",
        False,
    ):

        log_stage(
            "[9/12] PostgreSQL execution completed: "
            f"{result.get('row_count', 0)} rows, "
            f"{result.get('execution_time_ms', 0):.2f} ms."
        )

    else:

        log_stage(
            "[9/12] PostgreSQL execution failed."
        )

    return {
        "execution_result": result
    }


# =========================================================
# RESULT VALIDATION
# =========================================================

def result_validation_node(
    state: TextToSQLState,
):

    log_stage(
        "[10/12] Result validation..."
    )

    result = validate_result(
        result=state[
            "execution_result"
        ],
        allow_empty=True,
    )

    log_stage(
        "[10/12] Result validation completed: "
        f"{'VALID' if result.get('valid') else 'INVALID'}."
    )

    return {
        "result_validation": result
    }


# =========================================================
# ANSWER GENERATION
# =========================================================

def answer_generation_node(
    state: TextToSQLState,
):

    log_stage(
        "[11/12] Natural-language answer generation..."
    )

    validation = state.get(
        "result_validation",
        {},
    )

    # -----------------------------------------------------
    # Invalid result
    # -----------------------------------------------------

    if not validation.get(
        "valid",
        False,
    ):

        answer = (
            "The database query did not "
            "produce a valid result."
        )

        return {
            "status": "ERROR",
            "answer": answer,
            "conversation_history": [
                {
                    "role": "assistant",
                    "content": answer,
                }
            ],
        }

    # -----------------------------------------------------
    # Generate answer using validated result
    # -----------------------------------------------------

    result = generate_answer(
        question=state["question"],
        result=state[
            "execution_result"
        ],
    )

    if not result.get(
        "success",
        False,
    ):

        answer = (
            "I could not generate an "
            "answer from the database result."
        )

        return {
            "status": "ERROR",
            "answer": answer,
            "error": result.get(
                "error"
            ),
            "conversation_history": [
                {
                    "role": "assistant",
                    "content": answer,
                }
            ],
        }

    log_stage(
        "[11/12] Natural-language answer "
        "generation completed."
    )

    answer = result["answer"]

    return {
        "status": "SUCCESS",
        "answer": answer,
        "conversation_history": [
            {
                "role": "assistant",
                "content": answer,
            }
        ],
    }


# =========================================================
# BUILD LANGGRAPH
# =========================================================

def build_text_to_sql_graph(
    engine: Engine,
    checkpointer=None,
):

    graph = StateGraph(
        TextToSQLState
    )

    # -----------------------------------------------------
    # Nodes
    # -----------------------------------------------------

    graph.add_node(
        "reset_turn",
        reset_turn_state_node,
    )

    graph.add_node(
        "rewrite",
        rewrite_node,
    )

    graph.add_node(
        "decompose",
        decomposition_node,
    )

    graph.add_node(
        "retrieve",
        retrieval_node,
    )

    graph.add_node(
        "schema_link",
        schema_linking_node,
    )

    graph.add_node(
        "generate_sql",
        sql_generation_node,
    )

    graph.add_node(
        "clarification",
        clarification_node,
    )

    graph.add_node(
        "validate_sql",
        lambda state: sql_validation_node(
            state,
            engine,
        ),
    )

    graph.add_node(
        "safety",
        sql_safety_node,
    )

    graph.add_node(
        "correction",
        correction_node,
    )

    graph.add_node(
        "execute",
        lambda state: execution_node(
            state,
            engine,
        ),
    )

    graph.add_node(
        "validate_result",
        result_validation_node,
    )

    graph.add_node(
        "generate_answer",
        answer_generation_node,
    )

    graph.add_node(
        "error",
        error_node,
    )

    # =====================================================
    # MAIN FLOW
    # =====================================================

    # IMPORTANT:
    # Every new question first resets transient state.
    #
    # conversation_history is NOT reset because it is
    # persistent memory for the same thread.

    graph.add_edge(
        START,
        "reset_turn",
    )

    graph.add_edge(
        "reset_turn",
        "rewrite",
    )

    graph.add_edge(
        "rewrite",
        "decompose",
    )

    graph.add_edge(
        "decompose",
        "retrieve",
    )

    graph.add_edge(
        "retrieve",
        "schema_link",
    )

    graph.add_edge(
        "schema_link",
        "generate_sql",
    )

    # =====================================================
    # SQL GENERATION ROUTING
    # =====================================================

    graph.add_conditional_edges(
        "generate_sql",
        generated_sql_router,
        {
            "validate": "validate_sql",
            "clarification": "clarification",
            "error": "error",
        },
    )

    # =====================================================
    # VALIDATION ROUTING
    # =====================================================

    graph.add_conditional_edges(
        "validate_sql",
        validation_router,
        {
            "safety": "safety",
            "correction": "correction",
        },
    )

    # =====================================================
    # SAFETY ROUTING
    # =====================================================

    graph.add_conditional_edges(
        "safety",
        safety_router,
        {
            "execute": "execute",
            "correction": "correction",
        },
    )

    # =====================================================
    # CORRECTION LOOP
    # =====================================================

    graph.add_conditional_edges(
        "correction",
        correction_router,
        {
            "validate": "validate_sql",
            "error": "error",
        },
    )

    # =====================================================
    # EXECUTION
    # =====================================================

    graph.add_edge(
        "execute",
        "validate_result",
    )

    # =====================================================
    # RESULT VALIDATION
    # =====================================================

    graph.add_edge(
        "validate_result",
        "generate_answer",
    )

    # =====================================================
    # FINAL ANSWER
    # =====================================================

    graph.add_edge(
        "generate_answer",
        END,
    )

    # =====================================================
    # CLARIFICATION
    # =====================================================

    graph.add_edge(
        "clarification",
        END,
    )

    # =====================================================
    # ERROR
    # =====================================================

    graph.add_edge(
        "error",
        END,
    )

    return graph.compile(
        checkpointer=checkpointer
    )


# =========================================================
# THREAD STATE LOADER
# =========================================================

def get_thread_state(
    engine: Engine,
    thread_id: str,
) -> Dict[str, Any]:
    """
    Load the latest persisted state for a LangGraph thread.

    This is used by Streamlit when the user clicks an old
    investigation in the sidebar.
    """

    if not thread_id or not thread_id.strip():

        return {}

    database_url = engine.url

    checkpointer_url = database_url.set(
        drivername="postgresql"
    )

    connection_string = (
        checkpointer_url.render_as_string(
            hide_password=False
        )
    )

    # URI-safe encoding for database names/components
    # containing spaces.
    connection_string = connection_string.replace(
        " ",
        "%20",
    )

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    with PostgresSaver.from_conn_string(
        connection_string
    ) as checkpointer:

        checkpointer.setup()

        graph = build_text_to_sql_graph(
            engine=engine,
            checkpointer=checkpointer,
        )

        snapshot = graph.get_state(
            config
        )

        if snapshot is None:

            return {}

        return dict(
            snapshot.values or {}
        )


# =========================================================
# MAIN RUNNER
# =========================================================

def run_text_to_sql(
    question: str,
    engine: Engine,
    thread_id: str,
):
    """
    Run the Text-to-SQL pipeline inside a persistent
    LangGraph thread.

    Same thread_id:
        Same persisted conversation/checkpoint.

    New thread_id:
        New investigation.

    Important:
        Conversation history persists for the same thread,
        but transient pipeline state is reset at the beginning
        of every new question.
    """

    # -----------------------------------------------------
    # Validate input
    # -----------------------------------------------------

    if not question.strip():

        raise ValueError(
            "Question cannot be empty."
        )

    if not thread_id.strip():

        raise ValueError(
            "thread_id cannot be empty."
        )

    # -----------------------------------------------------
    # Inspect database
    # -----------------------------------------------------

    schema = get_database_schema(
        engine
    )

    # -----------------------------------------------------
    # Build PostgreSQL connection string
    # for LangGraph checkpointer
    #
    # Important:
    # PostgreSQL database names can contain spaces.
    # -----------------------------------------------------

    database_url = engine.url

    checkpointer_url = database_url.set(
        drivername="postgresql"
    )

    connection_string = (
        checkpointer_url.render_as_string(
            hide_password=False
        )
    )

    # URI-safe encoding for spaces.
    connection_string = connection_string.replace(
        " ",
        "%20",
    )

    # -----------------------------------------------------
    # Persistent PostgreSQL checkpointer
    # -----------------------------------------------------

    with PostgresSaver.from_conn_string(
        connection_string
    ) as checkpointer:

        # Create LangGraph checkpoint tables
        # if they do not already exist.
        checkpointer.setup()

        # -------------------------------------------------
        # Build LangGraph
        # -------------------------------------------------

        log_stage(
            "[0/12] Building LangGraph..."
        )

        graph = build_text_to_sql_graph(
            engine=engine,
            checkpointer=checkpointer,
        )

        # -------------------------------------------------
        # Initial state
        # -------------------------------------------------
        #
        # IMPORTANT:
        #
        # We intentionally DO NOT manually carry old
        # execution_result / result_validation / SQL etc.
        #
        # reset_turn_state_node handles all transient fields.
        #
        # conversation_history is persistent and the current
        # user question is appended using operator.add.
        # -------------------------------------------------

        initial_state: TextToSQLState = {

            "question": question.strip(),

            "conversation_history": [
                {
                    "role": "user",
                    "content": question.strip(),
                }
            ],

            "schema": schema,

            "correction_attempts": 0,

            "status": "RUNNING",
        }

        # -------------------------------------------------
        # Thread configuration
        # -------------------------------------------------

        config = {
            "configurable": {
                "thread_id": thread_id
            }
        }

        # -------------------------------------------------
        # Start pipeline
        # -------------------------------------------------

        log_stage(
            f"Starting Text-to-SQL pipeline "
            f"for thread: {thread_id}"
        )

        result = graph.invoke(
            initial_state,
            config=config,
        )

        # -------------------------------------------------
        # Pipeline completed
        # -------------------------------------------------

        log_stage(
            "[12/12] Text-to-SQL pipeline completed."
        )

        return result