# InsightX Studio (new web UI)

A separate, modern web interface for the InsightX backend. It lives only in this
`insightx-ui` folder. No other file in the project was changed, and the Streamlit
app keeps working exactly as before. You can run both at the same time.

## Run it

You need Node.js 20 or newer (https://nodejs.org).

1. Start the backend as usual, on port 8000:
   `uvicorn backend.main:app --port 8000`
2. In this folder, the first time only:
   `npm install`
3. Start the UI:
   `npm run dev`
4. Open http://localhost:3000

For a faster production version: `npm run build`, then `npm run start`.

If your backend runs somewhere else, copy `.env.example` to `.env.local` and change
`INSIGHTX_BACKEND_URL`. Restart `npm run dev` (or build again) after changing it.

## How it talks to the backend

The browser only calls this app at `/api/...`. Next.js forwards those calls to the
backend (see `next.config.ts`), so the backend needed no CORS change. Sign-in uses
the backend's own `/auth` endpoints and the same token Streamlit uses. No API keys
are in this folder; AI keys stay in the backend's `.env`.

## Pages

| Page | Uses |
|---|---|
| Sign in / Sign up | /auth/status, /auth/login, /auth/signup |
| Database | /connect-postgres (PostgreSQL, MySQL, SQL Server), /connect-sqlite, /upload-folder + /import-uploaded-files |
| Ask (Auto / Ask / Investigate) | /query, /investigate, /investigate/route, /threads/{id}/history, /chats |
| Dashboard | /dashboard/overview, /dashboard/charts |
| Alerts | /alerts/* |
| History | /history |
| Traces | /traces |
| AI usage | /ai/usage, /ai/routes |
| Account | /auth/me, /auth/password, /auth/logout-everywhere |

The Benchmark page stays in Streamlit, because the backend has no API for it.

Keys: `Ctrl+K` search and ask, `Ctrl+B` collapse the sidebar, `N` new chat.

## Checks

`npm run lint`, `npm run typecheck`, `npm run build`.

## Remove it

Delete this folder. On Windows, from the project folder:

```
rmdir /s /q insightx-ui
```
