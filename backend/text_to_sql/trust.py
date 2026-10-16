# backend/text_to_sql/trust.py
#
# Trust layer: is the answer real, or did the AI make it up?
#
# Three deterministic checks (no LLM involved):
#
# 1. Claim verification - every number in the answer must be found in
#    the SQL result (allowing for rounding, %, thousands/millions);
#    quoted/bold values must be result values. Numbers the LLM computed
#    or invented are "unverified".
#
# 2. Result sanity - SQL can run and still be wrong. The classic case
#    is join fan-out: SUM(orders.freight) after joining order_items
#    counts each order once per item. Suspected fan-out is confirmed
#    with a read-only probe (COUNT(*) vs COUNT(DISTINCT key) over the
#    query's own FROM/JOIN/WHERE) before it is reported.
#
# 3. Trust score - combines candidate agreement, corrections, schema
#    linking problems, sanity findings and claim verification into a
#    level (high / medium / low) with the reasons behind it.

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import sqlglot
from sqlglot import exp
from sqlalchemy.engine import Engine
from backend import dialects

# ============================================================
# 1. CLAIM VERIFICATION
# ============================================================

_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<sign>[-+−]?)(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)"
    r"(?P<pct>\s?%)?"
    r"(?:\s?(?P<scale>thousand|million|billion|trillion|lakh|crore|k|m|bn|b)\b)?",
    flags=re.IGNORECASE,
)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*]\s+)?\d+[.)]\s", flags=re.MULTILINE)
_SPAN_RE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\"([^\"]{2,80})\"|“([^”]{2,80})”")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b")

SCALES = {
    "thousand": 1e3, "k": 1e3, "lakh": 1e5, "million": 1e6, "m": 1e6,
    "crore": 1e7, "billion": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12,
}


def _fold(text: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(text))
    folded = "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()
    return re.sub(r"[\s_\-]+", " ", folded).strip(" .,:;!?*'\"")


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return float(value)
    except Exception:
        pass
    text = str(value).strip()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return float(text)
    return None


def extract_numbers(text: str) -> List[Dict[str, Any]]:
    """Numbers stated in an answer, with their decimals, % and scale."""
    cleaned = _LIST_MARKER_RE.sub(" ", text or "")
    cleaned = _DATE_RE.sub(lambda m: " " + m.group(0).replace("-", " ").replace(":", " ") + " ", cleaned)
    found = []
    for match in _NUMBER_RE.finditer(cleaned):
        raw = match.group("num")
        digits = raw.replace(",", "")
        try:
            value = float(digits)
        except ValueError:
            continue
        if match.group("sign") in ("-", "−"):
            value = -value
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        scale = (match.group("scale") or "").lower()
        found.append({
            "text": match.group(0).strip(),
            "value": value,
            "decimals": decimals,
            "percent": bool(match.group("pct")),
            "scale": SCALES.get(scale, 1.0),
        })
    return found


def _result_numbers(rows: Sequence[Dict[str, Any]], columns: Sequence[str]) -> List[float]:
    numbers: List[float] = []
    for row in rows:
        for column in columns:
            value = row.get(column)
            number = _as_number(value)
            if number is not None:
                numbers.append(number)
            elif value is not None:
                # digits inside text/date values (years, months, codes)
                numbers += [float(d) for d in re.findall(r"\d+(?:\.\d+)?", str(value))]
    return numbers


def _matches(claim: Dict[str, Any], numbers: Iterable[float]) -> bool:
    """
    The stated number equals a result number up to the precision it
    was written with: "4.09" matches 4.0864, "15.5 thousand" matches
    15540, "12.5%" matches 12.5 or 0.125.
    """
    tolerance = 0.5 * 10 ** (-claim["decimals"])
    targets = [(claim["value"] * claim["scale"], tolerance * claim["scale"])]
    if claim["percent"]:
        targets.append((claim["value"] / 100.0, tolerance / 100.0))
    return any(
        abs(number - target) <= allowed * 1.0001 + 1e-9
        for number in numbers
        for target, allowed in targets
    )


