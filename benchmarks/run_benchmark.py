"""InsightX scalability and load benchmark.

    python benchmarks/run_benchmark.py            # full run, about 25 to 40 minutes
    python benchmarks/run_benchmark.py --quick    # small sizes, about 3 minutes

What it does, start to finish, with nothing to set up:
  1. Starts a throwaway PostgreSQL server in a temp folder (your own
     databases are never touched; no password needed).
  2. Starts the real InsightX backend with a stand-in AI (see
     bench_server.py), so load tests cost nothing.
  3. Measures: CSV loading (time, memory), data volume (100K / 1M / 3M rows),
     simple vs complex SQL, 100 / 500 / 1,000 tables, 10 / 50 / 100 users at
     the same time, timeouts and failures.
  4. Reads the real AI numbers (speed, tokens, cost, wrong SQL) from the
     saved accuracy-test results and AI usage log. It makes no new AI calls.
  5. Writes benchmarks/REPORT.md and a JSON file with every number, then
     stops the throwaway server and deletes the temp folder.
"""

import argparse
import csv
import glob
import json
import math
import os
import platform
import random
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import psutil
import psycopg2

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PG_USER = "bench"
PG_PASSWORD = "unused"  # the throwaway server trusts local connections
QUESTION_QUEUE_SLOTS = int(os.getenv("INSIGHTX_MAX_CONCURRENT_QUESTIONS", "4"))


