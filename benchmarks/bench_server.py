"""Start the real InsightX backend for the benchmark, with a stand-in AI.

Everything in the app runs for real: FastAPI, the fair question queue,
the database pool, schema retrieval, the safety gate, the read-only
executor, traces and history. Only the call to the AI model is replaced
by a stand-in that waits a set time and returns a fixed answer. So a load
test with 100 users costs nothing and does not use the free AI limits.

Started by run_benchmark.py; not meant to be run by hand.
    python benchmarks/bench_server.py --port 8765 --workdir <temp folder>
"""

import argparse
import os
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8765)
parser.add_argument("--workdir", required=True)
args = parser.parse_args()
WORK = Path(args.workdir)

# Keep the benchmark away from the person's own files in logs/ and temp_uploads/.
import backend.config as config  # noqa: E402

object.__setattr__(config.ingestion_settings, "upload_dir", WORK / "uploads")
(WORK / "uploads").mkdir(parents=True, exist_ok=True)
from backend.text_to_sql import observability  # noqa: E402

observability.LOG_FILE = WORK / "text_to_sql.jsonl"

from backend import llm_service  # noqa: E402

# ---------------------------------------------------------------- stand-in AI
STATE = {"delay": 0.0, "answers": {}}
STATS = defaultdict(list)  # job -> prompt sizes in characters
LOCK = threading.Lock()


def _answer_for(prompt: str):
    best = None
    for question, answer in STATE["answers"].items():
        if question in prompt and (best is None or len(question) > len(best[0])):
            best = (question, answer)
    return best[1] if best else None


def fake_call_llm(prompt, task="small", temperature=None, max_tokens=None):
    prompt = str(prompt or "")
    with LOCK:
        STATS[task].append(len(prompt))
    if STATE["delay"]:
        time.sleep(STATE["delay"])
    answer = _answer_for(prompt) or {}
    sql = answer.get("sql", "SELECT 1 AS one")
    if task == "rewrite":
        return "REWRITE: same\nCANONICAL: true"
    if task == "decompose":
        return "Single-step question."
    if task == "schema_link":
        return "Final Relevant Schema:\n" + answer.get("link", "")
    if task in ("sql_generate", "sql_candidate", "sql_repair"):
        return f"STATUS: SUCCESS\nSQL:\n{sql}\nCONFIDENCE: High"
    if task == "follow_ups":
        return '["Show it by month", "Show the top 5"]'
    if task == "context":
        return ""
    return "Grounded answer."


llm_service.call_llm = fake_call_llm

from fastapi import Body  # noqa: E402

from backend.main import app  # noqa: E402


@app.post("/bench/config")
def bench_config(body: dict = Body(...)):
    if "delay" in body:
        STATE["delay"] = float(body["delay"])
    if "answers" in body:
        STATE["answers"] = dict(body["answers"])
    with LOCK:
        STATS.clear()
    return {"ok": True}


@app.get("/bench/ai-stats")
def bench_ai_stats():
    with LOCK:
        return {job: list(sizes) for job, sizes in STATS.items()}


if __name__ == "__main__":
    import uvicorn

    # Same start command as on Render (see render.yaml).
    uvicorn.run(app, host="127.0.0.1", port=args.port, timeout_keep_alive=75, log_level="warning")
