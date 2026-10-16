import heapq
from typing import Any, Dict, List, Optional, Tuple


def retrieve_relationships(
    schema: Dict[str, Any],
    relevant_tables: List[str]
) -> List[Dict[str, Any]]:
    """
    Retrieve graph relationships for relevant tables.

    Supports:
    - Actual PostgreSQL foreign keys
    - Inferred relationships
    """

    relationships = []

    relevant_set = set(
        relevant_tables
    )

    for relation in schema.get(
        "relationships",
        []
    ):

        source_table = relation.get(
            "source_table"
        )

        target_table = relation.get(
            "target_table"
        )

        if (
            source_table in relevant_set
            or
            target_table in relevant_set
        ):

            relationships.append({
                "source_table": source_table,
                "source_column": relation.get(
                    "source_column"
                ),
                "target_table": target_table,
                "target_column": relation.get(
                    "target_column"
                ),
                "type": relation.get(
                    "type",
                    "inferred"
                ),
                "confidence": relation.get(
                    "confidence",
                    "unknown"
                )
            })

    return relationships


def expand_related_tables(
    schema: Dict[str, Any],
    relevant_tables: List[str]
) -> List[str]:
    """
    Graph expansion.

    If one relevant table is connected to another table,
    include the connected table.

    This is important because semantic retrieval may find
    only one side of a relationship.
    """

    expanded_tables = set(
        relevant_tables
    )

    changed = True

    while changed:

        changed = False

        for relation in schema.get(
            "relationships",
            []
        ):

            source = relation.get(
                "source_table"
            )

            target = relation.get(
                "target_table"
            )

            if (
                source in expanded_tables
                and target not in expanded_tables
            ):
                expanded_tables.add(target)
                changed = True

            elif (
                target in expanded_tables
                and source not in expanded_tables
            ):
                expanded_tables.add(source)
                changed = True

    return list(expanded_tables)

# ============================================================
# JOIN-PATH PLANNING
# ============================================================
#
# expand_related_tables() adds every table reachable through any
# relationship, which on a connected database means the WHOLE schema.
# plan_join_paths() instead connects only the tables retrieval chose
# (the terminals), adding the fewest intermediate tables needed: an
# approximate Steiner tree built from shortest paths
# (Takahashi-Matsuyama), over a graph where declared foreign keys are
# cheaper than inferred relationships.

EDGE_COST = {"foreign_key": 1.0, "high": 1.15, "medium": 1.4}
DEFAULT_EDGE_COST = 1.6


def _edge_cost(relation: Dict[str, Any]) -> float:
    if relation.get("type") == "foreign_key":
        return EDGE_COST["foreign_key"]
    return EDGE_COST.get(str(relation.get("confidence", "")).lower(), DEFAULT_EDGE_COST)


def _join_graph(schema: Dict[str, Any]) -> Dict[str, Dict[str, Tuple[float, Dict[str, Any]]]]:
    """table -> neighbour -> (cost, cheapest relationship)"""
    graph: Dict[str, Dict[str, Tuple[float, Dict[str, Any]]]] = {}
    for table in schema.get("tables", []):
        graph.setdefault(table.get("table_name"), {})
    for relation in schema.get("relationships", []):
        source, target = relation.get("source_table"), relation.get("target_table")
        if not source or not target or source == target:
            continue
        cost = _edge_cost(relation)
        for a, b in ((source, target), (target, source)):
            current = graph.setdefault(a, {}).get(b)
            if current is None or cost < current[0]:
                graph[a][b] = (cost, relation)
    return graph


def _shortest_path_to_tree(graph, tree: set, target: str) -> Optional[List[str]]:
    """Cheapest path from any tree node to target (Dijkstra)."""
    distances = {node: 0.0 for node in tree}
    previous: Dict[str, str] = {}
    queue = [(0.0, node) for node in sorted(tree)]
    heapq.heapify(queue)
    while queue:
        distance, node = heapq.heappop(queue)
        if node == target:
            path = [node]
            while path[-1] in previous:
                path.append(previous[path[-1]])
            return list(reversed(path))
        if distance > distances.get(node, float("inf")):
            continue
        for neighbour, (cost, _) in sorted(graph.get(node, {}).items()):
            candidate = distance + cost
            if candidate < distances.get(neighbour, float("inf")):
                distances[neighbour] = candidate
                previous[neighbour] = node
                heapq.heappush(queue, (candidate, neighbour))
    return None


def _join(relation: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "left_table": relation.get("source_table"),
        "left_column": relation.get("source_column"),
        "right_table": relation.get("target_table"),
        "right_column": relation.get("target_column"),
        "type": relation.get("type", "inferred"),
        "confidence": relation.get("confidence", "unknown"),
        "condition": (
            f"{relation.get('source_table')}.{relation.get('source_column')} = "
            f"{relation.get('target_table')}.{relation.get('target_column')}"
        ),
    }


def plan_join_paths(
    schema: Dict[str, Any],
    terminals: List[str],
    max_connectors: int = 6,
    weak_terminals: Optional[set] = None,
) -> Dict[str, Any]:
    """
    Connect the terminal tables (most relevant first) with the
    cheapest set of joins.

    Returns:
        tables       terminals + connector tables, in plan order
        connectors   tables added only to connect terminals
        joins        join conditions on the plan (tree edges)
        unreachable  terminals with no relationship path to the rest
        dropped      weak terminals left out because joining them would
                     need extra connector tables

    Weak terminals (borderline retrieval matches) are only kept when
    they join the plan directly; they never pull in connector tables.
    """
    weak_terminals = weak_terminals or set()
    graph = _join_graph(schema)
    ordered = [table for table in dict.fromkeys(terminals) if table in graph]

    tree: List[str] = []
    in_tree: set = set()
    joins: List[Dict[str, Any]] = []
    unreachable: List[str] = []
    connectors: List[str] = []
    dropped: List[str] = []

    for terminal in ordered:
        if terminal in in_tree:
            continue
        if not tree:
            tree.append(terminal)
            in_tree.add(terminal)
            continue
        path = _shortest_path_to_tree(graph, in_tree, terminal)
        added_connectors = [node for node in (path or [])[1:-1] if node not in in_tree]
        if terminal in weak_terminals and (path is None or added_connectors):
            dropped.append(terminal)
            continue
        if path is None or len(connectors) + len(added_connectors) > max_connectors:
            unreachable.append(terminal)
            tree.append(terminal)
            in_tree.add(terminal)
            continue
        for left, right in zip(path, path[1:]):
            joins.append(_join(graph[left][right][1]))
        for node in path[1:]:
            if node not in in_tree:
                tree.append(node)
                in_tree.add(node)
        connectors += added_connectors

    chosen = set(ordered) - set(dropped)
    return {
        "tables": tree,
        # a table reached as a connector that is also a terminal is a terminal
        "connectors": [node for node in connectors if node not in chosen],
        "joins": joins,
        "unreachable": unreachable,
        "dropped": dropped,
    }


def relationships_among(schema: Dict[str, Any], tables: List[str]) -> List[Dict[str, Any]]:
    """Every relationship whose two tables are both in `tables`."""
    selected = set(tables)
    return [
        relation
        for relation in schema.get("relationships", [])
        if relation.get("source_table") in selected and relation.get("target_table") in selected
    ]
