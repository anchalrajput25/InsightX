# InsightX accuracy test

A checkable accuracy number for InsightX's Text-to-SQL, measured the
same way as the public Spider and BIRD benchmarks.

## Latest result (3 Nov 2026)

| Test set | Questions | Strict (Spider rule) | Lenient | 95% CI (lenient) |
|---|---|---|---|---|
| **Spider dev sample, 8 unseen databases** | 104 | **79.8%** | **85.6%** | 77.6% to 91.1% |
| Own gold set (`shop.sqlite`) | 24 | incomplete | incomplete | |

- By Spider difficulty, lenient: easy 83.3%, medium 92.3%, hard 88.2%, extra 64.7%.
- The own set is **incomplete**: 11 of its 24 questions were never answered because every free AI quota
  ran out that day. They count as failures in `results/LATEST_SUMMARY.md` (overall 78.9% lenient /
  71.9% strict on 128). Of the 13 shop questions that were answered, 12 were right.
- Free models only, one run. The gateway fell back between models, mostly Gemini 3.5 Flash Lite
  and Groq gpt-oss-120b / qwen3.8-27b (`results/LATEST_SUMMARY.md` lists the share of each).
- Full report with every failed question: `results/run-20261103-175139/SUMMARY.md`.

Spider is by Yale (Yu et al., 2018, *Spider: A Large-Scale Human-Labeled Dataset for Complex and
Cross-Domain Semantic Parsing and Text-to-SQL Task*), licensed CC BY-SA 4.0. Only the generated
question sets are stored here; the databases are downloaded by `prepare_spider.py`.

## What is measured

**Execution accuracy.** For every test question:

1. InsightX gets only the question and the database, and runs its full
   pipeline (the same code the `/query` API uses).
2. The SQL InsightX wrote and the **gold SQL** (the known correct answer)
   both run on the same database.
3. The result rows are compared. Same data means pass. The SQL text
   itself does not matter.

Two scores are reported:

| Score | Rule |
|---|---|
| **Strict** | Same columns and same rows. This is the official Spider rule. |
| **Lenient** | Every expected column is present with the same rows; extra helpful columns are allowed (for example the count behind a "top city" answer). |

Row order is only checked when the gold SQL has `ORDER BY`. Numbers are
compared to 4 decimals.

Saved AI answers and the similar-question cache are switched off, so
every question is really answered by the model.

## Test sets

| Set | Questions | Where from |
|---|---|---|
| Spider dev | 104 | 8 databases from the public [Spider](https://yale-lily.github.io/spider) dev set (concert_singer, pets_1, car_1, flight_2, employee_hire_evaluation, student_transcripts_tracking, world_1, dog_kennels). InsightX was never built or tuned on these. Fixed random sample (seed 42), official gold SQL, official Spider difficulty rules. Gold queries that error or return no rows are skipped. |
| Own gold set | 24 | `golden/shop.yaml` on `sample_datasets/shop.sqlite` (Chinook music store, Sakila DVD rental, Northwind). Plain business questions; every gold SQL was run and checked, and "top 1" questions with a tie were removed. |

## How to run

From a terminal in the project folder:

```
python evals/prepare_spider.py           # downloads Spider once (~200 MB)
python evals/run_eval.py --limit 2       # quick check: 2 questions per database
python evals/run_eval.py                 # all sets
python evals/run_eval.py --suite spider
python evals/run_eval.py --resume        # continue a stopped run
```

`--resume` asks again every question that failed only because of the AI service (rate limit,
timeout, quota). When all free models hit their per-minute limit, the runner waits 70 seconds
before the next question instead of failing the rest.

## Output

Each run writes `results/run-<time>/`:

- `SUMMARY.md`: scores overall, by difficulty, by database, failure causes, and every failed question with InsightX's SQL next to the gold SQL.
- `all_cases.json`: every question with status, SQL, latency, tokens, cost.
- One full report per database from the built-in harness (`backend/evaluation`).

`results/LATEST_SUMMARY.md` is always the newest summary.

## Files

| File | What it does |
|---|---|
| `prepare_spider.py` | Downloads Spider and writes `golden/spider/*.yaml` |
| `spider_hardness.py` | Spider's easy / medium / hard / extra rules |
| `run_eval.py` | Runs the test and writes the summary |
| `golden/shop.yaml` | Own gold set |
