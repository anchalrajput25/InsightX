from query_rewriter import rewrite_query
from query_decomposer import decompose_query


question = """
Which customers generated the highest revenue growth
in the last year compared with the previous year,
excluding refunded orders, and which product categories
contributed most to that growth?
"""


rewritten = rewrite_query(question)

print("\n" + "=" * 70)
print("QUERY REWRITER")
print("=" * 70)

print(rewritten)


decomposed = decompose_query(
    question,
    rewritten
)

print("\n" + "=" * 70)
print("QUERY DECOMPOSER")
print("=" * 70)

print(decomposed)