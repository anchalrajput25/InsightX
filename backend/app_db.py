# backend/app_db.py
#
# Where InsightX keeps its own data: accounts, AI usage, saved
# answers, traces and saved connections.
#
# Locally each store is a SQLite file in logs/. When the app is hosted,
# set INSIGHTX_APP_DB_URL to one PostgreSQL database (for example a free
# Neon database) and every store uses it, so nothing is lost when the
# server restarts and several servers can share the same data. A
# store's own variable (e.g. INSIGHTX_AUTH_DB_URL) still wins.

import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

BASE_DIR = Path(__file__).resolve().parents[1]


def store_url(url: Optional[str], env_name: str, filename: str) -> str:
    """The database URL for one store."""
    url = url or os.getenv(env_name) or os.getenv("INSIGHTX_APP_DB_URL")
    if not url:
        return f"sqlite:///{(BASE_DIR / 'logs' / filename).as_posix()}"
    url = url.strip()
    # Hosting sites often hand out postgres:// URLs.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


def store_engine(url: Optional[str], env_name: str, filename: str) -> Engine:
    url = store_url(url, env_name, filename)
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})
    return create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2, pool_recycle=1800)
