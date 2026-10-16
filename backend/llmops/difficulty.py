# backend/llmops/difficulty.py
#
# How hard a question looks, before any AI runs: easy, medium or hard.
# Used by smart routing (hard questions favour accurate models, easy
# ones favour fast, cheap ones) and to slice the quality numbers.
#
# A plain word-based score, no AI: comparisons, time windows, ratios,
# rankings within groups and several entities push a question up.

import re
from typing import Dict, List

SIGNALS = [
    # (name, pattern, points)
    ("compare", r"\b(compare|versus|vs\.?|difference between|relative to|against)\b", 2),
    ("ratio", r"\b(ratio|percent(age)?|share|proportion|rate|conversion|per\s+\w+)\b", 1),
    ("growth", r"\b(growth|change|increase|decrease|trend|month over month|year over year|yoy|mom)\b", 2),
    ("window", r"\b(running|cumulative|rolling|moving average|rank(ed|ing)?|within each|per each|for each)\b", 2),
    ("time", r"\b(19|20)\d\d\b|\b(month(ly)?|week(ly)?|daily|quarter(ly)?|year(ly)?|last \d+|since|between)\b", 1),
    ("aggregate", r"\b(average|avg|mean|median|sum|total|count|how many|number of|max(imum)?|min(imum)?)\b", 1),
    ("top", r"\b(top|bottom|highest|lowest|most|least|best|worst)\b", 1),
    ("negation", r"\b(never|without|not|no\s+\w+|excluding|except|did not|didn't)\b", 2),
    ("nested", r"\b(more than the average|above average|below average|than average|at least \d+ times)\b", 3),
    ("why", r"\b(why|cause|reason|explain)\b", 2),
    ("and", r"\band\b", 1),
]


def classify(question: str) -> Dict[str, object]:
    text = (question or "").lower()
    hits: List[str] = []
    score = 0
    for name, pattern, points in SIGNALS:
        found = len(re.findall(pattern, text))
        if found:
            hits.append(name)
            score += points * min(found, 2)
    words = len(text.split())
    if words > 18:
        score += 1
    if words > 32:
        score += 2
    level = "easy" if score <= 2 else "medium" if score <= 6 else "hard"
    return {"level": level, "score": score, "signals": hits, "words": words}


def level(question: str) -> str:
    return str(classify(question)["level"])
