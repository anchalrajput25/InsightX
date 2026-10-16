# backend/text_to_sql/schema_focus.py
#
# Builds the part of the schema the LLM actually sees for a question.
#
#   1. Column-level hybrid retrieval over all phrasings (retriever.py)
#   2. Stored values mentioned in the question (value_index.py)
#   3. Tables worth keeping: relative relevance cutoff, capped at top_k
#   4. Minimal join paths connecting them (graph_rag.plan_join_paths)
#   5. Wide tables pruned to relevant / key / join columns
#
# The result replaces the full-schema dump that used to go to the
# schema linker, so prompt size depends on the question, not on the
# size of the database. Names of the remaining tables are still listed
# so the model can tell when something it needs was not retrieved.

from collections import defaultdict
from typing import Any, Dict, List, Sequence

from backend.text_to_sql.graph_rag import plan_join_paths, relationships_among
from backend.text_to_sql.retriever import get_schema_index, retrieve_for_queries, tokenize
from backend.text_to_sql.value_index import match_values

# Relevance added to a table holding a stored value the question
# mentions (split between tables when the value occurs in several).
VALUE_HINT_BOOST = 0.6
MAX_VALUES_PER_COLUMN = 8
# Connector tables only make a join possible: keep few columns.
CONNECTOR_TABLE_COLUMNS = 8
MAX_OTHER_TABLE_NAMES = 300
# Terminals below this fraction of the best relevance are "weak": kept
# only when they join the other tables directly.
STRONG_RELEVANCE = 0.6
# Tables the question names outright that relevance alone left out.
MAX_NAMED_TABLES = 3


def _select_terminals(
    ranking: List[Dict[str, Any]],
    hints: List[Dict[str, Any]],
    top_k: int,
    relative_cutoff: float,
) -> Dict[str, Dict[str, float]]:
    """
    Tables to show the LLM. The cutoff is relative to the best
    retrieval relevance; tables holding a stored value the question
    mentions are kept as well. Returns {table: {"relevance", "score"}}
    where score (relevance + value boost) sets the order.
    """
    base: Dict[str, float] = {item["table"]: item["relevance"] for item in ranking}
    order = {item["table"]: position for position, item in enumerate(ranking)}

    tables_per_value: Dict[str, set] = defaultdict(set)
    for hint in hints:
        tables_per_value[hint["value"]].add(hint["table"])
    boost: Dict[str, float] = defaultdict(float)
    seen = set()
    for hint in hints:
        if (hint["table"], hint["value"]) in seen:
            continue
        seen.add((hint["table"], hint["value"]))
        boost[hint["table"]] += VALUE_HINT_BOOST * hint["score"] / len(tables_per_value[hint["value"]])

    best = max(base.values(), default=0.0)
    candidates = {table for table, value in base.items() if best > 0 and value >= relative_cutoff * best}
    candidates |= set(boost)
    if not candidates:
        return {}

    def score(table: str) -> float:
        return base.get(table, 0.0) + boost.get(table, 0.0)

    ranked = sorted(candidates, key=lambda table: (-score(table), order.get(table, len(order))))[:top_k]
    return {
        table: {"relevance": round(base.get(table, 0.0), 4), "score": round(score(table), 4), "value_hint": table in boost}
        for table in ranked
    }


def _named_tables(
    schema: Dict[str, Any],
    question: str,
    ranking: List[Dict[str, Any]],
    chosen: Sequence[str],
) -> List[str]:
    """
    On a big schema, tables full of matching columns (unit price,
    quantity, ...) can crowd out a small table the question names
    outright ("which product *category* ..."). For each question word
    that is part of some table's name but of no chosen table's name,
    add the best-ranked table whose name has it.
    """
    index = get_schema_index(schema)
    name_words = {name: set(index.splitter.tokens(name)) for name in index.table_names}
    covered = set().union(*(name_words.get(name, set()) for name in chosen)) if chosen else set()
    added: List[str] = []
    for word in dict.fromkeys(tokenize(question)):
        if word in covered:
            continue
        for item in ranking:
            if word in name_words.get(item["table"], ()):
                if item["table"] not in chosen and item["table"] not in added:
                    added.append(item["table"])
                    covered |= name_words[item["table"]]
                break
        if len(added) >= MAX_NAMED_TABLES:
            break
    return added


