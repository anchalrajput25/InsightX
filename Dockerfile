# InsightX in one container: the FastAPI backend runs inside on port
# 8000 and the Streamlit app is served on $PORT (7860 by default, the
# port Hugging Face Spaces expects).
#
#   docker build -t insightx .
#   docker run --env-file .env -p 7860:7860 insightx

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    PORT=7860

WORKDIR /app

# CPU-only PyTorch keeps the image small (the embedding model needs no GPU).
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch
COPY requirements.txt .
RUN pip install -r requirements.txt

# Download the embedding model now, so the first question is not slow.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY . .

# Hugging Face runs the container as user 1000.
RUN useradd --create-home --uid 1000 insightx \
    && mkdir -p logs temp_uploads \
    && chown -R insightx /app
USER insightx

EXPOSE 7860
CMD ["sh", "deploy/start.sh"]
