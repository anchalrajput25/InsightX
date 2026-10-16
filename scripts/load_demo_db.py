"""Load the 108-table demo warehouse into an online PostgreSQL (for example Neon).

    python scripts/load_demo_db.py

It asks for the connection string of your online database (the one Neon
shows, starting with postgresql://). Typing is hidden and nothing is saved.
Then it:

  1. creates a separate database "insightx_demo" (InsightX's own tables
     never share a database with the demo data),
  2. loads data/insightx_warehouse.sql.gz into it,
  3. creates a read-only user "insightx_reader" with a new random password,
  4. prints the read-only connection string to paste into the online app's
     INSIGHTX_DEMO_DATABASE_URL secret.

You can also set INSIGHTX_DEMO_LOAD_URL instead of typing the address.
"""

import argparse
import getpass
import gzip
import os
import secrets
import sys
import time

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from load_big_test_db import statements  # noqa: E402  (same folder)

READER = "insightx_reader"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dump", default=os.path.join(HERE, "..", "data", "insightx_warehouse.sql.gz"))
    parser.add_argument("--db", default="insightx_demo")
    args = parser.parse_args()

    if not os.path.exists(args.dump):
        sys.exit(f"Dump file not found: {args.dump}")
    url = os.environ.get("INSIGHTX_DEMO_LOAD_URL") or getpass.getpass(
        "Paste the online database connection string (hidden): ")
    url = url.strip()
    if not url.startswith(("postgresql://", "postgres://")):
        sys.exit("That does not look like a PostgreSQL connection string (postgresql://...).")

    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (args.db,))
        if cur.fetchone():
            answer = input(f'Database "{args.db}" already exists. Delete it and load again? [y/N] ')
            if answer.strip().lower() != "y":
                sys.exit("Stopped. Nothing was changed.")
            cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(args.db)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.db)))
    admin.close()

    print(f'Loading the demo data into "{args.db}". This can take a few minutes...')
    started = time.time()
    conn = psycopg2.connect(url, dbname=args.db)
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

    # A read-only user for the online app: it can read the demo tables
    # and nothing else, even if something tried to write.
    password = secrets.token_urlsafe(24)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER,))
        verb = "ALTER" if cur.fetchone() else "CREATE"
        cur.execute(sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(READER)), (password,))
        cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(READER)))
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(args.db), sql.Identifier(READER)))
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(READER)))
        cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(READER)))
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
        count = cur.fetchone()[0]
    conn.close()

    reader_url = make_url(url).set(username=READER, password=password, database=args.db)
    print(f'Done in {time.time() - started:.0f}s: "{args.db}" has {count} tables.')
    print()
    print("Paste this as the INSIGHTX_DEMO_DATABASE_URL secret of the online app")
    print("(it is read-only; keep it private, do not put it on GitHub):")
    print()
    print(reader_url.render_as_string(hide_password=False))


if __name__ == "__main__":
    main()
