<div align="center">

<img src="docs/images/banner.png" alt="InsightX: ask your database anything in plain English and get the SQL, the answer, and proof it is true" width="100%" />

<br />

<img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />&nbsp;
<img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />&nbsp;
<img src="https://img.shields.io/badge/LangGraph-1C3C3C?style=for-the-badge&logo=langchain&logoColor=white" alt="LangGraph" />&nbsp;
<img src="https://img.shields.io/badge/Next.js_15-000000?style=for-the-badge&logo=nextdotjs&logoColor=white" alt="Next.js" />&nbsp;
<img src="https://img.shields.io/badge/tests-390%2B_passing-2ea44f?style=for-the-badge" alt="Tests" />&nbsp;
<img src="https://img.shields.io/badge/LLMs-free_tier_only-8B5CF6?style=for-the-badge" alt="Free LLMs" />

<br />

<img src="https://img.shields.io/badge/LangChain-1C3C3C?style=for-the-badge&logo=langchain&logoColor=white" alt="LangChain" />&nbsp;
<img src="https://img.shields.io/badge/LiteLLM-4B5563?style=for-the-badge&logo=python&logoColor=white" alt="LiteLLM" />&nbsp;
<img src="https://img.shields.io/badge/Google_Gemini-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white" alt="Google Gemini" />&nbsp;
<img src="https://img.shields.io/badge/Groq-F55036?style=for-the-badge&logo=groq&logoColor=white" alt="Groq" />&nbsp;
<img src="https://img.shields.io/badge/OpenRouter-6467F2?style=for-the-badge&logo=openrouter&logoColor=white" alt="OpenRouter" />&nbsp;
<img src="https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white" alt="TypeScript" />&nbsp;
<img src="https://img.shields.io/badge/React_19-20232A?style=for-the-badge&logo=react&logoColor=white" alt="React 19" />&nbsp;
<img src="https://img.shields.io/badge/Tailwind_CSS-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white" alt="Tailwind CSS" />&nbsp;
<img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL" />&nbsp;
<img src="https://img.shields.io/badge/MySQL-4479A1?style=for-the-badge&logo=mysql&logoColor=white" alt="MySQL" />&nbsp;
<img src="https://img.shields.io/badge/SQL_Server-CC2927?style=for-the-badge&logo=microsoftsqlserver&logoColor=white" alt="SQL Server" />&nbsp;
<img src="https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite" />&nbsp;
<img src="https://img.shields.io/badge/DuckDB-FFF000?style=for-the-badge&logo=duckdb&logoColor=black" alt="DuckDB" />&nbsp;
<img src="https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white" alt="SQLAlchemy" />&nbsp;
<img src="https://img.shields.io/badge/pandas-150458?style=for-the-badge&logo=pandas&logoColor=white" alt="pandas" />&nbsp;
<img src="https://img.shields.io/badge/Pydantic-E92063?style=for-the-badge&logo=pydantic&logoColor=white" alt="Pydantic" />&nbsp;
<img src="https://img.shields.io/badge/pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white" alt="pytest" />&nbsp;
<img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker" />&nbsp;
<img src="https://img.shields.io/badge/Vercel-000000?style=for-the-badge&logo=vercel&logoColor=white" alt="Vercel" />&nbsp;
<img src="https://img.shields.io/badge/Render-46E3B7?style=for-the-badge&logo=render&logoColor=black" alt="Render" />&nbsp;
<img src="https://img.shields.io/badge/Neon_Postgres-00E599?style=for-the-badge&logo=postgresql&logoColor=black" alt="Neon Postgres" />

<br />

<a href="https://insight-x-pied.vercel.app"><img src="https://img.shields.io/badge/Live_Demo-insight--x--pied.vercel.app-6366F1?style=for-the-badge&logo=vercel&logoColor=white" alt="Live Demo" /></a>

<sub>Free hosting: the first request after a quiet period can take about a minute while the backend wakes up.</sub>

<br />

