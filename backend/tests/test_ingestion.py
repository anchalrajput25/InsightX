# Ingestion: upload safety, inspection/type detection, atomic import,
# connection error classification and profiler robustness.

import dataclasses
import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend import ingestion
from backend.database import classify_connection_error
from backend.ingestion import (
    IngestionError,
    extract_zip,
    find_data_files,
    import_files_to_postgres,
    infer_sql_type,
    inspect_files,
    make_unique,
    normalize_identifier,
    safe_upload_name,
)


def make_zip(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


# ============================================================
# TYPE INFERENCE
# ============================================================

@pytest.mark.parametrize(
    "values, expected",
    [
        (["1", "2", "-3", None], "BIGINT"),
        (["01003", "12345"], "TEXT"),            # leading zeros preserved
        (["0", "10"], "BIGINT"),                 # a lone zero is fine
        (["1.5", "2", "3e2"], "DOUBLE PRECISION"),
        (["99999999999999999999999"], "NUMERIC"),  # beyond int64
        (["true", "False", None], "BOOLEAN"),
        (["2017-10-02", "2018-01-31"], "DATE"),
        (["2017-10-02 10:56:33", "2018-01-31T08:00"], "TIMESTAMP"),
        (["2016-07-04", "2023-08-29 09:23:46"], "TIMESTAMP"),   # mixed precision
        (["03/04/2020"], "TEXT"),                # ambiguous day/month
        (["2017-13-45"], "TEXT"),                # not a real date
        (["NA", "1"], "TEXT"),                   # 'NA' is a value, not NULL
        ([None, None], "TEXT"),
        (["abc", "1"], "TEXT"),
    ],
)
def test_infer_sql_type(values, expected):
    series = pd.Series(values, dtype=object)
    assert infer_sql_type(series) == expected


# ============================================================
# IDENTIFIERS AND NAMES
# ============================================================

def test_normalize_identifier():
    assert normalize_identifier("Orders 2024", "table") == "orders_2024"
    assert normalize_identifier("2024", "table") == "table_2024"
    assert normalize_identifier("客户", "table") == "table"
    assert normalize_identifier("Order ID", "column_1") == "order_id"
    assert normalize_identifier("CustomerID", "column_1") == "customer_id"
    assert normalize_identifier("OrderDate", "column_1") == "order_date"
    assert normalize_identifier("HTTPServerName", "column_1") == "http_server_name"
    assert normalize_identifier("Address2", "column_1") == "address2"
    assert normalize_identifier("order_purchase_timestamp", "column_1") == "order_purchase_timestamp"
    assert normalize_identifier("Order Details", "table") == "order_details"
    assert len(normalize_identifier("x" * 100, "table")) == 63


def test_make_unique_resolves_collisions_within_length_limit():
    names = make_unique(["orders_2024", "orders_2024", "x" * 63, "x" * 63])
    assert names[:2] == ["orders_2024", "orders_2024_2"]
    assert len(set(names)) == 4
    assert all(len(name) <= 63 for name in names)


def test_safe_upload_name_strips_directories():
    assert safe_upload_name("../../evil.zip") == "evil.zip"
    assert safe_upload_name("..\\..\\evil.zip") == "evil.zip"
    assert safe_upload_name("") == "upload.zip"


# ============================================================
# ZIP EXTRACTION
# ============================================================

def test_extract_rejects_non_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(IngestionError, match="not a valid ZIP"):
        extract_zip(bad, tmp_path / "out")


def test_extract_enforces_entry_and_size_limits(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip({f"f{i}.csv": "a\n1\n" for i in range(5)}))
    with pytest.raises(IngestionError, match="limit"):
        extract_zip(archive, tmp_path / "o1", max_entries=3)
    with pytest.raises(IngestionError, match="limit"):
        extract_zip(archive, tmp_path / "o2", max_extracted_bytes=5)


def test_extract_ignores_macos_metadata_and_finds_nested_files(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip({
        "data/sales.csv": "a\n1\n",
        "__MACOSX/data/._sales.csv": "junk",
        ".hidden.csv": "junk",
        "notes.txt": "x",
    }))
    out = extract_zip(archive, tmp_path / "out")
    files = find_data_files(out)
    assert [path.relative_to(out).as_posix() for path in files] == ["data/sales.csv"]


# ============================================================
# INSPECTION
# ============================================================

def test_inspection_validates_whole_file_and_assigns_unique_tables(tmp_path):
    (tmp_path / "Orders 2024.csv").write_text("Order ID,Total\n1,9.5\n")
    (tmp_path / "orders-2024.csv").write_text("id\n2\n")
    (tmp_path / "broken.csv").write_text('a,b\n1,2\n3,"unterminated\n')
    (tmp_path / "empty.csv").write_text("")
    (tmp_path / "header_only.csv").write_text("a,b\n")

    profiles = {p["file_name"]: p for p in inspect_files(find_data_files(tmp_path), tmp_path)}

    assert profiles["broken.csv"]["importable"] is False
    assert "malformed" in profiles["broken.csv"]["error"]
    assert profiles["empty.csv"]["importable"] is False
    assert profiles["header_only.csv"]["importable"] is True
    assert any("no data rows" in w for w in profiles["header_only.csv"]["warnings"])

    tables = {profiles[name]["table_name"] for name in ("Orders 2024.csv", "orders-2024.csv")}
    assert tables == {"orders_2024", "orders_2024_2"}

    columns = profiles["Orders 2024.csv"]["column_types"]
    assert [(c["source_name"], c["column_name"], c["data_type"]) for c in columns] == [
        ("Order ID", "order_id", "BIGINT"),
        ("Total", "total", "DOUBLE PRECISION"),
    ]


# ============================================================
# UPLOAD ENDPOINT
# ============================================================

@pytest.fixture
def api(tmp_path, monkeypatch):
    from backend import main

    settings = dataclasses.replace(main.ingestion_settings, upload_dir=tmp_path / "uploads")
    settings.upload_dir.mkdir()
    monkeypatch.setattr(main, "ingestion_settings", settings)
    monkeypatch.setattr(ingestion, "ingestion_settings", settings)
    monkeypatch.setattr(main, "UPLOAD_SESSIONS", {})
    return main, TestClient(main.app), settings


def upload(client, data: bytes, filename: str = "data.zip"):
    return client.post("/upload-folder", files={"file": (filename, data, "application/zip")})


@pytest.mark.parametrize(
    "data, filename, status",
    [
        (b"not a zip", "data.zip", 400),
        (make_zip({}), "data.zip", 400),
        (make_zip({"readme.txt": "x"}), "data.zip", 400),
        (make_zip({"bad.csv": ""}), "data.zip", 400),
        (make_zip({"a.csv": "a\n1\n"}), "data.csv", 400),
    ],
)
def test_upload_rejections_leave_no_files(api, data, filename, status):
    _, client, settings = api
    response = upload(client, data, filename)
    assert response.status_code == status
    assert list(settings.upload_dir.iterdir()) == []


def test_upload_too_large_is_413(api, monkeypatch):
    main, client, settings = api
    small = dataclasses.replace(settings, max_upload_bytes=100)
    monkeypatch.setattr(ingestion, "ingestion_settings", small)
    response = upload(client, make_zip({"a.csv": "a\n" + "1\n" * 500}))
    assert response.status_code == 413
    assert list(settings.upload_dir.iterdir()) == []


def test_upload_filename_cannot_escape_session_directory(api, tmp_path):
    _, client, settings = api
    response = upload(client, make_zip({"a.csv": "a\n1\n"}), "../../evil.zip")
    assert response.status_code == 200
    assert not (tmp_path / "evil.zip").exists()
    assert not (settings.upload_dir.parent / "evil.zip").exists()


def test_upload_reports_mixed_files(api):
    _, client, _ = api
    response = upload(client, make_zip({"good.csv": "a\n1\n", "bad.csv": 'a\n"x\n'}))
    assert response.status_code == 200
    body = response.json()
    assert body["importable_files"] == 1
    assert [item["file_name"] for item in body["skipped_files"]] == ["bad.csv"]


def test_expired_upload_sessions_are_pruned(api):
    main, client, settings = api
    session_id = upload(client, make_zip({"a.csv": "a\n1\n"})).json()["session_id"]
    main.prune_expired_uploads(now=time.time() + settings.upload_ttl_seconds + 1)
    assert session_id not in main.UPLOAD_SESSIONS
    assert list(settings.upload_dir.iterdir()) == []


# ============================================================
# CONNECTION ERRORS
# ============================================================

@pytest.mark.parametrize(
    "message, status",
    [
        ('FATAL:  password authentication failed for user "bob"', 401),
        ('FATAL:  role "bob" does not exist', 401),
        ('FATAL:  database "nope" does not exist', 404),
        ("timeout expired", 504),
        ('could not translate host name "nohost" to address', 400),
        ('connection to server at "x", port 5432 failed: Connection refused', 503),
        ("something unexpected", 502),
    ],
)
def test_connection_error_classification(message, status):
    error = classify_connection_error(Exception(message), password="hunter2")
    assert error.status_code == status


def test_password_never_in_connection_error_message():
    error = classify_connection_error(Exception("failed for secretpw123"), password="secretpw123")
    assert "secretpw123" not in error.message


def test_connect_endpoint_refused_port_fails_fast():
    from backend import main

    client = TestClient(main.app)
    started = time.time()
    response = client.post(
        "/connect-postgres",
        data={"host": "127.0.0.1", "port": "1", "database": "x", "username": "u", "password": "secretpw123"},
    )
    assert response.status_code == 503
    assert "secretpw123" not in response.text
    assert time.time() - started < 15


# ============================================================
# IMPORT (database)
# ============================================================

def write_and_inspect(directory: Path, files: dict):
    for name, content in files.items():
        (directory / name).write_text(content)
    return inspect_files(find_data_files(directory), directory)


def public_tables(engine, prefix):
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE :p ORDER BY 1"),
            {"p": prefix + "%"},
        )
        return [row[0] for row in rows]


