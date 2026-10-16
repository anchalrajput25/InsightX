# backend/evaluation/golden.py
#
# Golden set: questions with the SQL that answers them correctly, for
# one database. YAML format:
#
#   name: my_database
#   description: optional text
#   cases:
#     - id: top_category_revenue          # unique
#       question: Which category has the highest revenue?
#       expected_sql: |
#         SELECT ... (read-only, no ROUND unless the question asks)
#       tags: [join, aggregation, ranking]  # free-form, used for breakdowns
#       order_matters: false                # true for "top N in order"
#       thread: optional-group              # cases sharing a thread run in
#                                           # order in one conversation
#                                           # (follow-up questions)
#       verified: true                      # false = unreviewed candidate
#       difficulty: medium                  # easy | medium | hard | extra
#       expect: answer                      # see EXPECTATIONS below
#
# Expected behaviour (expect):
#   answer     the agent must return the same data as expected_sql
#   no_answer  the data cannot answer the question (missing column,
#              outside the database): the agent must not return a
#              confident answer (it should clarify, fail, or flag low
#              trust). expected_sql is not used.
#   no_write   the question asks to change data: nothing may be
#              modified (table row counts are compared before and
#              after) and no write statement may be produced.
#
# Difficulty follows the Spider convention: easy (one table, at most
# one filter/aggregate), medium (join or grouping), hard (several
# joins, nested query, temporal logic), extra (combinations: window
# functions, ratios over joins, multi-step reasoning).
#
# Only verified cases are evaluated unless asked otherwise.

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

EXPECTATIONS = ("answer", "no_answer", "no_write")
DIFFICULTIES = ("easy", "medium", "hard", "extra")


class GoldenSetError(ValueError):
    """The golden-set file is malformed."""


@dataclass
class GoldenCase:
    id: str
    question: str
    expected_sql: str = ""
    tags: List[str] = field(default_factory=list)
    order_matters: bool = False
    thread: Optional[str] = None
    verified: bool = True
    notes: str = ""
    difficulty: str = "medium"
    expect: str = "answer"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        defaults = {"order_matters": False, "verified": True, "difficulty": "medium", "expect": "answer"}
        return {key: value for key, value in data.items()
                if value not in (None, "", []) and defaults.get(key, object()) != value}


@dataclass
class GoldenSet:
    name: str
    cases: List[GoldenCase]
    description: str = ""

    def selected(
        self,
        include_unverified: bool = False,
        tags: Optional[List[str]] = None,
        difficulties: Optional[List[str]] = None,
        ids: Optional[List[str]] = None,
    ) -> List[GoldenCase]:
        cases = [case for case in self.cases if include_unverified or case.verified]
        if tags:
            wanted = set(tags)
            cases = [case for case in cases if wanted & set(case.tags)]
        if difficulties:
            cases = [case for case in cases if case.difficulty in difficulties]
        if ids:
            wanted_ids = set(ids)
            cases = [case for case in cases if case.id in wanted_ids]
        return cases


def load_golden_set(path: Path) -> GoldenSet:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise GoldenSetError(f"{path}: invalid YAML: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise GoldenSetError(f"{path}: expected a mapping with a 'cases' list.")

    cases: List[GoldenCase] = []
    seen = set()
    for index, raw in enumerate(data["cases"], start=1):
        if not isinstance(raw, dict):
            raise GoldenSetError(f"{path}: case #{index} is not a mapping.")
        expect = str(raw.get("expect") or "answer").strip()
        if expect not in EXPECTATIONS:
            raise GoldenSetError(f"{path}: case #{index}: expect must be one of {', '.join(EXPECTATIONS)}.")
        difficulty = str(raw.get("difficulty") or "medium").strip()
        if difficulty not in DIFFICULTIES:
            raise GoldenSetError(f"{path}: case #{index}: difficulty must be one of {', '.join(DIFFICULTIES)}.")
        required = ("id", "question", "expected_sql") if expect == "answer" else ("id", "question")
        missing = [key for key in required if not str(raw.get(key) or "").strip()]
        if missing:
            raise GoldenSetError(f"{path}: case #{index} is missing {', '.join(missing)}.")
        case_id = str(raw["id"]).strip()
        if case_id in seen:
            raise GoldenSetError(f"{path}: duplicate case id '{case_id}'.")
        seen.add(case_id)
        cases.append(GoldenCase(
            id=case_id,
            question=str(raw["question"]).strip(),
            expected_sql=str(raw.get("expected_sql") or "").strip(),
            tags=[str(tag) for tag in raw.get("tags") or []],
            order_matters=bool(raw.get("order_matters", False)),
            thread=str(raw["thread"]).strip() if raw.get("thread") else None,
            verified=bool(raw.get("verified", True)),
            notes=str(raw.get("notes") or ""),
            difficulty=difficulty,
            expect=expect,
        ))

    return GoldenSet(
        name=str(data.get("name") or path.stem),
        description=str(data.get("description") or ""),
        cases=cases,
    )


def save_golden_set(golden: GoldenSet, path: Path) -> None:
    payload = {
        "name": golden.name,
        "description": golden.description,
        "cases": [case.to_dict() for case in golden.cases],
    }
    Path(path).write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
