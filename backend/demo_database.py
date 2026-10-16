# backend/demo_database.py
#
# "Try the demo database": one click connects the demo warehouse that the
# server owner set up, so visitors of the online app can ask questions
# without bringing their own data.
#
#   INSIGHTX_DEMO_DATABASE_URL    postgresql://user:password@host/db
#                                 (use a read-only user; see
#                                 scripts/load_demo_db.py)
#   INSIGHTX_DEMO_DATABASE_LABEL  name shown in the app (default
#                                 "Demo warehouse")
#
# The URL and its password stay on the server and are never sent to the
# browser. Without the variable, /demo-database says it is not available
# and the app shows nothing new. The connection itself goes through the
# normal /connect-postgres code, so it gets the same read-only checks.

import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import make_url

from backend.auth.deps import Caller, current_caller

router = APIRouter(tags=["demo"])


def demo_url() -> str:
    return (os.getenv("INSIGHTX_DEMO_DATABASE_URL") or "").strip()


def demo_label() -> str:
    return (os.getenv("INSIGHTX_DEMO_DATABASE_LABEL") or "Demo warehouse").strip()


@router.get("/demo-database")
def demo_database_info(caller: Caller = Depends(current_caller)) -> Dict[str, Any]:
    return {"available": bool(demo_url()), "label": demo_label()}


@router.post("/connect-demo")
def connect_demo(caller: Caller = Depends(current_caller)) -> Dict[str, Any]:
    url = demo_url()
    if not url:
        raise HTTPException(status_code=404, detail="No demo database is set up on this server.")
    try:
        parsed = make_url(url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="The demo database address on the server is not valid.") from exc

    from backend.main import connect_postgres  # here, to avoid a circular import

    result = connect_postgres(
        host=parsed.host or "",
        port=parsed.port,
        database=parsed.database or "",
        username=parsed.username or "",
        password=parsed.password or "",
        db_type="postgresql",
        caller=caller,
    )
    result["label"] = demo_label()
    return result
