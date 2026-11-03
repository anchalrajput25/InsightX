# evals/spider_hardness.py
#
# Difficulty of a SQL query by the Spider benchmark rules
# (easy / medium / hard / extra), so our results can be read the same
# way as published Spider results.
#
# Follows eval_hardness() in Spider's evaluation.py:
#   component1 = WHERE, GROUP BY, ORDER BY, LIMIT, each extra table
#                (join), each OR, each LIKE
#   component2 = nested queries, UNION / INTERSECT / EXCEPT
#   others     = more than one aggregate, more than one selected
#                column, more than one WHERE condition, more than one
#                GROUP BY column
# Parsed with sqlglot instead of Spider's own parser, so a few
# borderline queries can land one level away from the official label.

import sqlglot
from sqlglot import exp


def _count_conditions(node) -> int:
    if node is None:
        return 0
    return len(list(node.find_all(exp.And))) + len(list(node.find_all(exp.Or))) + 1


def _top_select(tree):
    while isinstance(tree, (exp.Union, exp.Intersect, exp.Except)):
        tree = tree.left
    return tree


def hardness(sql: str) -> str:
    tree = sqlglot.parse_one(sql, read="sqlite")

    component2 = 0
    if isinstance(tree, (exp.Union, exp.Intersect, exp.Except)):
        component2 += 1
    select = _top_select(tree)
    nested = [s for s in select.find_all(exp.Select) if s is not select]
    component2 += len(nested)

    def own(node_type):
        """Nodes of this type in the top query, not inside a subquery."""
        found = []
        for node in select.find_all(node_type):
            parent = node.parent
            inside = False
            while parent is not None and parent is not select:
                if isinstance(parent, exp.Select):
                    inside = True
                    break
                parent = parent.parent
            if not inside:
                found.append(node)
        return found

    where = select.args.get("where")
    group = select.args.get("group")
    order = select.args.get("order")
    limit = select.args.get("limit")
    joins = select.args.get("joins") or []

    component1 = 0
    component1 += 1 if where else 0
    component1 += 1 if group else 0
    component1 += 1 if order else 0
    component1 += 1 if limit else 0
    component1 += len(joins)
    component1 += len([n for n in own(exp.Or)])
    component1 += len([n for n in own(exp.Like)])

    others = 0
    aggregates = [n for n in own(exp.AggFunc)]
    if len(aggregates) > 1:
        others += 1
    if len(select.expressions) > 1:
        others += 1
    if where is not None and _count_conditions(where.this) > 1:
        others += 1
    if group is not None and len(group.expressions) > 1:
        others += 1

    if component1 <= 1 and others == 0 and component2 == 0:
        return "easy"
    if (others <= 2 and component1 <= 1 and component2 == 0) or \
            (component1 <= 2 and others < 2 and component2 == 0):
        return "medium"
    if (others > 2 and component1 <= 2 and component2 == 0) or \
            (2 < component1 <= 3 and others <= 2 and component2 == 0) or \
            (component1 <= 1 and others == 0 and component2 <= 1):
        return "hard"
    return "extra"


if __name__ == "__main__":
    tests = {
        "SELECT count(*) FROM singer": "easy",
        "SELECT name, country, age FROM singer ORDER BY age DESC": "medium",
        "SELECT T2.name FROM concert AS T1 JOIN stadium AS T2 ON T1.stadium_id = T2.stadium_id GROUP BY T1.stadium_id ORDER BY count(*) DESC LIMIT 1": "extra",
        "SELECT name FROM stadium WHERE stadium_id NOT IN (SELECT stadium_id FROM concert)": "hard",
    }
    for sql, want in tests.items():
        print(hardness(sql), "(Spider says", want + ")", sql[:60])
