# Multi-query retrieval with Reciprocal Rank Fusion (no database).

from backend.text_to_sql import retriever

SCHEMA = {
    "tables": [
        {"table_name": "sales_orders", "columns": [{"column_name": "order_id"}, {"column_name": "amount"}]},
        {"table_name": "clients", "columns": [{"column_name": "client_id"}, {"column_name": "town"}]},
        {"table_name": "warehouse_stock", "columns": [{"column_name": "sku"}, {"column_name": "quantity"}]},
    ]
}


def keyword_only(monkeypatch):
    monkeypatch.setattr(retriever, "semantic_similarities", lambda query, texts: None)


def test_variant_vocabulary_finds_tables_the_primary_misses(monkeypatch):
    keyword_only(monkeypatch)
    primary_only = retriever.retrieve_for_queries(SCHEMA, ["customers per city"], top_k=3)
    assert "clients" not in [item["table"] for item in primary_only]

    fused = retriever.retrieve_for_queries(
        SCHEMA, ["customers per city", "clients per town"], top_k=3
    )
    assert fused[0]["table"] == "clients"
    assert fused[0]["matched_queries"] == 1


def test_tables_matched_by_more_variants_rank_higher(monkeypatch):
    keyword_only(monkeypatch)
    fused = retriever.retrieve_for_queries(
        SCHEMA, ["order amount", "sales amount", "town"], top_k=3
    )
    assert fused[0]["table"] == "sales_orders"
    assert fused[0]["matched_queries"] == 2


def test_duplicate_and_empty_queries_are_ignored(monkeypatch):
    keyword_only(monkeypatch)
    once = retriever.retrieve_for_queries(SCHEMA, ["order amount"], top_k=3)
    repeated = retriever.retrieve_for_queries(SCHEMA, ["order amount", " order amount ", ""], top_k=3)
    assert [i["table"] for i in once] == [i["table"] for i in repeated]
    assert retriever.retrieve_for_queries(SCHEMA, ["", "  "]) == []


def test_document_embeddings_are_cached(monkeypatch):
    calls = []

    class CountingModel:
        def encode(self, texts):
            calls.append(list(texts))
            import numpy as np
            return np.ones((len(texts), 3))

    monkeypatch.setattr(retriever, "get_embedding_model", lambda: CountingModel())
    monkeypatch.setattr(retriever, "_DOCUMENT_EMBEDDINGS", {})
    retriever.encode_documents(["a", "b"])
    retriever.encode_documents(["a", "b", "c"])
    assert calls == [["a", "b"], ["c"]]
