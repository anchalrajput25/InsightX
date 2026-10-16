from typing import Any, Callable, Dict, List, Optional, TypedDict, Annotated
import functools
import logging
import operator
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver

from backend import telemetry
from backend.ai_gateway.errors import AIUnavailableError, UsageLimitError
from backend.config import query_settings
from backend.text_to_sql.schema_focus import build_schema_focus, prioritize_schema
from backend.text_to_sql.schema_verifier import render_linked_schema, verify_linking
from backend.text_to_sql.sql_candidates import run_parallel, select_candidate
from backend.text_to_sql.trust import assess_trust, check_result_sanity, verify_claims
from backend.text_to_sql.query_rewriter import rewrite_query_variants
from backend.text_to_sql.query_decomposer import decompose_query
from backend.text_to_sql.schema_linker import link_schema_structured
from backend.text_to_sql.sql_generator import CANDIDATE_STRATEGIES, candidate_llm, generate_sql
from backend.text_to_sql.sql_validator import validate_sql
from backend.text_to_sql.sql_safety import check_sql_safety
from backend.text_to_sql.sql_corrector import correct_sql
from backend.text_to_sql.query_executor import execute_query
from backend.text_to_sql.result_validator import validate_result
from backend.text_to_sql.answer_generator import generate_answer
from backend.text_to_sql.schema_context import filter_business_tables, get_agent_schema
from backend.text_to_sql import question_cache
from backend.ai_gateway.context import current_user
from backend import dialects


# =========================================================
# CHECKPOINT CONNECTION STRING
# =========================================================

# LangGraph checkpoint tables live in their own schema so they never
# mix with the user's business tables in "public".
CHECKPOINT_SCHEMA = os.getenv("INSIGHTX_CHECKPOINT_SCHEMA", "insightx")

if not re.fullmatch(r"[a-z_][a-z0-9_]*", CHECKPOINT_SCHEMA):
    raise ValueError(
        f"Invalid INSIGHTX_CHECKPOINT_SCHEMA '{CHECKPOINT_SCHEMA}': "
        "use lowercase letters, digits and underscores."
    )


def _build_checkpoint_connection_string(engine: Engine) -> str:
    """
    Build a psycopg-compatible URI for LangGraph PostgresSaver.

    PostgresSaver uses unqualified table names, so pinning the
    connection's search_path to CHECKPOINT_SCHEMA places all
    checkpoint tables in that schema.
    """
    checkpoint_url = engine.url.set(
        drivername="postgresql",
        query={
            **engine.url.query,
            # No space after -c: SQLAlchemy renders spaces as "+",
            # which libpq does not decode in URIs.
            "options": f"-csearch_path={CHECKPOINT_SCHEMA}",
        },
    )
    return checkpoint_url.render_as_string(hide_password=False).replace(" ", "%20")


def _ensure_checkpoint_schema(engine: Engine) -> None:
    """Create the checkpoint schema if it does not exist yet."""
    with engine.begin() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :name"),
            {"name": CHECKPOINT_SCHEMA},
        ).first()

        if exists:
            return

        try:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{CHECKPOINT_SCHEMA}"'))
        except Exception as exc:
            raise RuntimeError(
                f"Could not create checkpoint schema '{CHECKPOINT_SCHEMA}'. "
                "Grant CREATE on the database to this user, or create the "
                "schema manually and grant the user usage/create on it."
            ) from exc


# Conversation memory for MySQL, SQL Server and SQLite databases.
# PostgreSQL databases keep it in their own "insightx" schema; for the
# others it is kept in a local SQLite
# file when langgraph-checkpoint-sqlite is installed, otherwise in memory
# (lost when the backend restarts).
_SQLITE_CHECKPOINTS = Path(os.getenv(
    "INSIGHTX_SQLITE_CHECKPOINTS",
    str(Path(__file__).resolve().parents[2] / "logs" / "checkpoints.db"),
))
_memory_saver = None


@contextmanager
def _checkpointer(engine: Engine):
    """The LangGraph checkpointer that keeps this database's conversations."""
    global _memory_saver
    if not dialects.keeps_chat_memory_in_database(engine):
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError:
            SqliteSaver = None
        if SqliteSaver is not None:
            _SQLITE_CHECKPOINTS.parent.mkdir(parents=True, exist_ok=True)
            with SqliteSaver.from_conn_string(str(_SQLITE_CHECKPOINTS)) as checkpointer:
                checkpointer.setup()
                yield checkpointer
            return
        if _memory_saver is None:
            from langgraph.checkpoint.memory import InMemorySaver

            _memory_saver = InMemorySaver()
        yield _memory_saver
        return

    _ensure_checkpoint_schema(engine)
    with PostgresSaver.from_conn_string(_build_checkpoint_connection_string(engine)) as checkpointer:
        checkpointer.setup()
        yield checkpointer


# =========================================================
# STATE
# =========================================================

class TextToSQLState(TypedDict, total=False):
    question: str
    conversation_history: Annotated[list[Dict[str, str]], operator.add]
    schema: Dict[str, Any]
    rewritten_query: Any
    query_variants: list[str]
    decomposition: Any
    retrieved_schema: Any
    graph_context: Any
    focused_schema: Dict[str, Any]
    schema_linking: Any
    linked_schema: Dict[str, Any]
    sql_candidates: list
    sql_selection: Dict[str, Any]
    candidate_execution: Dict[str, Any]
    sanity: Dict[str, Any]
    cache: Dict[str, Any]
    claim_check: Dict[str, Any]
    trust: Dict[str, Any]
    raw_sql_generation: str
    generated_sql: str
    validation_result: Dict[str, Any]
    safety_result: Dict[str, Any]
    corrected_sql: str
    correction_attempts: int
    execution_result: Dict[str, Any]
    result_validation: Dict[str, Any]
    answer: str
    status: str
    clarification: str
    error: Optional[str]
    error_stage: str
    # True when the turn failed because no AI model had capacity
    ai_unavailable: bool


logger = logging.getLogger("insightx.agent")


def log_stage(message: str) -> None:
    print(f"\n[Text-to-SQL] {message}", flush=True)


