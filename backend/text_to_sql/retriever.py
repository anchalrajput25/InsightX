# backend/text_to_sql/retriever.py
#
# Schema retrieval that scales to large databases.
#
# Every COLUMN is a search document (table words + column words +
# type hints + description), indexed two ways:
#
#   lexical  - BM25 over tokens (camelCase/snake_case split, stopwords
#              removed, light stemming, so "customers" matches
#              customer_id and "shipped" matches shipped_date, but
#              "count" no longer matches "country" by substring)
#   semantic - sentence embeddings (cached per document text)
#
# A table's relevance combines its best-matching column with a
# table-level document (table name + all column names). Several
# phrasings of the question are fused with Reciprocal Rank Fusion.
#
# Nothing here knows any particular database: everything is derived
# from the schema that was profiled at connect/import time.

import copy
import hashlib
import json
import logging
import math
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger("insightx.retriever")


# ---------------------------------------------------------
# Embedding model
# ---------------------------------------------------------

_embedding_model = None
_embedding_failed = False
_MODEL_LOCK = threading.Lock()


def get_embedding_model():
    """Load the embedding model only when required."""
    global _embedding_model
    if _embedding_model is None:
        with _MODEL_LOCK:
            if _embedding_model is None:
                from sentence_transformers import SentenceTransformer
                _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model


# ---------------------------------------------------------
# Document embedding cache
# ---------------------------------------------------------
#
# Schema documents only change when the schema changes, but every
# question (and every query variant) needs their embeddings. Vectors
# are cached by the hash of the document text, so unchanged columns
# are never re-encoded and a changed one is re-encoded automatically.

_DOCUMENT_EMBEDDINGS: Dict[str, np.ndarray] = {}
_EMBEDDING_LOCK = threading.Lock()
MAX_CACHED_EMBEDDINGS = 200_000


def _text_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def encode_documents(texts: Sequence[str]) -> np.ndarray:
    keys = [_text_key(text) for text in texts]

    with _EMBEDDING_LOCK:
        missing = [
            (key, text)
            for key, text in dict(zip(keys, texts)).items()
            if key not in _DOCUMENT_EMBEDDINGS
        ]

    if missing:
        vectors = get_embedding_model().encode([text for _, text in missing])
        with _EMBEDDING_LOCK:
            if len(_DOCUMENT_EMBEDDINGS) + len(missing) > MAX_CACHED_EMBEDDINGS:
                _DOCUMENT_EMBEDDINGS.clear()
            for (key, _), vector in zip(missing, vectors):
                _DOCUMENT_EMBEDDINGS[key] = np.asarray(vector)

    with _EMBEDDING_LOCK:
        return np.vstack([_DOCUMENT_EMBEDDINGS[key] for key in keys])