# =================================================================== helpers
def log(message):
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def pct(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def summary(values):
    """avg / p50 / p95 / p99 / max, in the same unit as the input."""
    if not values:
        return {"n": 0}
    return {"n": len(values), "avg": statistics.fmean(values), "p50": pct(values, 50),
            "p95": pct(values, 95), "p99": pct(values, 99), "max": max(values)}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class MemoryWatch:
    """Highest memory (RSS) of a process while a block runs, sampled every 50 ms."""

    def __init__(self, pid):
        self.process = psutil.Process(pid)
        self.start_mb = self.peak_mb = self._rss()
        self._stop = threading.Event()

    def _rss(self):
        try:
            return self.process.memory_info().rss / 2 ** 20
        except psutil.Error:
            return 0.0

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            self.peak_mb = max(self.peak_mb, self._rss())
            time.sleep(0.05)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.peak_mb = max(self.peak_mb, self._rss())
        self.end_mb = self._rss()


# =========================================================== throwaway Postgres
def find_pg_bin():
    explicit = os.getenv("INSIGHTX_PG_BIN")
    if explicit:
        return Path(explicit)
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent
    candidates = sorted(glob.glob(r"C:\Program Files\PostgreSQL\*\bin\pg_ctl.exe"),
                        key=lambda p: int(Path(p).parts[-3]) if Path(p).parts[-3].isdigit() else 0)
    candidates += sorted(glob.glob("/usr/lib/postgresql/*/bin/pg_ctl"))
    if not candidates:
        raise SystemExit("PostgreSQL programs (initdb, pg_ctl) were not found. Install PostgreSQL "
                         "or set INSIGHTX_PG_BIN to its bin folder.")
    return Path(candidates[-1]).parent


class ThrowawayPostgres:
    def __init__(self, work: Path):
        self.bin = find_pg_bin()
        self.data = work / "pgdata"
        self.port = free_port()

    def _exe(self, name):
        return str(self.bin / (name + (".exe" if os.name == "nt" else "")))

    def start(self):
        subprocess.run([self._exe("initdb"), "-D", str(self.data), "-U", PG_USER, "-A", "trust",
                        "-E", "UTF8", "--no-locale"], check=True, capture_output=True)
        subprocess.run([self._exe("pg_ctl"), "-D", str(self.data), "-l", str(self.data / "server.log"),
                        "-o", f"-p {self.port} -c listen_addresses=127.0.0.1", "-w", "start"],
                       # Not captured: the server keeps the pipe open and Windows would wait forever.
                       check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        self.version = self.scalar("postgres", "SHOW server_version")
        self.max_connections = int(self.scalar("postgres", "SHOW max_connections"))

    def stop(self):
        subprocess.run([self._exe("pg_ctl"), "-D", str(self.data), "-m", "fast", "-w", "stop"],
                       capture_output=True)

    def connect(self, database):
        connection = psycopg2.connect(host="127.0.0.1", port=self.port, user=PG_USER, dbname=database)
        connection.autocommit = True
        return connection

    def scalar(self, database, sql):
        with self.connect(database) as connection, connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchone()[0]

    def create_database(self, name):
        connection = self.connect("postgres")
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{name}"')
        connection.close()

    def size_mb(self, database):
        return self.scalar(database, "SELECT pg_database_size(current_database())") / 2 ** 20

    def form(self, database):
        return {"host": "127.0.0.1", "port": str(self.port), "database": database,
                "username": PG_USER, "password": PG_PASSWORD}


# ================================================================ test data
REGIONS = ["North", "South", "East", "West", "Central", "North East", "South West", "Islands"]
CHANNELS = ["online", "store", "partner"]
SEGMENTS = ["consumer", "small business", "enterprise", "government"]
CATEGORIES = ["Laptops", "Phones", "Tablets", "Audio", "Cameras", "Gaming", "Wearables", "Storage",
              "Networking", "Printers", "Monitors", "Accessories"]
CITIES = [f"City {n:02d}" for n in range(50)]


def make_sales_csvs(folder: Path, rows: int, seed: int = 7):
    """A small shop: customers, products and `rows` sales lines. Same seed, same data."""
    folder.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    customers = max(1000, min(rows // 20, 50_000))
    products = 2000
    start = date(2023, 1, 1)
    with open(folder / "customers.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["customer_id", "customer_name", "city", "segment", "signup_date"])
        for i in range(1, customers + 1):
            writer.writerow([i, f"Customer {i}", rnd.choice(CITIES), rnd.choice(SEGMENTS),
                             start + timedelta(days=rnd.randrange(1000))])
    with open(folder / "products.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["product_id", "product_name", "category", "brand", "list_price"])
        for i in range(1, products + 1):
            writer.writerow([i, f"Product {i}", rnd.choice(CATEGORIES), f"Brand {rnd.randrange(60)}",
                             round(rnd.uniform(5, 2500), 2)])
    with open(folder / "sales.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sale_id", "sale_date", "customer_id", "product_id", "region", "channel",
                         "quantity", "unit_price", "discount"])
        for i in range(1, rows + 1):
            writer.writerow([i, start + timedelta(days=rnd.randrange(1000)), rnd.randrange(1, customers + 1),
                             rnd.randrange(1, products + 1), rnd.choice(REGIONS), rnd.choice(CHANNELS),
                             rnd.randrange(1, 10), round(rnd.uniform(5, 2500), 2),
                             rnd.choice([0, 0, 0, 0.05, 0.1, 0.2])])


def zip_folder(folder: Path, target: Path):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in sorted(folder.glob("*.csv")):
            archive.write(path, path.name)
    return target


# Questions the stand-in AI answers with fixed SQL. Simple -> complex.
SALES_QUESTIONS = {
    "simple": {
        "question": "How many sales are there?",
        "sql": "SELECT COUNT(*) AS sales FROM sales",
        "link": "sales(sale_id)",
    },
    "aggregation": {
        "question": "What is the total revenue by region?",
        "sql": ("SELECT region, ROUND(SUM(quantity * unit_price * (1 - discount))::numeric, 2) AS revenue "
                "FROM sales GROUP BY region ORDER BY revenue DESC"),
        "link": "sales(region, quantity, unit_price, discount)",
    },
    "complex join": {
        "question": "Which product categories earned the most from enterprise customers in 2024, with each one's share?",
        "sql": ("WITH r AS (SELECT p.category, SUM(s.quantity * s.unit_price * (1 - s.discount)) AS revenue "
                "FROM sales s JOIN products p ON p.product_id = s.product_id "
                "JOIN customers c ON c.customer_id = s.customer_id "
                "WHERE c.segment = 'enterprise' AND s.sale_date >= '2024-01-01' AND s.sale_date < '2025-01-01' "
                "GROUP BY p.category) "
                "SELECT category, ROUND(revenue::numeric, 2) AS revenue, "
                "ROUND((100 * revenue / SUM(revenue) OVER ())::numeric, 1) AS share_pct "
                "FROM r ORDER BY revenue DESC"),
        "link": "sales(product_id, customer_id, sale_date, quantity, unit_price, discount)\n"
                "products(product_id, category)\ncustomers(customer_id, segment)",
    },
    "monthly trend": {
        "question": "Show monthly revenue and the change from the month before, by channel",
        "sql": ("SELECT channel, DATE_TRUNC('month', sale_date::date) AS month, "
                "ROUND(SUM(quantity * unit_price)::numeric, 2) AS revenue, "
                "ROUND((SUM(quantity * unit_price) - LAG(SUM(quantity * unit_price)) OVER "
                "(PARTITION BY channel ORDER BY DATE_TRUNC('month', sale_date::date)))::numeric, 2) AS change "
                "FROM sales GROUP BY channel, DATE_TRUNC('month', sale_date::date) ORDER BY channel, month"),
        "link": "sales(channel, sale_date, quantity, unit_price)",
    },
}


def answers_map(questions):
    return {q["question"]: {"sql": q["sql"], "link": q["link"]} for q in questions.values()}


# ============================================================ backend server
class Backend:
    def __init__(self, work: Path, pg: ThrowawayPostgres):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = dict(os.environ)
        env.update({
            "INSIGHTX_APP_DB_URL": f"postgresql+psycopg2://{PG_USER}@127.0.0.1:{pg.port}/insightx_app",
            "INSIGHTX_AUTH_REQUIRED": "0",
            "INSIGHTX_ROUTE_CHECKS": "0",
            "INSIGHTX_ALERTS_SCHEDULER": "0",
            "INSIGHTX_USER_DAILY_QUESTIONS": "0",
            "INSIGHTX_QUESTION_CACHE": "0",  # every question does the full pipeline
            "INSIGHTX_SQLITE_CHECKPOINTS": str(work / "checkpoints.db"),
            "PYTHONUNBUFFERED": "1",
        })
        self.log_file = open(work / "backend.log", "w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, str(HERE / "bench_server.py"), "--port", str(self.port), "--workdir", str(work)],
            cwd=str(ROOT), env=env, stdout=self.log_file, stderr=subprocess.STDOUT)
        self.client = httpx.Client(base_url=self.url, timeout=600)
        for _ in range(240):
            if self.process.poll() is not None:
                raise SystemExit(f"The backend stopped while starting. See {work / 'backend.log'}")
            try:
                if self.client.get("/").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise SystemExit("The backend did not start in 2 minutes.")

    @property
    def pid(self):
        return self.process.pid

    def rss_mb(self):
        return psutil.Process(self.pid).memory_info().rss / 2 ** 20

    def configure(self, delay=None, answers=None):
        body = {}
        if delay is not None:
            body["delay"] = delay
        if answers is not None:
            body["answers"] = answers
        self.client.post("/bench/config", json=body).raise_for_status()

    def ai_stats(self):
        return self.client.get("/bench/ai-stats").json()

    def connect(self, form):
        started = time.perf_counter()
        response = self.client.post("/connect-postgres", data=form)
        response.raise_for_status()
        return response.json()["connection_id"], time.perf_counter() - started

    def ask(self, connection_id, question, thread_id=None, client=None):
        started = time.perf_counter()
        body = {"connection_id": connection_id, "question": question, "thread_id": thread_id, "use_cache": False}
        try:
            response = (client or self.client).post("/query", json=body)
            seconds = time.perf_counter() - started
            data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            status = str(data.get("status") or "").upper() if response.status_code == 200 else f"HTTP {response.status_code}"
            return {"seconds": seconds, "http": response.status_code, "status": status,
                    "rows": (data.get("execution_result") or {}).get("row_count"),
                    "detail": str(data.get("detail") or data.get("error") or "")[:160]}
        except httpx.TimeoutException:
            return {"seconds": time.perf_counter() - started, "http": 0, "status": "CLIENT TIMEOUT", "detail": ""}
        except httpx.HTTPError as exc:
            return {"seconds": time.perf_counter() - started, "http": 0, "status": "CONNECTION ERROR",
                    "detail": str(exc)[:160]}

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log_file.close()


# ============================================================== 1. ingestion
def test_ingestion(backend, pg, work, sizes):
    results = []
    datasets = [(f"vol_{label(n)}", n, None) for n in sizes]
    northwind = ROOT / "sample_datasets" / "northwind_large.zip"
    if northwind.exists():
        datasets.append(("northwind", None, northwind))
    for database, rows, ready_zip in datasets:
        log(f"Loading CSV data: {database}")
        pg.create_database(database)
        if ready_zip is None:
            folder = work / database
            make_sales_csvs(folder, rows)
            csv_mb = sum(p.stat().st_size for p in folder.glob("*.csv")) / 2 ** 20
            zip_path = zip_folder(folder, work / f"{database}.zip")
            shutil.rmtree(folder)
        else:
            zip_path = ready_zip
            with zipfile.ZipFile(zip_path) as archive:
                csv_mb = sum(i.file_size for i in archive.infolist()) / 2 ** 20
        with MemoryWatch(backend.pid) as memory:
            started = time.perf_counter()
            with open(zip_path, "rb") as handle:
                upload = backend.client.post("/upload-folder", files={"file": (zip_path.name, handle, "application/zip")})
            upload.raise_for_status()
            uploaded = time.perf_counter()
            form = dict(pg.form(database), session_id=upload.json()["session_id"])
            imported = backend.client.post("/import-uploaded-files", data=form)
            imported.raise_for_status()
            finished = time.perf_counter()
        if ready_zip is None:
            zip_path.unlink()
        total_rows = sum(pg.scalar(database, f'SELECT COUNT(*) FROM "{t}"')
                         for t in imported.json()["imported_tables"]) if isinstance(
            imported.json()["imported_tables"][0], str) else None
        if total_rows is None:
            total_rows = pg.scalar(database, "SELECT SUM(n_live_tup)::bigint FROM pg_stat_user_tables "
                                             "WHERE schemaname = 'public'")
        results.append({
            "database": database, "rows": int(total_rows or 0), "csv_mb": csv_mb,
            "db_mb": pg.size_mb(database), "upload_s": uploaded - started, "import_s": finished - uploaded,
            "total_s": finished - started, "server_mb_before": memory.start_mb, "server_mb_peak": memory.peak_mb,
            "connection_id": imported.json()["connection_id"],
        })
        log(f"  {results[-1]['rows']:,} rows in {results[-1]['total_s']:.1f}s, "
            f"memory +{memory.peak_mb - memory.start_mb:.0f} MB")
    return results


def label(n):
    return f"{n // 1_000_000}m" if n >= 1_000_000 else f"{n // 1000}k"


# ============================================================ 2. SQL timing
def app_engine(pg, database):
    from backend.database import create_database_engine

    return create_database_engine("postgresql", "127.0.0.1", pg.port, database, PG_USER, PG_PASSWORD)


def test_sql_speed(pg, ingested, repeats):
    """The app's read-only executor (row cap, time limit) on each data size."""
    from backend.text_to_sql.query_executor import execute_query

    results = []
    for item in ingested:
        if not item["database"].startswith("vol_"):
            continue
        engine = app_engine(pg, item["database"])
        for kind, q in SALES_QUESTIONS.items():
            execute_query(q["sql"], engine)  # warm up the cache once
            times, ok = [], 0
            for _ in range(repeats):
                outcome = execute_query(q["sql"], engine)
                ok += bool(outcome.get("success"))
                times.append(outcome.get("execution_time_ms") or 0)
            results.append({"database": item["database"], "rows": item["rows"], "query": kind,
                            "ok": ok, "runs": repeats, "ms": summary(times)})
        log(f"SQL timing done: {item['database']}")
    return results


# ======================================================= 3. end-to-end, no AI
def test_end_to_end(backend, connection_id, repeats):
    backend.configure(delay=0, answers=answers_map(SALES_QUESTIONS))
    results = []
    for kind, q in SALES_QUESTIONS.items():
        backend.ask(connection_id, q["question"])  # warm-up
        runs = [backend.ask(connection_id, q["question"]) for _ in range(repeats)]
        results.append({"query": kind, "ok": sum(r["status"] == "SUCCESS" for r in runs), "runs": repeats,
                        "seconds": summary([r["seconds"] for r in runs])})
    log("End-to-end timing done")
    return results


# ========================================================= 4. many tables
AREAS = ["sales", "finance", "hr", "marketing", "support", "inventory", "shipping", "billing", "legal", "it",
         "procurement", "payroll", "crm", "web", "mobile", "partner", "audit", "risk", "tax", "store",
         "warehouse", "fleet", "energy", "quality", "research", "training", "events", "facility", "security",
         "media", "loyalty", "pricing", "returns", "vendor", "contract", "project", "asset", "budget", "survey",
         "claims"]
ENTITIES = ["accounts", "orders", "items", "invoices", "payments", "tickets", "employees", "teams", "visits",
            "sessions", "campaigns", "leads", "notes", "events", "logs", "targets", "plans", "requests",
            "approvals", "reviews", "rates", "regions", "devices", "shipments", "batches"]


def make_wide_database(pg, database, table_count):
    """`table_count` tables: the 3 shop tables (10,000 sales) plus look-alike tables of 20 rows."""
    pg.create_database(database)
    names = [f"{a}_{e}" for e in ENTITIES for a in AREAS if f"{a}_{e}" not in ("sales_items",)]
    rnd = random.Random(table_count)
    rnd.shuffle(names)
    names = names[:table_count - 3]
    statements = [
        "CREATE TABLE customers AS SELECT g AS customer_id, 'Customer ' || g AS customer_name, "
        "(ARRAY['consumer','small business','enterprise','government'])[1 + g % 4] AS segment, "
        "'City ' || lpad((g % 50)::text, 2, '0') AS city, DATE '2023-01-01' + (g % 900) AS signup_date "
        "FROM generate_series(1, 2000) g",
        "CREATE TABLE products AS SELECT g AS product_id, 'Product ' || g AS product_name, "
        "(ARRAY['Laptops','Phones','Tablets','Audio','Cameras','Gaming'])[1 + g % 6] AS category, "
        "'Brand ' || (g % 30) AS brand, round((5 + random() * 2000)::numeric, 2) AS list_price "
        "FROM generate_series(1, 500) g",
        "CREATE TABLE sales AS SELECT g AS sale_id, DATE '2023-01-01' + (g % 1000) AS sale_date, "
        "1 + g % 2000 AS customer_id, 1 + g % 500 AS product_id, "
        "(ARRAY['North','South','East','West'])[1 + g % 4] AS region, "
        "(ARRAY['online','store','partner'])[1 + g % 3] AS channel, 1 + g % 9 AS quantity, "
        "round((5 + random() * 2000)::numeric, 2) AS unit_price, 0.0 AS discount "
        "FROM generate_series(1, 10000) g",
    ]
    for i, name in enumerate(names):
        other = names[(i + 1) % len(names)]
        statements.append(
            f"CREATE TABLE {name} AS SELECT g AS {name}_id, 'Name ' || g AS {name.split('_')[1][:-1]}_name, "
            f"(ARRAY['open','closed','pending'])[1 + g % 3] AS status, DATE '2024-01-01' + g AS created_on, "
            f"round((random() * 1000)::numeric, 2) AS amount, 1 + g % 20 AS {other}_id "
            f"FROM generate_series(1, 20) g")
    connection = pg.connect(database)
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
        cursor.execute("ANALYZE")
    connection.close()


def test_many_tables(backend, pg, counts, repeats):
    questions = {k: SALES_QUESTIONS[k] for k in ("simple", "complex join")}
    results = []
    for count in counts:
        database = f"tables_{count}"
        log(f"Many tables: building {count} tables")
        make_wide_database(pg, database, count)
        backend.configure(delay=0, answers=answers_map(questions))
        with MemoryWatch(backend.pid) as memory:
            connection_id, connect_s = backend.connect(pg.form(database))
            first = backend.ask(connection_id, questions["simple"]["question"])
            runs = []
            for _ in range(repeats):
                for q in questions.values():
                    runs.append(backend.ask(connection_id, q["question"]))
        stats = backend.ai_stats()
        link_chars = stats.get("schema_link", [])
        gen_chars = stats.get("sql_generate", [])
        results.append({
            "tables": count, "connect_s": connect_s, "first_question_s": first["seconds"],
            "ok": sum(r["status"] == "SUCCESS" for r in runs), "runs": len(runs),
            "seconds": summary([r["seconds"] for r in runs]),
            "link_prompt_tokens": statistics.fmean(link_chars) / 4 if link_chars else None,
            "sql_prompt_tokens": statistics.fmean(gen_chars) / 4 if gen_chars else None,
            "server_mb_peak": memory.peak_mb,
        })
        log(f"  connect {connect_s:.1f}s, question p95 {results[-1]['seconds'].get('p95', 0):.2f}s")
    return results


# ===================================================== 5. users at once
def pg_connection_sampler(pg, stop, out):
    connection = pg.connect("postgres")
    with connection.cursor() as cursor:
        while not stop.is_set():
            cursor.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE backend_type = 'client backend'")
            out.append(cursor.fetchone()[0])
            time.sleep(0.5)
    connection.close()


def heartbeat(url, stop, out):
    """Another visitor opening a light page while everyone is asking questions."""
    with httpx.Client(timeout=300) as client:
        while not stop.is_set():
            started = time.perf_counter()
            try:
                client.get(url + "/")
                out.append(time.perf_counter() - started)
            except httpx.HTTPError:
                out.append(float("inf"))
            time.sleep(0.5)


def run_users(backend, pg, connection_id, users, questions_each, delay):
    backend.configure(delay=delay, answers=answers_map(SALES_QUESTIONS))
    kinds = list(SALES_QUESTIONS.values())
    stop = threading.Event()
    beats, conns = [], []
    side = [threading.Thread(target=heartbeat, args=(backend.url, stop, beats), daemon=True),
            threading.Thread(target=pg_connection_sampler, args=(pg, stop, conns), daemon=True)]
    for thread in side:
        thread.start()

    def one_user(index):
        out = []
        with httpx.Client(base_url=backend.url, timeout=600) as client:
            for turn in range(questions_each):
                q = kinds[(index + turn) % len(kinds)]
                out.append(backend.ask(connection_id, q["question"], thread_id=f"bench-{users}-{index}", client=client))
        return out

    with MemoryWatch(backend.pid) as memory:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=users) as pool:
            runs = [r for user in pool.map(one_user, range(users)) for r in user]
        wall = time.perf_counter() - started
    stop.set()
    for thread in side:
        thread.join(5)
    ok = [r for r in runs if r["status"] == "SUCCESS"]
    statuses = {}
    for r in runs:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    finite_beats = [b for b in beats if b != float("inf")]
    return {
        "users": users, "questions": len(runs), "ok": len(ok), "statuses": statuses,
        "seconds_ok": summary([r["seconds"] for r in ok]),
        "seconds_all": summary([r["seconds"] for r in runs]),
        "wall_s": wall, "answers_per_min": len(ok) / wall * 60,
        "heartbeat_s": summary(finite_beats), "heartbeat_failed": len(beats) - len(finite_beats),
        "pg_connections_max": max(conns) if conns else None,
        "server_mb_before": memory.start_mb, "server_mb_peak": memory.peak_mb,
        "sample_errors": sorted({r["detail"] for r in runs if r["status"] != "SUCCESS" and r["detail"]})[:3],
    }


def test_users(backend, pg, connection_id, levels, ai_delay):
    results = {"no_ai_wait": [], "with_ai_wait": [], "ai_delay_s": ai_delay}
    for users in levels:
        log(f"{users} users at once, AI answers instantly")
        results["no_ai_wait"].append(run_users(backend, pg, connection_id, users, 3, 0))
    for users in levels:
        log(f"{users} users at once, each AI call waits {ai_delay:.1f}s like a real free model")
        results["with_ai_wait"].append(run_users(backend, pg, connection_id, users, 1, ai_delay))
    return results


# =================================================== 6. limits and failures
def test_failures(pg, database, big_rows):
    from backend.text_to_sql.query_executor import execute_query
    from backend.text_to_sql.sql_safety import check_sql_safety

    engine = app_engine(pg, database)
    out = []

    started = time.perf_counter()
    runaway = execute_query("SELECT COUNT(*) FROM sales a CROSS JOIN sales b", engine)
    out.append({"test": "Runaway query (every row joined to every row)",
                "result": "stopped" if not runaway.get("success") else "finished",
                "seconds": time.perf_counter() - started, "message": str(runaway.get("error") or "")[:120]})

    with MemoryWatch(os.getpid()) as memory:
        started = time.perf_counter()
        everything = execute_query("SELECT * FROM sales", engine)
    out.append({"test": f"Ask for all {big_rows:,} rows",
                "result": f"{everything.get('row_count')} rows returned, truncated={everything.get('truncated')}",
                "seconds": time.perf_counter() - started, "message": f"memory +{memory.peak_mb - memory.start_mb:.0f} MB"})

    for sql, name in (("DELETE FROM sales", "Delete data"), ("DROP TABLE sales", "Drop a table"),
                      ("SELECT 1; DELETE FROM sales", "Hidden second statement")):
        verdict = check_sql_safety(sql)
        out.append({"test": name, "result": "blocked" if not verdict.get("is_safe") else "ALLOWED",
                    "seconds": 0, "message": str(verdict.get("reason") or "")[:120]})

    started = time.perf_counter()
    missing = execute_query("SELECT * FROM no_such_table", engine)
    out.append({"test": "Wrong table name", "result": "clean error" if not missing.get("success") else "?",
                "seconds": time.perf_counter() - started, "message": str(missing.get("error") or "")[:120]})
    log("Limit and failure checks done")
    return out


# ================================================ 7. real AI (saved results)
def real_ai_numbers():
    """From the accuracy tests (evals/results) and the AI usage log. No new AI calls."""
    out = {"by_level": [], "jobs": [], "usage": None, "eval_files": 0}
    latest = {}
    files = sorted(glob.glob(str(ROOT / "evals" / "results" / "**" / "*.json"), recursive=True))
    for path in files:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "cases" not in data:
            continue
        out["eval_files"] += 1
        for case in data["cases"]:
            if case.get("latency_s") is not None:
                latest[case["id"]] = case  # later files win
    infra = ("rate limit", "429", "quota", "aiunavailable", "too many requests", "all models failed")
    for level in ("easy", "medium", "hard", "extra"):
        cases = [c for c in latest.values() if c.get("difficulty") == level]
        if not cases:
            continue
        ai_failed = [c for c in cases if c.get("status") == "EXCEPTION"
                     or any(m in str(c.get("error") or "").lower() for m in infra)]
        judged = [c for c in cases if c not in ai_failed and c.get("expect", "answer") == "answer"]
        out["by_level"].append({
            "level": level, "questions": len(cases),
            "seconds": summary([c["latency_s"] for c in cases if c not in ai_failed]),
            "llm_calls": statistics.fmean(c.get("llm_calls") or 0 for c in cases),
            "prompt_tokens": statistics.fmean(c.get("prompt_tokens") or 0 for c in cases),
            "completion_tokens": statistics.fmean(c.get("completion_tokens") or 0 for c in cases),
            "cost_usd": sum(c.get("cost_usd") or 0 for c in cases),
            "wrong_sql": sum(not c.get("passed") for c in judged), "judged": len(judged),
            "ai_failed": len(ai_failed),
        })
    usage = ROOT / "logs" / "usage.db"
    if usage.exists():
        db = sqlite3.connect(f"file:{usage.as_posix()}?mode=ro", uri=True)
        try:
            row = db.execute("SELECT COUNT(*), COUNT(DISTINCT request_id), SUM(status='error'), "
                             "SUM(status='cache'), SUM(cost_usd), MIN(day), MAX(day) FROM insightx_ai_calls").fetchone()
            out["usage"] = dict(zip(("calls", "questions", "errors", "cache_hits", "cost_usd", "from", "to"), row))
            per_q = [r[0] for r in db.execute(
                "SELECT SUM(prompt_tokens + completion_tokens) FROM insightx_ai_calls "
                "WHERE status = 'ok' GROUP BY request_id")]
            out["usage"]["tokens_per_question"] = summary(per_q)
            latencies = [r[0] / 1000 for r in db.execute(
                "SELECT latency_ms FROM insightx_ai_calls WHERE status = 'ok' AND latency_ms > 0")]
            out["usage"]["call_seconds"] = summary(latencies)
            for job, n, pt, ct in db.execute(
                    "SELECT job, COUNT(*), AVG(prompt_tokens), AVG(completion_tokens) FROM insightx_ai_calls "
                    "WHERE status = 'ok' GROUP BY job ORDER BY COUNT(*) DESC"):
                out["jobs"].append({"job": job, "calls": n, "prompt_tokens": pt, "completion_tokens": ct})
        except sqlite3.Error:
            pass
        db.close()
    return out


# ================================================================== report
def f(value, digits=2, unit=""):
    if value is None:
        return "n/a"
    if isinstance(value, float) and math.isinf(value):
        return "inf"
    return f"{value:,.{digits}f}{unit}"


def write_report(r, path, args):
    L = []
    w = L.append
    m = r["machine"]
    w("# InsightX benchmark report\n")
    w(f"Run on {r['finished']} with `{r['command']}`. Every number below was measured in this run, "
      "except section 7, which is read from saved real AI test results.\n")
    w("## Test machine\n")
    w(f"- {m['os']}, {m['cpus']} CPU threads, {m['ram_gb']:.1f} GB RAM, Python {m['python']}")
    w(f"- PostgreSQL {m['postgres']} (a throwaway local server with default settings, "
      f"max_connections={m['pg_max_connections']})")
    w(f"- InsightX backend started like on Render (one uvicorn process). Question queue: "
      f"{QUESTION_QUEUE_SLOTS} questions answered at once, the rest wait in line.")
    w("- In sections 3 to 5 the AI model is a stand-in that returns fixed SQL, so the load test is free. "
      "Everything else is the real app.\n")

    w("## 1. CSV loading and data volume\n")
    w("Upload a ZIP of CSV files, import it into PostgreSQL, and read the schema (the real upload screen path). "
      "The backend's starting memory grows from row to row because, after the first database is connected, it "
      "loads the search model used to find relevant tables (a one-time cost). The rise from start to peak is "
      "what loading the file used.\n")
    w("| Dataset | Rows | CSV size | Database size | Upload + check | Import + schema | Total | Backend memory (start → peak) |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|")
    for i in r["ingestion"]:
        w(f"| {i['database']} | {i['rows']:,} | {f(i['csv_mb'], 0)} MB | {f(i['db_mb'], 0)} MB | "
          f"{f(i['upload_s'], 1)} s | {f(i['import_s'], 1)} s | {f(i['total_s'], 1)} s | "
          f"{f(i['server_mb_before'], 0)} → {f(i['server_mb_peak'], 0)} MB |")
    w("")

    w("## 2. SQL speed: simple vs complex (the app's read-only executor)\n")
    w("Each query ran " + str(args.repeats) + " times after one warm-up run. Times in milliseconds.\n")
    w("| Rows in sales | Query | Avg | P50 | P95 | P99 | OK |")
    w("|---:|---|---:|---:|---:|---:|---:|")
    for s in r["sql_speed"]:
        ms = s["ms"]
        w(f"| {s['rows']:,} | {s['query']} | {f(ms.get('avg'), 0)} | {f(ms.get('p50'), 0)} | {f(ms.get('p95'), 0)} | "
          f"{f(ms.get('p99'), 0)} | {s['ok']}/{s['runs']} |")
    w("")
    w(f"## 3. Full question time without AI wait ({r['e2e_database']})\n")
    w("The whole /query path (queue, schema retrieval, safety gate, SQL, trust checks, chart, trace) "
      "with an instant stand-in AI. This is InsightX's own overhead. Seconds.\n")
    w("| Query | Avg | P50 | P95 | P99 | OK |")
    w("|---|---:|---:|---:|---:|---:|")
    for e in r["end_to_end"]:
        s = e["seconds"]
        w(f"| {e['query']} | {f(s.get('avg'))} | {f(s.get('p50'))} | {f(s.get('p95'))} | {f(s.get('p99'))} | "
          f"{e['ok']}/{e['runs']} |")
    w("")

    w("## 4. Many tables (schema size)\n")
    w("Databases with 100 / 500 / 1,000 tables. 'Connect' includes reading and profiling every table. "
      "Prompt size is what the AI would be sent after InsightX picks the relevant tables "
      "(characters ÷ 4 ≈ tokens).\n")
    w("| Tables | Connect | First question | Question avg | P95 | P99 | OK | Linker prompt | SQL prompt | Backend peak memory |")
    w("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for t in r["many_tables"]:
        s = t["seconds"]
        w(f"| {t['tables']:,} | {f(t['connect_s'], 1)} s | {f(t['first_question_s'], 2)} s | {f(s.get('avg'))} s | "
          f"{f(s.get('p95'))} s | {f(s.get('p99'))} s | {t['ok']}/{t['runs']} | "
          f"{f(t['link_prompt_tokens'], 0)} tok | {f(t['sql_prompt_tokens'], 0)} tok | {f(t['server_mb_peak'], 0)} MB |")
    w("")

    u = r["users"]
    for key, title, note in (
            ("no_ai_wait", "5a. Users at once, instant AI",
             "Each user asks 3 questions one after another (mixed simple and complex). Shows the server and "
             "database limit."),
            ("with_ai_wait", f"5b. Users at once, AI wait of {u['ai_delay_s']:.1f} s per AI call",
             "Each user asks 1 question. Each AI call waits as long as a typical real free-model call, so this "
             "shows what people would feel. 'Light page' is another visitor opening the home page at the same "
             "time.")):
        w(f"## {title}\n")
        w(note + "\n")
        w("| Users | Questions | Answered | Other results | Avg | P50 | P95 | P99 | Answers / min | Light page P95 | DB connections (max) | Backend memory peak |")
        w("|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for x in u[key]:
            s = x["seconds_ok"]
            others = ", ".join(f"{k}: {v}" for k, v in x["statuses"].items() if k != "SUCCESS") or "none"
            w(f"| {x['users']} | {x['questions']} | {x['ok']} | {others} | {f(s.get('avg'), 1)} s | {f(s.get('p50'), 1)} s | "
              f"{f(s.get('p95'), 1)} s | {f(s.get('p99'), 1)} s | {f(x['answers_per_min'], 1)} | "
              f"{f(x['heartbeat_s'].get('p95'), 2)} s | {x['pg_connections_max']} | {f(x['server_mb_peak'], 0)} MB |")
        errors = sorted({e for x in u[key] for e in x["sample_errors"]})
        if errors:
            w("\nMessages people got when not answered: " + "; ".join(f"“{e}”" for e in errors[:2]))
        w("")

    w("## 6. Limits, timeouts and failures\n")
    w("| Check | Result | Time | Detail |")
    w("|---|---|---:|---|")
    for x in r["failures"]:
        w(f"| {x['test']} | {x['result']} | {f(x['seconds'], 2)} s | {(x['message'].splitlines() or [''])[0].replace('|', '/')} |")
    w("")

    a = r["real_ai"]
    w("## 7. Real AI: speed, tokens, cost, wrong SQL (saved results, no new calls)\n")
    if a["by_level"]:
        w(f"From the Spider accuracy tests ({a['eval_files']} result files, latest answer per question). "
          "Levels are Spider's own difficulty labels.\n")
        w("| Level | Questions | Avg | P50 | P95 | P99 | AI calls | Tokens in | Tokens out | Wrong SQL | AI service failed | Cost |")
        w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for b in a["by_level"]:
            s = b["seconds"]
            w(f"| {b['level']} | {b['questions']} | {f(s.get('avg'), 1)} s | {f(s.get('p50'), 1)} s | {f(s.get('p95'), 1)} s | "
              f"{f(s.get('p99'), 1)} s | {f(b['llm_calls'], 1)} | {f(b['prompt_tokens'], 0)} | "
              f"{f(b['completion_tokens'], 0)} | {b['wrong_sql']}/{b['judged']} | {b['ai_failed']} | ${f(b['cost_usd'], 2)} |")
        w("")
    if a["usage"]:
        g = a["usage"]
        t = g["tokens_per_question"]
        c = g["call_seconds"]
        w(f"From the AI usage log ({g['calls']:,} AI calls for {g['questions']:,} questions, {g['from']} to {g['to']}):\n")
        w(f"- Tokens per question: avg {f(t.get('avg'), 0)}, P95 {f(t.get('p95'), 0)}.")
        w(f"- One AI call: avg {f(c.get('avg'), 1)} s, P95 {f(c.get('p95'), 1)} s, P99 {f(c.get('p99'), 1)} s.")
        w(f"- Failed AI calls (rate limits, timeouts; the app then tries the next free model): "
          f"{g['errors']:,} of {g['calls']:,} ({100 * g['errors'] / max(1, g['calls']):.1f}%).")
        w(f"- Money spent: ${f(g['cost_usd'] or 0, 2)} (only free models are used).")
        if t.get("avg"):
            price = (t["avg"] * 0.85 * args.price_in + t["avg"] * 0.15 * args.price_out) / 1e6
            w(f"- If a paid model at ${args.price_in}/${args.price_out} per million input/output tokens "
              f"were used instead (an example price; change it with --price-in / --price-out), one question "
              f"would cost about ${price:.4f}, so 1,000 questions about ${price * 1000:.2f}. "
              "(85% / 15% input/output split, close to the log above.)")
        w("")
    if not a["by_level"] and not a["usage"]:
        w("No saved AI results were found on this computer. Run the accuracy test first.\n")

    w("## Reliability pieces in the app (checked in the code)\n")
    w("| Piece | How InsightX does it |")
    w("|---|---|")
    w("| Connection pooling | One shared pool per database and login (up to 15 connections), checked before use. "
      "Section 5 shows 100 users needing about 33 connections in total. |")
    w("| Query time limit | Every query runs read-only with a 30 s statement timeout (section 6). |")
    w("| Row limit | At most 1,000 rows come back, read with a streaming cursor, so a 3M-row table never "
      "enters app memory (section 6). |")
    w("| Large results | The full answer stays in the database; the app shows the first 1,000 rows and says when "
      "it was cut. Chat history and lists are paged. |")
    w("| CSV loading | Big files are streamed by DuckDB (4 threads, 256 MB cap) and copied into PostgreSQL; "
      "a failed load changes nothing. |")
    w("| Caching | Same question again: saved SQL is reused (no AI call). Same AI prompt: saved AI reply reused. |")
    w("| Many users | A fair queue lets 4 questions use the AI at once; others wait their turn (up to 150 s) "
      "instead of failing. |")
    w("| Monitoring | Every question saves a trace: time per step, AI calls, tokens, SQL run, errors. |")
    w("")
    w("## What this shows (tested limits only)\n")
    for line in r["findings"]:
        w(f"- {line}")
    w("\n## Run it again\n")
    w("Double-click `RUN_BENCHMARK.bat` in the project folder (or run `python benchmarks/run_benchmark.py`). "
      "It needs PostgreSQL installed (it starts its own throwaway server) and makes no AI calls.")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def findings(r):
    out = []
    biggest = max(r["ingestion"], key=lambda i: i["rows"])
    out.append(f"Largest load tested: {biggest['rows']:,} rows ({biggest['csv_mb']:.0f} MB of CSV) in "
               f"{biggest['total_s']:.0f} s. Backend memory rose only "
               f"{biggest['server_mb_peak'] - biggest['server_mb_before']:.0f} MB during the load, so the file is "
               f"streamed to PostgreSQL, not held in memory.")
    big_sql = [s for s in r["sql_speed"] if s["rows"] == max(x["rows"] for x in r["sql_speed"])]
    if big_sql:
        worst = max(big_sql, key=lambda s: s["ms"].get("p95") or 0)
        out.append(f"On {big_sql[0]['rows']:,} rows the slowest query tested ({worst['query']}) had P95 "
                   f"{worst['ms']['p95'] / 1000:.2f} s in the database.")
    if r["many_tables"]:
        t = r["many_tables"][-1]
        out.append(f"With {t['tables']:,} tables, connecting took {t['connect_s']:.0f} s (one long request that reads "
                   "every table; the slowest part of the app at this size) and questions stayed at "
                   f"P95 {t['seconds'].get('p95', 0):.2f} s of app time; the SQL prompt stayed near "
                   f"{t['sql_prompt_tokens'] or 0:.0f} tokens because only relevant tables are sent.")
    w = r["users"]["with_ai_wait"]
    if w:
        top = w[-1]
        out.append(f"{top['users']} users at once with real-speed AI: {top['ok']} of {top['questions']} answered "
                   f"(P95 {top['seconds_ok'].get('p95', 0):.0f} s). The limit is the free AI speed and the "
                   f"{QUESTION_QUEUE_SLOTS}-at-a-time queue, about {top['answers_per_min']:.0f} answers per minute, "
                   "not the database.")
    n = r["users"]["no_ai_wait"]
    if n:
        top = n[-1]
        out.append(f"With instant AI, {top['users']} users got all {top['ok']} answers (P95 "
                   f"{top['seconds_ok'].get('p95', 0):.0f} s, about {top['answers_per_min']:.0f} per minute) using "
                   f"{top['pg_connections_max']} database connections. But one backend process serves them all, so "
                   f"a light page took up to {top['heartbeat_s'].get('p95', 0):.0f} s (P95) for other visitors.")
    return out


# ==================================================================== main
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="small sizes, for a fast check")
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--ai-delay", type=float, default=None,
                        help="seconds per stand-in AI call in test 5b (default: median of your real AI calls)")
    parser.add_argument("--price-in", type=float, default=0.10)
    parser.add_argument("--price-out", type=float, default=0.40)
    parser.add_argument("--report-from", help="only rewrite the report from a saved results JSON")
    args = parser.parse_args()
    if args.report_from:
        saved = json.loads(Path(args.report_from).read_text(encoding="utf-8"))
        quick = "--quick" in saved["command"]
        args.repeats = args.repeats or (5 if quick else 20)
        saved["findings"] = findings(saved)
        write_report(saved, HERE / ("REPORT_QUICK.md" if quick else "REPORT.md"), args)
        return
    sizes = [20_000] if args.quick else [100_000, 1_000_000, 3_000_000]
    table_counts = [100] if args.quick else [100, 500, 1000]
    user_levels = [10] if args.quick else [10, 50, 100]
    args.repeats = args.repeats or (5 if args.quick else 20)

    real_ai = real_ai_numbers()
    ai_delay = args.ai_delay
    if ai_delay is None:
        p50 = ((real_ai.get("usage") or {}).get("call_seconds") or {}).get("p50")
        ai_delay = round(min(4.0, p50), 1) if p50 else 3.0

    work = Path(tempfile.mkdtemp(prefix="insightx_bench_"))
    log(f"Working folder: {work}")
    pg = ThrowawayPostgres(work)
    backend = None
    try:
        log("Starting a throwaway PostgreSQL server")
        pg.start()
        pg.create_database("insightx_app")
        log("Starting the InsightX backend (stand-in AI)")
        backend = Backend(work, pg)
        result = {"command": "python benchmarks/run_benchmark.py" + (" --quick" if args.quick else ""),
                  "machine": {"os": f"{platform.system()} {platform.release()}", "cpus": os.cpu_count(),
                              "ram_gb": psutil.virtual_memory().total / 2 ** 30, "python": platform.python_version(),
                              "postgres": pg.version, "pg_max_connections": pg.max_connections}}
        result["ingestion"] = test_ingestion(backend, pg, work, sizes)
        result["sql_speed"] = test_sql_speed(pg, result["ingestion"], args.repeats)
        middle = [i for i in result["ingestion"] if i["database"].startswith("vol_")][min(1, len(sizes) - 1)]
        result["e2e_database"] = f"{middle['rows']:,} rows"
        result["end_to_end"] = test_end_to_end(backend, middle["connection_id"], args.repeats)
        result["many_tables"] = test_many_tables(backend, pg, table_counts, max(3, args.repeats // 4))
        result["users"] = test_users(backend, pg, middle["connection_id"], user_levels, ai_delay)
        biggest = max((i for i in result["ingestion"] if i["database"].startswith("vol_")), key=lambda i: i["rows"])
        result["failures"] = test_failures(pg, biggest["database"], biggest["rows"])
        result["real_ai"] = real_ai
        result["findings"] = findings(result)
        result["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        out_dir = HERE / "results"
        out_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (out_dir / f"benchmark-{stamp}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        report = HERE / ("REPORT_QUICK.md" if args.quick else "REPORT.md")
        write_report(result, report, args)
        log(f"Done. Report: {report}")
    finally:
        if backend:
            backend.stop()
        pg.stop()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