def fresh_turn_state() -> Dict[str, Any]:
    """Per-turn pipeline fields, reset at the start of every question."""
    return {
        "rewritten_query": "",
        "query_variants": [],
        "decomposition": "",
        "retrieved_schema": [],
        "graph_context": {},
        "focused_schema": {},
        "schema_linking": "",
        "linked_schema": {},
        "sql_candidates": [],
        "sql_selection": {},
        "candidate_execution": {},
        "sanity": {},
        "cache": {},
        "claim_check": {},
        "trust": {},
        "raw_sql_generation": "",
        "generated_sql": "",
        "validation_result": {},
        "safety_result": {},
        "corrected_sql": "",
        "correction_attempts": 0,
        "execution_result": {},
        "result_validation": {},
        "answer": "",
        "status": "RUNNING",
        "clarification": "",
        "error": None,
        "error_stage": "",
        "ai_unavailable": False,
    }


# =========================================================
# SCHEMA INSPECTION
# =========================================================

def get_database_schema(engine: Engine) -> Dict[str, Any]:
    log_stage("[0/12] Inspecting database schema...")
    inspector = inspect(engine)
    tables = []
    relationships = []

    for table_name in filter_business_tables(inspector, inspector.get_table_names()):
        columns = []
        for column in inspector.get_columns(table_name):
            columns.append({
                "column_name": column.get("name", ""),
                "data_type": str(column.get("type", "")),
                "description": (column.get("comment") or "").strip(),
                "nullable": bool(column.get("nullable", True)),
            })

        primary_key = inspector.get_pk_constraint(table_name) or {}
        foreign_keys = inspector.get_foreign_keys(table_name) or []

        tables.append({
            "table_name": table_name,
            "columns": columns,
            "primary_key": primary_key.get("constrained_columns", []) or [],
            "foreign_keys": foreign_keys,
        })

        for fk in foreign_keys:
            source_columns = fk.get("constrained_columns", []) or []
            target_columns = fk.get("referred_columns", []) or []
            target_table = fk.get("referred_table")
            if target_table and source_columns and target_columns:
                for source_col, target_col in zip(source_columns, target_columns):
                    relationships.append({
                        "source_table": table_name,
                        "source_column": source_col,
                        "target_table": target_table,
                        "target_column": target_col,
                        "type": "foreign_key",
                    })

    log_stage(f"[0/12] Schema loaded: {len(tables)} tables.")
    return {"tables": tables, "relationships": relationships}


# =========================================================
# QUERY REWRITING
# =========================================================

def rewrite_node(state: TextToSQLState):
    log_stage("[1/12] Query rewriting...")
    result = rewrite_query_variants(
        question=state["question"],
        conversation_context=state.get("conversation_history", []),
        alternative_count=query_settings.query_variants,
    )
    primary = str(result.get("primary") or "").strip() or state["question"]
    alternatives = [str(text).strip() for text in result.get("alternatives") or [] if str(text).strip()]
    log_stage(f"[1/12] Query rewriting completed: 1 primary + {len(alternatives)} alternative queries.")
    # The primary rewrite drives linking and SQL generation; all
    # variants (primary first) are used to widen schema retrieval.
    return {"rewritten_query": primary, "query_variants": [primary] + alternatives}


# =========================================================
# QUESTION CACHE
# =========================================================

def _cache_identity(state: TextToSQLState, engine: Engine):
    return (
        current_user(),
        question_cache.database_key(engine),
        question_cache.schema_key(state.get("schema") or {}),
    )


def cache_check_node(state: TextToSQLState, engine: Engine, stage: str):
    """
    Reuse the SQL of an earlier trusted answer to the same question.

    "raw": before rewriting, for the first question of a chat, in the
    user's own words.
    "rewritten": after rewriting, for follow-ups only ("and by month?"
    means nothing on its own), in their standalone form.
    """
    if not question_cache.lookups_enabled():
        return {}
    if stage == "raw":
        if not question_cache.first_question_in_thread(state.get("conversation_history", [])):
            return {}
        question = state["question"]
    else:
        if question_cache.first_question_in_thread(state.get("conversation_history", [])):
            return {}
        question = state.get("rewritten_query") or ""
        if not question:
            return {}
    try:
        entry = question_cache.get_cache().lookup(*_cache_identity(state, engine), question)
    except Exception as error:  # the cache must never break a question
        log_stage(f"Question cache unavailable: {type(error).__name__}: {error}")
        return {}
    if not entry:
        return {}
    log_stage(f"Question cache: {entry['match']} question asked before; reusing its SQL (no AI for the SQL).")
    cache = {
        "hit": True,
        "match": entry["match"],
        "question": entry["question"],
        "answer": entry["answer"],
        "result_key": entry["result_key"],
        "trust": entry["trust"],
        "asked_at": entry["created"],
        "answer_reused": False,
    }
    update: Dict[str, Any] = {
        "cache": cache,
        "generated_sql": entry["sql"],
        "sql_selection": {"source": "cache", "reason": f"Reused the SQL of an earlier question: {entry['question']}"},
    }
    if not state.get("rewritten_query"):
        update["rewritten_query"] = state["question"]
        update["query_variants"] = [state["question"]]
    return update


def cache_router(next_node: str):
    def route(state: TextToSQLState):
        if _failed(state):
            return "error"
        return "safety" if (state.get("cache") or {}).get("hit") else next_node
    return route


def _remember_answer(state: TextToSQLState, engine: Optional[Engine], answer: str, trust: Dict[str, Any]) -> None:
    """Keep a trusted answer for the next time the same question is asked."""
    if engine is None or not question_cache.enabled() or int(trust.get("score") or 0) < question_cache.MIN_TRUST_SCORE:
        return
    # Stored the way it will be looked up: a first question in the
    # user's words, a follow-up in its standalone (rewritten) form.
    if question_cache.first_question_in_thread(state.get("conversation_history", [])):
        wording = state["question"]
    else:
        wording = state.get("rewritten_query") or ""
    try:
        question_cache.get_cache().store(
            *_cache_identity(state, engine), wording, state.get("generated_sql") or "",
            answer, state["execution_result"], trust,
        )
    except Exception as error:
        log_stage(f"Question cache not updated: {type(error).__name__}: {error}")


