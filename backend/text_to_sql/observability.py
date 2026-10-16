import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------
# LOG DIRECTORY
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "text_to_sql.jsonl"


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def generate_query_id() -> str:
    """Generate a unique ID for every Text-to-SQL request."""
    return str(uuid.uuid4())


def get_timestamp() -> str:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _safe_value(value: Any) -> Any:
    """
    Convert common non-JSON-serializable values into
    JSON-safe representations.
    """

    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): _safe_value(val)
            for key, val in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _safe_value(item)
            for item in value
        ]

    return str(value)


# ---------------------------------------------------------
# QUERY TRACE
# ---------------------------------------------------------

class QueryTrace:
    """
    Stores observability information for one complete
    Text-to-SQL request.
    """

    def __init__(
        self,
        question: str,
        query_id: Optional[str] = None,
    ):
        self.query_id = query_id or generate_query_id()
        self.start_time = time.perf_counter()

        self.data = {
            "query_id": self.query_id,
            "timestamp": get_timestamp(),
            "question": question,

            "query_rewriting": None,
            "query_decomposition": None,
            "retrieval": None,
            "graph_rag": None,
            "schema_linking": None,
            "sql_generation": None,
            "sql_validation": None,
            "sql_safety": None,
            "sql_correction": None,
            "query_execution": None,
            "result_validation": None,
            "answer_generation": None,

            "errors": [],
            "metadata": {},
        }

    # -----------------------------------------------------
    # STAGE RECORDING
    # -----------------------------------------------------

    def record_stage(
        self,
        stage_name: str,
        data: Dict[str, Any],
    ) -> None:
        """
        Store output/details for one pipeline stage.
        """

        self.data[stage_name] = _safe_value(data)

    # -----------------------------------------------------
    # ERROR RECORDING
    # -----------------------------------------------------

    def record_error(
        self,
        stage: str,
        error: str,
    ) -> None:

        self.data["errors"].append(
            {
                "stage": stage,
                "error": str(error),
                "timestamp": get_timestamp(),
            }
        )

    # -----------------------------------------------------
    # METADATA
    # -----------------------------------------------------

    def add_metadata(
        self,
        key: str,
        value: Any,
    ) -> None:

        self.data["metadata"][key] = _safe_value(value)

    # -----------------------------------------------------
    # FINALIZE
    # -----------------------------------------------------

    def finalize(self) -> Dict[str, Any]:
        """
        Finalize trace and calculate total pipeline latency.
        """

        end_time = time.perf_counter()

        total_time_ms = round(
            (end_time - self.start_time) * 1000,
            2,
        )

        self.data["total_execution_time_ms"] = total_time_ms

        return self.data


# ---------------------------------------------------------
# FILE LOGGER
# ---------------------------------------------------------

def log_query_trace(
    trace: QueryTrace,
) -> Dict[str, Any]:
    """
    Finalize and append one trace to the JSONL log file.
    """

    data = trace.finalize()

    try:
        with LOG_FILE.open(
            "a",
            encoding="utf-8",
        ) as file:

            file.write(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

        return {
            "success": True,
            "query_id": data["query_id"],
            "log_file": str(LOG_FILE),
            "error": None,
        }

    except Exception as exc:

        return {
            "success": False,
            "query_id": data["query_id"],
            "log_file": str(LOG_FILE),
            "error": str(exc),
        }


# ---------------------------------------------------------
# SIMPLE EVENT LOGGER
# ---------------------------------------------------------

def log_event(
    event_name: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Log an individual system event.
    """

    event = {
        "event": event_name,
        "timestamp": get_timestamp(),
        "data": _safe_value(data or {}),
    }

    try:
        with LOG_FILE.open(
            "a",
            encoding="utf-8",
        ) as file:

            file.write(
                json.dumps(
                    event,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

        return {
            "success": True,
            "error": None,
        }

    except Exception as exc:

        return {
            "success": False,
            "error": str(exc),
        }