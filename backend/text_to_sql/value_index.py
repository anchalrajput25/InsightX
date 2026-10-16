# backend/text_to_sql/value_index.py
#
# Value index: maps words in the question to values that are actually
# stored in the database, BEFORE SQL is generated.
#
#   "How many orders were cancelled?"   -> orders.status = 'canceled'
#   "customers in sao paulo"            -> customers.city = 'São Paulo'
#   "products in the Beverages category" -> categories.name = 'Beverages'
#
# The values come from profiling (schema_profiler.collect_top_values):
# only low-cardinality text columns are indexed, so names, e-mails and
# identifiers never enter the index. Matching is accent/case
# insensitive, with a conservative fuzzy fallback for spelling variants.
#
# A hint tells retrieval which table/column the question refers to,
# and tells the SQL generator the exact literal to use. Value grounding
# (value_grounding.py) still checks the final SQL against the database.

import difflib
import hashlib
import json
import re
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Sequence, Tuple

from backend.text_to_sql.retriever import STOPWORDS, fold, split_words, stem


def split_identifier(name: str) -> str:
    return " ".join(split_words(name))

MIN_VALUE_CHARS = 3
MAX_VALUE_TOKENS = 5
# Fuzzy matching only for words this long; short words are too easy
# to confuse ("cat" vs "car").
MIN_FUZZY_CHARS = 5
FUZZY_THRESHOLD = 0.88
MAX_HINTS = 12
# A value stored in more than this many columns is too generic to
# identify a column (e.g. "yes", "other").
MAX_COLUMNS_PER_VALUE = 6

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalize_value(text: Any) -> str:
    return " ".join(_NON_WORD_RE.split(fold(text))).strip()


def _indexable(normalized: str) -> bool:
    if len(normalized.replace(" ", "")) < MIN_VALUE_CHARS:
        return False
    if not any(ch.isalpha() for ch in normalized):
        return False
    words = normalized.split()
    if len(words) > MAX_VALUE_TOKENS:
        return False
    return not all(word in STOPWORDS for word in words)


class ValueIndex:
    def __init__(self, schema: Dict[str, Any]):
        # normalized value -> [(table, column, stored value)]
        self.exact: Dict[str, List[Tuple[str, str, str]]] = {}
        for table in schema.get("tables", []):
            for column in table.get("columns", []):
                for stored in column.get("values") or []:
                    normalized = normalize_value(stored)
                    if not _indexable(normalized):
                        continue
                    self.exact.setdefault(normalized, []).append(
                        (table.get("table_name", ""), column.get("column_name", ""), str(stored))
                    )
        # Words used in table/column names. A question word that names a
        # schema concept ("product") is not fuzzily matched to a value
        # ("Produce"); exact value matches are still used.
        self.schema_words = set()
        for table in schema.get("tables", []):
            self.schema_words.update(normalize_value(split_identifier(table.get("table_name", ""))).split())
            for column in table.get("columns", []):
                self.schema_words.update(normalize_value(split_identifier(column.get("column_name", ""))).split())
        self.schema_stems = {stem(word) for word in self.schema_words}
        # Fuzzy candidates bucketed by (word count, first letter).
        self.buckets: Dict[Tuple[int, str], List[str]] = {}
        for normalized in self.exact:
            key = (len(normalized.split()), normalized[0])
            self.buckets.setdefault(key, []).append(normalized)

    def __len__(self) -> int:
        return len(self.exact)


_CACHE: "OrderedDict[str, ValueIndex]" = OrderedDict()
_LOCK = threading.Lock()
MAX_CACHED = 8


def _fingerprint(schema: Dict[str, Any]) -> str:
    payload = [
        (table.get("table_name"), [(c.get("column_name"), c.get("values")) for c in table.get("columns", [])])
        for table in schema.get("tables", [])
    ]
    return hashlib.sha1(json.dumps(payload, default=str).encode("utf-8")).hexdigest()


def get_value_index(schema: Dict[str, Any]) -> ValueIndex:
    key = _fingerprint(schema)
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    index = ValueIndex(schema)
    with _LOCK:
        _CACHE[key] = index
        while len(_CACHE) > MAX_CACHED:
            _CACHE.popitem(last=False)
    return index


def _ngrams(words: List[str]) -> List[Tuple[int, int, str]]:
    grams = []
    for size in range(min(MAX_VALUE_TOKENS, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            chunk = words[start:start + size]
            if all(word in STOPWORDS for word in chunk):
                continue
            grams.append((start, start + size, " ".join(chunk)))
    return grams


def _fuzzy(index: ValueIndex, gram: str) -> List[Tuple[str, float]]:
    if len(gram.replace(" ", "")) < MIN_FUZZY_CHARS:
        return []
    words = gram.split()
    if all(word in index.schema_words or stem(word) in index.schema_stems for word in words):
        return []
    candidates = index.buckets.get((len(gram.split()), gram[0]), [])
    matches = []
    for candidate in candidates:
        if abs(len(candidate) - len(gram)) > 3:
            continue
        matcher = difflib.SequenceMatcher(None, gram, candidate)
        if matcher.real_quick_ratio() < FUZZY_THRESHOLD or matcher.quick_ratio() < FUZZY_THRESHOLD:
            continue
        ratio = matcher.ratio()
        if ratio >= FUZZY_THRESHOLD:
            matches.append((candidate, ratio))
    return matches


def match_values(schema: Dict[str, Any], texts: Sequence[str], limit: int = MAX_HINTS) -> List[Dict[str, Any]]:
    """
    Find stored values mentioned in the texts (question, primary
    rewrite). Returns hints, best first:
        {"table", "column", "value", "matched_text", "score", "match"}
    Longer exact phrases win over the words inside them.
    """
    index = get_value_index(schema)
    if not len(index):
        return []

    found: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for text in texts:
        words = normalize_value(text).split()
        covered = [False] * len(words)
        for start, end, gram in _ngrams(words):
            if all(covered[start:end]):
                continue  # inside a longer phrase that already matched
            matches = [(gram, 1.0)] if gram in index.exact else _fuzzy(index, gram)
            if not matches:
                continue
            for normalized, score in matches:
                locations = index.exact[normalized]
                if len(locations) > MAX_COLUMNS_PER_VALUE:
                    continue
                for table, column, stored in locations:
                    key = (table, column, stored)
                    hint = {
                        "table": table,
                        "column": column,
                        "value": stored,
                        "matched_text": gram,
                        "score": round(score, 3),
                        "match": "exact" if score == 1.0 else "fuzzy",
                        "words": end - start,
                    }
                    previous = found.get(key)
                    if previous is None or (hint["score"], hint["words"]) > (previous["score"], previous["words"]):
                        found[key] = hint
            if any(score == 1.0 for _, score in matches):
                for position in range(start, end):
                    covered[position] = True

    hints = sorted(found.values(), key=lambda hint: (-hint["score"], -hint["words"], hint["table"], hint["column"]))
    for hint in hints:
        hint.pop("words", None)
    return hints[:limit]
