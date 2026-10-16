# backend/evaluation
#
# Database-agnostic evaluation harness for the Text-to-SQL agent.
#
#   golden.py    - golden-set format (question -> expected SQL) and loading
#   compare.py   - execution-accuracy comparison of result sets
#   runner.py    - runs the agent over a golden set and computes metrics
#   generate.py  - proposes candidate golden cases for any database
#   __main__.py  - CLI: python -m backend.evaluation {check,run,generate}
#
# Nothing here knows about a specific dataset: golden sets are data
# files that live next to the code (see golden/), one per database.