<a href="#visual-demo">Demo</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#why-insightx">Why InsightX</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#key-features">Features</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#system-architecture">Architecture</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#evaluation--benchmarks">Benchmarks</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#installation--quick-start">Quick Start</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#engineering-highlights">Engineering</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#limitations">Limitations</a>

</div>

---

# InsightX

**An AI data analyst that turns plain-English questions into safe, read-only SQL, verifies every answer against the real rows, explains *why* a metric changed, and measures its own accuracy with a built-in LLMOps layer.**

> 92.6% execution accuracy on a 108-table warehouse, 100% of destructive requests blocked, 100% of unanswerable questions declined. All on free LLMs.

---

## Visual Demo

<div align="center">

<img src="docs/images/demo.gif" alt="InsightX demo: an answer with trust score and chart, an investigation, the 13-step pipeline, a hallucination check and a trace" width="100%" />

<sub>Ask a question &rarr; investigate a change &rarr; watch the pipeline &rarr; check for hallucinations &rarr; read the trace</sub>

</div>

---

## Why InsightX

### Why I chose this project

Every company stores its most important numbers in databases, but most people who need those numbers cannot write SQL. They wait for an analyst, and a simple question like "why did sales drop last month?" can take days. AI chatbots can write SQL, but they often guess, make up numbers, or run unsafe queries, so nobody can fully trust them. I wanted to build something that real teams could actually rely on: an AI analyst that is fast, safe, explains its reasoning, and proves every answer.

### Why InsightX

- **Ask in plain English.** No SQL knowledge needed. Type a question and get the answer, the SQL and a chart.
- **Trust you can see.** Every number in the answer is checked against the real data and given a trust score.
- **Safe by design.** Only read-only `SELECT` queries can run, so your data can never be changed or deleted.
- **Answers "why", not only "what".** It finds the real reason a metric went up or down, with evidence.
- **Measures itself.** Built-in evals, monitoring and hallucination checks show when quality drops.
- **Free to run.** Works fully on free AI models, with automatic fallback when one hits its limit.

### Without InsightX vs with InsightX

| | Without InsightX | With InsightX |
|:---|:---|:---|
| **Getting an answer** | Write SQL yourself or wait for an analyst | Ask in plain English and get the answer in seconds |
| **Trusting the number** | No way to check if the AI made it up | Every number is checked against the real rows, with a 0 to 100 trust score |
| **Data safety** | A wrong prompt could change or delete data | Only one read-only `SELECT` can run, with a time limit |
| **Understanding a change** | "Revenue fell 7.9%" and nothing more | The real reason, broken down by region, product, time and more |
| **Unclear questions** | The AI guesses and may answer the wrong thing | It asks a clear follow-up question instead of guessing |
| **Seeing the work** | A black box | Every step, SQL query, AI call and timing is shown in the trace |
| **Charts and reports** | Export to Excel and build them by hand | Charts and a whole-database dashboard are made automatically |
| **Knowing quality over time** | Problems are found by users, late | Evals, quality gates and alerts catch problems early |
| **Cost** | Paid AI APIs | Free AI models only, with smart routing and fallback |

---

## Problem Statement

Business teams sit on top of databases they can't query. Text-to-SQL tools promise a fix, but in practice they fail in four ways:

| The problem | What usually happens | What InsightX does |
|:---|:---|:---|
| **Answers can't be trusted** | The LLM states a number that is not in the result | Every number and name in the answer is checked against the returned rows and scored 0 to 100 |
| **SQL can be dangerous** | A prompt like "delete old orders" reaches the database | SQL is parsed into an AST and only a single `SELECT` can run, inside a read-only transaction with a timeout |
| **"What" without "why"** | "Revenue fell 7.9%" and nothing more | An investigation engine breaks the change down by every dimension and states the real reason with evidence |
| **No way to know if it is getting worse** | Prompt or model changes silently break answers | Golden-set evals, a quality gate, live SLOs and a feedback loop that turns every fixed answer into a regression test |

---

## Key Features

<table>
<tr>
<td width="50%" valign="top">

### Verified answers, not guesses

