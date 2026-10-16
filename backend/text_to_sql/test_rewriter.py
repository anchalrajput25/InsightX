from query_rewriter import rewrite_query


question = """
Which customers generated the highest revenue growth
in the last year compared with the previous year,
excluding refunded orders, and which product categories
contributed most to that growth?
"""

result = rewrite_query(question)

print("\n" + "=" * 70)
print("ADVANCED QUERY REWRITER")
print("=" * 70)

print(result)