# backend/llmops/capture.py
#
# Watches answered questions without touching the answering code.
#
# A plain ASGI middleware sits in front of the app. For POST /query it
#   1. reads the question from the request body (and hands the same
#      bytes on to the app unchanged),
#   2. marks how hard the question looks, for smart routing,
#   3. copies the JSON response as it goes out (the response itself is
#      passed through untouched),
#   4. after the response is sent, runs the hallucination checks and
#      saves one "answer check" row in a background thread.
# Any error in here is logged and swallowed: watching must never break
# answering.

import contextvars
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from backend.llmops import difficulty, hallucination
from backend.llmops.store import get_store

logger = logging.getLogger("insightx.llmops")

WATCHED = {"/query"}
MAX_CAPTURE_BYTES = 8 * 1024 * 1024
PREVIEW_ROWS = int(os.getenv("INSIGHTX_LLMOPS_KEEP_ROWS", "20"))

# The difficulty of the question being answered (read by smart routing).
current_difficulty: contextvars.ContextVar[str] = contextvars.ContextVar("insightx_llmops_difficulty", default="")
# Fallback for calls made in threads that did not copy the context.
_recent: Dict[str, Any] = {}
_recent_lock = threading.Lock()


def remember(user_id: str, level: str) -> None:
    with _recent_lock:
        _recent[user_id] = (level, time.time())
        if len(_recent) > 5000:
            oldest = sorted(_recent.items(), key=lambda item: item[1][1])[:1000]
            for key, _ in oldest:
                _recent.pop(key, None)


def difficulty_now(user_id: Optional[str] = None) -> str:
    level = current_difficulty.get()
    if level:
        return level
    if user_id:
        with _recent_lock:
            item = _recent.get(user_id)
        if item and time.time() - item[1] < 600:
            return item[0]
    return ""


def _header(scope: Dict[str, Any], name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def resolve_user(scope: Dict[str, Any]) -> Optional[str]:
    """The same user id the app's current_caller gives, or None."""
    try:
        from backend.ai_gateway.context import clean_user_id
        from backend.auth.deps import auth_required, bearer_token

        if not auth_required():
            return clean_user_id(_header(scope, b"x-insightx-user"))
        from backend.auth.service import user_for_token

        user = user_for_token(bearer_token(_header(scope, b"authorization")))
        return user["email"] if user else None
    except Exception:
        logger.debug("LLMOps could not resolve the user", exc_info=True)
        return None


def build_row(user_id: str, request: Dict[str, Any], response: Dict[str, Any], level: str,
              seconds: float) -> Dict[str, Any]:
    """One answer-check row from a /query request and its response."""
    checks = hallucination.check_answer(response)
    result = response.get("execution_result") or {}
    rows = list(result.get("rows") or [])
    trust = response.get("trust") or {}
    trace = response.get("trace") or {}
    trust_score = trust.get("score")
    return {
        "user_id": user_id,
        "trace_id": str(trace.get("id") or "")[:32],
        "thread_id": str(response.get("thread_id") or request.get("thread_id") or "")[:100],
        "question": str(response.get("question") or request.get("question") or "")[:4000],
        "status": str(response.get("status") or "").upper()[:30] or "UNKNOWN",
        "sql": str(response.get("sql") or response.get("corrected_sql") or response.get("generated_sql") or ""),
        "answer": str(response.get("answer") or response.get("clarification") or response.get("error") or ""),
        "row_count": result.get("row_count") if result.get("row_count") is not None else (len(rows) if rows else None),
        "seconds": float(trace.get("seconds") or seconds),
        "trust_level": str(trust.get("level") or "")[:10],
        "trust_score": int(trust_score) if isinstance(trust_score, (int, float)) else None,
        "difficulty": level,
        "faithfulness": checks.get("score"),
        "risk": checks.get("risk") or "none",
        "hallucinated": 1 if checks.get("hallucinated") else 0,
        "checks": checks,
        "preview": {"columns": list(result.get("columns") or []), "rows": rows[:max(PREVIEW_ROWS, 0)]},
    }


def _save(user_id: str, request: Dict[str, Any], body: bytes, level: str, seconds: float) -> None:
    try:
        response = json.loads(body.decode("utf-8"))
        if not isinstance(response, dict):
            return
        get_store().add_answer(build_row(user_id, request, response, level, seconds))
    except Exception:
        logger.warning("LLMOps could not save an answer check", exc_info=True)


class CaptureMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") not in WATCHED:
            await self.app(scope, receive, send)
            return

        # Read the whole request body, then replay it to the app.
        chunks = []
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                # client went away before sending the body
                await self.app(scope, _replay([message]), send)
                return
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        raw = b"".join(chunks)

        try:
            request = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(request, dict):
                request = {}
        except ValueError:
            request = {}
        level = difficulty.level(str(request.get("question") or "")) if request.get("question") else ""
        user_id = resolve_user(scope) if request else None
        if user_id and level:
            remember(user_id, level)
        token = current_difficulty.set(level)
        try:
            from backend.llmops import routing

            routing.sync()  # wraps the gateway only when smart routing is on or in shadow mode
        except Exception:
            logger.debug("LLMOps routing sync failed", exc_info=True)

        state = {"status": 0, "json": False, "body": [], "size": 0}
        started = time.time()

        async def watch(message):
            try:
                if message["type"] == "http.response.start":
                    state["status"] = message.get("status", 0)
                    content_type = ""
                    for key, value in message.get("headers") or []:
                        if key.lower() == b"content-type":
                            content_type = value.decode("latin-1")
                    state["json"] = "application/json" in content_type
                elif message["type"] == "http.response.body" and state["status"] == 200 and state["json"]:
                    piece = message.get("body", b"")
                    state["size"] += len(piece)
                    if state["size"] <= MAX_CAPTURE_BYTES:
                        state["body"].append(piece)
            except Exception:
                logger.debug("LLMOps capture failed", exc_info=True)
            await send(message)

        try:
            await self.app(scope, _replay([{"type": "http.request", "body": raw, "more_body": False}], receive),
                           watch)
        finally:
            current_difficulty.reset(token)

        if user_id and state["status"] == 200 and state["json"] and state["size"] <= MAX_CAPTURE_BYTES:
            threading.Thread(
                target=_save, args=(user_id, request, b"".join(state["body"]), level, time.time() - started),
                name="llmops-capture", daemon=True,
            ).start()


def _replay(messages, receive=None):
    queue = list(messages)

    async def replayed():
        if queue:
            return queue.pop(0)
        if receive is not None:
            return await receive()
        return {"type": "http.disconnect"}

    return replayed