# =========================================================
# QUERY DECOMPOSITION
# =========================================================

def decomposition_node(state: TextToSQLState):
    log_stage("[2/12] Query decomposition...")
    result = decompose_query(
        question=state["question"],
        rewritten_query=state["rewritten_query"],
    )
    log_stage("[2/12] Query decomposition completed.")
    return {"decomposition": result}


# =========================================================
# HYBRID RETRIEVAL + GRAPHRAG
# =========================================================

def retrieval_node(state: TextToSQLState):
    log_stage("[3/12] Schema retrieval (columns + values + join paths)...")
    # Rewrites first (they resolve follow-ups), then the user's own
    # words, so a poor rewrite can never hide what was actually asked.
    variants = state.get("query_variants") or [str(state["rewritten_query"])]
    queries = list(dict.fromkeys([*variants, state["question"]]))

    focus = build_schema_focus(
        schema=state["schema"],
        queries=queries,
        question=state["question"],
        top_k=query_settings.retrieval_top_k,
        relative_cutoff=query_settings.retrieval_relative_cutoff,
        wide_table_columns=query_settings.wide_table_columns,
    )
    context = focus["graph_context"]
    log_stage(
        f"[3/12] Retrieval completed: {len(context.get('retrieved_tables', []))} retrieved + "
        f"{len(context.get('connector_tables', []))} connector tables "
        f"(of {len(state['schema'].get('tables', []))}), "
        f"{len(context.get('value_hints', []))} value hints."
    )
    return {
        "retrieved_schema": focus["retrieved_schema"],
        "graph_context": context,
        "focused_schema": focus["focused_schema"],
    }


# =========================================================
# SCHEMA LINKING
# =========================================================

def schema_linking_node(state: TextToSQLState):
    log_stage("[4/12] Schema linking...")
    # Only the focused part of the schema (retrieved tables, connector
    # tables on the join paths, value hints) goes to the LLM; its JSON
    # answer is then verified against the full schema.
    linking = link_schema_structured(
        question=state["question"],
        rewritten_query=str(state["rewritten_query"]),
        decomposed_query=str(state["decomposition"]),
        schema=state.get("focused_schema") or state["schema"],
    )
    context = state.get("graph_context") or {}
    linked = verify_linking(linking["parsed"], state["schema"], context)
    rendered = render_linked_schema(linked, state["schema"], context)
    log_stage(
        f"[4/12] Schema linking completed ({linked['status']}): {len(linked['tables'])} tables, "
        f"{len(linked['joins'])} joins, {len(linked['corrections'])} corrections, "
        f"{len(linked['issues'])} issues."
    )
    return {"schema_linking": rendered, "linked_schema": {**linked, "raw": linking["raw"]}}


# =========================================================
# SQL EXTRACTION
# =========================================================

# Section headers emitted by sql_generator.py's output contract.
# Any of these after "SQL:" marks the end of the SQL statement.
_GENERATOR_SECTIONS = (
    "STATUS",
    "SQL",
    "QUERY_PLAN",
    "USED_TABLES",
    "USED_COLUMNS",
    "JOIN_PATH",
    "SQL_FEATURES",
    "ASSUMPTIONS",
    "VALIDATION_NOTES",
    "CLARIFICATION_REQUIRED",
    "REASON",
    "EXPLANATION",
    "CONFIDENCE",
)

# A line that starts a metadata section: optional markdown decoration,
# then a known section name or any UPPER_SNAKE label, then a single
# colon. ":(?!:)" keeps PostgreSQL casts such as "END::int" as SQL.
_SECTION_HEADER_RE = re.compile(
    r"^[ \t]*(?:[#*>]+[ \t]*)?"
    r"(?:(?:" + "|".join(_GENERATOR_SECTIONS) + r")\b|(?-i:[A-Z][A-Z0-9]*_[A-Z0-9_]+))"
    r"[ \t]*\**[ \t]*:(?!:)",
    flags=re.IGNORECASE | re.MULTILINE,
)

_SQL_LABEL_RE = re.compile(
    r"^[ \t]*(?:[#*>]+[ \t]*)?SQL[ \t]*\**[ \t]*:(?!:)\**[ \t]*",
    flags=re.IGNORECASE | re.MULTILINE,
)

_FENCE_RE = re.compile(
    r"```[ \t]*(?:sql|postgresql|postgres)?[ \t]*\n?(.*?)```",
    flags=re.IGNORECASE | re.DOTALL,
)


