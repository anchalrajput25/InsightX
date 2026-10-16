"""Load the 108-table test warehouse into PostgreSQL (works on Windows, no psql needed).

    python scripts/load_big_test_db.py
    python scripts/load_big_test_db.py --dump data/insightx_warehouse.sql.gz --db insightx_big --user postgres

Creates the database (asks before replacing an existing one), then runs the
plain-SQL dump: normal statements one by one, COPY blocks streamed with
copy_expert. Prints a table count at the end.
"""

import argparse
import getpass
import gzip
import io
import os
import sys
import time

import psycopg2
from psycopg2 import sql


def statements(lines):
    """Yield ("sql", text) or ("copy", (copy_statement, data)) from a pg_dump file."""
    buffer = []
    lines = iter(lines)
    for line in lines:
        if not buffer:
            stripped = line.strip()
            if not stripped or stripped.startswith("--") or stripped.startswith("\\"):
                continue  # blank, comment, or psql meta-command (\restrict ...)
            if line.startswith("COPY ") and line.rstrip().endswith("FROM stdin;"):
                data = io.StringIO()
                for row in lines:
                    if row.rstrip("\r\n") == "\\.":
                        break
                    data.write(row)
                data.seek(0)
                yield "copy", (line.rstrip().rstrip(";"), data)
                continue
        buffer.append(line)
        text = "".join(buffer)
        # A statement ends at ";" at the end of a line, outside a quoted string.
        if line.rstrip().endswith(";") and text.count("'") % 2 == 0:
            yield "sql", text
            buffer = []
    if "".join(buffer).strip():
        yield "sql", "".join(buffer)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dump", default=os.path.join(here, "..", "data", "insightx_warehouse.sql.gz"))
    parser.add_argument("--db", default="insightx_big")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="postgres")
    args = parser.parse_args()

    if not os.path.exists(args.dump):
        sys.exit(f"Dump file not found: {args.dump}")
    password = os.environ.get("PGPASSWORD") or getpass.getpass(f"PostgreSQL password for {args.user}: ")
    connect = dict(host=args.host, port=args.port, user=args.user, password=password)

    admin = psycopg2.connect(dbname="postgres", **connect)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (args.db,))
        if cur.fetchone():
            answer = input(f'Database "{args.db}" already exists. Delete it and load again? [y/N] ')
            if answer.strip().lower() != "y":
                sys.exit("Stopped. Nothing was changed.")
            cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(args.db)))
        cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(args.db)))
    admin.close()

    started = time.time()
    conn = psycopg2.connect(dbname=args.db, **connect)
    tables = 0
    with conn, conn.cursor() as cur, gzip.open(args.dump, "rt", encoding="utf-8", newline="") as dump:
        for kind, item in statements(dump):
            if kind == "copy":
                copy_sql, data = item
                cur.copy_expert(copy_sql, data)
                tables += 1
                print(f"\r  loaded {tables} tables", end="", flush=True)
            elif not item.lstrip().upper().startswith("SELECT PG_CATALOG.SET_CONFIG('SEARCH_PATH'"):
                cur.execute(item)
        cur.execute("ANALYZE")
    print()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
        count = cur.fetchone()[0]
    conn.close()
    print(f'Done in {time.time() - started:.0f}s: database "{args.db}" has {count} tables.')


if __name__ == "__main__":
    main()