def _prune_columns(
    table: Dict[str, Any],
    keep: set,
    matched: List[str],
    max_columns: int,
) -> Dict[str, Any]:
    columns = table.get("columns", [])
    if len(columns) <= max_columns:
        return {"columns": columns, "omitted_columns": 0}
    wanted = set(keep) | set(table.get("primary_key") or [])
    for name in matched:
        if len(wanted) >= max_columns:
            break
        wanted.add(name)
    kept = [column for column in columns if column.get("column_name") in wanted]
    return {"columns": kept, "omitted_columns": len(columns) - len(kept)}


MAX_DESCRIPTION_CHARS = 160


def _short(text: str) -> str:
    """Database comments can be long; the prompt only needs the gist."""
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_DESCRIPTION_CHARS else text[: MAX_DESCRIPTION_CHARS - 3].rstrip() + "..."


def _column_view(column: Dict[str, Any], with_values: bool = True) -> Dict[str, Any]:
    view = {"column_name": column.get("column_name"), "data_type": column.get("data_type")}
    if column.get("description"):
        view["description"] = _short(column["description"])
    values = (column.get("values") or []) if with_values else []
    if values:
        view["stored_values"] = values[:MAX_VALUES_PER_COLUMN]
        if len(values) > MAX_VALUES_PER_COLUMN:
            view["more_values"] = len(values) - MAX_VALUES_PER_COLUMN
    return view