def _cosine(query_vector: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_norm = np.linalg.norm(query_vector) or 1.0
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    return (matrix @ query_vector) / (norms * query_norm)


def semantic_similarities(query: str, texts: Sequence[str]) -> Optional[np.ndarray]:
    """
    Cosine similarity of the query to each text, or None when the
    embedding model is unavailable (retrieval then runs lexically
    instead of failing the whole question).
    """
    global _embedding_failed
    if not texts or _embedding_failed:
        return None
    try:
        documents = encode_documents(list(texts))
        query_vector = np.asarray(get_embedding_model().encode([query]))[0]
    except Exception as exc:
        _embedding_failed = True
        logger.warning("Embedding model unavailable; using lexical retrieval only: %s", exc)
        return None
    return _cosine(query_vector, documents)


# ---------------------------------------------------------
# Tokenization
# ---------------------------------------------------------

STOPWORDS = frozenset("""
a an the and or but if then else of to in on at by for from with without into onto
over under about as is are was were be been being am do does did done have has had
having i me my we our you your he she it its they them their this that these those
there here what which who whom whose when where why how all any both each every few
more most less least some such no nor not only own same so than too very can could
will would shall should may might must just also please show list give tell find get
display return many much number count counts total totals amount top bottom highest
lowest largest smallest biggest best worst max maximum min minimum average avg mean
sum per across between among during within data database table tables record records
row rows value values field fields column columns information info based using use
""".split())

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD_RE = re.compile(r"[a-z0-9]+")

_DATE_HINT_TOKENS = ("date", "time", "day", "month", "year", "when")


def fold(text: Any) -> str:
    """Lower case without accents (São -> sao)."""
    normalized = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def split_words(text: Any) -> List[str]:
    """camelCase / snake_case / punctuation aware word split."""
    return _WORD_RE.findall(fold(_CAMEL_BOUNDARY_RE.sub(" ", str(text))))


def stem(word: str) -> str:
    """Light, deterministic suffix stripping (plural, -ing, -ed)."""
    if word.isdigit() or len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            if len(word) > 3 and word[-1] == word[-2] and word[-1] != "s":
                word = word[:-1]
            break
    return word


def tokenize(text: Any) -> List[str]:
    return [
        stem(word)
        for word in split_words(text)
        if word not in STOPWORDS and (len(word) > 1 or word.isdigit())
    ]


def normalize_text(text: str) -> str:
    """Kept for compatibility: lower-case words joined by spaces."""
    return " ".join(split_words(text))


# ---------------------------------------------------------
# BM25
# ---------------------------------------------------------

class BM25:
    def __init__(self, documents: Sequence[Sequence[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.term_freqs = [Counter(doc) for doc in documents]
        self.lengths = np.array([len(doc) for doc in documents], dtype=float)
        self.average_length = float(self.lengths.mean()) if len(documents) else 0.0
        document_freq: Counter = Counter()
        for freqs in self.term_freqs:
            document_freq.update(freqs.keys())
        count = len(documents)
        self.idf = {
            term: math.log(1.0 + (count - df + 0.5) / (df + 0.5))
            for term, df in document_freq.items()
        }
        self.postings: Dict[str, List[int]] = {}
        for index, freqs in enumerate(self.term_freqs):
            for term in freqs:
                self.postings.setdefault(term, []).append(index)

    def scores(self, query_tokens: Sequence[str]) -> np.ndarray:
        scores = np.zeros(len(self.term_freqs))
        if not self.average_length:
            return scores
        for term in set(query_tokens):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for index in self.postings[term]:
                tf = self.term_freqs[index][term]
                norm = self.k1 * (1 - self.b + self.b * self.lengths[index] / self.average_length)
                scores[index] += idf * tf * (self.k1 + 1) / (tf + norm)
        return scores


# ---------------------------------------------------------
# Schema index
# ---------------------------------------------------------

COLUMN_NAME_WEIGHT = 3
TABLE_NAME_WEIGHT = 3


# Glued names (salesorderheader, orderdate, totaldue) are split into
# words the schema itself uses elsewhere (multi-word names, database
# comments) or that are common in business data. The glued word is
# kept too, so an exact match still scores.
GLUED_MIN_LENGTH = 7
_SHORT_PIECES = {"id", "no"}
COMMON_SCHEMA_WORDS = set("""
account active actual address age amount annual archive area assembly audit average balance bank bill
billing birth body bonus branch brand budget business campaign card carrier case category channel city
class code comment commission company contact contract cost count country credit currency customer
date day deal default delivery department description detail discount document due duration email
employee end entity event expense flag freight from gender group header history hour hire identifier
income inventory invoice item job key last level line list location login manager margin marital
measure media method model month name national number offer online order organization owner
package paid parent part payment percent period person phone photo plan position postal price
product profit promotion province purchase quantity quota rate rating reason receipt record region
rental reorder report request return revenue review role safety salary sale sales schedule scrap
sequence service shift ship shipment shipping size special staff standard start state status stock
store sub subtotal supplier tax team territory time title total track transaction type unit update
user value vendor version week weight work year zip
""".split())


class GluedWordSplitter:
    def __init__(self, vocabulary):
        self.vocabulary = {word for word in vocabulary if len(word) >= 3} | _SHORT_PIECES

    def _known(self, piece: str) -> bool:
        return piece in self.vocabulary or (piece.endswith("s") and piece[:-1] in self.vocabulary)

    def split(self, word: str) -> List[str]:
        """Fewest known pieces covering the whole word, else []."""
        if len(word) < GLUED_MIN_LENGTH or self._known(word) or not word.isalpha():
            return []
        best: List[Optional[List[str]]] = [None] * (len(word) + 1)
        best[0] = []
        for end in range(1, len(word) + 1):
            for start in range(end):
                piece = word[start:end]
                if best[start] is None or not self._known(piece):
                    continue
                candidate = best[start] + [piece]
                if best[end] is None or len(candidate) < len(best[end]):
                    best[end] = candidate
        pieces = best[-1]
        return pieces if pieces and len(pieces) > 1 else []

    def words(self, name: Any) -> List[str]:
        result = []
        for word in split_words(name):
            pieces = self.split(word)
            result += pieces or [word]
        return result

    def tokens(self, text: Any) -> List[str]:
        tokens = tokenize(text)
        for word in split_words(text):
            tokens += [stem(piece) for piece in self.split(word) if piece not in STOPWORDS]
        return tokens


def schema_vocabulary(schema: Dict[str, Any]) -> set:
    """Words a schema spells out itself: multi-word names and comments."""
    vocabulary = set(COMMON_SCHEMA_WORDS)
    for table in schema.get("tables", []):
        texts = [table.get("table_name", ""), table.get("description") or ""]
        for column in table.get("columns", []):
            texts += [column.get("column_name", ""), column.get("description") or ""]
        for text in texts:
            words = split_words(text)
            if len(words) > 1:
                vocabulary.update(words)
    return vocabulary


def _type_hint_tokens(data_type: str) -> List[str]:
    lowered = str(data_type).lower()
    if "date" in lowered or "time" in lowered:
        return list(_DATE_HINT_TOKENS)
    return []


def _readable(name: str) -> str:
    return " ".join(split_words(name))


class SchemaIndex:
    """Column- and table-level search documents for one schema."""

    def __init__(self, schema: Dict[str, Any]):
        self.tables: List[Dict[str, Any]] = [
            table for table in schema.get("tables", []) if table.get("table_name")
        ]
        self.table_names = [table["table_name"] for table in self.tables]
        self.columns: List[Dict[str, Any]] = []

        column_tokens: List[List[str]] = []
        self.column_texts: List[str] = []
        table_tokens: List[List[str]] = []
        self.table_texts: List[str] = []
        self.splitter = GluedWordSplitter(schema_vocabulary(schema))
        tokens_of = self.splitter.tokens

        def readable(text: str) -> str:
            return " ".join(self.splitter.words(text))

        for table_position, table in enumerate(self.tables):
            name = table["table_name"]
            name_tokens = tokens_of(name)
            table_description = table.get("description") or ""
            all_column_tokens: List[str] = []
            readable_columns = []

            for column in table.get("columns", []):
                column_name = column.get("column_name", "")
                description = column.get("description") or ""
                data_type = column.get("data_type", "")
                tokens = (
                    tokens_of(column_name) * COLUMN_NAME_WEIGHT
                    + name_tokens
                    + tokenize(description)
                    + _type_hint_tokens(data_type)
                )
                column_tokens.append(tokens)
                self.column_texts.append(
                    f"{readable(name)}: {readable(column_name)} ({data_type}) {description}".strip()
                )
                self.columns.append({
                    "table": name,
                    "table_position": table_position,
                    "column": column_name,
                })
                all_column_tokens += tokens_of(column_name)
                readable_columns.append(readable(column_name))

            # The keyword table document is names only. Comments go into
            # the column documents and the meaning-based (embedding)
            # text: they often name other things ("record of each
            # purchase order, sales order or work order", "foreign key
            # to SalesOrderHeader"), and as table keywords they lift
            # unrelated tables above the one the question names.
            table_tokens.append(name_tokens * TABLE_NAME_WEIGHT + all_column_tokens)
            about = f" {table_description.strip()}" if table_description.strip() else ""
            self.table_texts.append(
                f"Table {readable(name)}.{about} Columns: {', '.join(readable_columns)}"
            )

        self.column_bm25 = BM25(column_tokens)
        self.table_bm25 = BM25(table_tokens)
        self.column_table = np.array([c["table_position"] for c in self.columns], dtype=int)


_INDEX_CACHE: "OrderedDict[str, SchemaIndex]" = OrderedDict()
_INDEX_LOCK = threading.Lock()
MAX_CACHED_INDEXES = 8


def schema_fingerprint(schema: Dict[str, Any]) -> str:
    payload = [
        (
            table.get("table_name"),
            table.get("description") or "",
            [(c.get("column_name"), c.get("data_type"), c.get("description")) for c in table.get("columns", [])],
        )
        for table in schema.get("tables", [])
    ]
    return hashlib.sha1(json.dumps(payload, default=str).encode("utf-8")).hexdigest()


def get_schema_index(schema: Dict[str, Any]) -> SchemaIndex:
    key = schema_fingerprint(schema)
    with _INDEX_LOCK:
        index = _INDEX_CACHE.get(key)
        if index is not None:
            _INDEX_CACHE.move_to_end(key)
            return index
    index = SchemaIndex(schema)
    with _INDEX_LOCK:
        _INDEX_CACHE[key] = index
        while len(_INDEX_CACHE) > MAX_CACHED_INDEXES:
            _INDEX_CACHE.popitem(last=False)
    return index


def warm_schema_index(schema: Dict[str, Any]) -> None:
    """Build the index and embed every column/table document once."""
    index = get_schema_index(schema)
    semantic_similarities("warm up", index.column_texts)
    semantic_similarities("warm up", index.table_texts)


def _normalized(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    top = float(values.max())
    return values / top if top > 0 else np.zeros_like(values)


def _min_max(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    low, high = float(values.min()), float(values.max())
    if high - low < 1e-9:
        return np.zeros_like(values)
    return (values - low) / (high - low)


# Weights of the hybrid score.
LEXICAL_WEIGHT = 0.5
SEMANTIC_WEIGHT = 0.5
BEST_COLUMN_WEIGHT = 0.6
TABLE_DOCUMENT_WEIGHT = 0.4
MATCHED_COLUMNS_PER_TABLE = 6


def score_query(index: SchemaIndex, query: str) -> List[Dict[str, Any]]:
    """
    Score every table for one phrasing. Returns tables with a
    positive score, best first, each with its best-matching columns.
    """
    if not index.tables:
        return []

    query_tokens = tokenize(query)
    column_lexical = _normalized(index.column_bm25.scores(query_tokens))
    table_lexical = _normalized(index.table_bm25.scores(query_tokens))

    column_semantic = semantic_similarities(query, index.column_texts) if index.columns else None
    table_semantic = semantic_similarities(query, index.table_texts)

    if column_semantic is not None:
        column_scores = LEXICAL_WEIGHT * column_lexical + SEMANTIC_WEIGHT * _min_max(column_semantic)
    else:
        column_scores = column_lexical

    if table_semantic is not None:
        table_document = LEXICAL_WEIGHT * table_lexical + SEMANTIC_WEIGHT * _min_max(table_semantic)
    else:
        table_document = table_lexical

    best_column = np.zeros(len(index.tables))
    if index.columns:
        np.maximum.at(best_column, index.column_table, column_scores)

    table_scores = BEST_COLUMN_WEIGHT * best_column + TABLE_DOCUMENT_WEIGHT * table_document

    results = []
    for position, table in enumerate(index.tables):
        score = float(table_scores[position])
        if score <= 0:
            continue
        column_positions = np.where(index.column_table == position)[0]
        ranked_columns = sorted(
            (
                (index.columns[i]["column"], float(column_scores[i]))
                for i in column_positions
                if column_scores[i] > 0
            ),
            key=lambda item: -item[1],
        )[:MATCHED_COLUMNS_PER_TABLE]
        results.append({
            "table": table["table_name"],
            "columns": table.get("columns", []),
            "keyword_score": round(float(table_lexical[position]), 4),
            "semantic_score": (
                round(float(table_semantic[position]), 4) if table_semantic is not None else 0.0
            ),
            "hybrid_score": round(score, 4),
            "matched_columns": [
                {"column": name, "score": round(value, 4)} for name, value in ranked_columns
            ],
        })

    results.sort(key=lambda item: -item["hybrid_score"])
    return results


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------

def build_schema_documents(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Table-level documents (kept for compatibility and debugging)."""
    index = get_schema_index(schema)
    return [
        {"table": table["table_name"], "text": text, "columns": table.get("columns", [])}
        for table, text in zip(index.tables, index.table_texts)
    ]


def retrieve_relevant_schema(
    schema: Dict[str, Any],
    query: str,
    top_k: int = 8,
) -> List[Dict[str, Any]]:
    """Hybrid (BM25 + embeddings) column-aware table ranking for one query."""
    return score_query(get_schema_index(schema), query)[:top_k]


RRF_K = 60


def retrieve_for_queries(
    schema: Dict[str, Any],
    queries: Sequence[str],
    top_k: int = 8,
    rrf_k: int = RRF_K,
) -> List[Dict[str, Any]]:
    """
    Retrieve schema for several phrasings of the same question and
    merge the rankings with Reciprocal Rank Fusion:

        fused_score(table) = sum over queries of 1 / (rrf_k + rank)

    A table ranked well by several phrasings wins; a table found by
    only one phrasing (e.g. thanks to a synonym) can still make the
    cut. The first query is the primary rewrite and breaks ties.

    Each item also carries relevance: the table's score relative to
    the best table, averaged over the phrasings (0-1). It is used to
    decide how many tables are worth showing to the LLM.
    """
    queries = [query for query in dict.fromkeys(q.strip() for q in queries) if query]
    if not queries:
        return []

    index = get_schema_index(schema)
    fused: Dict[str, Dict[str, Any]] = {}

    for query_index, query in enumerate(queries):
        ranking = score_query(index, query)
        best = ranking[0]["hybrid_score"] if ranking else 0.0
        for rank, item in enumerate(ranking, start=1):
            entry = fused.get(item["table"])
            if entry is None:
                entry = fused[item["table"]] = {
                    **copy.copy(item),
                    "matched_columns": [],
                    "fused_score": 0.0,
                    "relevance": 0.0,
                    "matched_queries": 0,
                    "best_rank": rank,
                    "primary_rank": None,
                    "_columns": {},
                }
            entry["fused_score"] += 1.0 / (rrf_k + rank)
            entry["relevance"] += (item["hybrid_score"] / best if best else 0.0) / len(queries)
            entry["matched_queries"] += 1
            entry["best_rank"] = min(entry["best_rank"], rank)
            if query_index == 0:
                entry["primary_rank"] = rank
            for matched in item["matched_columns"]:
                previous = entry["_columns"].get(matched["column"], 0.0)
                entry["_columns"][matched["column"]] = max(previous, matched["score"])

    ordered = sorted(
        fused.values(),
        key=lambda item: (
            -item["fused_score"],
            item["primary_rank"] if item["primary_rank"] is not None else float("inf"),
        ),
    )
    for item in ordered:
        columns = item.pop("_columns")
        item["matched_columns"] = [
            {"column": name, "score": round(score, 4)}
            for name, score in sorted(columns.items(), key=lambda pair: -pair[1])[:MATCHED_COLUMNS_PER_TABLE]
        ]
        item["fused_score"] = round(item["fused_score"], 6)
        item["relevance"] = round(item["relevance"], 4)
    return ordered[:top_k]
