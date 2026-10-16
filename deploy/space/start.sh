#!/bin/sh
# Start the FastAPI backend (inside the container only) and the Next.js
# app on $PORT. If either one stops, the container stops, so the host
# restarts it.

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

cd insightx-ui
node node_modules/next/dist/bin/next start -p "${PORT:-7860}" -H 0.0.0.0 &
FRONTEND=$!
cd ..

# Stop when either process exits.
while kill -0 "$BACKEND" 2>/dev/null && kill -0 "$FRONTEND" 2>/dev/null; do
    sleep 5
done
kill "$BACKEND" "$FRONTEND" 2>/dev/null
exit 1