def _strip_fences(sql: str) -> str:
    sql = re.sub(r"^```[ \t]*(?:sql|postgresql|postgres)?[ \t]*", "", sql.strip(), flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip()


def extract_generated_sql(text: str) -> str:
    """
    Extract only the executable SQL from sql_generator.py output.

    The generator returns STATUS / SQL / QUERY_PLAN / USED_TABLES / ...
    sections. The SQL section ends at the next section header, so
    trailing metadata never reaches validation or execution.
    """
    if not isinstance(text, str):
        return ""

    text = text.strip()
    label = _SQL_LABEL_RE.search(text)

    if label:
        section = text[label.end():]
        next_header = _SECTION_HEADER_RE.search(section)
        if next_header:
            section = section[:next_header.start()]
        fenced = _FENCE_RE.search(section)
        sql = fenced.group(1) if fenced else section
    else:
        fenced = _FENCE_RE.search(text)
        sql = fenced.group(1) if fenced else text

    return _strip_fences(sql)


def _generation_status(raw_generation: str) -> str:
    """Read the STATUS value from the generator output, if present."""
    match = re.search(
        r"^[ \t]*(?:[#*>]+[ \t]*)?STATUS[ \t]*\**[ \t]*:\s*\**[ \t]*([A-Z_]+)",
        raw_generation,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1).upper() if match else ""


# =========================================================
# FAILURE HANDLING
# =========================================================

# Human-readable stage names for failure messages.
STAGE_LABELS = {
    "rewrite": "question rewriting",
    "decompose": "question analysis",
    "retrieve": "schema retrieval",
    "schema_link": "schema linking",
    "generate_sql": "SQL generation",
    "select_sql": "SQL candidate selection",
    "clarification": "clarification",
    "safety": "SQL safety check",
    "validate_sql": "SQL validation",
    "correction": "SQL correction",
    "execute": "SQL execution",
    "validate_result": "result verification",
    "generate_answer": "answer generation",
}


def _stage_failure(stage: str, message: str) -> Dict[str, Any]:
    """State update that routes the turn to the error node."""
    return {"status": "ERROR", "error_stage": stage, "error": message}


def _guard(stage: str, node: Callable[[TextToSQLState], Dict[str, Any]]):
    """
    Wrap a node so an unexpected exception (LLM timeout, parsing
    error, database error) becomes an ERROR state for this stage
    instead of aborting the graph with an HTTP 500.
    """
    @functools.wraps(node)
    def guarded(state: TextToSQLState) -> Dict[str, Any]:
        started = time.perf_counter()
        update: Dict[str, Any] = {}
        try:
            update = node(state) or {}
            return update
        except (AIUnavailableError, UsageLimitError) as exc:
            # Capacity, not a bug: no traceback, and the user sees the reason as is.
            log_stage(f"[{stage}] AI unavailable: {exc}")
            update = {**_stage_failure(stage, str(exc)), "ai_unavailable": True}
            return update
        except Exception as exc:
            logger.exception("Stage '%s' failed", stage)
            log_stage(f"[{stage}] FAILED: {type(exc).__name__}: {exc}")
            update = _stage_failure(stage, f"{type(exc).__name__}: {exc}")
            return update
        finally:
            failed = update.get("status") == "ERROR"
            telemetry.record_stage(
                stage, time.perf_counter() - started,
                status="error" if failed else ("cache" if (update.get("cache") or {}).get("hit") and
                                               stage.startswith("cache_check") else "ok"),
                error=str(update.get("error") or "") if failed else "",
            )
    return guarded


def _failed(state: TextToSQLState) -> bool:
    return state.get("status") == "ERROR"


def _continue_or_error(next_node: str):
    """Router for linear edges: go to next_node unless the stage failed."""
    def route(state: TextToSQLState) -> str:
        return "error" if _failed(state) else next_node
    route.__name__ = f"continue_to_{next_node}"
    return route


# =========================================================
# SQL GENERATION
# =========================================================

def _candidate_count() -> int:
    return max(1, int(query_settings.sql_candidates))


# Wording that signals a question needing more than a plain lookup or
# group-by; such questions keep all SQL candidates.
_HARD_QUESTION = re.compile(
    r"\b(percent|percentage|share|ratio|rate|proportion|running|cumulative|rank|median|average of|"
    r"per\s+\w+\s+per|more than one|at least|without|never|no\s+\w+|not|neither|compared?|versus|vs|"
    r"growth|change|difference|between|each\s+\w+\s+(?:and|with)|both|except|only|first|last|"
    r"top\s+\d+\s+\w+\s+(?:for|in|by)\s+each)\b",
    re.IGNORECASE,
)


def _is_simple_question(state: TextToSQLState) -> bool:
    linked = state.get("linked_schema") or {}
    tables = linked.get("tables") or []
    return len(tables) <= 1 and not _HARD_QUESTION.search(str(state.get("question") or ""))


def _candidates_for(state: TextToSQLState) -> int:
    count = _candidate_count()
    if count > 1 and query_settings.adaptive_candidates and _is_simple_question(state):
        return 1
    return count


def _parse_generation(index: int, raw: Any) -> Dict[str, Any]:
    if isinstance(raw, Exception) or raw is None:
        return {"index": index, "status": "ERROR", "sql": "", "raw": "",
                "error": str(raw) if raw is not None else "timed out"}
    text = str(raw).strip()
    status = _generation_status(text)
    sql = "" if status in ("NEEDS_CLARIFICATION", "FAILED") else extract_generated_sql(text)
    return {"index": index, "status": status or ("READY" if sql else "EMPTY"), "sql": sql, "raw": text, "error": ""}


# Metrics that need cost data. A model that cannot find costs tends to
# return revenue and call it profit; that answer looks right but is not.
_PROFIT_WORDS = re.compile(r"\b(profit|profits|profitable|profitability|margin|margins|net income|net earnings|earnings after|roi|return on investment)\b", re.I)
_COST_WORDS = re.compile(r"(cost|expense|salar|wage|payroll|cogs|purchase|freight|fee|tax)", re.I)


def unsupported_metric(question: str, sql: str) -> str:
    """A clarification message when the SQL answers a profit question without any cost column."""
    if not _PROFIT_WORDS.search(question or "") or not sql:
        return ""
    try:
        import sqlglot
        from sqlglot import exp

        names = {column.name for column in sqlglot.parse_one(sql, read=dialects.sqlglot_name()).find_all(exp.Column)}
    except Exception:
        names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql))
    if any(_COST_WORDS.search(name) for name in names):
        return ""
    return (
        "STATUS:\nNEEDS_CLARIFICATION\n\nSQL:\nNOT GENERATED\n\n"
        "CLARIFICATION_REQUIRED:\nThis data has no cost or expense information, so profit or margin "
        "cannot be worked out. Would you like the total revenue (money received) instead?\n\n"
        "REASON:\nProfit needs both revenue and costs; only revenue columns were found for this question.\n"
    )


