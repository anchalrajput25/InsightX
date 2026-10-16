"""
Generic tests for the Query Rewriter.

Offline tests (always run) drive rewrite_query() with a scripted fake
LLM and check the mechanics: parsing, echo detection, the single retry,
SQL rejection, fallbacks and conversation-history handling.

Live tests (INSIGHTX_LIVE_LLM=1) call the configured LLM with questions
from many query categories and domains, and check generic properties of
the output instead of exact strings.

Run from the project root:
    python -m unittest backend.text_to_sql.test_query_rewriter_generic -v
    INSIGHTX_LIVE_LLM=1 python -m unittest backend.text_to_sql.test_query_rewriter_generic -v
"""

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

import backend.text_to_sql.query_rewriter as qr  # noqa: E402


# ============================================================
# QUERY CATEGORIES
# ============================================================
#
# Each case: category, latest question, optional history, and
# generic expectations used by the live tests:
#   include_any: list of groups; each group needs one match
#   exclude:     strings that must not appear
#
# The questions span several domains on purpose.

def _history(*turns):
    roles = ["user", "assistant"]
    return [{"role": roles[i % 2], "content": text} for i, text in enumerate(turns)]


CASES = [
    {
        "category": "aggregation",
        "question": "how many orders were delivered late?",
        "include_any": [["count", "number", "how many"], ["order"], ["late"]],
    },
    {
        "category": "ranking with limit",
        "question": "top 5 sellers by revenue in 2018",
        "include_any": [["5", "five"], ["seller"], ["revenue"], ["2018"], ["highest", "top", "most"]],
    },
    {
        "category": "comparison",
        "question": "compare average ticket size between credit card and boleto payments",
        "include_any": [["average"], ["credit card"], ["boleto"], ["compar", "between", "versus", "vs"]],
    },
    {
        "category": "multi-condition filter",
        "question": "customers from RJ who placed more than 3 orders and never left a review",
        "include_any": [["RJ"], ["3", "three"], ["more than", "greater than", "over", "exceed"],
                        ["review"], ["never", "no ", "not", "without"]],
    },
    {
        "category": "time-based",
        "question": "monthly signups in the first half of 2017",
        "include_any": [["2017"], ["month"], ["signup", "sign-up", "sign up", "registration"],
                        ["first half", "january", "jan", "h1"]],
    },
    {
        "category": "trend",
        "question": "how has the average delivery time changed over the years?",
        "include_any": [["average"], ["delivery time"], ["year"], ["trend", "chang", "over"]],
    },
    {
        "category": "percentage",
        "question": "what share of students passed the final exam?",
        "include_any": [["percent", "share", "proportion", "ratio", "%"], ["student"], ["pass"], ["final exam"]],
    },
    {
        "category": "average",
        "question": "average salary per department",
        "include_any": [["average"], ["salary"], ["department"]],
    },
    {
        "category": "minimum",
        "question": "which warehouse has the lowest stock of item X-200?",
        "include_any": [["warehouse"], ["lowest", "least", "minimum", "smallest"], ["X-200"]],
    },
    {
        "category": "maximum (implicit superlative)",
        "question": "biggest order last year?",
        "include_any": [["order"], ["highest", "largest", "biggest", "maximum"], ["last year", "previous year"]],
    },
    {
        "category": "already clear",
        "question": "Count the number of patients admitted in March 2021.",
        "include_any": [["patient"], ["admitted", "admission"], ["March 2021"]],
    },
    {
        "category": "follow-up: new period",
        "history": _history(
            "Which product category had the highest sales in 2017?",
            "Health & beauty had the highest sales in 2017.",
        ),
        "question": "what about 2018?",
        "include_any": [["2018"], ["categor"], ["sales"], ["highest", "most", "top", "largest"]],
        "exclude": ["2017"],
    },
    {
        "category": "follow-up: new filter value",
        "history": _history(
            "How many customers are in Delhi?",
            "There are 1,240 customers in Delhi.",
        ),
        "question": "and for Mumbai?",
        "include_any": [["Mumbai"], ["customer"], ["count", "number", "how many"]],
        "exclude": ["Delhi"],
    },
    {
        "category": "follow-up: relative period",
        "history": _history(
            "Show total refunds this month.",
            "Total refunds this month were $4,300.",
        ),
        "question": "what about last month?",
        "include_any": [["refund"], ["last month", "previous month", "prior month"], ["total", "sum"]],
    },
    {
        "category": "follow-up: reference to prior answer",
        "history": _history(
            "Which store had the highest revenue last quarter?",
            "The Downtown store had the highest revenue last quarter.",
        ),
        "question": "how many employees work in that store?",
        "include_any": [["employee"], ["Downtown"], ["count", "number", "how many"]],
        "exclude": ["that store"],
    },
    {
        "category": "conversational (non-analytical)",
        "question": "thanks, that's helpful!",
        "include_any": [["thank"]],
    },
]