def no_staging_schemas(engine):
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT COUNT(*) FROM pg_namespace WHERE nspname LIKE 'insightx_staging_%'")
        ).scalar() == 0


@pytest.fixture
def clean_imports(engine):
    def drop():
        with engine.begin() as connection:
            connection.execute(text("DROP VIEW IF EXISTS itimp_view"))
            for table in public_tables(engine, "itimp_"):
                connection.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
    drop()
    yield
    drop()


def test_import_types_values_and_skips(engine, tmp_path, clean_imports):
    specs = write_and_inspect(tmp_path, {
        "itimp_customers.csv": "id,zip,signup,active,score,note\n1,01003,2020-01-02 10:00:00,true,1.5,NA\n2,12345,,false,2,\n",
        "itimp_broken.csv": 'a\n"x\n',
    })
    outcome = import_files_to_postgres(specs, engine, tmp_path)

    assert [t["table_name"] for t in outcome["imported_tables"]] == ["itimp_customers"]
    assert [s["file_name"] for s in outcome["skipped_files"]] == ["itimp_broken.csv"]

    with engine.connect() as connection:
        rows = connection.execute(text("SELECT * FROM itimp_customers ORDER BY id")).mappings().all()
        types = dict(connection.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='itimp_customers'"
        )).all())

    assert types == {
        "id": "bigint", "zip": "text", "signup": "timestamp without time zone",
        "active": "boolean", "score": "double precision", "note": "text",
    }
    assert rows[0]["zip"] == "01003"
    assert rows[0]["note"] == "NA"
    assert rows[1]["signup"] is None and rows[1]["note"] is None
    assert rows[1]["active"] is False
    assert no_staging_schemas(engine)