def sql_generation_node(state: TextToSQLState):
    count = _candidates_for(state)
    log_stage(f"[5/12] SQL generation ({count} candidate{'s' if count > 1 else ''})...")
    arguments = dict(
        question=state["question"],
        rewritten_query=str(state["rewritten_query"]),
        decomposed_query=str(state["decomposition"]),
        linked_schema=str(state.get("schema_linking") or ""),
        prepared_schema=bool(state.get("linked_schema")),
    )

    def primary():
        return generate_sql(**arguments)

    def extra(number):
        return lambda: generate_sql(
            **arguments,
            model=candidate_llm(query_settings.candidate_temperature),
            strategy=CANDIDATE_STRATEGIES[number % len(CANDIDATE_STRATEGIES)],
        )

    if count == 1:
        outputs = [primary()]  # errors propagate to _guard as before
    else:
        outputs = run_parallel([primary] + [extra(n) for n in range(1, count)], query_settings.candidate_wait_s)
        if isinstance(outputs[0], Exception):
            raise outputs[0]  # the primary candidate is required

    parsed = [_parse_generation(index, raw) for index, raw in enumerate(outputs)]
    candidates = [item for item in parsed if item["sql"]]
    clarifications = [item for item in parsed if item["status"] == "NEEDS_CLARIFICATION"]
    summary = [{key: item[key] for key in ("index", "status", "sql", "error")} for item in parsed]
    primary_item = parsed[0]

    # Majority vote between "here is SQL" and "I need clarification";
    # a tie goes to the primary (deterministic) candidate.
    wants_clarification = bool(clarifications) and (
        len(clarifications) > len(candidates)
        or (len(clarifications) == len(candidates) and primary_item["status"] == "NEEDS_CLARIFICATION")
    )
    if wants_clarification:
        chosen = primary_item if primary_item["status"] == "NEEDS_CLARIFICATION" else clarifications[0]
        log_stage("[5/12] SQL generation: clarification required.")
        return {"raw_sql_generation": chosen["raw"], "generated_sql": "", "sql_candidates": summary}

    if not candidates:
        raw_result = primary_item["raw"]
        if primary_item["status"] == "FAILED":
            reason = _section_value(raw_result, "REASON") or "The SQL generator could not produce a query."
            log_stage("[5/12] SQL generation reported FAILED.")
        else:
            reason = "The SQL generator returned no SQL."
            log_stage("[5/12] SQL generation returned no SQL.")
        return {
            "raw_sql_generation": raw_result,
            "generated_sql": "",
            "sql_candidates": summary,
            **_stage_failure("generate_sql", reason),
        }

    first = candidates[0]
    refusal = unsupported_metric(state["question"], first["sql"])
    if refusal:
        log_stage("[5/12] SQL generation: profit asked but no cost column used; asking instead of guessing.")
        return {"raw_sql_generation": refusal, "generated_sql": "", "sql_candidates": summary}
    log_stage(f"[5/12] SQL generation completed: {len(candidates)} of {count} candidates contain SQL.")
    return {"raw_sql_generation": first["raw"], "generated_sql": first["sql"], "sql_candidates": summary}


# =========================================================
# CANDIDATE SELECTION (self-consistency)
# =========================================================

def candidate_selection_node(state: TextToSQLState, engine: Engine):
    candidates = [
        {"index": item["index"], "sql": item["sql"]}
        for item in state.get("sql_candidates") or []
        if item.get("sql")
    ]
    if len(candidates) < 2:
        return {}

    log_stage(f"[5b/12] Executing {len(candidates)} SQL candidates and comparing results...")
    outcome = select_candidate(candidates, engine, max_rows=query_settings.max_result_rows)
    selection = outcome["selection"]
    by_index = {item["index"]: item for item in outcome["summary"]}
    summary = []
    for item in state.get("sql_candidates") or []:
        evaluated = by_index.get(item["index"])
        if evaluated:
            item = {
                **item,
                "execution": evaluated["status"],  # ok / unsafe / invalid / failed
                "execution_error": evaluated.get("error", ""),
                **{key: evaluated[key] for key in ("row_count", "cluster", "selected") if key in evaluated},
            }
        summary.append(item)

    if outcome["winner"] is None:
        # Nothing ran cleanly: continue with the first candidate so the
        # normal validation/correction loop can repair it.
        log_stage("[5b/12] No candidate executed cleanly; continuing with the first one.")
        return {"sql_candidates": summary, "sql_selection": selection}

    winner = outcome["winner"]
    log_stage(f"[5b/12] Selected candidate {winner['index']}: {selection['reason']}.")
    return {
        "generated_sql": winner["sql"],
        "sql_candidates": summary,
        "sql_selection": selection,
        "candidate_execution": {"sql": winner["sql"], "result": outcome["execution"]},
    }


def generated_sql_router(state: TextToSQLState):
    if _failed(state):
        return "error"
    raw_generation = str(state.get("raw_sql_generation", "")).strip()
    status = _generation_status(raw_generation)
    if status == "NEEDS_CLARIFICATION":
        return "clarification"
    # No STATUS line: fall back to an explicit clarification marker.
    if not status and _section_value(raw_generation, "CLARIFICATION_REQUIRED"):
        return "clarification"
    return "safety"


# =========================================================
# CLARIFICATION
# =========================================================

_SECTION_PREFIX_RE = r"^[ \t]*(?:[#*>-]+[ \t]*)?\**[ \t]*"


def _section_value(text: str, section: str) -> str:
    """Value of a 'SECTION: value' line, tolerating markdown decoration."""
    match = re.search(
        _SECTION_PREFIX_RE + re.escape(section) + r"[ \t]*\**[ \t]*:(?!:)[ \t]*\**[ \t]*(.*)$",
        text or "",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1).strip().strip("*").strip() if match else ""


def clarification_node(state: TextToSQLState):
    generated = str(state.get("raw_sql_generation", ""))
    clarification = _section_value(generated, "CLARIFICATION_REQUIRED")
    reason = _section_value(generated, "REASON")

    if not clarification:
        clarification = "Could you clarify what you mean so I can map it to the data?"

    answer = "I need clarification before generating SQL.\n\n" + clarification
    if reason:
        answer += "\n\nReason: " + reason

    return {
        "status": "NEEDS_CLARIFICATION",
        "clarification": clarification,
        "answer": answer,
        "conversation_history": [{"role": "assistant", "content": answer}],
    }


# =========================================================
# SAFETY / VALIDATION
# =========================================================
#
# Order: deterministic safety first (no database access), then
# validation, which runs EXPLAIN against the database.

def sql_safety_node(state: TextToSQLState):
    log_stage("[6/12] SQL safety check...")
    result = check_sql_safety(state["generated_sql"])
    log_stage(f"[6/12] SQL safety completed: {'SAFE' if result.get('safe') else 'UNSAFE'}.")
    return {"safety_result": result}


def sql_validation_node(state: TextToSQLState, engine: Engine):
    log_stage("[7/12] SQL validation...")
    result = validate_sql(sql=state["generated_sql"], engine=engine, run_explain=True)
    log_stage(f"[7/12] SQL validation completed: {'VALID' if result.get('valid') else 'INVALID'}.")
    return {"validation_result": result}