def verify_claims(
    answer: str,
    question: str,
    execution_result: Dict[str, Any],
    max_rows: int = 1000,
) -> Dict[str, Any]:
    """
    Check the answer against the result rows it was generated from.
    Returns {"status": "verified"|"unverified"|"not_applicable",
             "numbers": [{text, verified}], "unverified_numbers": [...],
             "values": [{text, verified}], "unverified_values": [...]}
    """
    rows = list((execution_result or {}).get("rows") or [])[:max_rows]
    columns = list((execution_result or {}).get("columns") or [])
    if not answer or not rows:
        return {"status": "not_applicable", "numbers": [], "unverified_numbers": [],
                "values": [], "unverified_values": []}

    allowed = _result_numbers(rows, columns)
    allowed.append(float((execution_result or {}).get("row_count") or len(rows)))
    question_numbers = [claim["value"] for claim in extract_numbers(question)]

    numbers = []
    for claim in extract_numbers(answer):
        verified = _matches(claim, allowed) or claim["value"] in question_numbers
        numbers.append({"text": claim["text"], "verified": verified})

    # Quoted / bold / code spans that look like data values must be
    # result values (or come from the question or a column name).
    known_text = {_fold(value) for row in rows for value in row.values() if value is not None}
    known_text |= {_fold(column) for column in columns}
    folded_question = _fold(question)
    values = []
    for match in _SPAN_RE.finditer(answer):
        span = next(group for group in match.groups() if group)
        folded = _fold(span)
        if not folded or extract_numbers(span) or len(folded) < 2:
            continue  # numbers are checked above
        verified = (
            folded in known_text
            or any(folded in text or text in folded for text in known_text if len(text) >= 3)
            or folded in folded_question
        )
        values.append({"text": span, "verified": verified})

    unverified_numbers = [item["text"] for item in numbers if not item["verified"]]
    unverified_values = [item["text"] for item in values if not item["verified"]]
    return {
        "status": "unverified" if unverified_numbers or unverified_values else "verified",
        "numbers": numbers,
        "unverified_numbers": unverified_numbers,
        "values": values,
        "unverified_values": unverified_values,
    }


# ============================================================
# 2. RESULT SANITY: JOIN FAN-OUT
# ============================================================

FANOUT_AGGREGATES = (exp.Sum, exp.Avg, exp.Count)


def _single_key(table: Dict[str, Any]) -> Optional[str]:
    primary_key = table.get("primary_key") or []
    if len(primary_key) == 1:
        return primary_key[0]
    for column in table.get("columns", []):
        if column.get("unique"):
            return column.get("column_name")
    return None


