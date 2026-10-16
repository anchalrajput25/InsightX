# backend/main.py

import logging
import os
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend import chat_list, connections, dialects, history, telemetry, tracing
from backend.text_to_sql import question_cache
from backend.ai_gateway import AIUnavailableError, UsageLimitError, ai_request, get_gateway
from backend.ai_gateway.report import openrouter_key_status, usage_summary
from backend.auth.deps import Caller, auth_required, current_caller
from backend.auth.routes import router as auth_router
from backend.auth.store import get_user_store
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from backend.config import ingestion_settings
from backend.database import (
    DatabaseConnectionError,
    create_database_engine,
    test_postgres_connection,
)

from backend.ingestion import (
    IngestionError,
    UploadTooLargeError,
    extract_zip,
    find_data_files,
    import_files_to_postgres,
    inspect_files,
    safe_upload_name,
    save_upload,
)

from backend.schema_profiler import (
    get_database_schema,
)

from backend.text_to_sql.agent import (
    get_thread_state,
    add_to_thread,
    run_text_to_sql,
)

from backend.investigation.engine import InvestigationError
from backend.investigation.planner import looks_like_investigation
from backend.investigation.service import run_investigation
from backend.text_to_sql.chart_context import chart_context
from backend.text_to_sql.follow_ups import suggest_follow_ups
from backend.text_to_sql.retriever import warm_schema_index
from backend.text_to_sql.schema_context import (
    cache_schema_from_profile,
    invalidate_schema_cache,
)


# ============================================================
# APP
# ============================================================

