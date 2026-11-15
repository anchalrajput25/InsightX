# InsightX benchmark report

Run on 2026-11-15 13:31 with `python benchmarks/run_benchmark.py`. Every number below was measured in this run, except section 7, which is read from saved real AI test results.

## Test machine

- Windows 11, 12 CPU threads, 7.4 GB RAM, Python 3.13.5
- PostgreSQL 18.6 (a throwaway local server with default settings, max_connections=100)
- InsightX backend started like on Render (one uvicorn process). Question queue: 4 questions answered at once, the rest wait in line.
- In sections 3 to 5 the AI model is a stand-in that returns fixed SQL, so the load test is free. Everything else is the real app.

## 1. CSV loading and data volume

Upload a ZIP of CSV files, import it into PostgreSQL, and read the schema (the real upload screen path). The backend's starting memory grows from row to row because, after the first database is connected, it loads the search model used to find relevant tables (a one-time cost). The rise from start to peak is what loading the file used.

| Dataset | Rows | CSV size | Database size | Upload + check | Import + schema | Total | Backend memory (start → peak) |
|---|---:|---:|---:|---:|---:|---:|---:|
| vol_100k | 214,000 | 5 MB | 18 MB | 1.9 s | 2.3 s | 4.2 s | 303 → 351 MB |
| vol_1m | 1,052,000 | 56 MB | 110 MB | 7.6 s | 15.3 s | 22.9 s | 473 → 764 MB |
| vol_3m | 3,051,895 | 166 MB | 307 MB | 17.8 s | 41.1 s | 58.9 s | 826 → 917 MB |
| northwind | 642,338 | 15 MB | 51 MB | 4.4 s | 7.1 s | 11.6 s | 719 → 786 MB |

## 2. SQL speed: simple vs complex (the app's read-only executor)

Each query ran 20 times after one warm-up run. Times in milliseconds.

| Rows in sales | Query | Avg | P50 | P95 | P99 | OK |
|---:|---|---:|---:|---:|---:|---:|
| 214,000 | simple | 8 | 8 | 8 | 9 | 20/20 |
| 214,000 | aggregation | 36 | 35 | 38 | 40 | 20/20 |
| 214,000 | complex join | 22 | 22 | 25 | 25 | 20/20 |
| 214,000 | monthly trend | 65 | 63 | 73 | 74 | 20/20 |
| 1,052,000 | simple | 67 | 65 | 76 | 77 | 20/20 |
| 1,052,000 | aggregation | 354 | 351 | 384 | 386 | 20/20 |
| 1,052,000 | complex join | 215 | 212 | 238 | 242 | 20/20 |
| 1,052,000 | monthly trend | 665 | 656 | 712 | 725 | 20/20 |
| 3,051,895 | simple | 235 | 232 | 256 | 259 | 20/20 |
| 3,051,895 | aggregation | 1,077 | 1,075 | 1,131 | 1,154 | 20/20 |
| 3,051,895 | complex join | 733 | 730 | 788 | 831 | 20/20 |
| 3,051,895 | monthly trend | 2,094 | 2,104 | 2,233 | 2,325 | 20/20 |

## 3. Full question time without AI wait (1,052,000 rows)

The whole /query path (queue, schema retrieval, safety gate, SQL, trust checks, chart, trace) with an instant stand-in AI. This is InsightX's own overhead. Seconds.

| Query | Avg | P50 | P95 | P99 | OK |
|---|---:|---:|---:|---:|---:|
| simple | 0.76 | 0.77 | 0.86 | 0.90 | 20/20 |
| aggregation | 0.55 | 0.53 | 0.62 | 0.63 | 20/20 |
| complex join | 0.51 | 0.50 | 0.61 | 0.73 | 20/20 |
| monthly trend | 0.98 | 0.95 | 1.20 | 1.25 | 20/20 |

## 4. Many tables (schema size)

Databases with 100 / 500 / 1,000 tables. 'Connect' includes reading and profiling every table. Prompt size is what the AI would be sent after InsightX picks the relevant tables (characters ÷ 4 ≈ tokens).

| Tables | Connect | First question | Question avg | P95 | P99 | OK | Linker prompt | SQL prompt | Backend peak memory |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 9.3 s | 0.47 s | 0.26 s | 0.36 s | 0.36 s | 10/10 | 1,823 tok | 1,713 tok | 709 MB |
| 500 | 82.5 s | 0.69 s | 0.46 s | 0.57 s | 0.57 s | 10/10 | 4,235 tok | 2,508 tok | 761 MB |
| 1,000 | 255.6 s | 1.82 s | 1.38 s | 2.46 s | 2.46 s | 10/10 | 4,253 tok | 2,471 tok | 811 MB |

## 5a. Users at once, instant AI

Each user asks 3 questions one after another (mixed simple and complex). Shows the server and database limit.

| Users | Questions | Answered | Other results | Avg | P50 | P95 | P99 | Answers / min | Light page P95 | DB connections (max) | Backend memory peak |
|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 30 | 30 | none | 3.0 s | 2.6 s | 4.8 s | 4.8 s | 154.7 | 0.07 s | 31 | 786 MB |
| 50 | 150 | 150 | none | 14.0 s | 12.3 s | 22.8 s | 22.9 s | 170.7 | 9.72 s | 33 | 792 MB |
| 100 | 300 | 300 | none | 24.2 s | 21.2 s | 34.5 s | 34.6 s | 196.6 | 14.54 s | 33 | 770 MB |