def fanout_probes(sql: str, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Candidate fan-out checks for a query: one per aggregated table that
    has a single-column key, in a query with joins. Each probe counts
    rows vs distinct keys over the query's own FROM/JOIN/WHERE.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialects.sqlglot_name())
    except Exception:
        return []
    if not isinstance(tree, exp.Select) or not tree.args.get("joins"):
        return []

    tables = {table.get("table_name"): table for table in schema.get("tables", [])}
    # The FROM clause key is "from_" in newer sqlglot, "from" in older.
    from_clause = tree.args.get("from_") or tree.args.get("from")
    if from_clause is None:
        return []
    aliases: Dict[str, str] = {}
    for table in [from_clause.this] + [join.this for join in tree.args["joins"]]:
        if isinstance(table, exp.Table) and table.name in tables:
            aliases[table.alias_or_name] = table.name

    probes: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for aggregate in tree.find_all(*FANOUT_AGGREGATES):
        if aggregate.find(exp.Distinct) or isinstance(aggregate.this, exp.Star):
            continue  # COUNT(DISTINCT ..) and COUNT(*) are not fan-out sensitive here
        owners = set()
        for column in aggregate.find_all(exp.Column):
            table = aliases.get(column.table) if column.table else None
            if table is None and not column.table:
                candidates = [t for t in set(aliases.values())
                              if column.name in {c.get("column_name") for c in tables[t].get("columns", [])}]
                table = candidates[0] if len(candidates) == 1 else None
            if table:
                owners.add(table)
        if len(owners) != 1:
            continue
        table = owners.pop()
        alias = next(a for a, t in aliases.items() if t == table)
        key = _single_key(tables[table])
        if not key or table in seen:
            continue
        seen.add(table)

        probe = tree.copy()
        for part in ("group", "order", "limit", "offset", "having", "distinct", "qualify"):
            probe.set(part, None)
        probe.set("expressions", [
            exp.alias_(exp.Count(this=exp.Star()), "n_rows"),
            exp.alias_(exp.Count(this=exp.Distinct(expressions=[exp.column(key, table=alias)])), "n_keys"),
        ])
        probes.append({
            "table": table,
            "key": key,
            "aggregate": aggregate.sql(dialect=dialects.sqlglot_name()),
            "sql": probe.sql(dialect=dialects.sqlglot_name()),
        })
    return probes


def check_fanout(sql: str, schema: Dict[str, Any], engine: Engine) -> List[Dict[str, Any]]:
    """Confirmed fan-out findings (read-only probes; failures are skipped)."""
    from backend.text_to_sql.query_executor import execute_query

    findings = []
    for probe in fanout_probes(sql, schema):
        result = execute_query(probe["sql"], engine, max_rows=1)
        if not result.get("success") or not result.get("rows"):
            continue
        row = result["rows"][0]
        rows, keys = int(row.get("n_rows") or 0), int(row.get("n_keys") or 0)
        if keys and rows > keys:
            findings.append({
                **{k: probe[k] for k in ("table", "key", "aggregate")},
                "rows": rows,
                "distinct_keys": keys,
                "factor": round(rows / keys, 2),
                "message": (
                    f"Possible double counting: {probe['aggregate']} aggregates {probe['table']}, but the joins "
                    f"repeat each {probe['table']} row {rows / keys:.2f}x on average ({rows} joined rows for "
                    f"{keys} distinct {probe['table']}.{probe['key']}). Aggregate {probe['table']} before joining, "
                    f"or remove the join that multiplies its rows."
                ),
            })
    return findings


def check_result_sanity(
    sql: str,
    execution_result: Dict[str, Any],
    schema: Dict[str, Any],
    engine: Optional[Engine],
) -> Dict[str, Any]:
    """
    Returns {"fanout": [...], "warnings": [...]}; fan-out findings are
    blocking (sent to correction when possible), warnings are not.
    """
    warnings: List[str] = []
    rows = (execution_result or {}).get("rows") or []
    columns = (execution_result or {}).get("columns") or []
    if rows and len(rows) == 1 and columns and all(rows[0].get(column) is None for column in columns):
        warnings.append("The query returned only NULL: probably no rows matched the filters.")
    if (execution_result or {}).get("truncated"):
        warnings.append(f"The result was cut off at {execution_result.get('row_count')} rows.")
    fanout = check_fanout(sql, schema, engine) if engine is not None and sql else []
    return {"fanout": fanout, "warnings": warnings}


# ============================================================
# 3. TRUST SCORE
# ============================================================

def assess_trust(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Combine every signal the pipeline produced into a trust level.
    Returns {"level", "score", "reasons": [{"signal", "effect", "detail"}]}.
    """
    score = 100
    reasons: List[Dict[str, Any]] = []

    def note(signal: str, effect: int, detail: str) -> None:
        nonlocal score
        score += effect
        reasons.append({"signal": signal, "effect": effect, "detail": detail})

    claims = state.get("claim_check") or {}
    if claims.get("unverified_numbers"):
        note("answer", -45, "Numbers in the answer are not in the query result: " + ", ".join(claims["unverified_numbers"]))
    elif claims.get("status") == "verified" and claims.get("numbers"):
        note("answer", 0, f"All {len(claims['numbers'])} numbers in the answer were found in the query result.")
    if claims.get("unverified_values"):
        note("answer", -15, "Values in the answer not found in the result: " + ", ".join(claims["unverified_values"]))

    sanity = state.get("sanity") or {}
    for finding in sanity.get("fanout") or []:
        note("sql", -40, finding["message"])
    for warning in sanity.get("warnings") or []:
        note("result", -10, warning)

    selection = state.get("sql_selection") or {}
    executed = selection.get("executed_ok") or 0
    if executed > 1:
        agreeing = max(selection.get("clusters") or [0])
        if agreeing == executed:
            note("candidates", 0, f"All {executed} SQL candidates returned the same result.")
        elif agreeing * 2 > executed:
            note("candidates", -15, f"{agreeing} of {executed} SQL candidates agreed.")
        else:
            note("candidates", -30, f"Only {agreeing} of {executed} SQL candidates agreed.")

    corrections = int(state.get("correction_attempts") or 0)
    if corrections:
        note("sql", -10 * corrections, f"The SQL needed {corrections} automatic correction(s).")

    linked = state.get("linked_schema") or {}
    if linked.get("status") == "fallback":
        note("schema", -10, "Schema linking output was unusable; retrieval results were used instead.")
    if linked.get("missing"):
        note("schema", -20, "Schema linking reported missing information: " + "; ".join(linked["missing"][:2]))
    if linked.get("ambiguities"):
        note("schema", -10, "The question is ambiguous: " + "; ".join(linked["ambiguities"][:2]))

    score = max(0, min(100, score))
    level = "high" if score >= 80 else "medium" if score >= 55 else "low"
    return {"level": level, "score": score, "reasons": reasons}