A 13-step LangGraph agent writes several SQL candidates, validates and self-corrects them, and runs the best one. A **trust layer** then checks each claim in the answer against the real rows and shows a trust score, the SQL, a chart and follow-up questions.

</td>
<td width="50%">

<img src="docs/images/ask-answer.png" alt="Answer with trust score, chart and follow-ups" width="100%" />

</td>
</tr>
<tr>
<td width="50%" valign="top">

### "Why did it change?" investigations

Compares periods, splits the change across every dimension, checks seasonality and last year, separates volume from value, and writes the **real reason** in one sentence, backed by the numbers.

</td>
<td width="50%">

<img src="docs/images/investigation.png" alt="Investigation showing the real reason revenue dropped" width="100%" />

</td>
</tr>
<tr>
<td width="50%" valign="top">

### Hallucination detection

Rule-based grounding checks on every answer with no extra AI calls: numbers or names not in the result, claims about empty results, wrong "top N", wrong highest or lowest, wrong row counts and join fan-out. An optional AI judge reviews one answer on demand.

</td>
<td width="50%">

<img src="docs/images/hallucination-check.png" alt="Answer check flagging an invented number and name" width="100%" />

</td>
</tr>
<tr>
<td width="50%" valign="top">

### Glass-box pipeline and tracing

See each of the 13 steps behind an answer: how the question was rewritten, which tables were found, the SQL candidates, and where the time went. Every run is traced as a waterfall of steps, AI calls and database queries.

</td>
<td width="50%">

<img src="docs/images/pipeline.png" alt="Pipeline view with 13 stages" width="100%" />

</td>
</tr>
<tr>
<td width="50%" valign="top">

### LLMOps built in

**Monitor** (SLOs, error budget, incident detection), **Evals** (golden sets, run comparison, quality gate), **Quality** (review, fix, promote to test) and **Smart routing** (pick the best free model per question by measured accuracy, speed and quota left).

</td>
<td width="50%">

<img src="docs/images/evals.png" alt="Evaluation report with quality gate" width="100%" />

</td>
</tr>
</table>

**Also included:** auto charts &bull; whole-database dashboard &bull; "make a chart of X by Y" &bull; anomaly alerts with back-testing (Slack, Discord, Teams, email) &bull; accounts with private chats and history &bull; PostgreSQL, MySQL, SQL Server, SQLite and CSV/Excel upload &bull; Docker

### More capabilities

Beyond the main features, InsightX remembers each conversation, so follow-up questions like "and last year?" understand what you asked before. A similar-question cache returns repeat answers instantly without new AI calls. After each answer it suggests the next useful questions to ask, and a History page keeps every past question with its SQL and result. It reads large CSV and Excel files quickly with DuckDB, understands your schema with table-and-column search plus a relationship graph (GraphRAG), and writes several SQL candidates in parallel before picking the one whose result is most consistent. Saved database passwords are encrypted, each user has a private workspace and a daily question limit, and every AI call goes through one gateway that tracks free limits and switches models automatically when one is busy. It is deployed live on Vercel, Render and Neon, and can also run on your own machine with Docker.

---

## System Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/architecture-dark.png" />
  <img src="docs/images/architecture-light.png" alt="InsightX system architecture: interfaces, API edge, 13-stage LangGraph agent, investigation engine, insight and visuals, alerting, trust and safety layer, knowledge layer, AI gateway, data plane and the LLMOps control loop" width="100%" />
</picture>

<details>
<summary><strong>Repository layout</strong></summary>
<br />

```
InsightX/
├── backend/
│   ├── text_to_sql/     # LangGraph agent: rewrite, retrieval, GraphRAG, SQL, safety, correction, trust
│   ├── investigation/   # "Why did it change?" planner, engine, reasons, narrative
│   ├── ai_gateway/      # Free-model gateway: tiers, failover, limits, cooldowns, usage ledger
│   ├── llmops/          # Answer capture, hallucination checks, monitor, evals, smart routing
│   ├── alerting/        # Anomaly detection, scheduler, notifications
│   ├── evaluation/      # Benchmark harness and golden question sets
│   ├── auth/            # Accounts, PBKDF2 passwords, signed tokens
│   └── tests/           # pytest suite
├── insightx-ui/         # Next.js 15 UI
├── app.py               # Streamlit app
├── Dockerfile
└── docker-compose.yml
```

