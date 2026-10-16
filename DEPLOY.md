# Putting InsightX online (free)

InsightX runs as one Docker container: the backend runs inside it and
the Streamlit app is served on port 7860.

## What you need (all free)

1. A **Hugging Face** account (huggingface.co) to run the app.
2. A **Neon** account (neon.tech) for a PostgreSQL database that keeps
   accounts, chats, traces and saved answers safe when the app restarts.
3. At least one free AI key: Gemini, Groq or OpenRouter.

## Steps

1. In Neon, create a project and copy its connection string
   (it starts with `postgresql://`).
2. In Hugging Face, create a new **Space**, choose **Docker** and
   **Blank**, and make it Public or Private.
3. Upload the project files to the Space (Files → Add file → Upload),
   or push them with git. Do not upload `.env`, `logs/` or `data/`.
4. Put this at the very top of the Space's `README.md`:

   ```
   ---
   title: InsightX
   sdk: docker
   app_port: 7860
   ---
   ```
5. In the Space, open **Settings → Variables and secrets** and add
   these as **secrets**:
   - `INSIGHTX_APP_DB_URL`: the Neon connection string
   - `INSIGHTX_AUTH_SECRET`: a long random text (keep it forever)
   - `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`: your keys
6. The Space builds by itself (about 10 minutes the first time). Then
   open the Space link, create an account and connect a database.

The database you ask questions about must be reachable from the
internet (for example a Neon, Supabase or other online database).
A database on your own PC (localhost) cannot be reached from the
online app.

## Run the same container on your PC

With Docker Desktop installed, in the project folder:

```
docker compose up --build
```

Then open http://localhost:7860. This also starts a PostgreSQL
database for InsightX's own data.
