# backend/text_to_sql/question_cache.py
#
# Question cache: answer a question that was already answered well,
# without asking the AI again.
#
# After a trusted answer, the question, its SQL and its answer are kept
# per person and per database. When the same person asks the same
# question again (or the same question in other words: different word
# case, punctuation, plurals, filler words, "highest" for "most") on the same
# database, the stored SQL is reused. The SQL still runs again through
# the safety gate, validation and the read-only executor, so the answer
# always reflects the current data:
#   - same rows as last time -> the stored answer is shown (no AI call)
#   - different rows         -> only the short answer step uses the AI
#
# Matching is deliberately strict. "Similar" means the same words after
# normalising, never "close in meaning": "top 5" vs "top 10", "2017" vs
# "2018", "city" vs "state" or "delivered" vs "canceled" never match.
#
# Defaults to a SQLite file in logs/; INSIGHTX_QUESTION_CACHE_URL can
# point at PostgreSQL for a deployment with several backend servers.
# INSIGHTX_QUESTION_CACHE=0 turns the cache off.

import contextvars
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import Column, Float, Index, Integer, MetaData, String, Table, Text, delete, insert, select, update
from sqlalchemy.engine import Engine

from backend import app_db

BASE_DIR = Path(__file__).resolve().parents[2]

# Entries older than this are not reused (the schema or the meaning of
# the data may have moved on); 30 days by default.
MAX_AGE_SECONDS = float(os.getenv("INSIGHTX_QUESTION_CACHE_DAYS", "30")) * 86400
# Only answers at least this trusted are stored.
MIN_TRUST_SCORE = int(os.getenv("INSIGHTX_QUESTION_CACHE_MIN_TRUST", "80"))
# Kept per person and database; the oldest unused entries go first.
MAX_ENTRIES = int(os.getenv("INSIGHTX_QUESTION_CACHE_MAX", "500"))

metadata = MetaData()

questions = Table(
    "insightx_question_cache", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(120), nullable=False),
    Column("database_key", String(64), nullable=False),
    Column("schema_key", String(64), nullable=False),
    Column("exact_key", String(64), nullable=False),
    Column("similar_key", String(64), nullable=False),
    Column("question", Text, nullable=False),
    Column("sql", Text, nullable=False),
    Column("answer", Text, nullable=False),
    Column("result_key", String(64), nullable=False),
    Column("trust", Text, nullable=False),
    Column("created", Float, nullable=False),
    Column("last_used", Float, nullable=False),
    Column("hits", Integer, nullable=False, default=0),
    Index("ix_insightx_question_cache_lookup", "user_id", "database_key", "similar_key"),
)


# Set for one question when the person asks for a fresh answer: the
# cache is not read, but the new answer is still stored.
_skip_lookup = contextvars.ContextVar("insightx_skip_question_cache", default=False)


def enabled() -> bool:
    return os.getenv("INSIGHTX_QUESTION_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")


def lookups_enabled() -> bool:
    return enabled() and not _skip_lookup.get()


class fresh_answer:
    """Context manager: answer this question without reading the cache."""

    def __enter__(self):
        self._token = _skip_lookup.set(True)
        return self

    def __exit__(self, *exc):
        _skip_lookup.reset(self._token)
        return False


# ------------------------------------------------------------
# Normalising questions
# ------------------------------------------------------------

# Words that do not change what is asked.
FILLER = set("""
a an the of for in on at to from with by per each every and or is are was were be been being do does did
what which who whom whose when where how please show me tell give list find get display i we you my our
can could would will shall should that this these those there their it its as all any some
much many number amount has have had having
""".split())
# Words that do: kept even though they look like filler.
KEEP = {"not", "no", "without", "only", "before", "after", "between", "above", "below", "over", "under",
        "first", "last", "next", "previous", "same", "different", "per", "each", "by"}
