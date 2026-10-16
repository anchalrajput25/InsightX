# Alternative phrasings from the query rewriter (no LLM calls).

from langchain_core.messages import AIMessage

from backend.text_to_sql import query_rewriter as qr


class Scripted:
    def __init__(self, *outputs):
        self.outputs = list(outputs)

    def invoke(self, messages):
        return AIMessage(content=self.outputs.pop(0))


PRIMARY = "Find the <group> with the highest <measure>."


def test_parses_primary_and_alternatives(monkeypatch):
    monkeypatch.setattr(qr, "llm", Scripted(
        "CANONICAL: no\n"
        f"REWRITTEN_QUERY: {PRIMARY}\n"
        "ALTERNATIVE_QUERIES:\n"
        "- Which <group> has the largest <measure>?\n"
        "- 2. Identify the <group> with the top <measure>.\n"
        f"- {PRIMARY}\n"
        "- SELECT g FROM t\n"
        "- **Determine the <group> ranked first by <measure>.**\n"
        "- Show the <group> leading in <measure>."
    ))
    result = qr.rewrite_query_variants("which <group> has most <measure>", alternative_count=3)
    assert result["primary"] == PRIMARY
    assert result["alternatives"] == [
        "Which <group> has the largest <measure>?",
        "Identify the <group> with the top <measure>.",
        "Determine the <group> ranked first by <measure>.",
    ]


def test_missing_alternatives_section_is_fine(monkeypatch):
    monkeypatch.setattr(qr, "llm", Scripted(f"CANONICAL: no\nREWRITTEN_QUERY: {PRIMARY}"))
    assert qr.rewrite_query_variants("q") == {"primary": PRIMARY, "alternatives": []}


def test_alternatives_do_not_leak_into_primary():
    raw = f"REWRITTEN_QUERY: {PRIMARY}\nALTERNATIVE_QUERIES:\n- other phrasing"
    assert qr._parse_response(raw)[0] == PRIMARY


def test_llm_failure_falls_back_without_alternatives(monkeypatch):
    class Broken:
        def invoke(self, messages):
            raise RuntimeError("rate limited")

    monkeypatch.setattr(qr, "llm", Broken())
    assert qr.rewrite_query_variants("original question") == {
        "primary": "original question", "alternatives": []
    }


def test_rewrite_query_keeps_string_contract(monkeypatch):
    monkeypatch.setattr(qr, "llm", Scripted(
        f"REWRITTEN_QUERY: {PRIMARY}\nALTERNATIVE_QUERIES:\n- other"
    ))
    assert qr.rewrite_query("q") == PRIMARY