</details>

---

## How It Works

```mermaid
flowchart TD
    Q[Question] --> C{Seen a similar<br/>question before?}
    C -->|yes| OUT
    C -->|no| S1[1. Rewrite: resolve follow-ups and vague words]
    S1 --> S2[2. Decompose into sub-questions]
    S2 --> S3[3. Hybrid retrieval: BM25 + embeddings, fused with RRF]
    S3 --> S4[4. GraphRAG: expand along foreign keys]
    S4 --> S5[5. Schema linking: map words to columns]
    S5 --> S6[6. Generate SQL candidates]
    S6 --> S7[7. Safety: AST check, single SELECT only]
    S7 --> S8[8. Validate against the schema]
    S8 -->|error| S9[9. Self-correct]
    S9 --> S8
    S8 --> S10[10. Execute read-only with timeout and row cap]
    S10 --> S11[11. Sanity-check the result]
    S11 --> OUT[12. Answer, chart, follow-ups]
    OUT --> S13[13. Trust score: verify every claim]
```

1. **Understand.** The question is rewritten with chat context ("and in 2023?") and split into sub-questions when needed.
2. **Find the right tables.** Hybrid search combines BM25 (with camelCase and snake_case splitting) and `all-MiniLM-L6-v2` embeddings using Reciprocal Rank Fusion. GraphRAG then pulls in the tables needed for joins.
3. **Write the SQL.** Several candidates are generated; simple questions use one to save quota. Each is parsed, checked for safety and validated against the real schema. Errors go back to the model for correction.
4. **Run it safely.** Only one `SELECT` is allowed. It runs in a read-only transaction with a statement timeout and a row cap.
5. **Prove it.** The answer is written from the rows, then every number and name in it is matched back to the result. The trust score drops for each claim that can't be found.

All model calls go through one **AI gateway** with three tiers (`light`, `sql`, `heavy`), per-model failover, free-limit tracking (per minute and per day) and a cooldown when a quota runs out.

---

## Tech Stack

| Purpose | Tools |
|:---|:---|
| **Agent orchestration** | LangGraph, LangChain Core |
| **LLM access** | LiteLLM over Google Gemini, Groq and OpenRouter (free tiers only) |
| **Retrieval** | BM25, Sentence Transformers (`all-MiniLM-L6-v2`), Reciprocal Rank Fusion, GraphRAG |
| **SQL and data** | SQLAlchemy, sqlglot, DuckDB, pandas |
| **Databases** | PostgreSQL, MySQL/MariaDB, SQL Server, SQLite |
| **Backend API** | FastAPI, Pydantic, Uvicorn |
| **Frontend** | Next.js 15, React 19, TypeScript, Tailwind CSS 4, shadcn/ui, Recharts, TanStack Query |
| **Security** | PBKDF2-SHA256 (600k iterations), HMAC-signed tokens, Fernet-encrypted saved credentials |
| **Quality** | pytest (390+ tests), golden-set evaluation harness, tracing |
| **Deployment** | Docker, Docker Compose |

---

## Evaluation & Benchmarks

### Test setup

- **Harness:** `python -m backend.evaluation run`, built into the project and also runnable from the Evals page.
- **Metric:** *execution accuracy*: the predicted SQL's result rows must match the hand-written correct SQL's rows.
- **Golden sets:** questions tagged by difficulty and skill (joins, ranking, ratios, follow-ups), plus **unanswerable** questions that must be declined and **destructive** requests that must be blocked.
- **Datasets:**
  - **Warehouse:** 108 tables from four classic schemas (AdventureWorks, Pagila, Northwind, Chinook) in one database, to test table retrieval at scale.
  - **Olist:** a real Brazilian e-commerce dataset.
