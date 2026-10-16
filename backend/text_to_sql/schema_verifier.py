# backend/text_to_sql/schema_verifier.py
#
# Deterministic verification of the schema linker's JSON.
#
# The LLM proposes tables, columns, joins and filters; this module
# checks every one of them against the real schema and repairs what it
# safely can:
#
#   tables   unknown names dropped (or fixed when a close name exists)
#   columns  must exist; a column named under the wrong table is moved
#            to the one table that has it; near-miss names are fixed
#   joins    both sides must exist; marked "relationship" when backed
#            by a declared/inferred relationship, otherwise "unverified";
#            joins between incompatible types are dropped
#   tables   left disconnected are connected through the relationship
#            graph (graph_rag.plan_join_paths)
#   filters  text values are replaced by the stored spelling when the
#            column's stored values contain a match
#
# Every repair is recorded in "corrections", every problem in "issues",
# so the UI and the evaluation can show what the LLM got wrong. When
# the linker output is unusable, the focused schema from retrieval is
# used instead ("fallback").

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.text_to_sql.graph_rag import plan_join_paths
from backend.text_to_sql.value_index import normalize_value
from backend import dialects

NAME_MATCH_CUTOFF = 0.85
VALUE_MATCH_CUTOFF = 0.88
ROLES = {"measure", "dimension", "filter", "time", "join", "identifier", "sort"}
MAX_TEXT_ITEMS = 10
MAX_RENDER_VALUES = 8

_QUOTES_RE = re.compile(r'^[\s"`\[]+|[\s"`\]]+$')


def _clean_name(name: Any) -> str:
    text = _QUOTES_RE.sub("", str(name or ""))
    # drop a schema prefix: public.orders -> orders
    return text.split(".")[-1].strip() if text.count(".") == 1 and " " not in text else text.strip()


def _type_family(data_type: Any) -> str:
    lowered = str(data_type or "").lower()
    if any(word in lowered for word in ("int", "serial", "numeric", "decimal", "double", "float", "real")):
        return "number"
    if any(word in lowered for word in ("char", "text", "uuid", "citext")):
        return "text"
    if any(word in lowered for word in ("date", "time")):
        return "temporal"
    if "bool" in lowered:
        return "boolean"
    return lowered or "unknown"


class SchemaCatalog:
    """Case-insensitive lookup over the agent schema."""

    def __init__(self, schema: Dict[str, Any]):
        self.tables: Dict[str, Dict[str, Any]] = {}
        self.table_lookup: Dict[str, str] = {}
        self.columns: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.column_lookup: Dict[str, Dict[str, str]] = {}
        self.tables_with_column: Dict[str, List[str]] = {}
        for table in schema.get("tables", []):
            name = table.get("table_name")
            if not name:
                continue
            self.tables[name] = table
            self.table_lookup[name.lower()] = name
            self.columns[name] = {}
            self.column_lookup[name] = {}
            for column in table.get("columns", []):
                column_name = column.get("column_name")
                if not column_name:
                    continue
                self.columns[name][column_name] = column
                self.column_lookup[name][column_name.lower()] = column_name
                self.tables_with_column.setdefault(column_name.lower(), []).append(name)
        self.relationship_pairs = set()
        for relation in schema.get("relationships", []):
            left = (relation.get("source_table"), relation.get("source_column"))
            right = (relation.get("target_table"), relation.get("target_column"))
            self.relationship_pairs.add(frozenset((left, right)))

    def resolve_table(self, name: Any) -> Tuple[Optional[str], bool]:
        """(table, was_corrected)"""
        cleaned = _clean_name(name)
        exact = self.table_lookup.get(cleaned.lower())
        if exact:
            return exact, exact != cleaned
        close = difflib.get_close_matches(cleaned.lower(), list(self.table_lookup), n=1, cutoff=NAME_MATCH_CUTOFF)
        return (self.table_lookup[close[0]], True) if close else (None, False)

    def resolve_column(self, table: str, name: Any) -> Tuple[Optional[str], bool]:
        cleaned = _clean_name(name)
        lookup = self.column_lookup.get(table, {})
        exact = lookup.get(cleaned.lower())
        if exact:
            return exact, exact != cleaned
        close = difflib.get_close_matches(cleaned.lower(), list(lookup), n=1, cutoff=NAME_MATCH_CUTOFF)
        return (lookup[close[0]], True) if close else (None, False)

    def column_type(self, table: str, column: str) -> str:
        return str(self.columns.get(table, {}).get(column, {}).get("data_type", ""))

    def stored_values(self, table: str, column: str) -> List[str]:
        return list(self.columns.get(table, {}).get(column, {}).get("values") or [])