def build_schema_focus(
    schema: Dict[str, Any],
    queries: Sequence[str],
    question: str,
    top_k: int = 8,
    relative_cutoff: float = 0.35,
    wide_table_columns: int = 40,
) -> Dict[str, Any]:
    """
    Returns {"retrieved_schema", "graph_context", "focused_schema"}.
    """
    all_tables = [table for table in schema.get("tables", []) if table.get("table_name")]
    by_name = {table["table_name"]: table for table in all_tables}

    ranking = retrieve_for_queries(schema, queries, top_k=max(len(all_tables), 1))
    texts = [question] + [query for query in queries[:1] if query and query != question]
    hints = match_values(schema, texts)

    terminal_scores = _select_terminals(ranking, hints, top_k, relative_cutoff)
    if terminal_scores:
        relevance = {item["table"]: item["relevance"] for item in ranking}
        for name in _named_tables(schema, question, ranking, list(terminal_scores)):
            terminal_scores[name] = {
                "relevance": round(relevance.get(name, 0.0), 4), "score": 0.0,
                "value_hint": False, "named": True,
            }
    terminals = list(terminal_scores)

    if not terminals:
        # Nothing matched (e.g. an unusual vocabulary without the
        # embedding model): fall back to the full schema rather than
        # guessing a subset.
        return {
            "retrieved_schema": ranking[:top_k],
            "graph_context": {
                "relevant_tables": [table["table_name"] for table in all_tables],
                "retrieved_tables": [],
                "connector_tables": [],
                "expanded_tables": [table["table_name"] for table in all_tables],
                "relationships": schema.get("relationships", []),
                "join_paths": [],
                "value_hints": hints,
                "unreachable_tables": [],
                "fallback": "full_schema",
            },
            "focused_schema": schema,
        }

    best = max(info["relevance"] for info in terminal_scores.values())
    weak = {
        table for table, info in terminal_scores.items()
        if not info["value_hint"] and not info.get("named") and info["relevance"] < STRONG_RELEVANCE * best
    }
    plan = plan_join_paths(schema, terminals, weak_terminals=weak)
    terminals = [table for table in terminals if table not in plan["dropped"]]
    selected = plan["tables"]
    relationships = relationships_among(schema, selected)

    join_columns: Dict[str, set] = defaultdict(set)
    for relation in relationships:
        join_columns[relation["source_table"]].add(relation["source_column"])
        join_columns[relation["target_table"]].add(relation["target_column"])
    hint_columns: Dict[str, set] = defaultdict(set)
    for hint in hints:
        hint_columns[hint["table"]].add(hint["column"])
    matched_columns = {
        item["table"]: [column["column"] for column in item.get("matched_columns", [])]
        for item in ranking
    }

    focused_tables = []
    for name in selected:
        table = by_name[name]
        is_connector = name in plan["connectors"]
        pruned = _prune_columns(
            table,
            keep=join_columns[name] | hint_columns[name],
            matched=matched_columns.get(name, []),
            max_columns=min(wide_table_columns, CONNECTOR_TABLE_COLUMNS) if is_connector else wide_table_columns,
        )
        entry = {
            "table_name": name,
            "role": "connector" if is_connector else "retrieved",
            **({"description": _short(table["description"])} if table.get("description") else {}),
            "primary_key": table.get("primary_key") or [],
            "columns": [_column_view(column, with_values=not is_connector) for column in pruned["columns"]],
        }
        if pruned["omitted_columns"]:
            entry["omitted_columns"] = pruned["omitted_columns"]
        focused_tables.append(entry)

    other_tables = [table["table_name"] for table in all_tables if table["table_name"] not in set(selected)]
    join_paths = [join["condition"] for join in plan["joins"]]

    focused_schema = {
        "tables": focused_tables,
        "relationships": relationships,
        "join_paths": join_paths,
        "value_hints": hints,
        "other_tables_not_shown": other_tables[:MAX_OTHER_TABLE_NAMES],
    }

    graph_context = {
        "relevant_tables": selected,
        "retrieved_tables": terminals,
        "connector_tables": plan["connectors"],
        "expanded_tables": selected,
        "relationships": relationships,
        "join_paths": join_paths,
        "value_hints": hints,
        "unreachable_tables": plan["unreachable"],
        "table_relevance": {table: terminal_scores[table] for table in terminals},
        "dropped_tables": plan["dropped"],
        "total_tables": len(all_tables),
    }
    return {
        "retrieved_schema": ranking[:top_k],
        "graph_context": graph_context,
        "focused_schema": focused_schema,
    }


def focus_notes(graph_context: Dict[str, Any]) -> str:
    """
    Deterministic facts for the SQL generator: join conditions from
    the relationship graph and stored values matching the question.
    """
    lines: List[str] = []
    join_paths = graph_context.get("join_paths") or []
    if join_paths:
        lines.append("JOIN CONDITIONS (from database relationships; use these when joining these tables):")
        lines += [f"- {condition}" for condition in join_paths]
    hints = graph_context.get("value_hints") or []
    if hints:
        if lines:
            lines.append("")
        lines.append(
            "STORED VALUES MATCHING WORDS IN THE QUESTION (if the question filters on one of "
            "these, use the stored literal exactly as shown; ignore hints that are not about a filter):"
        )
        for hint in hints:
            literal = str(hint["value"]).replace("'", "''")
            lines.append(
                f"- {hint['table']}.{hint['column']} = '{literal}'"
                f"  (question text: \"{hint['matched_text']}\", {hint['match']} match)"
            )
    return "\n".join(lines)


def prioritize_schema(schema: Dict[str, Any], tables_first: Sequence[str]) -> Dict[str, Any]:
    """Full schema with the focused tables first (for size-capped prompts)."""
    priority = {name: position for position, name in enumerate(tables_first)}
    tables = sorted(
        schema.get("tables", []),
        key=lambda table: priority.get(table.get("table_name"), len(priority)),
    )
    return {**schema, "tables": tables}