# ============================================================
# FAKE LLM
# ============================================================

class ScriptedLLM:
    """Returns scripted outputs in order and records every call."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return AIMessage(content=output)


def run_with(llm, question, context=None):
    with patch.object(qr, "llm", llm):
        return qr.rewrite_query(question, context)


def human_text(messages):
    return "\n".join(str(m.content) for m in messages if isinstance(m, HumanMessage))


# ============================================================
# OFFLINE TESTS
# ============================================================

class RewriteFlowTests(unittest.TestCase):

    def test_normalized_rewrite_returned_without_retry(self):
        llm = ScriptedLLM("CANONICAL: no\nREWRITTEN_QUERY: Find the <group> with the highest <measure>.")
        self.assertEqual(run_with(llm, "which <group> has the most <measure>?"),
                         "Find the <group> with the highest <measure>.")
        self.assertEqual(len(llm.calls), 1)

    def test_echo_declared_canonical_is_accepted_without_retry(self):
        question = "Count the number of <items> in <period>."
        llm = ScriptedLLM(f"CANONICAL: yes\nREWRITTEN_QUERY: {question}")
        self.assertEqual(run_with(llm, question), question)
        self.assertEqual(len(llm.calls), 1)

    def test_echo_not_declared_canonical_retries_once(self):
        question = "most <items> per <group>?"
        llm = ScriptedLLM(
            f"CANONICAL: no\nREWRITTEN_QUERY: {question}",
            "CANONICAL: no\nREWRITTEN_QUERY: Find the <group> with the highest number of <items>.",
        )
        self.assertEqual(run_with(llm, question), "Find the <group> with the highest number of <items>.")
        self.assertEqual(len(llm.calls), 2)
        retry_messages = llm.calls[1]
        self.assertIsInstance(retry_messages[-2], AIMessage)
        self.assertIn("did not normalize", retry_messages[-1].content)

    def test_unlabeled_echo_retries(self):
        question = "<measure> by <group>"
        llm = ScriptedLLM(question, "REWRITTEN_QUERY: Calculate the total <measure> per <group>.")
        self.assertEqual(run_with(llm, question), "Calculate the total <measure> per <group>.")
        self.assertEqual(len(llm.calls), 2)

    def test_echo_ignores_case_and_punctuation(self):
        question = "Total <measure> per <group>?"
        llm = ScriptedLLM("REWRITTEN_QUERY: total <MEASURE> per <group>.", f"REWRITTEN_QUERY: {question}")
        run_with(llm, question)
        self.assertEqual(len(llm.calls), 2)

    def test_retry_that_echoes_again_is_accepted(self):
        question = "Calculate the average <measure>."
        llm = ScriptedLLM(f"REWRITTEN_QUERY: {question}", f"CANONICAL: yes\nREWRITTEN_QUERY: {question}")
        self.assertEqual(run_with(llm, question), question)
        self.assertEqual(len(llm.calls), 2)

    def test_at_most_one_retry(self):
        question = "<measure> trend"
        llm = ScriptedLLM(question, question, "should never be used")
        run_with(llm, question)
        self.assertEqual(len(llm.calls), 2)

    def test_sql_output_is_retried(self):
        llm = ScriptedLLM(
            "REWRITTEN_QUERY: SELECT a, COUNT(*) FROM t GROUP BY a",
            "REWRITTEN_QUERY: Count the <items> per <group>.",
        )
        self.assertEqual(run_with(llm, "<items> per <group>"), "Count the <items> per <group>.")
        self.assertIn("SQL", llm.calls[1][-1].content)

    def test_sql_twice_falls_back_to_original(self):
        llm = ScriptedLLM("```sql\nSELECT 1\n```", "WITH x AS (SELECT 1) SELECT * FROM x")
        self.assertEqual(run_with(llm, "<question>"), "<question>")

    def test_english_resembling_sql_keywords_is_not_sql(self):
        for rewrite in [
            "With <filter> applied, find the <group> with the highest <measure> from <period>.",
            "Select the <entities> from <place> who placed more than <N> <items>.",
        ]:
            with self.subTest(rewrite=rewrite):
                llm = ScriptedLLM(f"REWRITTEN_QUERY: {rewrite}")
                self.assertEqual(run_with(llm, "<question>"), rewrite)
                self.assertEqual(len(llm.calls), 1)

    def test_sql_variants_are_detected(self):
        for sql in [
            "SELECT a FROM t",
            "select a, count(*) from t group by a",
            "select a from t where b = 1",
            "WITH x AS (SELECT 1) SELECT * FROM x",
            "```sql\nselect 1\n```",
        ]:
            with self.subTest(sql=sql):
                self.assertIsNotNone(qr._invalid_reason(qr._parse_response(sql)[0]))

    def test_too_long_output_falls_back(self):
        llm = ScriptedLLM("x " * 2000, "y " * 2000)
        self.assertEqual(run_with(llm, "<question>"), "<question>")

    def test_llm_error_falls_back_to_question(self):
        llm = ScriptedLLM(RuntimeError("provider down"))
        self.assertEqual(run_with(llm, "<question>"), "<question>")

    def test_retry_error_keeps_valid_first_answer(self):
        question = "<measure> per <group>"
        llm = ScriptedLLM(f"REWRITTEN_QUERY: {question}", RuntimeError("timeout"))
        self.assertEqual(run_with(llm, question), question)

    def test_empty_question(self):
        llm = ScriptedLLM()
        self.assertEqual(run_with(llm, "   "), "")
        self.assertEqual(llm.calls, [])


class ParsingTests(unittest.TestCase):

    def test_formats(self):
        expected = "Find the <group> with the highest <measure>."
        variants = [
            f"CANONICAL: no\nREWRITTEN_QUERY: {expected}",
            f"**CANONICAL:** no\n**REWRITTEN_QUERY:** {expected}",
            f"<think>reasoning</think>\nCANONICAL: no\nREWRITTEN_QUERY: {expected}",
            f"REWRITTEN_QUERY: {expected}\nCANONICAL: no",
            f"REWRITTEN_QUERY:\n\"{expected}\"",
            f"REWRITTEN_QUERY: Find the <group> with\nthe highest <measure>.",
            f"Rewritten query: {expected}",
            expected,
        ]
        for raw in variants:
            with self.subTest(raw=raw):
                self.assertEqual(qr._parse_response(raw)[0], expected)

    def test_canonical_flag(self):
        self.assertIs(qr._parse_response("CANONICAL: yes\nREWRITTEN_QUERY: q")[1], True)
        self.assertIs(qr._parse_response("CANONICAL: no\nREWRITTEN_QUERY: q")[1], False)
        self.assertIsNone(qr._parse_response("q")[1])

    def test_aimessage_content_blocks(self):
        message = AIMessage(content=[{"type": "text", "text": "REWRITTEN_QUERY: q"}])
        self.assertEqual(qr._parse_response(qr._message_text(message))[0], "q")


class ContextTests(unittest.TestCase):

    def test_history_list_excludes_current_question(self):
        history = _history("Q1 about <measure>", "A1 says <value>") + [{"role": "user", "content": "what about <X>?"}]
        context = qr._build_context(history, "what about <X>?")
        self.assertIn("A1 says <value>", context)
        self.assertNotIn("what about <X>?", context)

    def test_history_keeps_most_recent_messages(self):
        history = _history(*[f"message {i}" for i in range(30)])
        context = qr._build_context(history, "latest")
        self.assertIn("message 29", context)
        self.assertNotIn("message 0\n", context + "\n")

    def test_oversized_context_keeps_the_end(self):
        context = qr._build_context("old " * 5000 + "RECENT", "q")
        self.assertTrue(context.endswith("RECENT"))
        self.assertLessEqual(len(context), qr.MAX_CONTEXT_CHARS)

    def test_no_context(self):
        self.assertEqual(qr._build_context(None, "q"), qr.NO_CONTEXT)
        self.assertEqual(qr._build_context([], "q"), qr.NO_CONTEXT)

    def test_every_category_reaches_the_prompt(self):
        for case in CASES:
            with self.subTest(category=case["category"]):
                history = case.get("history", []) + [{"role": "user", "content": case["question"]}]
                llm = ScriptedLLM("CANONICAL: no\nREWRITTEN_QUERY: normalized request")
                self.assertEqual(run_with(llm, case["question"], history), "normalized request")
                prompt_text = human_text(llm.calls[0])
                self.assertIn(case["question"], prompt_text)
                for turn in case.get("history", []):
                    self.assertIn(turn["content"], prompt_text)


class PromptTests(unittest.TestCase):

    def test_examples_are_abstract_patterns(self):
        examples = qr.SYSTEM_PROMPT.split("PATTERN EXAMPLES", 1)[1].split("OUTPUT FORMAT", 1)[0]
        for line in examples.splitlines():
            if line.strip().startswith(("-", "->")):
                with self.subTest(line=line):
                    self.assertIn("<", line)

    def test_prompt_forbids_sql_and_invented_schema(self):
        self.assertIn("Do NOT write SQL", qr.SYSTEM_PROMPT)
        self.assertIn("Do NOT invent table or column names", qr.SYSTEM_PROMPT)


# ============================================================
# LIVE TESTS
# ============================================================

def _numbers(text):
    return set(re.findall(r"\d+(?:[.,]\d+)*", text))


@unittest.skipUnless(os.getenv("INSIGHTX_LIVE_LLM") == "1", "set INSIGHTX_LIVE_LLM=1 to call the real LLM")
class LiveRewriterTests(unittest.TestCase):

    results = []

    def _check(self, case, output):
        question = case["question"]
        history_text = " ".join(t["content"] for t in case.get("history", []))
        source = f"{question} {history_text}"
        lowered = output.lower()

        self.assertTrue(output.strip(), "empty rewrite")
        self.assertNotIn("\n", output, "rewrite must be one line")
        self.assertLessEqual(len(output), 400, "rewrite too long")
        self.assertIsNone(qr._SQL_LIKE_RE.search(output), "rewrite contains SQL")
        self.assertNotIn("content=", output, "AIMessage repr leaked")

        invented_identifiers = set(re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", output)) - set(
            re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", source.lower())
        )
        self.assertFalse(invented_identifiers, f"invented identifiers: {invented_identifiers}")

        self.assertFalse(_numbers(output) - _numbers(source), "rewrite added numbers not in the input")
        self.assertTrue(_numbers(question) <= _numbers(output) or "five" in lowered,
                        "rewrite dropped a number from the question")

        for group in case["include_any"]:
            self.assertTrue(any(term.lower() in lowered for term in group), f"missing one of {group}")
        for term in case.get("exclude", []):
            self.assertNotIn(term.lower(), lowered)

    def test_categories(self):
        calls = {"n": 0}
        real_invoke = qr.llm.invoke

        def counting_invoke(messages):
            calls["n"] += 1
            return real_invoke(messages)

        failures = []
        with patch.object(qr.llm, "invoke", side_effect=counting_invoke):
            for case in CASES:
                history = case.get("history", []) + [{"role": "user", "content": case["question"]}]
                before = calls["n"]
                output = qr.rewrite_query(case["question"], history)
                used = calls["n"] - before
                try:
                    self._check(case, output)
                    status = "PASS"
                except AssertionError as exc:
                    status = f"FAIL ({exc})"
                    failures.append(case["category"])
                self.results.append((case["category"], case["question"], output, used, status))

        print("\n")
        for category, question, output, used, status in self.results:
            print(f"[{status}] {category}  (LLM calls: {used})")
            print(f"    in : {question}")
            print(f"    out: {output}")
        print(f"\n{len(CASES) - len(failures)}/{len(CASES)} categories passed")

        self.assertFalse(failures, f"failed categories: {failures}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