## 5b. Users at once, AI wait of 1.9 s per AI call

Each user asks 1 question. Each AI call waits as long as a typical real free-model call, so this shows what people would feel. 'Light page' is another visitor opening the home page at the same time.

| Users | Questions | Answered | Other results | Avg | P50 | P95 | P99 | Answers / min | Light page P95 | DB connections (max) | Backend memory peak |
|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 10 | 10 | none | 26.7 s | 26.3 s | 32.1 s | 32.1 s | 17.7 | 0.01 s | 31 | 766 MB |
| 50 | 50 | 50 | none | 117.6 s | 112.3 s | 146.5 s | 150.8 s | 18.9 | 0.00 s | 31 | 766 MB |
| 100 | 100 | 100 | none | 234.7 s | 226.8 s | 291.0 s | 292.9 s | 19.5 | 0.01 s | 31 | 755 MB |

## 6. Limits, timeouts and failures

| Check | Result | Time | Detail |
|---|---|---:|---|
| Runaway query (every row joined to every row) | stopped | 30.05 s | canceling statement due to statement timeout |
| Ask for all 3,051,895 rows | 1000 rows returned, truncated=True | 0.19 s | memory +0 MB |
| Delete data | blocked | 0.00 s | Forbidden SQL operation detected. |
| Drop a table | blocked | 0.00 s | Forbidden SQL operation detected. |
| Hidden second statement | blocked | 0.00 s | Multiple SQL statements detected. |
| Wrong table name | clean error | 0.00 s | relation "no_such_table" does not exist |

## 7. Real AI: speed, tokens, cost, wrong SQL (saved results, no new calls)

From the Spider accuracy tests (37 result files, latest answer per question). Levels are Spider's own difficulty labels.

| Level | Questions | Avg | P50 | P95 | P99 | AI calls | Tokens in | Tokens out | Wrong SQL | AI service failed | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| easy | 25 | 45.1 s | 19.3 s | 112.1 s | 484.6 s | 5.1 | 7,181 | 1,155 | 3/23 | 2 | $0.00 |
| medium | 59 | 33.8 s | 23.4 s | 104.6 s | 155.0 s | 5.8 | 8,699 | 2,281 | 4/56 | 3 | $0.00 |
| hard | 22 | 38.9 s | 13.9 s | 179.2 s | 179.2 s | 5.9 | 9,579 | 2,290 | 1/18 | 4 | $0.00 |
| extra | 22 | 44.3 s | 24.9 s | 169.7 s | 169.7 s | 6.2 | 10,456 | 2,900 | 7/19 | 3 | $0.00 |

From the AI usage log (1,534 AI calls for 473 questions, 2026-10-10 to 2026-11-15):

- Tokens per question: avg 6,526, P95 16,961.
- One AI call: avg 5.6 s, P95 22.3 s, P99 51.3 s.
- Failed AI calls (rate limits, timeouts; the app then tries the next free model): 208 of 1,534 (13.6%).
- Money spent: $0.00 (only free models are used).
- If a paid model at $0.1/$0.4 per million input/output tokens were used instead (an example price; change it with --price-in / --price-out), one question would cost about $0.0009, so 1,000 questions about $0.95. (85% / 15% input/output split, close to the log above.)

## Reliability pieces in the app (checked in the code)

| Piece | How InsightX does it |
|---|---|
| Connection pooling | One shared pool per database and login (up to 15 connections), checked before use. Section 5 shows 100 users needing about 33 connections in total. |
| Query time limit | Every query runs read-only with a 30 s statement timeout (section 6). |
| Row limit | At most 1,000 rows come back, read with a streaming cursor, so a 3M-row table never enters app memory (section 6). |
| Large results | The full answer stays in the database; the app shows the first 1,000 rows and says when it was cut. Chat history and lists are paged. |
| CSV loading | Big files are streamed by DuckDB (4 threads, 256 MB cap) and copied into PostgreSQL; a failed load changes nothing. |
| Caching | Same question again: saved SQL is reused (no AI call). Same AI prompt: saved AI reply reused. |
| Many users | A fair queue lets 4 questions use the AI at once; others wait their turn (up to 150 s) instead of failing. |
| Monitoring | Every question saves a trace: time per step, AI calls, tokens, SQL run, errors. |

## What this shows (tested limits only)

- Largest load tested: 3,051,895 rows (166 MB of CSV) in 59 s. Backend memory rose only 91 MB during the load, so the file is streamed to PostgreSQL, not held in memory.
- On 3,051,895 rows the slowest query tested (monthly trend) had P95 2.23 s in the database.
- With 1,000 tables, connecting took 256 s (one long request that reads every table; the slowest part of the app at this size) and questions stayed at P95 2.46 s of app time; the SQL prompt stayed near 2471 tokens because only relevant tables are sent.
- 100 users at once with real-speed AI: 100 of 100 answered (P95 291 s). The limit is the free AI speed and the 4-at-a-time queue, about 19 answers per minute, not the database.
- With instant AI, 100 users got all 300 answers (P95 34 s, about 197 per minute) using 33 database connections. But one backend process serves them all, so a light page took up to 15 s (P95) for other visitors.

## Run it again

Double-click `RUN_BENCHMARK.bat` in the project folder (or run `python benchmarks/run_benchmark.py`). It needs PostgreSQL installed (it starts its own throwaway server) and makes no AI calls.
