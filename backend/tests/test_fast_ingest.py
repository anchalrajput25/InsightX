# DuckDB fast path for big CSV files: same types, NULLs and values as
# the pandas path, and a fallback when DuckDB cannot read a file.

import csv
import io
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text

from backend import fast_ingest
from backend.ingestion import import_files_to_postgres, infer_sql_type, inspect_files

pytestmark = pytest.mark.skipif(not fast_ingest.available(), reason="duckdb is not installed")

CASES = [
    ["1", "2", "-3", None],
    ["01003", "12345"],
    ["0", "10"],
    ["1.5", "2", "3e2"],
    ["99999999999999999999999"],
    ["true", "False", None],
    ["2017-10-02", "2018-01-31"],
    ["2017-10-02 10:56:33", "2018-01-31T08:00"],
    ["2016-07-04", "2023-08-29 09:23:46"],
    ["03/04/2020"],
    ["2017-13-45"],
    ["NA", "1"],
    [None, None],
    ["abc", "1"],
    [" 42 ", "7"],
    ["1e400"],
    ["+5", "6"],
    ["inf", "1"],
    ["2020-02-30"],
    ["007.5"],
]


@pytest.fixture
def always_fast(monkeypatch):
    monkeypatch.setattr(fast_ingest, "MIN_BYTES", 0)


def _write(path: Path, columns: dict):
    width = max(len(values) for values in columns.values())
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(columns))
        for index in range(width):
            writer.writerow([
                "" if index >= len(values) or values[index] is None else values[index]
                for values in columns.values()
            ])


def test_duckdb_types_match_the_pandas_rules(tmp_path):
    columns = {f"c{index}": values for index, values in enumerate(CASES)}
    path = tmp_path / "types.csv"
    _write(path, columns)
    names, types, nulls, rows, _ = fast_ingest.inspect_csv(path)

    frame = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    expected = [infer_sql_type(frame[name]) for name in frame.columns]
    assert names == list(frame.columns)
    assert dict(zip(names, types)) == dict(zip(names, expected))
    assert nulls == [int(frame[name].isna().sum()) for name in frame.columns]
    assert rows == len(frame)


def test_big_csv_is_inspected_and_imported_with_duckdb(engine, tmp_path, always_fast):
    (tmp_path / "itfast_customers.csv").write_text(
        'id,zip,signup,active,score,note\n'
        '1,01003,2020-01-02 10:00:00,true,1.5,NA\n'
        '2,12345,,false,2,""\n'
        '3,00001,2021-05-06,true,3,"line one\nline two, with comma"\n'
    )
    specs = inspect_files([tmp_path / "itfast_customers.csv"], tmp_path)
    assert specs[0]["reader"] == "duckdb" and specs[0]["rows"] == 3
    try:
        outcome = import_files_to_postgres(specs, engine, tmp_path)
        assert outcome["imported_tables"][0]["rows"] == 3
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT * FROM itfast_customers ORDER BY id")).mappings().all()
            types = dict(connection.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='itfast_customers'"
            )).all())
        assert types == {
            "id": "bigint", "zip": "text", "signup": "timestamp without time zone",
            "active": "boolean", "score": "double precision", "note": "text",
        }
        assert rows[0]["zip"] == "01003" and rows[0]["note"] == "NA"
        assert rows[1]["signup"] is None and rows[1]["note"] is None
        assert rows[2]["note"] == "line one\nline two, with comma"
        assert not list(tmp_path.glob("insightx_*.csv"))  # temp file removed
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS itfast_customers"))


def test_files_duckdb_cannot_read_fall_back_to_pandas(tmp_path, always_fast):
    (tmp_path / "latin.csv").write_bytes("name,city\nJosé,São Paulo\n".encode("cp1252"))
    (tmp_path / "broken.csv").write_text('a\n"x\n')
    specs = {spec["file_name"]: spec for spec in inspect_files(
        [tmp_path / "latin.csv", tmp_path / "broken.csv"], tmp_path
    )}
    assert specs["latin.csv"]["reader"] == "pandas" and specs["latin.csv"]["importable"]
    assert specs["latin.csv"]["preview"][0]["city"] == "São Paulo"
    assert not specs["broken.csv"]["importable"] and specs["broken.csv"]["error"]


def test_small_files_keep_the_pandas_path(tmp_path):
    (tmp_path / "small.csv").write_text("a,b\n1,2\n")
    assert inspect_files([tmp_path / "small.csv"], tmp_path)[0]["reader"] == "pandas"