FILLER -= KEEP
# Words with the same meaning in a question about data.
SYNONYMS = {
    "highest": "most", "maximum": "most", "max": "most", "largest": "most", "biggest": "most",
    "greatest": "most", "top": "most", "best": "most",
    "lowest": "least", "minimum": "least", "min": "least", "smallest": "least", "fewest": "least",
    "bottom": "least", "worst": "least",
    "average": "avg", "mean": "avg",
    "sum": "total", "overall": "total",
    "count": "count", "counts": "count",
    "customers": "customer", "clients": "customer", "client": "customer",
    "monthly": "month", "yearly": "year", "annual": "year", "annually": "year", "daily": "day",
    "weekly": "week", "quarterly": "quarter",
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _stem(word: str) -> str:
    if word.isdigit() or len(word) <= 3:
        return word
    for suffix, keep in (("ies", "y"), ("sses", "ss"), ("ses", "s"), ("s", "")):
        if word.endswith(suffix) and not word.endswith("ss"):
            return word[: -len(suffix)] + keep
    return word


def exact_text(question: str) -> str:
    """Lower case, accents and punctuation removed, spaces collapsed."""
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", _fold(question))
    return " ".join(words)


def similar_text(question: str) -> str:
    """
    The question's meaning-bearing words, normalised, in their order.
    Order is kept: "orders from Delhi to Mumbai" must not match
    "orders from Mumbai to Delhi".
    """
    words = []
    for word in exact_text(question).split():
        if word in FILLER:
            continue
        word = SYNONYMS.get(word, word)
        word = SYNONYMS.get(_stem(word), _stem(word))
        words.append(word)
    # "how many" asks for a count ("highest number of X" is just "most X").
    if re.search(r"\bhow many\b|\bnumber of\b", _fold(question)) and not {"most", "least"} & set(words):
        words.insert(0, "count")
    deduped = [word for index, word in enumerate(words) if index == 0 or word != words[index - 1]]
    return " ".join(deduped)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schema_key(schema: Dict[str, Any]) -> str:
    """Changes when a table or column is added, removed or renamed."""
    parts = []
    for table in (schema or {}).get("tables") or []:
        columns = sorted(
            f"{c.get('column_name') or c.get('name')}:{c.get('data_type', '')}" for c in table.get("columns") or []
        )
        parts.append(f"{table.get('table_name')}({','.join(columns)})")
    return _hash("|".join(sorted(parts)))


def database_key(engine) -> str:
    """The database, without its password."""
    return _hash(engine.url.render_as_string(hide_password=True))


def result_key(execution_result: Dict[str, Any]) -> str:
    rows = (execution_result or {}).get("rows") or []
    payload = json.dumps(
        {"columns": (execution_result or {}).get("columns") or [], "rows": rows,
         "row_count": (execution_result or {}).get("row_count")},
        sort_keys=True, default=str,
    )
    return _hash(payload)


# ------------------------------------------------------------
# Store
# ------------------------------------------------------------

class QuestionCache:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_QUESTION_CACHE_URL", "question_cache.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    def lookup(self, user_id: str, database: str, schema: str, question: str) -> Optional[Dict[str, Any]]:
        """The best stored entry for this question, or None."""
        exact, similar = exact_text(question), similar_text(question)
        if not similar:
            return None
        oldest = time.time() - MAX_AGE_SECONDS
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(questions).where(
                    questions.c.user_id == user_id,
                    questions.c.database_key == database,
                    questions.c.schema_key == schema,
                    questions.c.similar_key == _hash(similar),
                    questions.c.created >= oldest,
                ).order_by(questions.c.created.desc())
            ).all()
        if not rows:
            return None
        exact_rows = [row for row in rows if row.exact_key == _hash(exact)]
        row = (exact_rows or rows)[0]
        entry = dict(row._mapping)
        entry["match"] = "same" if exact_rows else "similar"
        entry["trust"] = json.loads(entry["trust"] or "{}")
        with self._lock, self.engine.begin() as connection:
            connection.execute(
                update(questions).where(questions.c.id == row.id)
                .values(hits=questions.c.hits + 1, last_used=time.time())
            )
        return entry

    def store(self, user_id: str, database: str, schema: str, question: str, sql: str, answer: str,
              execution_result: Dict[str, Any], trust: Dict[str, Any]) -> None:
        exact, similar = exact_text(question), similar_text(question)
        if not similar or not sql.strip() or not answer.strip():
            return
        now = time.time()
        with self._lock, self.engine.begin() as connection:
            # One entry per wording: a newer answer replaces the older one.
            connection.execute(delete(questions).where(
                questions.c.user_id == user_id, questions.c.database_key == database,
                questions.c.exact_key == _hash(exact),
            ))
            connection.execute(insert(questions).values(
                user_id=user_id, database_key=database, schema_key=schema,
                exact_key=_hash(exact), similar_key=_hash(similar), question=question.strip()[:2000],
                sql=sql, answer=answer, result_key=result_key(execution_result),
                trust=json.dumps(trust or {}, default=str), created=now, last_used=now, hits=0,
            ))
            ids = connection.execute(
                select(questions.c.id).where(questions.c.user_id == user_id, questions.c.database_key == database)
                .order_by(questions.c.last_used.desc()).offset(MAX_ENTRIES)
            ).scalars().all()
            if ids:
                connection.execute(delete(questions).where(questions.c.id.in_(ids)))

    def forget(self, user_id: str, database: Optional[str] = None, question: Optional[str] = None) -> int:
        """Remove a person's entries (all, one database's, or one question's)."""
        condition = [questions.c.user_id == user_id]
        if database:
            condition.append(questions.c.database_key == database)
        if question:
            condition.append(questions.c.similar_key == _hash(similar_text(question)))
        with self._lock, self.engine.begin() as connection:
            return connection.execute(delete(questions).where(*condition)).rowcount or 0

    def entries(self, user_id: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(questions.c.question, questions.c.hits, questions.c.created, questions.c.last_used)
                .where(questions.c.user_id == user_id).order_by(questions.c.last_used.desc())
            ).all()
        return [dict(row._mapping) for row in rows]


_cache: Optional[QuestionCache] = None
_cache_lock = threading.Lock()


def get_cache() -> QuestionCache:
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = QuestionCache()
        return _cache


def set_cache(cache: Optional[QuestionCache]) -> None:
    """Tests use this to swap in a temporary cache."""
    global _cache
    with _cache_lock:
        _cache = cache


def first_question_in_thread(history: Iterable[Dict[str, Any]]) -> bool:
    """True when the only message so far is the current question."""
    return sum(1 for message in history or [] if message.get("role") in ("user", "assistant")) <= 1