MAX_CORRECTION_ATTEMPTS = 2


def _can_correct(state: TextToSQLState) -> bool:
    if state.get("correction_attempts", 0) < MAX_CORRECTION_ATTEMPTS:
        return True
    log_stage("[8/12] Maximum SQL correction attempts reached.")
    return False


def safety_router(state: TextToSQLState):
    if _failed(state):
        return "error"
    if state.get("safety_result", {}).get("safe", False):
        return "validate"
    return "correction" if _can_correct(state) else "error"


def validation_router(state: TextToSQLState):
    if _failed(state):
        return "error"
    if state.get("validation_result", {}).get("valid", False):
        return "execute"
    return "correction" if _can_correct(state) else "error"


def execution_router(state: TextToSQLState):
    if _failed(state):
        return "error"
    if state.get("execution_result", {}).get("success", False):
        return "validate_result"
    # Out of attempts: the failed result flows through result
    # validation so the final answer reports the execution failure.
    return "correction" if _can_correct(state) else "validate_result"


# =========================================================
# SQL CORRECTION
# =========================================================

def _describe_failure(result: Dict[str, Any]) -> str:
    parts = [str(result.get("reason") or "").strip()]
    parts += [str(error).strip() for error in result.get("errors") or []]
    return "\n".join(part for part in parts if part)


def _failure_reason(state: TextToSQLState) -> str:
    """
    Describe the stage that actually failed, checked in flow order
    (safety -> validate -> execute). Correction clears downstream
    results, so only stages that ran for the current SQL are present.
    """
    safety = state.get("safety_result") or {}
    if safety and not safety.get("safe", False):
        return "SQL safety check failed:\n" + _describe_failure(safety)

    validation = state.get("validation_result") or {}
    if validation and not validation.get("valid", False):
        return "SQL validation failed:\n" + _describe_failure(validation)

    execution = state.get("execution_result") or {}
    if execution and not execution.get("success", False):
        return "PostgreSQL execution failed:\n" + str(execution.get("error") or "Unknown execution error.")

    fanout = (state.get("sanity") or {}).get("fanout") or []
    if fanout:
        return "The SQL ran, but its result is wrong:\n" + "\n".join(item["message"] for item in fanout)

    return "SQL validation or execution failed."


def correction_node(state: TextToSQLState):
    log_stage("[8/12] SQL correction...")
    error_reason = _failure_reason(state)
    attempts = state.get("correction_attempts", 0) + 1
    current_sql = str(state.get("generated_sql", "")).strip()
    previous_attempts = [current_sql] if current_sql else []

    correction = correct_sql(
        question=state["question"],
        failed_sql=current_sql,
        error_message=str(error_reason),
        # Full schema (the correction may need a table retrieval
        # missed), with the focused tables first so they survive the
        # prompt size cap.
        schema=prioritize_schema(
            state["schema"],
            (state.get("graph_context") or {}).get("relevant_tables") or [],
        ),
        previous_attempts=previous_attempts,
    )

    corrected_sql = str(correction.get("sql", "")).strip() if correction.get("success") else ""

    if not corrected_sql:
        log_stage(f"[8/12] SQL correction failed (attempt {attempts}).")
        return {
            "corrected_sql": "",
            "correction_attempts": attempts,
            **_stage_failure(
                "correction",
                correction.get("error") or "SQL correction returned empty SQL.",
            ),
        }

    log_stage(f"[8/12] SQL correction completed (attempt {attempts}).")
    return {
        "corrected_sql": corrected_sql,
        "generated_sql": corrected_sql,
        "correction_attempts": attempts,
        # The corrected SQL is checked from scratch; results for the
        # previous SQL must not leak into routing or failure reasons.
        "safety_result": {},
        "validation_result": {},
        "execution_result": {},
        "sanity": {},
        "error": None,
    }


def correction_router(state: TextToSQLState):
    # The attempt limit is enforced before entering correction
    # (_can_correct), so every corrected SQL is re-checked.
    return "error" if _failed(state) else "safety"


def error_node(state: TextToSQLState):
    stage = state.get("error_stage") or ""
    error = state.get("error") or _failure_reason(state)

    if state.get("ai_unavailable"):
        answer = str(error)
    elif stage:
        label = STAGE_LABELS.get(stage, stage)
        answer = f"I couldn't answer this question because the {label} step failed."
    else:
        # Reached when correction attempts are exhausted.
        answer = "I could not generate a valid and safe SQL query for this question."

    log_stage(f"[ERROR] stage={stage or 'sql_checks'}: {str(error).splitlines()[0] if error else ''}")
    return {
        "status": "ERROR",
        "error": error,
        "error_stage": stage or "sql_checks",
        "answer": answer,
        "conversation_history": [{"role": "assistant", "content": answer}],
    }


# =========================================================
# EXECUTION / RESULT VALIDATION / ANSWER
# =========================================================

def execution_node(state: TextToSQLState, engine: Engine):
    log_stage("[9/12] PostgreSQL execution...")
    sql = state.get("generated_sql", "")
    cached = state.get("candidate_execution") or {}
    if cached.get("sql") == sql and (cached.get("result") or {}).get("success"):
        # Executed moments ago during candidate selection under the same
        # read-only guards; it has since passed the safety gate and
        # validation again, so the result is reused, not re-run.
        log_stage("[9/12] Reusing the result from candidate selection.")
        return {"execution_result": cached["result"], "candidate_execution": {}}
    result = execute_query(sql=sql, engine=engine)
    if result.get("success", False):
        truncated = " (truncated)" if result.get("truncated") else ""
        log_stage(
            f"[9/12] PostgreSQL execution completed: {result.get('row_count', 0)} rows{truncated}, "
            f"{result.get('execution_time_ms', 0):.2f} ms."
        )
    else:
        log_stage("[9/12] PostgreSQL execution failed.")
    return {"execution_result": result}


