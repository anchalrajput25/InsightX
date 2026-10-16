#!/bin/sh
# Start the backend (inside the container only) and the Streamlit app.
# If either one stops, the container stops, so the host restarts it.

export INSIGHTX_BACKEND_URL="${INSIGHTX_BACKEND_URL:-http://127.0.0.1:8000}"

uvicorn backend.main:app --host 127.0.0.1 --port 8000 --timeout-keep-alive 75 &
BACKEND=$!

# Wait until the backend answers (up to 2 minutes).
python - <<'PY'
import time, urllib.request
for _ in range(120):
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/", timeout=2)
        break
    except Exception:
        time.sleep(1)
PY

streamlit run app.py \
    --server.port "${PORT:-7860}" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableXsrfProtection false \
    --server.enableCORS false \
    --browser.gatherUsageStats false &
FRONTEND=$!

# Stop when either process exits.
while kill -0 "$BACKEND" 2>/dev/null && kill -0 "$FRONTEND" 2>/dev/null; do
    sleep 5
done
kill "$BACKEND" "$FRONTEND" 2>/dev/null
exit 1
