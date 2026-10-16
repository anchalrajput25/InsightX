# InsightX online (Hugging Face Space): one container with the Next.js
# app on $PORT (7860, the port Spaces expects) and the FastAPI backend
# inside on 127.0.0.1:8000. The browser only talks to the Next.js app,
# which forwards /api/... to the backend, so no API key reaches it.
#
# push_to_hf.bat copies this file to the root of the Space as Dockerfile.

# ---- 1. Build the Next.js app ----
FROM node:20-slim AS ui
WORKDIR /ui
ENV NEXT_TELEMETRY_DISABLED=1 \
    INSIGHTX_BACKEND_URL=http://127.0.0.1:8000
COPY insightx-ui/package.json insightx-ui/package-lock.json ./
RUN npm ci
COPY insightx-ui/ ./
RUN npm run build

# ---- 2. Backend + the built app ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=7860

WORKDIR /app

# Node runtime for "next start" (same Debian base as node:20-slim).
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ui /usr/local/bin/node /usr/local/bin/node

# CPU-only PyTorch keeps the image small (the embedding model needs no GPU).
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch
COPY requirements.txt .
RUN pip install -r requirements.txt

# Download the embedding model now, so the first question is not slow.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY . .
COPY --from=ui /ui /app/insightx-ui

# Hugging Face runs the container as user 1000.
RUN useradd --create-home --uid 1000 insightx \
    && mkdir -p logs temp_uploads \
    && chown -R insightx /app
USER insightx

EXPOSE 7860
CMD ["sh", "deploy/space/start.sh"]