def result_validation_node(state: TextToSQLState, engine: Optional[Engine] = None):
    log_stage("[10/12] Result validation...")
    result = validate_result(result=state["execution_result"], allow_empty=True)
    sanity: Dict[str, Any] = {}
    if result.get("valid"):
        # SQL that runs can still be wrong (e.g. joins double-counting).
        sanity = check_result_sanity(
            state.get("generated_sql", ""), state["execution_result"], state["schema"], engine
        )
    log_stage(
        f"[10/12] Result validation completed: {'VALID' if result.get('valid') else 'INVALID'}"
        f"{', fan-out detected' if sanity.get('fanout') else ''}."
    )
    return {"result_validation": result, "sanity": sanity}


def result_router(state: TextToSQLState):
    if _failed(state):
        return "error"
    if (state.get("sanity") or {}).get("fanout") and _can_correct(state):
        return "correction"
    return "generate_answer"


def answer_generation_node(state: TextToSQLState, engine: Optional[Engine] = None):
    log_stage("[11/12] Natural-language answer generation...")
    validation = state.get("result_validation", {})

    if not validation.get("valid", False):
        answer = "The database query did not produce a valid result."
        return {
            "status": "ERROR",
            "error_stage": "validate_result",
            "answer": answer,
            "error": _describe_failure(validation) or None,
            "conversation_history": [{"role": "assistant", "content": answer}],
        }

    cache = dict(state.get("cache") or {})
    if cache.get("hit") and cache.get("result_key") == question_cache.result_key(state["execution_result"]):
        # Same SQL, same rows as last time: the stored answer still holds.
        answer = cache["answer"]
        claim_check = verify_claims(answer, state["question"], state["execution_result"])
        if claim_check["status"] != "unverified":
            cache["answer_reused"] = True
            trust = dict(cache.get("trust") or assess_trust({**state, "claim_check": claim_check}))
            trust["reasons"] = list(trust.get("reasons") or []) + [{
                "signal": "cache", "effect": 0,
                "detail": "Same question as before; its SQL was run again on the current data and gave the same result.",
            }]
            log_stage("[11/12] Same result as last time: reusing the stored answer (no AI call).")
            message = {"role": "assistant", "content": answer}
            evidence = result_evidence(state["execution_result"])
            if evidence:
                message["evidence"] = evidence
            return {
                "status": "SUCCESS",
                "answer": answer,
                "claim_check": claim_check,
                "trust": trust,
                "cache": cache,
                "conversation_history": [message],
            }

    result = generate_answer(question=state["question"], result=state["execution_result"])
    if not result.get("success", False):
        answer = "I could not generate an answer from the database result."
        return {
            "status": "ERROR",
            "error_stage": "generate_answer",
            "answer": answer,
            "error": result.get("error"),
            "conversation_history": [{"role": "assistant", "content": answer}],
        }

    answer = result["answer"]
    claim_check = verify_claims(answer, state["question"], state["execution_result"])
    if claim_check["status"] == "unverified":
        log_stage("[11/12] Answer states values that are not in the result; regenerating once.")
        problems = []
        if claim_check["unverified_numbers"]:
            problems.append("These numbers are not in the result: " + ", ".join(claim_check["unverified_numbers"]))
        if claim_check["unverified_values"]:
            problems.append("These values are not in the result: " + ", ".join(claim_check["unverified_values"]))
        retry = generate_answer(
            question=state["question"],
            result=state["execution_result"],
            correction_note="\n".join(problems),
        )
        if retry.get("success"):
            retry_check = verify_claims(retry["answer"], state["question"], state["execution_result"])
            if len(retry_check["unverified_numbers"]) + len(retry_check["unverified_values"]) <= (
                len(claim_check["unverified_numbers"]) + len(claim_check["unverified_values"])
            ):
                answer, claim_check = retry["answer"], retry_check
        claim_check["regenerated"] = True

    trust = assess_trust({**state, "claim_check": claim_check})
    if cache.get("hit"):
        # The data changed since the SQL was stored; keep the lower of
        # the stored and the fresh trust (linking was not redone).
        stored = cache.get("trust") or {}
        if stored and int(stored.get("score") or 0) < trust["score"]:
            trust = {**trust, "score": stored["score"], "level": stored.get("level", trust["level"])}
        trust["reasons"] = list(trust.get("reasons") or []) + [{
            "signal": "cache", "effect": 0,
            "detail": "Reused the SQL of an earlier question; the data has changed, so the answer was written again.",
        }]
    log_stage(f"[11/12] Natural-language answer generation completed (trust: {trust['level']}, {trust['score']}).")
    _remember_answer(state, engine, answer, trust)
    message = {"role": "assistant", "content": answer}
    evidence = result_evidence(state["execution_result"])
    if evidence:
        message["evidence"] = evidence
    return {
        "status": "SUCCESS",
        "answer": answer,
        "claim_check": claim_check,
        "trust": trust,
        "conversation_history": [message],
    }


def result_evidence(execution_result: Dict[str, Any], max_rows: int = 5, max_chars: int = 600) -> str:
    """
    Compact, exact rendering of the first result rows, e.g.
    "customer_city=sao paulo, customers=15540".

    Stored with the assistant message so later turns resolve
    references ("that city") to the stored database value, not to the
    answer's wording (which may re-case, accent or translate it).
    """
    rows = (execution_result or {}).get("rows") or []
    lines = [
        ", ".join(f"{column}={value}" for column, value in row.items())
        for row in rows[:max_rows]
    ]
    if len(rows) > max_rows:
        lines.append(f"... ({execution_result.get('row_count', len(rows))} rows in total)")
    return "\n".join(lines)[:max_chars]


def answer_router(state: TextToSQLState):
    # A guarded exception in answer generation leaves no answer; the
    # error node records one so the thread history stays consistent.
    if _failed(state) and not state.get("answer"):
        return "error"
    return "end"


# =========================================================
# BUILD LANGGRAPH
# =========================================================