def _split_reference(reference: Any) -> Tuple[str, str]:
    """'orders.customer_id' -> ('orders', 'customer_id')"""
    text = _QUOTES_RE.sub("", str(reference or ""))
    parts = [part for part in re.split(r"\.", text) if part]
    if len(parts) >= 2:
        return parts[-2].strip(' "`'), parts[-1].strip(' "`')
    return "", text.strip()


class _Verifier:
    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema
        self.catalog = SchemaCatalog(schema)
        self.tables: List[str] = []
        self.columns: Dict[Tuple[str, str], str] = {}  # (table, column) -> role
        self.joins: List[Dict[str, Any]] = []
        self.filters: List[Dict[str, Any]] = []
        self.corrections: List[str] = []
        self.issues: List[str] = []

    def add_table(self, table: str) -> None:
        if table not in self.tables:
            self.tables.append(table)

    # ---------------- tables ----------------
    def verify_tables(self, names: List[Any]) -> None:
        for name in names or []:
            table, corrected = self.catalog.resolve_table(name)
            if table is None:
                self.issues.append(f"unknown table {name!r} dropped")
                continue
            if corrected:
                self.corrections.append(f"table {name!r} -> {table}")
            self.add_table(table)

    # ---------------- columns ----------------
    def locate_column(self, table_hint: Any, column_name: Any) -> Optional[Tuple[str, str]]:
        hinted_table, column_part = None, column_name
        if not table_hint and "." in str(column_name or ""):
            table_hint, column_part = _split_reference(column_name)
        if table_hint:
            hinted_table, corrected_table = self.catalog.resolve_table(table_hint)
            if hinted_table:
                column, corrected = self.catalog.resolve_column(hinted_table, column_part)
                if column:
                    if corrected or corrected_table:
                        self.corrections.append(f"column {table_hint}.{column_part} -> {hinted_table}.{column}")
                    return hinted_table, column
        # Not in the named table: the one linked/other table that has it.
        owners = self.catalog.tables_with_column.get(_clean_name(column_part).lower(), [])
        linked_owners = [table for table in owners if table in self.tables]
        candidates = linked_owners if len(linked_owners) == 1 else (owners if len(owners) == 1 else [])
        if candidates:
            table = candidates[0]
            column = self.catalog.column_lookup[table][_clean_name(column_part).lower()]
            self.corrections.append(f"column {table_hint or '?'}.{column_part} -> {table}.{column}")
            return table, column
        return None

    def verify_columns(self, columns: List[Any]) -> None:
        for item in columns or []:
            if isinstance(item, str):
                item = {"column": item}
            if not isinstance(item, dict):
                continue
            located = self.locate_column(item.get("table"), item.get("column"))
            if located is None:
                self.issues.append(f"unknown column {item.get('table') or '?'}.{item.get('column')} dropped")
                continue
            table, column = located
            self.add_table(table)
            role = str(item.get("role") or "").lower()
            self.columns.setdefault((table, column), role if role in ROLES else "")

    # ---------------- joins ----------------
    def add_join(self, left: Tuple[str, str], right: Tuple[str, str], source: str) -> None:
        key = frozenset((left, right))
        if any(frozenset(((j["left_table"], j["left_column"]), (j["right_table"], j["right_column"]))) == key
               for j in self.joins):
            return
        backed = key in self.catalog.relationship_pairs
        self.joins.append({
            "left_table": left[0], "left_column": left[1],
            "right_table": right[0], "right_column": right[1],
            "condition": f"{left[0]}.{left[1]} = {right[0]}.{right[1]}",
            "verified": "relationship" if backed else "unverified",
            "source": source,
        })
        for table, column in (left, right):
            self.add_table(table)
            self.columns.setdefault((table, column), "join")

    def verify_joins(self, joins: List[Any]) -> None:
        for item in joins or []:
            if isinstance(item, str) and "=" in item:
                left_text, right_text = item.split("=", 1)
                item = {"left": left_text, "right": right_text}
            if not isinstance(item, dict):
                continue
            sides = []
            for key in ("left", "right"):
                table_hint, column = _split_reference(item.get(key))
                located = self.locate_column(table_hint, column)
                if located is None:
                    self.issues.append(f"join side {item.get(key)!r} does not exist; join dropped")
                    break
                sides.append(located)
            if len(sides) != 2:
                continue
            (lt, lc), (rt, rc) = sides
            if lt == rt:
                self.issues.append(f"self-join {lt}.{lc} = {rt}.{rc} ignored")
                continue
            if _type_family(self.catalog.column_type(lt, lc)) != _type_family(self.catalog.column_type(rt, rc)):
                self.issues.append(f"join {lt}.{lc} = {rt}.{rc} compares different types; dropped")
                continue
            self.add_join((lt, lc), (rt, rc), "linker")

    def connect_tables(self) -> None:
        """Connect tables the linker left disconnected."""
        if len(self.tables) < 2:
            return
        components = self._components()
        if len(components) == 1:
            return
        plan = plan_join_paths(self.schema, list(self.tables), max_connectors=4)
        for join in plan["joins"]:
            left = (join["left_table"], join["left_column"])
            right = (join["right_table"], join["right_column"])
            if frozenset((left, right)) not in {
                frozenset(((j["left_table"], j["left_column"]), (j["right_table"], j["right_column"])))
                for j in self.joins
            } and not self._already_connected(left[0], right[0]):
                self.add_join(left, right, "relationship graph")
                self.corrections.append(f"added join {join['condition']}")
        if plan["unreachable"]:
            self.issues.append(
                "no known relationship connects: " + ", ".join(plan["unreachable"])
            )

    def _components(self) -> List[set]:
        parent = {table: table for table in self.tables}

        def find(table):
            while parent[table] != table:
                parent[table] = parent[parent[table]]
                table = parent[table]
            return table

        for join in self.joins:
            a, b = join["left_table"], join["right_table"]
            if a in parent and b in parent:
                parent[find(a)] = find(b)
        groups: Dict[str, set] = {}
        for table in self.tables:
            groups.setdefault(find(table), set()).add(table)
        return list(groups.values())

    def _already_connected(self, a: str, b: str) -> bool:
        return any(a in group and b in group for group in self._components())

    # ---------------- filters ----------------
    def verify_filters(self, filters: List[Any]) -> None:
        for item in filters or []:
            if not isinstance(item, dict):
                continue
            located = self.locate_column(item.get("table"), item.get("column"))
            if located is None:
                self.issues.append(f"filter on unknown column {item.get('table') or '?'}.{item.get('column')} dropped")
                continue
            table, column = located
            self.add_table(table)
            self.columns.setdefault((table, column), "filter")
            value = item.get("value")
            operator = str(item.get("operator") or "=").strip() or "="
            entry = {"table": table, "column": column, "operator": operator, "value": value, "check": "not_checked"}
            stored = self.catalog.stored_values(table, column)
            if isinstance(value, str) and stored:
                entry.update(self._match_stored(value, stored, table, column))
            self.filters.append(entry)

    def _match_stored(self, value: str, stored: List[str], table: str, column: str) -> Dict[str, Any]:
        if value in stored:
            return {"check": "stored_value"}
        normalized = {normalize_value(candidate): candidate for candidate in stored}
        key = normalize_value(value)
        if key in normalized:
            replacement = normalized[key]
        else:
            close = difflib.get_close_matches(key, list(normalized), n=1, cutoff=VALUE_MATCH_CUTOFF)
            replacement = normalized[close[0]] if close else None
        if replacement is None:
            self.issues.append(f"filter value {value!r} is not among the stored values of {table}.{column}")
            return {"check": "not_found"}
        self.corrections.append(f"filter value {table}.{column}: {value!r} -> {replacement!r}")
        return {"value": replacement, "check": "corrected", "original_value": value}