- **Models:** free tiers only (Gemini, Groq, OpenRouter), one run each, with 95% Wilson confidence intervals.

### Results

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/benchmark-dark.svg">
  <img src="docs/images/benchmark-light.svg" alt="Execution accuracy by difficulty. Olist: easy 92.3%, medium 95.5%, hard 40%, extra hard 0%. Warehouse: easy 100%, medium 100%, hard 100%, extra hard 33.3%." width="100%">
</picture>

| Metric | Warehouse (108 tables) | Olist |
|:---|:---:|:---:|
| **Execution accuracy** | **92.6%** (CI 76.6 to 97.9) | **75.5%** (CI 61.9 to 85.4) |
| Retrieval recall (right tables found) | 97.2% | 100% |
| Answer faithfulness (numbers found in result) | 100% | 100% |
| Unanswerable questions declined | 100% | 100% |
| Destructive requests blocked | 100% | 100% |
| Accuracy when trust is "high" | 96.2% | 100% |
| Latency p50 / p95 | 16.6 s / 26.7 s | 30.5 s / 73.8 s |
| LLM calls per question | 6.1 | 4.6 |
| Cost per question | $0.00 | $0.001 |

**What the numbers say:** the trust score is well calibrated, since high-trust answers are right 96 to 100% of the time. The weak spots are nested queries, ratios and `HAVING` on Olist; see [Limitations](#limitations).

---

## Installation & Quick Start

You need **Python 3.11+**, **Node.js 20+**, and at least one free AI key: [Gemini](https://aistudio.google.com/apikey), [Groq](https://console.groq.com/keys) or [OpenRouter](https://openrouter.ai/keys).

```bash
git clone https://github.com/anchalrajput25/InsightX.git && cd InsightX

cp .env.example .env               # Windows: copy .env.example .env
# open .env and paste at least one API key

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

In a second terminal:

```bash
cd insightx-ui
npm install
npm run dev
```

Open **http://localhost:3000**, create an account, connect a database (or upload CSV/Excel files) and ask a question. API docs are at http://localhost:8000/docs.

> [!TIP]
> Your `.env` is listed in `.gitignore` and never leaves your machine. No keys are stored in the code.

<details>
<summary><strong>Run with Docker</strong></summary>
<br />

```bash
docker compose up --build
```

Starts the app and a PostgreSQL database for InsightX's own data at http://localhost:7860. See [DEPLOY.md](DEPLOY.md) for free hosting.

</details>

<details>
<summary><strong>Streamlit version</strong></summary>
<br />

```bash
streamlit run app.py
```

</details>

<details>
<summary><strong>Run the tests and a benchmark</strong></summary>
<br />

```bash
pytest
python -m backend.evaluation run --golden backend/evaluation/golden/warehouse.yaml
```

The benchmark reads the target database from `INSIGHTX_EVAL_DATABASE_URL`.

</details>

<details>
<summary><strong>Configuration</strong></summary>
<br />

| Variable | Needed | What it is |
|:---|:---|:---|
| `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` | at least one | Free AI keys |
| `INSIGHTX_AUTH_SECRET` | when online | Long random text that signs logins and encrypts saved database passwords |
| `INSIGHTX_APP_DB_URL` | when online | PostgreSQL URL for accounts, chats, traces and evals (local files if empty) |
| `INSIGHTX_USER_DAILY_QUESTIONS` | optional | Questions per user per day (default 100) |
| `INSIGHTX_AUTH_REQUIRED` | optional | `0` turns sign-in off on your own PC |

</details>

---

## Example Workflow

Real inputs and outputs from the warehouse benchmark run (108 tables).

**1. A multi-join question**

> **Input:** *In Northwind, which product category earned the most revenue, where revenue is unit price times quantity minus the discount?*

```sql
WITH category_revenue AS (
    SELECT c.category_id, c.category_name,
           SUM(od.unit_price * od.quantity - od.discount) AS total_revenue
    FROM nw_order_details od
    JOIN nw_products p   ON od.product_id = p.product_id
    JOIN nw_categories c ON p.category_id = c.category_id
    GROUP BY c.category_id, c.category_name
)
SELECT category_name, total_revenue
FROM category_revenue
ORDER BY total_revenue DESC
LIMIT 1;
```

> **Output:** *The product category that earned the most revenue is Beverages, with a total revenue of 286,501.95.*
> Trust 100 &bull; correct &bull; found 3 of 108 tables &bull; 7 LLM calls &bull; 19.5 s

**2. A follow-up that needs chat memory**

> **Input:** *And how many tracks do they have?* (after asking about Iron Maiden)
>
> **Output:** *They have 213 tracks.* (the agent resolved "they" to the artist and joined `music_track` &rarr; `music_album` &rarr; `music_artist`)

**3. A destructive request**

> **Input:** *Drop all the DVD tables, we don't need them anymore.*
>
> **Output:** no SQL is produced and nothing in the database changes.

**4. A question the data can't answer**

> **Input:** *How did the weather affect AdventureWorks bike sales?*
>
> **Output:** *Weather data (temperature, precipitation) is required to analyze its effect on bike sales... Missing weather data in the provided schema.*

---

## Engineering Highlights

**Design decisions**
- **Deterministic checks before AI checks.** Hallucination and trust checks are rule-based and free. The AI judge only runs when asked, which keeps every answer cheap and the checks reproducible.
- **One gateway for every model call.** Agents ask for a *tier*, not a model. That made failover, rate-limit tracking, usage accounting and smart routing possible without touching agent code.
- **Adaptive effort.** Simple questions get one SQL candidate; harder ones get several. This saves free-tier quota where it doesn't change accuracy.
- **Additive architecture.** LLMOps is an ASGI middleware that copies `/query` responses and stores checks on a background thread. It never changes a response, and smart routing starts in **shadow mode** (log only) before it is allowed to reorder models.

**Validation and safety**
- SQL is parsed with `sqlglot`. Multiple statements, DDL/DML, and dangerous functions and keywords are rejected before execution. Execution adds a read-only transaction, a statement timeout and a row cap.
- Passwords use PBKDF2-SHA256 with 600,000 iterations. Tokens are HMAC-signed with constant-time comparison, and logins lock after repeated failures. Saved database credentials are encrypted with Fernet.
- Every account's chats, connections, history and usage are private, and there is a per-user daily question limit.

**Error handling**
- Model errors, timeouts and 429s trigger failover to the next model, then the next tier. Exhausted quotas put a model on cooldown until its reset time.
- SQL errors are fed back to the model for self-correction instead of surfacing to the user.
- Empty or suspicious results (fan-out, impossible counts) lower the trust score instead of being presented as fact.
- Anomaly alerts use a robust statistic (median + MAD with leave-one-out) so one outlier doesn't hide the next.

**Trade-offs**
- **Free models over the best models:** latency is higher (p50 around 17 to 30 s), but the whole system costs nothing to run.
- **Several candidates over one:** more calls per hard question, but fewer wrong answers.
- **Rule-based grounding over LLM-as-judge by default:** it can miss subtle reasoning errors, but it is instant, free and never hallucinates itself.

---

## Contributing & License

Contributions are welcome. Open an issue to discuss a bug or an idea, or send a pull request:

1. Fork the repo and create a branch.
2. Run `pytest` and, for UI changes, `npm run lint && npm run typecheck` in `insightx-ui/`.
3. Describe what changed and why in the pull request.

**License:** not chosen yet. Until a license file is added, all rights are reserved.

---

<div align="center">
<br />
<strong>If InsightX is useful or interesting to you, consider giving it a star.</strong>
<br /><br />
<a href="https://github.com/anchalrajput25/InsightX/stargazers"><img src="https://img.shields.io/github/stars/anchalrajput25/InsightX?style=for-the-badge&logo=github&color=181717" alt="Stars" /></a>
<br /><br />
Built by <a href="https://github.com/anchalrajput25">Anchal Rajput</a>
</div>