def test_import_is_atomic_when_a_file_fails(engine, tmp_path, clean_imports, monkeypatch):
    specs = write_and_inspect(tmp_path, {"itimp_a.csv": "x\n1\n", "itimp_b.csv": "y\n2\n"})
    original = ingestion.read_data_file

    def failing(path):
        if Path(path).name == "itimp_b.csv":
            raise IngestionError("disk read failed")
        return original(path)

    monkeypatch.setattr(ingestion, "read_data_file", failing)
    with pytest.raises(IngestionError):
        import_files_to_postgres(specs, engine, tmp_path)

    assert public_tables(engine, "itimp_") == []
    assert no_staging_schemas(engine)


def test_import_replaces_and_reports_existing_tables(engine, tmp_path, clean_imports):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE itimp_sales (old int)"))
    specs = write_and_inspect(tmp_path, {"itimp_sales.csv": "amount\n10\n"})
    outcome = import_files_to_postgres(specs, engine, tmp_path)

    assert outcome["replaced_tables"] == ["itimp_sales"]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT amount FROM itimp_sales")).scalar() == 10


def test_import_does_not_drop_tables_with_dependents(engine, tmp_path, clean_imports):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE itimp_sales (old int)"))
        connection.execute(text("INSERT INTO itimp_sales VALUES (7)"))
        connection.execute(text("CREATE VIEW itimp_view AS SELECT old FROM itimp_sales"))
    specs = write_and_inspect(tmp_path, {"itimp_sales.csv": "amount\n10\n"})

    with pytest.raises(IngestionError, match="no tables were changed"):
        import_files_to_postgres(specs, engine, tmp_path)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT old FROM itimp_sales")).scalar() == 7
    assert no_staging_schemas(engine)


# ============================================================
# PROFILER (database)
# ============================================================

def test_profiler_survives_unorderable_columns_and_skips_large_duplicate_scans(engine, clean_imports, monkeypatch):
    from backend import schema_profiler

    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE itimp_mixed AS SELECT g AS id, (g % 2 = 0) AS flag, "
            "json_build_object('k', g) AS payload, 'v' || g AS label FROM generate_series(1, 50) g"
        ))

    small = dataclasses.replace(schema_profiler.database_settings, duplicate_scan_max_rows=10)
    monkeypatch.setattr(schema_profiler, "database_settings", small)

    profile = schema_profiler.get_database_schema(engine)
    table = next(t for t in profile["tables"] if t["table_name"] == "itimp_mixed")

    assert "error" not in table
    columns = {c["column_name"]: c for c in table["columns"]}
    # boolean/json have no min(): skipped without breaking later columns
    assert columns["flag"]["min_value"] is None
    assert columns["label"]["distinct_count"] == 50
    assert columns["label"]["min_value"] == "v1"
    assert table["duplicate_check"].startswith("skipped")
