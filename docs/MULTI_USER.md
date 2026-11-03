# Many people at the same time

InsightX is built for several people asking at once. Each person has their own login, chats
and daily limit, and requests run in parallel threads. Two things broke with many people
asking at once, and one thing loses data on free hosting. All three are now handled.

## 1. The shared free AI quota (fixed)

Every question needs several AI calls, and all users share the same free per-minute limits.
Before, when 30 people asked at once, the minute's quota was spread across all 30 questions.
Each question moved a little, then every remaining call failed at once with "busy", so
**nobody** got an answer.

Now:

- **Fair queue for questions** (`backend/ai_gateway/slots.py`). A few questions run at a
  time; the rest wait their turn in arrival order. The first people get their answer in
  seconds and the rest follow as the quota refills. If the wait is too long, the person gets
  a clear message: "Many people are asking questions right now ... try again in a minute".
- **Wait instead of fail** (`backend/ai_gateway/gateway.py`). When every free model is only at
  its per-minute limit, an AI call waits for the next free slot instead of failing. A used-up
  daily quota still fails at once, with the usual message.
- **No overshooting the limit** (`backend/ai_gateway/health.py`). Checking and counting a
  model's per-minute limit is one locked step, so parallel requests never send more than the
  free limit (overshooting causes 429 errors and a 60-second cooldown for everyone).

Measured with 30 people asking at once (4 AI calls each, 60 free calls per minute):

| | Answered | Over-limit calls | Finish times |
|---|---|---|---|
| Before | **0 of 30** | 0 | everyone failed after the first minute |
| After | **30 of 30** | 0 | first 12 in 2-6 s, the rest at 61-70 s as the quota refilled |

## 2. Everyone arriving right after a restart (fixed)

After the server restarts (free hosting sleeps when idle), the first request for a saved
database reads its schema. Before, every request arriving at that moment profiled the whole
database itself, holding connections until the pool ran out. Requests then failed with
"QueuePool limit reached" (only 40 of 80 succeeded with 20 people). Now one request reads the
schema and the others wait and reuse it (`backend/text_to_sql/schema_context.py`): 80 of 80
succeeded.

Work that needs no AI (dashboards, history, what-if reruns) was never limited: 60 people at
once gave 240 of 240 successful requests.

## 3. Keep accounts and history across restarts (setting)

Without `INSIGHTX_APP_DB_URL`, accounts, chats and history are saved in files on the server.
Render's free disk is wiped on every restart. Point it at a free Neon PostgreSQL database:

1. In Neon (neon.tech): open your project, then **Connect**. Switch **Connection pooling**
   off, so the host has no `-pooler` in it, and copy the connection string.
2. In Render: open the **insightx-api** service, then **Environment**, then
   **Add Environment Variable**. Set Key `INSIGHTX_APP_DB_URL` and Value the Neon string.
   Click **Save Changes**; Render redeploys by itself.
3. Never put this string in a file in the repository.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `INSIGHTX_MAX_CONCURRENT_QUESTIONS` | 4 | Questions answered at the same time (0 = no queue) |
| `INSIGHTX_QUESTION_QUEUE_SECONDS` | 150 | Longest wait for a turn before "busy, try again" |
| `INSIGHTX_LLM_QUEUE_SECONDS` | 90 | Longest wait per AI call for a free per-minute slot (0 = fail at once) |
| `INSIGHTX_USER_DAILY_QUESTIONS` | 100 (20 on Render) | Questions per person per day |
| `INSIGHTX_APP_DB_URL` | (files) | PostgreSQL for accounts, chats, history, usage |

The real ceiling is the free AI quota: about 5-10 questions a minute for the whole app. Adding
more free providers or a self-hosted model (`LLM_PROVIDER=vllm`) raises it without code changes.