def build_text_to_sql_graph(engine: Engine, checkpointer=None):
    graph = StateGraph(TextToSQLState)

    nodes = {
        "rewrite": rewrite_node,
        "decompose": decomposition_node,
        "retrieve": retrieval_node,
        "schema_link": schema_linking_node,
        "generate_sql": sql_generation_node,
        "select_sql": lambda state: candidate_selection_node(state, engine),
        "clarification": clarification_node,
        "safety": sql_safety_node,
        "validate_sql": lambda state: sql_validation_node(state, engine),
        "correction": correction_node,
        "execute": lambda state: execution_node(state, engine),
        "validate_result": lambda state: result_validation_node(state, engine),
        "generate_answer": lambda state: answer_generation_node(state, engine),
        "cache_check": lambda state: cache_check_node(state, engine, "raw"),
        "cache_check_rewritten": lambda state: cache_check_node(state, engine, "rewritten"),
    }
    for name, node in nodes.items():
        graph.add_node(name, _guard(name, node))
    # The error node only builds a message from state; it is the
    # terminal failure path and is not guarded.
    graph.add_node("error", error_node)

    # The question cache is checked before rewriting (first question of
    # a chat) and after it (follow-ups, in their standalone form).
    graph.add_edge(START, "cache_check")
    graph.add_conditional_edges(
        "cache_check", cache_router("rewrite"),
        {"rewrite": "rewrite", "safety": "safety", "error": "error"},
    )
    graph.add_conditional_edges(
        "rewrite", _continue_or_error("cache_check_rewritten"),
        {"cache_check_rewritten": "cache_check_rewritten", "error": "error"},
    )
    graph.add_conditional_edges(
        "cache_check_rewritten", cache_router("decompose"),
        {"decompose": "decompose", "safety": "safety", "error": "error"},
    )

    linear = [
        ("decompose", "retrieve"),
        ("retrieve", "schema_link"),
        ("schema_link", "generate_sql"),
    ]
    for source, target in linear:
        graph.add_conditional_edges(
            source,
            _continue_or_error(target),
            {target: target, "error": "error"},
        )

    graph.add_conditional_edges(
        "generate_sql",
        generated_sql_router,
        # Every generated SQL first goes through candidate selection
        # (a no-op for a single candidate), then the normal safety gate.
        {"safety": "select_sql", "clarification": "clarification", "error": "error"},
    )
    graph.add_conditional_edges(
        "select_sql",
        _continue_or_error("safety"),
        {"safety": "safety", "error": "error"},
    )
    graph.add_conditional_edges(
        "safety",
        safety_router,
        {"validate": "validate_sql", "correction": "correction", "error": "error"},
    )
    graph.add_conditional_edges(
        "validate_sql",
        validation_router,
        {"execute": "execute", "correction": "correction", "error": "error"},
    )
    graph.add_conditional_edges(
        "correction",
        correction_router,
        {"safety": "safety", "error": "error"},
    )
    graph.add_conditional_edges(
        "execute",
        execution_router,
        {"validate_result": "validate_result", "correction": "correction", "error": "error"},
    )
    graph.add_conditional_edges(
        "validate_result",
        result_router,
        {"generate_answer": "generate_answer", "correction": "correction", "error": "error"},
    )
    graph.add_conditional_edges(
        "generate_answer",
        answer_router,
        {"end": END, "error": "error"},
    )
    graph.add_conditional_edges(
        "clarification",
        _continue_or_error("end"),
        {"end": END, "error": "error"},
    )
    graph.add_edge("error", END)

    return graph.compile(checkpointer=checkpointer)

# =========================================================
# THREAD STATE LOADER
# =========================================================

def add_to_thread(engine: Engine, thread_id: str, messages: List[Dict[str, Any]]) -> None:
    """Add messages (e.g. a "why" investigation and its answer) to a chat's conversation."""
    if not thread_id or not thread_id.strip() or not messages:
        return
    config = {"configurable": {"thread_id": thread_id}}
    with dialects.using(engine), _checkpointer(engine) as checkpointer:
        graph = build_text_to_sql_graph(engine=engine, checkpointer=checkpointer)
        # As if the run ended: the next question starts a fresh run with this memory.
        graph.update_state(config, {"conversation_history": list(messages)}, as_node="error")


def get_thread_state(engine: Engine, thread_id: str) -> Dict[str, Any]:
    """Load the latest persisted LangGraph state for a thread."""
    if not thread_id or not thread_id.strip():
        return {}

    config = {"configurable": {"thread_id": thread_id}}

    with dialects.using(engine), _checkpointer(engine) as checkpointer:
        graph = build_text_to_sql_graph(engine=engine, checkpointer=checkpointer)
        snapshot = graph.get_state(config)
        if snapshot is None:
            return {}
        return dict(snapshot.values or {})


# =========================================================
# MAIN RUNNER
# =========================================================

def run_text_to_sql(question: str, engine: Engine, thread_id: str):
    with dialects.using(engine):
        return _run_text_to_sql(question, engine, thread_id)


def _run_text_to_sql(question: str, engine: Engine, thread_id: str):
    if not question.strip():
        raise ValueError("Question cannot be empty.")
    if not thread_id.strip():
        raise ValueError("thread_id cannot be empty.")

    # Cached per database (filled by /connect-postgres and
    # /import-uploaded-files); includes profiler-inferred relationships.
    log_stage("[0/12] Loading database schema...")
    started = time.perf_counter()
    schema = get_agent_schema(engine)
    telemetry.record_stage("load_schema", time.perf_counter() - started)
    log_stage(
        f"[0/12] Schema loaded: {len(schema.get('tables', []))} tables, "
        f"{len(schema.get('relationships', []))} relationships."
    )

    started = time.perf_counter()
    with _checkpointer(engine) as checkpointer:
        log_stage("[0/12] Building LangGraph...")
        graph = build_text_to_sql_graph(engine=engine, checkpointer=checkpointer)
        telemetry.record_stage("prepare", time.perf_counter() - started)

        # Transient fields are reset explicitly: on an existing thread
        # the checkpointer would otherwise carry the previous turn's
        # values into this one. conversation_history is append-only
        # (operator.add), so the thread's history is preserved.
        initial_state: TextToSQLState = {
            **fresh_turn_state(),
            "question": question.strip(),
            "conversation_history": [
                {"role": "user", "content": question.strip()}
            ],
            "schema": schema,
        }

        config = {"configurable": {"thread_id": thread_id}}
        log_stage(f"Starting Text-to-SQL pipeline for thread: {thread_id}")

        result = graph.invoke(initial_state, config=config)
        log_stage("[12/12] Text-to-SQL pipeline completed.")
        return result