logger = logging.getLogger("insightx.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Upload sessions live in memory, so directories left by a previous
    # run can never be imported; remove them at startup.
    ingestion_settings.upload_dir.mkdir(parents=True, exist_ok=True)
    for directory in ingestion_settings.upload_dir.iterdir():
        if directory.is_dir():
            _remove_directory(directory)
    # Park AI routes whose free model was retired, now and every few hours.
    from backend.ai_gateway.discovery import start_background_checks

    stop_route_checks = start_background_checks() if os.getenv("INSIGHTX_ROUTE_CHECKS", "1") != "0" else None
    yield
    if stop_route_checks:
        stop_route_checks.set()


app = FastAPI(
    title="InsightX Backend",
    version="1.0.0",
    lifespan=lifespan,
)

# Sign-up / sign-in. Every other endpoint (except the health check)
# takes `caller: Caller = Depends(current_caller)`; a test checks that.
app.include_router(auth_router)


# ============================================================
# CONNECTION SESSIONS
#
# Open engines are kept in memory; the connection details (password
# encrypted) are also saved by backend.connections, so after a restart,
# or on another backend server, the connection is opened again on
# first use.
# ============================================================

POSTGRES_SESSIONS: dict[str, dict] = {}

_RESTORE_LOCK = threading.Lock()

UPLOAD_SESSIONS: dict[str, dict] = {}

# Endpoints run in FastAPI's thread pool; guard the upload registry.
_UPLOAD_LOCK = threading.Lock()


# ============================================================
# REQUEST MODELS
# ============================================================

class QueryRequest(BaseModel):

    connection_id: str
    question: str
    thread_id: Optional[str] = None
    # False: do not reuse an earlier answer to the same question.
    use_cache: bool = True


class ExecutionSummary(BaseModel):
    """Result of running the final SQL (rows capped server-side)."""

    success: bool = False
    status: str = ""
    error: Optional[str] = None
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    max_rows: Optional[int] = None
    execution_time_ms: float = 0.0


class QueryResponse(BaseModel):
    """
    Public contract of POST /query.

    Contains the answer, the evidence behind it (final SQL and capped
    result rows) and per-stage pipeline outputs for the UI. The
    database schema and other internal graph state are not returned.
    """

    status: str
    answer: str = ""
    question: str = ""
    error: Optional[str] = None
    error_stage: str = ""
    clarification: str = ""

    # Evidence
    sql: str = ""
    execution_result: ExecutionSummary = Field(default_factory=ExecutionSummary)

    # Pipeline stages (shown in the UI's pipeline view)
    rewritten_query: Any = ""
    query_variants: List[str] = Field(default_factory=list)
    decomposition: Any = ""
    retrieved_schema: Any = Field(default_factory=list)
    graph_context: Any = Field(default_factory=dict)
    schema_linking: Any = ""
    # Verified linker output: tables, columns, joins, filters,
    # corrections and issues found by deterministic verification.
    linked_schema: Dict[str, Any] = Field(default_factory=dict)
    # Every SQL candidate and how the final one was chosen.
    sql_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    sql_selection: Dict[str, Any] = Field(default_factory=dict)
    # Trust layer: how far the answer can be believed, and why.
    trust: Dict[str, Any] = Field(default_factory=dict)
    claim_check: Dict[str, Any] = Field(default_factory=dict)
    sanity: Dict[str, Any] = Field(default_factory=dict)
    # Question cache: set when the SQL (and maybe the answer) of an
    # earlier identical question was reused.
    cache: Dict[str, Any] = Field(default_factory=dict)
    # Saved trace of this question: id, seconds, AI calls, tokens.
    trace: Dict[str, Any] = Field(default_factory=dict)
    generated_sql: str = ""
    corrected_sql: str = ""
    correction_attempts: int = 0
    safety_result: Dict[str, Any] = Field(default_factory=dict)
    validation_result: Dict[str, Any] = Field(default_factory=dict)
    result_validation: Dict[str, Any] = Field(default_factory=dict)
    # Wider rows for the chart when the answer is a "top N" pick.
    chart_data: Dict[str, Any] = Field(default_factory=dict)
    # Related questions to ask next (clickable under the answer).
    follow_ups: List[str] = Field(default_factory=list)

    conversation_history: List[Dict[str, Any]] = Field(default_factory=list)
    connection_id: str
    thread_id: str


def build_query_response(
    result: Dict[str, Any],
    connection_id: str,
    thread_id: str,
    chart_data: Optional[Dict[str, Any]] = None,
    trace: Optional[Dict[str, Any]] = None,
    follow_ups: Optional[List[str]] = None,
) -> QueryResponse:
    """Map final LangGraph state to the public response contract."""

    # Database values (Decimal, datetime, ...) become JSON-native.
    execution = jsonable_encoder(
        result.get("execution_result") or {}
    )

    return QueryResponse(
        status=str(result.get("status") or "ERROR"),
        answer=str(result.get("answer") or ""),
        question=str(result.get("question") or ""),
        error=result.get("error"),
        error_stage=str(result.get("error_stage") or ""),
        clarification=str(result.get("clarification") or ""),
        sql=str(result.get("generated_sql") or ""),
        execution_result=ExecutionSummary(**{
            key: value
            for key, value in execution.items()
            if key in ExecutionSummary.model_fields
        }),
        rewritten_query=jsonable_encoder(result.get("rewritten_query", "")),
        query_variants=[str(item) for item in result.get("query_variants") or []],
        decomposition=jsonable_encoder(result.get("decomposition", "")),
        retrieved_schema=jsonable_encoder(result.get("retrieved_schema", [])),
        graph_context=jsonable_encoder(result.get("graph_context", {})),
        schema_linking=jsonable_encoder(result.get("schema_linking", "")),
        linked_schema=jsonable_encoder(result.get("linked_schema") or {}),
        sql_candidates=jsonable_encoder(result.get("sql_candidates") or []),
        sql_selection=jsonable_encoder(result.get("sql_selection") or {}),
        trust=jsonable_encoder(result.get("trust") or {}),
        claim_check=jsonable_encoder(result.get("claim_check") or {}),
        sanity=jsonable_encoder(result.get("sanity") or {}),
        cache=jsonable_encoder({
            key: value for key, value in (result.get("cache") or {}).items()
            if key in ("hit", "match", "question", "asked_at", "answer_reused")
        }),
        generated_sql=str(result.get("generated_sql") or ""),
        corrected_sql=str(result.get("corrected_sql") or ""),
        correction_attempts=int(result.get("correction_attempts") or 0),
        safety_result=jsonable_encoder(result.get("safety_result") or {}),
        validation_result=jsonable_encoder(result.get("validation_result") or {}),
        result_validation=jsonable_encoder(result.get("result_validation") or {}),
        chart_data=jsonable_encoder(chart_data or {}),
        trace=jsonable_encoder(trace or {}),
        follow_ups=[str(item) for item in follow_ups or []],
        conversation_history=jsonable_encoder(result.get("conversation_history") or []),
        connection_id=connection_id,
        thread_id=thread_id,
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/")
def health_check():

    return {
        "status": "running",
        "service": "InsightX Backend",
    }


@app.get("/test")
def test():

    print("TEST ENDPOINT HIT")

    return {
        "message": "Backend is working"
    }


# ============================================================
# SESSION HELPERS
# ============================================================

def create_connection_id() -> str:

    return str(uuid.uuid4())


def create_thread_id() -> str:

    return str(uuid.uuid4())


def get_connection_session(
    connection_id: str,
    caller: Optional[Caller] = None,
) -> dict:

    session = POSTGRES_SESSIONS.get(
        connection_id
    ) or restore_connection(connection_id)

    # Someone else's connection looks exactly like a missing one.
    if session and caller is not None and not caller.owns(session.get("owner")):
        session = None

    if not session:

        raise HTTPException(
            status_code=404,
            detail="Database connection session not found.",
        )

    return session


def remember_connection(connection_id: str, session: dict, password: str) -> None:
    """Save the connection so it outlives this server process."""
    if not connections.enabled():
        return
    try:
        connections.get_store().save(
            connection_id,
            owner=session.get("owner"),
            source_type=session["source_type"],
            db_type=dialects.kind(session["engine"]),
            host=session["host"],
            port=session["port"],
            database=session["database"],
            username=session["username"],
            password=password,
        )
    except Exception as exc:  # the connection still works in memory
        logger.warning("Connection %s not saved: %s", connection_id, _first_line(exc))


def restore_connection(connection_id: str) -> Optional[dict]:
    """Open a saved connection again (after a restart), or None."""
    if not connections.enabled():
        return None
    with _RESTORE_LOCK:
        session = POSTGRES_SESSIONS.get(connection_id)
        if session:
            return session
        try:
            saved = connections.get_store().load(connection_id)
        except Exception as exc:
            logger.warning("Saved connection %s not readable: %s", connection_id, _first_line(exc))
            return None
        if saved is None:
            return None
        try:
            engine = create_database_engine(
                saved["db_type"],
                host=saved["host"],
                port=saved["port"],
                database=saved["database"],
                username=saved["username"],
                password=saved["password"],
            )
        except DatabaseConnectionError as exc:
            logger.warning("Saved connection %s could not be opened: %s", connection_id, exc.message)
            return None
        session = {
            "engine": engine,
            "owner": saved["owner"],
            "source_type": saved["source_type"],
            "host": saved["host"],
            "port": saved["port"],
            "database": saved["database"],
            "username": saved["username"],
        }
        POSTGRES_SESSIONS[connection_id] = session
        return session


# ============================================================
# CONNECTION HELPERS
# ============================================================

def open_verified_engine(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    db_type: str = "postgresql",
) -> Engine:
    """
    Create an engine and prove it can connect. Raises HTTPException
    with a classified status (401/404/503/504/...) and a message that
    never contains the password.
    """
    try:
        engine = create_database_engine(
            db_type,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
        )
    except DatabaseConnectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    try:
        test_postgres_connection(engine)
    except DatabaseConnectionError as exc:
        engine.dispose()
        logger.warning(
            "%s connection to %s:%s/%s as %s failed (%s)",
            dialects.label(db_type), host, port, database, username, exc.status_code,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message,
        ) from exc

    return engine


def warm_retrieval_index(agent_schema: Dict[str, Any]) -> None:
    """
    Build the retrieval index (and column embeddings) in the
    background, so the first question on a large database does not
    pay for it. Failures only mean the first question builds it.
    """
    def build() -> None:
        try:
            warm_schema_index(agent_schema)
        except Exception as exc:
            logger.warning("Retrieval index warm-up failed: %s", exc)

    threading.Thread(target=build, name="retrieval-warmup", daemon=True).start()


def profile_database(engine: Engine) -> Dict[str, Any]:
    """Profile the database and cache it as the agent schema."""
    try:
        schema = get_database_schema(engine)
    except Exception as exc:
        logger.exception("Schema inspection failed")
        raise HTTPException(
            status_code=500,
            detail=f"Schema inspection failed: {_first_line(exc)}",
        ) from exc

    # Reuse this profile (incl. inferred relationships) as the agent
    # schema so /query does not re-inspect every turn.
    agent_schema = cache_schema_from_profile(engine, schema)
    warm_retrieval_index(agent_schema)
    return schema


def _first_line(exc: Exception) -> str:
    message = str(getattr(exc, "orig", None) or exc).strip()
    return message.splitlines()[0] if message else type(exc).__name__


# ============================================================
# CONNECT POSTGRESQL
# ============================================================

@app.post("/connect-postgres")
def connect_postgres(
    host: str = Form(...),
    port: Optional[int] = Form(None),
    database: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db_type: str = Form("postgresql"),
    caller: Caller = Depends(current_caller),
):
    """Connect a PostgreSQL database, or a MySQL / SQL Server one (db_type=mysql / mssql)."""
    if not dialects.is_known(db_type) or dialects.kind(db_type) == dialects.SQLITE:
        raise HTTPException(status_code=400, detail="db_type must be 'postgresql', 'mysql' or 'mssql'.")
    db_type = dialects.kind(db_type.strip())
    port = port or dialects.DEFAULT_PORTS[db_type]

    engine = open_verified_engine(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        db_type=db_type,
    )

    try:
        schema = profile_database(engine)
    except HTTPException:
        engine.dispose()
        raise

    connection_id = create_connection_id()

    # Keep engine in memory for this local deployment.
    POSTGRES_SESSIONS[connection_id] = {
        "engine": engine,
        "owner": caller.account_id,
        "source_type": db_type,
        "host": host,
        "port": port,
        "database": database,
        "username": username,
    }
    remember_connection(connection_id, POSTGRES_SESSIONS[connection_id], password)

    return {
        "status": "success",
        "source_type": db_type,
        "connection_id": connection_id,
        "message": f"{dialects.label(db_type)} connected successfully.",
        "schema": schema,
        "next_action": "ready_for_query",
    }


# ============================================================
# CONNECT A SQLITE FILE
# ============================================================

SQLITE_DIR = Path(__file__).resolve().parents[1] / "logs" / "sqlite_files"


@app.post("/connect-sqlite")
def connect_sqlite(
    file: UploadFile = File(...),
    caller: Caller = Depends(current_caller),
):
    """Upload a SQLite database file (.db / .sqlite) and ask questions about it."""
    file_name = safe_upload_name(file.filename or "database.db")
    SQLITE_DIR.mkdir(parents=True, exist_ok=True)
    connection_id = create_connection_id()
    path = SQLITE_DIR / f"{connection_id}.db"
    try:
        save_upload(file.file, path)
    except UploadTooLargeError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    try:
        engine = open_verified_engine(host="", port=0, database=str(path), username="", password="",
                                      db_type=dialects.SQLITE)
        try:
            schema = profile_database(engine)
        except HTTPException:
            engine.dispose()
            raise
    except HTTPException:
        path.unlink(missing_ok=True)
        raise

    POSTGRES_SESSIONS[connection_id] = {
        "engine": engine,
        "owner": caller.account_id,
        "source_type": dialects.SQLITE,
        "host": "",
        "port": 0,
        "database": str(path),
        "username": "",
        "file_name": file_name,
    }
    remember_connection(connection_id, POSTGRES_SESSIONS[connection_id], "")

    return {
        "status": "success",
        "source_type": dialects.SQLITE,
        "connection_id": connection_id,
        "message": f"SQLite file '{file_name}' connected successfully.",
        "schema": schema,
        "next_action": "ready_for_query",
    }


# ============================================================
# UPLOAD SESSIONS
# ============================================================

def _remove_directory(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("Could not remove upload directory %s", path)


def prune_expired_uploads(now: Optional[float] = None) -> None:
    """
    Remove upload sessions older than the TTL and upload directories
    that belong to no active session (e.g. left by a crash/restart).
    """
    now = time.time() if now is None else now
    ttl = ingestion_settings.upload_ttl_seconds

    with _UPLOAD_LOCK:
        expired = [
            session_id
            for session_id, session in UPLOAD_SESSIONS.items()
            if now - session["created_at"] > ttl
        ]
        for session_id in expired:
            session = UPLOAD_SESSIONS.pop(session_id)
            _remove_directory(Path(session["directory"]))
        active = set(UPLOAD_SESSIONS)

    upload_dir = ingestion_settings.upload_dir
    if not upload_dir.exists():
        return
    for directory in upload_dir.iterdir():
        if not directory.is_dir() or directory.name in active:
            continue
        try:
            age = now - directory.stat().st_mtime
        except OSError:
            continue
        if age > ttl:
            _remove_directory(directory)


def get_upload_session(session_id: str, caller: Optional[Caller] = None) -> Dict[str, Any]:
    with _UPLOAD_LOCK:
        session = UPLOAD_SESSIONS.get(session_id)
    if session and caller is not None and not caller.owns(session.get("owner")):
        session = None
    if not session or not Path(session["extracted"]).exists():
        raise HTTPException(
            status_code=404,
            detail="Upload session not found or expired. Please upload the ZIP again.",
        )
    return session


def finish_upload_session(session_id: str) -> None:
    with _UPLOAD_LOCK:
        session = UPLOAD_SESSIONS.pop(session_id, None)
    if session:
        _remove_directory(Path(session["directory"]))


# ============================================================
# UPLOAD DATABASE FOLDER
# ============================================================

@app.get("/upload/fast-loader")
def fast_loader_status(caller: Caller = Depends(current_caller)):
    """Whether big CSV files load with DuckDB, and which Python runs the backend."""
    import sys

    from backend import fast_ingest

    return {
        "available": fast_ingest.available(),
        "version": getattr(fast_ingest.duckdb, "__version__", None),
        "min_mb": round(fast_ingest.MIN_BYTES / 1024 / 1024, 1),
        "python": sys.executable,
    }


@app.post("/upload-folder")
def upload_folder(
    file: UploadFile = File(...),
    caller: Caller = Depends(current_caller),
):
    """
    Save, extract and inspect an uploaded ZIP of CSV/Excel files.

    Every file is read completely; files that cannot be imported are
    reported with importable=false and an error. Nothing is written to
    a database here.
    """
    prune_expired_uploads()

    filename = safe_upload_name(file.filename)
    if not filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a .zip file containing CSV or Excel files.",
        )

    session_id = str(uuid.uuid4())
    session_directory = ingestion_settings.upload_dir / session_id
    extracted_directory = session_directory / "extracted"
    session_directory.mkdir(parents=True, exist_ok=False)

    try:
        zip_path = session_directory / filename
        save_upload(file.file, zip_path)

        extract_zip(zip_path, extracted_directory)
        # The archive is no longer needed once extracted.
        zip_path.unlink(missing_ok=True)

        data_files = find_data_files(extracted_directory)
        if not data_files:
            raise IngestionError(
                "No CSV or Excel data files were found in the uploaded folder."
            )

        file_profiles = inspect_files(data_files, extracted_directory)

        if not any(profile["importable"] for profile in file_profiles):
            problems = "; ".join(
                f"{profile['file_name']}: {profile['error']}"
                for profile in file_profiles
            )
            raise IngestionError(f"None of the files can be imported. {problems}")

    except UploadTooLargeError as exc:
        _remove_directory(session_directory)
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    except IngestionError as exc:
        _remove_directory(session_directory)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except Exception as exc:
        _remove_directory(session_directory)
        logger.exception("Upload %s failed", session_id)
        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {_first_line(exc)}",
        ) from exc

    with _UPLOAD_LOCK:
        UPLOAD_SESSIONS[session_id] = {
            "owner": caller.account_id,
            "directory": str(session_directory),
            "extracted": str(extracted_directory),
            "files": file_profiles,
            "created_at": time.time(),
        }

    skipped = [profile for profile in file_profiles if not profile["importable"]]

    return {
        "status": "success",
        "session_id": session_id,
        "files": file_profiles,
        "file_profiles": file_profiles,
        "total_files": len(file_profiles),
        "importable_files": len(file_profiles) - len(skipped),
        "skipped_files": [
            {"file_name": profile["file_name"], "reason": profile["error"]}
            for profile in skipped
        ],
        "next_action": "connect_postgresql_and_import",
    }


# ============================================================
# IMPORT UPLOADED FILES INTO POSTGRESQL
# ============================================================

@app.post("/import-uploaded-files")
def import_uploaded_files(
    session_id: str = Form(...),
    host: str = Form(...),
    port: int = Form(5432),
    database: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    caller: Caller = Depends(current_caller),
):
    """
    Import the inspected files atomically, then profile the database.

    The upload session is kept when the import fails, so the user can
    fix the connection details and retry without uploading again.
    """
    session = get_upload_session(session_id, caller)

    engine = open_verified_engine(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
    )

    try:
        # Tables are about to change: drop any cached schema for this
        # database (also used by other open sessions).
        invalidate_schema_cache(engine)

        outcome = import_files_to_postgres(
            file_specs=session["files"],
            engine=engine,
            base_directory=Path(session["extracted"]),
        )

    except IngestionError as exc:
        engine.dispose()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except Exception as exc:
        engine.dispose()
        logger.exception("Import for upload %s failed", session_id)
        raise HTTPException(
            status_code=500,
            detail=f"Import failed; no tables were changed. {_first_line(exc)}",
        ) from exc

    try:
        schema = profile_database(engine)
    except HTTPException as exc:
        engine.dispose()
        finish_upload_session(session_id)
        raise HTTPException(
            status_code=exc.status_code,
            detail=(
                "The data was imported, but schema inspection failed. "
                "Connect to the database again to retry. "
                f"{exc.detail}"
            ),
        ) from exc

    connection_id = create_connection_id()

    POSTGRES_SESSIONS[connection_id] = {
        "engine": engine,
        "owner": caller.account_id,
        "source_type": "uploaded_folder",
        "host": host,
        "port": port,
        "database": database,
        "username": username,
    }
    remember_connection(connection_id, POSTGRES_SESSIONS[connection_id], password)

    finish_upload_session(session_id)

    return {
        "status": "success",
        "source_type": "uploaded_folder",
        "connection_id": connection_id,
        "message": "Files imported into PostgreSQL and schema generated.",
        "imported_tables": outcome["imported_tables"],
        "replaced_tables": outcome["replaced_tables"],
        "skipped_files": outcome["skipped_files"],
        "total_tables": len(outcome["imported_tables"]),
        "schema": schema,
        "next_action": "ready_for_query",
    }


# ============================================================
# QUERY ENDPOINT
# ============================================================

def _start_question(caller: Caller) -> str:
    """Who is asking; refuses with 429 once their daily question limit is used."""
    try:
        get_gateway().check_user_quota(caller.user_id)
    except UsageLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return caller.user_id


def _own_chat(thread_id: str, caller: Caller) -> None:
    """A chat belongs to whoever asked in it first; others get 'not found'."""
    if caller.account_id is not None and not get_user_store().claim_chat(thread_id, caller.account_id):
        raise HTTPException(status_code=404, detail="Chat not found.")


@app.post("/query", response_model=QueryResponse)
def query_database(
    request: QueryRequest,
    caller: Caller = Depends(current_caller),
) -> QueryResponse:

    user_id = _start_question(caller)

    # --------------------------------------------------------
    # Get correct user's database session
    # --------------------------------------------------------

    connection_session = (
        get_connection_session(
            request.connection_id,
            caller,
        )
    )

    engine = connection_session[
        "engine"
    ]

    # --------------------------------------------------------
    # Create thread if frontend did not provide one
    # --------------------------------------------------------

    thread_id = (
        request.thread_id
        or create_thread_id()
    )
    _own_chat(thread_id, caller)

    # --------------------------------------------------------
    # Run LangGraph Text-to-SQL pipeline
    # --------------------------------------------------------

    # Everything this question does is timed and saved as its trace.
    with telemetry.collect() as stats:
        try:
            with ai_request(user_id=user_id), (
                nullcontext() if request.use_cache else question_cache.fresh_answer()
            ):
                result = run_text_to_sql(
                    question=request.question,
                    thread_id=thread_id,
                    engine=engine,
                )

            chart_data = {}
            if str(result.get("status") or "").upper() == "SUCCESS":
                started = time.perf_counter()
                try:
                    with dialects.using(engine):
                        chart_data = chart_context(
                            result.get("generated_sql") or "",
                            result.get("execution_result") or {},
                            engine,
                        )
                except Exception as error:
                    print(f"Chart context skipped: {type(error).__name__}: {error}")
                telemetry.record_stage("chart", time.perf_counter() - started)

            follow_ups = []
            if str(result.get("status") or "").upper() == "SUCCESS":
                started = time.perf_counter()
                try:
                    with ai_request(user_id=user_id):
                        follow_ups = suggest_follow_ups(request.question, result)
                except Exception as error:
                    print(f"Follow-up questions skipped: {type(error).__name__}: {error}")
                telemetry.record_stage("follow_ups", time.perf_counter() - started)

        except Exception as error:
            tracing.save(stats, user_id, request.question, result=None, error=f"{type(error).__name__}: {error}")
            return _query_failure(error, request, thread_id)

    trace = tracing.save(stats, user_id, request.question, result=result)
    history.record(
        _chat_list_owner(caller), "question", request.question,
        str(result.get("status") or "ERROR").upper(),
        answer=str(result.get("answer") or result.get("clarification") or result.get("error") or ""),
        sql=str(result.get("generated_sql") or ""),
        database=_database_label(connection_session), thread_id=thread_id,
    )

    # Agent-stage failures are returned as status="ERROR" with
    # error/error_stage; only infrastructure failures (schema
    # loading, checkpoint store) reach _query_failure.
    return build_query_response(
        result=result,
        connection_id=request.connection_id,
        thread_id=thread_id,
        chart_data=chart_data,
        trace=trace,
        follow_ups=follow_ups,
    )


def _query_failure(error: Exception, request: "QueryRequest", thread_id: str):
    if isinstance(error, AIUnavailableError):
        raise HTTPException(status_code=503, detail=str(error)) from error

    # Keep the real backend traceback visible in the FastAPI
    # terminal instead of hiding the root cause behind a generic 500.
    import traceback

    print("\n" + "=" * 70)
    print("INSIGHTX /query FAILED")
    print("=" * 70)
    print(f"connection_id: {request.connection_id}")
    print(f"thread_id: {thread_id}")
    print(f"question: {request.question}")
    print(f"error_type: {type(error).__name__}")
    print(f"error: {error}")
    traceback.print_exc()
    print("=" * 70 + "\n")

    raise HTTPException(
        status_code=500,
        detail=f"Text-to-SQL pipeline failed: {type(error).__name__}: {error}",
    )


# ============================================================
# INVESTIGATION MODE ("why did the metric change?")
# ============================================================

class InvestigateRequest(BaseModel):
    connection_id: str
    question: str = Field(min_length=1, max_length=4000)
    # The chat this was asked in: the question and its answer join that
    # chat's conversation.
    thread_id: Optional[str] = None


@app.post("/investigate")
def investigate(request: InvestigateRequest, caller: Caller = Depends(current_caller)):
    """
    Root-cause analysis of a metric change: verified plan, deterministic
    SQL analysis (trend, drivers, drill-down, related signals), and an
    explanation whose numbers are checked against the computed facts.
    """
    user_id = _start_question(caller)
    engine = get_connection_session(request.connection_id, caller)["engine"]
    if request.thread_id:
        _own_chat(request.thread_id, caller)
    with telemetry.collect() as stats:
        try:
            with ai_request(user_id=user_id):
                report = run_investigation(request.question, engine)
        except Exception as exc:
            tracing.save(stats, user_id, request.question, kind="investigation",
                         error=f"{type(exc).__name__}: {exc}")
            if isinstance(exc, AIUnavailableError):
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if isinstance(exc, InvestigationError):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            logger.exception("Investigation failed")
            raise HTTPException(status_code=500, detail=f"Investigation failed: {_first_line(exc)}") from exc
    report["trace"] = tracing.save(stats, user_id, request.question, kind="investigation",
                                   result={"status": report.get("status")}) or {}
    summary = history.investigation_summary(report)
    history.record(_chat_list_owner(caller), "investigation", request.question, str(report.get("status") or ""),
                   answer=summary["answer"], database=_database_label(get_connection_session(request.connection_id, caller)),
                   thread_id=request.thread_id or "", detail=summary["detail"])
    if request.thread_id:
        messages = [{"role": "user", "content": request.question.strip()},
                    {"role": "assistant", "content": "🔎 " + (summary["answer"] or "Investigation finished.")}]
        try:
            add_to_thread(engine, request.thread_id, messages)
            report["conversation_history"] = jsonable_encoder(
                get_thread_state(engine, request.thread_id).get("conversation_history") or [])
        except Exception:
            logger.exception("Could not add the investigation to chat %s", request.thread_id)
    return jsonable_encoder(report)


# ============================================================
# TRACES (every step of a question, with timings)
# ============================================================

@app.get("/traces")
def list_traces(limit: int = 50, caller: Caller = Depends(current_caller)):
    """Your most recent questions with their timing summary."""
    return {"traces": jsonable_encoder(tracing.get_store().recent(caller.user_id, limit))}


@app.get("/traces/{trace_id}")
def get_trace(trace_id: str, caller: Caller = Depends(current_caller)):
    """One trace with its full timeline. Only your own traces are visible."""
    trace = tracing.get_store().get(caller.user_id, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return jsonable_encoder(trace)


# ============================================================
# AI USAGE (gateway status, usage ledger, provider credits)
# ============================================================

@app.get("/ai/usage")
def ai_usage(days: int = 30, user: Optional[str] = None, caller: Caller = Depends(current_caller)):
    """
    Usage summary from the ledger: requests, failures, cache, tokens, cost,
    per day / model / step. With sign-in on, everyone sees only their own.
    """
    days = max(1, min(int(days), 365))
    if auth_required():
        user = caller.user_id
    return usage_summary(days=days, user_id=user or None)


@app.get("/ai/routes")
def ai_routes(caller: Caller = Depends(current_caller)):
    """Every model route: tier, order, free limits, today's count, cooldown, key configured."""
    gateway = get_gateway()
    gateway._seed_from_store()
    return {
        "routes": gateway.status(),
        "settings": {
            "user_daily_questions": gateway.settings.user_daily_questions,
            "cache_enabled": gateway.settings.cache_enabled,
            "cache_ttl_hours": gateway.settings.cache_ttl_s / 3600,
        },
        "openrouter": openrouter_key_status(),
    }


@app.get("/investigate/route")
def investigation_route(question: str, caller: Caller = Depends(current_caller)):
    """Whether a question looks like 'why did X change?' (UI routing hint)."""
    return {"investigation": looks_like_investigation(question)}


# ============================================================
# CHAT LIST
# ============================================================

def _chat_list_owner(caller: Caller) -> str:
    # With sign-in off everyone shares one list, as before.
    return f"account:{caller.account_id}" if caller.account_id is not None else "local"


class ChatListBody(BaseModel):
    chats: List[Dict[str, Any]] = Field(default_factory=list)


@app.get("/chats")
def get_chats(caller: Caller = Depends(current_caller)):
    """This person's list of chats (titles and dates)."""
    return {"chats": chat_list.get_store().get(_chat_list_owner(caller))}


@app.put("/chats")
def put_chats(body: ChatListBody, caller: Caller = Depends(current_caller)):
    """Replace this person's list of chats."""
    return {"chats": chat_list.get_store().put(_chat_list_owner(caller), body.chats)}


# ============================================================
# QUESTION HISTORY (every question, every chat)
# ============================================================

def _database_label(session: Dict[str, Any]) -> str:
    database = str(session.get("database") or "")
    if str(session.get("source_type") or "") == "sqlite":
        database = Path(database).name
    kind = str(session.get("source_type") or "")
    return f"{database} ({dialects.label(kind)})" if dialects.is_known(kind) else database


@app.get("/history")
def get_history(search: str = "", kind: str = "", limit: int = 50, offset: int = 0,
                caller: Caller = Depends(current_caller)):
    """Every question this person asked, newest first, with its answer."""
    return jsonable_encoder(history.get_store().list(_chat_list_owner(caller), search, kind, limit, offset))


@app.delete("/history/{entry_id}")
def delete_history_entry(entry_id: str, caller: Caller = Depends(current_caller)):
    if not history.get_store().delete(_chat_list_owner(caller), entry_id):
        raise HTTPException(status_code=404, detail="History entry not found.")
    return {"deleted": 1}


@app.delete("/history")
def clear_history(caller: Caller = Depends(current_caller)):
    return {"deleted": history.get_store().clear(_chat_list_owner(caller))}


# ============================================================
# CHAT HISTORY
# ============================================================

@app.get("/threads/{thread_id}/history")
def thread_history(
    thread_id: str,
    connection_id: str,
    caller: Caller = Depends(current_caller),
):
    """
    Persisted conversation of one chat (thread) in the connected
    database's checkpoint store. The UI uses this to restore a chat
    without opening its own database connection.
    """
    engine = get_connection_session(connection_id, caller)["engine"]
    if caller.account_id is not None and get_user_store().chat_owner(thread_id) not in (None, caller.account_id):
        raise HTTPException(status_code=404, detail="Chat not found.")

    try:
        state = get_thread_state(engine, thread_id)
    except Exception as exc:
        logger.exception("Loading history for thread %s failed", thread_id)
        raise HTTPException(
            status_code=500,
            detail=f"Could not load chat history: {_first_line(exc)}",
        ) from exc

    return {
        "thread_id": thread_id,
        "conversation_history": jsonable_encoder(
            state.get("conversation_history") or []
        ),
    }


# ============================================================
# DISCONNECT DATABASE
# ============================================================

@app.delete(
    "/disconnect-postgres/{connection_id}"
)
def disconnect_postgres(
    connection_id: str,
    caller: Caller = Depends(current_caller),
):

    get_connection_session(connection_id, caller)  # 404 unless it is the caller's
    session = POSTGRES_SESSIONS.pop(
        connection_id,
        None,
    )
    if connections.enabled():
        try:
            connections.get_store().forget(connection_id)
        except Exception as exc:
            logger.warning("Saved connection %s not removed: %s", connection_id, _first_line(exc))

    if not session:

        raise HTTPException(
            status_code=404,
            detail="Connection session not found.",
        )

    engine = session.get(
        "engine"
    )

    try:

        if engine is not None:
            engine.dispose()

    except Exception:
        pass

    # An uploaded SQLite file is deleted with its connection.
    if session.get("source_type") == dialects.SQLITE:
        try:
            Path(session["database"]).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete SQLite file %s", session["database"])

    return {
        "status": "success",
        "message": (
            "Database connection closed."
        ),
    }

# Dashboard page (backend/dashboard.py).
from backend import dashboard  # noqa: E402

app.include_router(dashboard.router)

# Alerts page (backend/alerting/): its endpoints and the background checker.
from backend.alerting import api as alerting_api  # noqa: E402
from backend.alerting import service as alerting_service  # noqa: E402
app.include_router(alerting_api.router)
alerting_service.start_scheduler()

# LLMOps pages (backend/llmops/): monitor, evals, hallucination checks, feedback, smart routing.
from backend.llmops import api as llmops_api  # noqa: E402
app.include_router(llmops_api.router)
llmops_api.install(app)

# "Try the demo database" for the online app (backend/demo_database.py).
from backend import demo_database  # noqa: E402
app.include_router(demo_database.router)
