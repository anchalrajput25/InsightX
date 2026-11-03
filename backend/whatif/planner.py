# backend/whatif/planner.py
#
# "What if prices rise 10% and we drop the 2 worst sellers?" -> scenario
# spec (JSON). This is the only LLM step: the model names the metric,
# the columns and the levers; spec.verify_scenario() then checks every
# part against the real schema and builder.py writes all SQL itself.

import re
from typing import Any, Dict, Optional, Tuple

from backend.investigation.planner import _parse, describe_for_planning
from backend.llm_service import get_llm
from backend.whatif.spec import WhatIfError

llm = get_llm(job="whatif_plan")

WHATIF_RE = re.compile(
    r"\b(what\s+if|what\s+would|what\s+happens|suppose|supposing|imagine|scenario|if\s+(?:we|i|the|our|prices?|sales)"
    r"|agar|maan\s+lo|forecast|predict\w*|projection|project(?:ed)?|next\s+(?:\d+\s+)?(?:day|week|month|quarter|year)s?"
    r"|sensitivity)\b",
    flags=re.IGNORECASE,
)
FORECAST_RE = re.compile(
    r"\b(forecast\w*|predict\w*|projection|project(?:ed)?|future|upcoming|coming|next\s+(?:\d+\s+)?"
    r"(?:day|week|month|quarter|year)s?|agle|aane\s+wale)\b",
    flags=re.IGNORECASE,
)

PROMPT = """
You turn a what-if or forecast question about a database into a scenario.
Use ONLY the tables and columns below. Do not write SQL queries.

Return ONLY one JSON object:
{{
  "metric_name": "<short name, e.g. revenue>",
  "metric_expression": "<ONE SQL aggregate over table.column references, e.g. SUM(t.price * t.quantity), COUNT(*), AVG(t.score)>",
  "fact_table": "<the table whose rows the metric aggregates>",
  "time_column": "<table.column with the date of each fact row, or null>",
  "grain": "day|week|month|quarter|year",
  "filters": "<SQL condition the question restricts the data to, or null>",
  "breakdown": "<table.column category to show the impact by, or null>",
  "levers": [ <changes, see below> ],
  "forecast_periods": <number of future periods to forecast, 0 if no forecast is asked>
}}

Levers (one per change; several changes = several levers):
- {{"type": "scale", "column": "t.price", "percent": 10}}                 price up 10%
- {{"type": "scale", "column": "t.price", "percent": 10, "elasticity": -1.5}}  only if the user says how demand reacts
- {{"type": "shift", "column": "t.days", "amount": -2}}                   a number 2 lower
- {{"type": "shift", "column": null, "amount": -2}}                       the metric's own per-row value 2 lower
                                                                         (e.g. delivery time = AVG(delivered - purchased))
- {{"type": "set", "column": "t.discount", "value": 0}}                   a column set to a value
- {{"type": "cap", "column": "t.discount", "limit": 0.1}}                 at most 0.1 (floor = at least)
- {{"type": "volume", "percent": 20}}                                     20% more rows (orders, customers)
- {{"type": "exclude", "where": {{"column": "t.city", "op": "=", "values": ["sao paulo"]}}}}   those rows disappear
- {{"type": "exclude", "where": {{"column": "s.seller_id", "rank": "bottom", "n": 2}}}}        the 2 worst sellers
Any lever can take "where" to apply only to some rows, e.g.
  {{"type": "scale", "column": "t.price", "percent": 5, "where": {{"column": "c.category", "op": "=", "values": ["toys"]}}}}
"where" ops: =, !=, in, not in, >, >=, <, <=. Values exactly as the user wrote them.

Rules: write columns as table.column; no subqueries; percent is a number (10 means +10%,
-15 means -15%); a pure forecast question has "levers": []; "next quarter" with monthly
data is 3 periods; prefer month unless the question implies otherwise.

QUESTION: {question}

SCHEMA:
{schema}
"""


def looks_like_whatif(question: str) -> bool:
    """'What if prices rise 10%?' / 'forecast next quarter' (routing hint for the UI/API)."""
    return bool(WHATIF_RE.search(question or ""))


def plan_whatif(question: str, schema: Dict[str, Any], model=None) -> Tuple[Dict[str, Any], str]:
    """(scenario spec, raw LLM output)."""
    question = (question or "").strip()
    if not question:
        raise WhatIfError("Question cannot be empty.")
    prompt = PROMPT.format(question=question, schema=describe_for_planning(schema, question))
    response = (model or llm).invoke(prompt)
    raw = str(getattr(response, "content", response))
    spec: Optional[Dict[str, Any]] = _parse(raw)
    if spec is None:
        raise WhatIfError("Could not turn the question into a scenario. Try naming the number and the change, "
                          "for example 'what if prices rise 10%'.")
    if not FORECAST_RE.search(question):
        spec["forecast_periods"] = 0  # small models add a forecast nobody asked for
    return spec, raw