def _text_list(value: Any) -> List[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in items if str(item).strip()][:MAX_TEXT_ITEMS]


def _metrics(value: Any, verifier: "_Verifier") -> List[Dict[str, str]]:
    """Keep metric expressions whose columns all exist."""
    import sqlglot
    from sqlglot import exp

    metrics = []
    for item in value or []:
        if not isinstance(item, dict) or not item.get("expression"):
            continue
        expression = str(item["expression"])
        name = str(item.get("name") or "metric")
        try:
            tree = sqlglot.parse_one(expression, read=dialects.sqlglot_name())
        except Exception:
            verifier.issues.append(f"metric {name!r} is not a valid SQL expression; dropped")
            continue
        unknown, found = [], []
        for column in tree.find_all(exp.Column):
            located = verifier.locate_column(column.table or None, column.name)
            if located is None:
                unknown.append(column.sql())
            else:
                found.append(located)
        if unknown:
            verifier.issues.append(f"metric {name!r} uses unknown columns {unknown}; dropped")
            continue
        for table, column in found:
            verifier.add_table(table)
            verifier.columns.setdefault((table, column), "measure")
        metrics.append({"name": name, "expression": expression})
    return metrics[:MAX_TEXT_ITEMS]


def verify_linking(
    parsed: Optional[Dict[str, Any]],
    schema: Dict[str, Any],
    focus_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Verify the linker JSON against the schema. Returns the linked
    schema used for SQL generation:
        {status, tables, columns, joins, filters, metrics, time,
         ambiguities, missing, corrections, issues}
    status: "verified" | "fallback"
    """
    focus_context = focus_context or {}
    verifier = _Verifier(schema)

    if isinstance(parsed, dict):
        verifier.verify_tables(parsed.get("tables") or [])
        verifier.verify_columns(parsed.get("columns") or [])
        verifier.verify_joins(parsed.get("joins") or [])
        verifier.verify_filters(parsed.get("filters") or [])
    metrics = _metrics(parsed.get("metrics") if isinstance(parsed, dict) else None, verifier)

    status = "verified"
    if not verifier.tables:
        # Unusable linker output: use what retrieval selected.
        status = "fallback"
        verifier.issues.append(
            "schema linker output was not usable JSON" if not isinstance(parsed, dict)
            else "schema linker selected no existing table"
        )
        for table in focus_context.get("relevant_tables") or []:
            if table in verifier.catalog.tables:
                verifier.add_table(table)
        for condition in focus_context.get("join_paths") or []:
            left_text, _, right_text = condition.partition("=")
            left, right = _split_reference(left_text), _split_reference(right_text)
            if all(part in verifier.catalog.columns.get(table, {}) for table, part in (left, right)):
                verifier.add_join(left, right, "relationship graph")

    time_info = None
    if isinstance(parsed, dict) and isinstance(parsed.get("time"), dict):
        located = verifier.locate_column(parsed["time"].get("table"), parsed["time"].get("column"))
        if located:
            verifier.add_table(located[0])
            verifier.columns.setdefault(located, "time")
            time_info = {
                "table": located[0],
                "column": located[1],
                "interpretation": str(parsed["time"].get("interpretation") or ""),
            }

    verifier.connect_tables()

    return {
        "status": status,
        "tables": verifier.tables,
        "columns": [
            {"table": table, "column": column, "role": role}
            for (table, column), role in verifier.columns.items()
        ],
        "joins": verifier.joins,
        "filters": verifier.filters,
        "metrics": metrics,
        "time": time_info,
        "ambiguities": _text_list(parsed.get("ambiguities")) if isinstance(parsed, dict) else [],
        "missing": _text_list(parsed.get("missing")) if isinstance(parsed, dict) else [],
        "corrections": verifier.corrections,
        "issues": verifier.issues,
    }


def _sql_literal(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def render_linked_schema(
    linked: Dict[str, Any],
    schema: Dict[str, Any],
    focus_context: Optional[Dict[str, Any]] = None,
    max_columns_per_table: int = 40,
) -> str:
    """
    Deterministic text for the SQL generator. Every table of the
    linked schema is listed with its columns (linked columns marked
    with their role) so the generator is not limited to the columns
    the linker happened to name.
    """
    focus_context = focus_context or {}
    catalog = SchemaCatalog(schema)
    roles = {(item["table"], item["column"]): item["role"] for item in linked.get("columns", [])}
    lines = ["TABLES (verified against the database; use only these):"]

    for table in linked.get("tables", []):
        table_info = catalog.tables.get(table, {})
        columns = table_info.get("columns", [])
        linked_names = {column for (t, column) in roles if t == table}
        primary_key = set(table_info.get("primary_key") or [])
        if len(columns) > max_columns_per_table:
            columns = [c for c in columns if c.get("column_name") in linked_names | primary_key][:max_columns_per_table]
            omitted = len(table_info.get("columns", [])) - len(columns)
        else:
            omitted = 0
        lines.append(f"- {table}")
        for column in columns:
            name = column.get("column_name")
            entry = f"    {name} {column.get('data_type', '')}".rstrip()
            tags = []
            if name in primary_key:
                tags.append("primary key")
            role = roles.get((table, name))
            if role:
                tags.append(f"linked: {role}")
            if tags:
                entry += f"  [{', '.join(tags)}]"
            values = column.get("values") or []
            if values and (role or len(values) <= MAX_RENDER_VALUES):
                shown = ", ".join(_sql_literal(v) for v in values[:MAX_RENDER_VALUES])
                more = f", ... ({len(values) - MAX_RENDER_VALUES} more)" if len(values) > MAX_RENDER_VALUES else ""
                entry += f"  stored values: {shown}{more}"
            lines.append(entry)
        if omitted:
            lines.append(f"    ... {omitted} more columns not relevant to this question")

    joins = linked.get("joins") or []
    lines.append("")
    if joins:
        lines.append("JOIN CONDITIONS (use these to join the tables above):")
        for join in joins:
            note = "known relationship" if join["verified"] == "relationship" else "proposed by schema linking, not a declared relationship"
            lines.append(f"- {join['condition']}   ({note})")
    elif len(linked.get("tables", [])) > 1:
        lines.append("JOIN CONDITIONS: none known between these tables.")

    filters = linked.get("filters") or []
    if filters:
        lines.append("")
        lines.append("FILTERS (values checked against stored data):")
        for item in filters:
            check = {
                "stored_value": "exact stored value",
                "corrected": f"corrected from {item.get('original_value')!r} to the stored spelling",
                "not_found": "WARNING: not among the stored values",
                "not_checked": "not checked",
            }.get(item.get("check"), "")
            lines.append(
                f"- {item['table']}.{item['column']} {item['operator']} {_sql_literal(item['value'])}   ({check})"
            )

    used_filters = {(item["table"], item["column"]) for item in filters}
    hints = [
        hint for hint in focus_context.get("value_hints") or []
        if (hint["table"], hint["column"]) not in used_filters and hint["table"] in linked.get("tables", [])
    ]
    if hints:
        lines.append("")
        lines.append("OTHER STORED VALUES MATCHING WORDS IN THE QUESTION (use the exact literal if you filter on them):")
        for hint in hints:
            lines.append(f"- {hint['table']}.{hint['column']} = {_sql_literal(hint['value'])}  (question: \"{hint['matched_text']}\")")

    if linked.get("metrics"):
        lines.append("")
        lines.append("METRICS (from schema linking):")
        lines += [f"- {metric['name']} = {metric['expression']}" for metric in linked["metrics"]]

    if linked.get("time"):
        time_info = linked["time"]
        lines.append("")
        lines.append(f"TIME COLUMN: {time_info['table']}.{time_info['column']}  {time_info.get('interpretation', '')}".rstrip())

    for title, key in (("AMBIGUITIES (state the assumption you make)", "ambiguities"), ("MISSING FROM THE SCHEMA", "missing")):
        if linked.get(key):
            lines.append("")
            lines.append(f"{title}:")
            lines += [f"- {text}" for text in linked[key]]

    return "\n".join(lines)
