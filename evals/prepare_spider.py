# evals/prepare_spider.py
#
# Gets the public Spider benchmark (Yale, 2018) and builds the
# InsightX test sets from its DEV split.
#
#   1. Downloads spider_data.zip (questions + gold SQL + SQLite
#      databases) into evals/spider_data/, unless it is already there.
#      If the download fails, download the zip yourself from
#      https://yale-lily.github.io/spider and put it in evals/spider_data/.
#   2. Picks a fixed sample of dev questions (same every time, seed 42)
#      from databases InsightX was never built or tuned on.
#   3. Runs every gold SQL once; drops the few that fail or return no
#      rows (an empty result would "match" too easily).
#   4. Writes evals/golden/spider/<database>.yaml in the format the
#      InsightX evaluation harness reads (backend/evaluation/golden.py),
#      with Spider difficulty labels.
#
# Run:  python evals/prepare_spider.py

import json
import random
import re
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

import yaml

EVALS = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS))
from spider_hardness import hardness  # noqa: E402

DATA = EVALS / "spider_data"
OUT = EVALS / "golden" / "spider"

# Dev databases used, and how many questions from each (total 104).
DATABASES = {
    "concert_singer": 13,
    "pets_1": 13,
    "car_1": 13,
    "flight_2": 13,
    "employee_hire_evaluation": 13,
    "student_transcripts_tracking": 13,
    "world_1": 13,
    "dog_kennels": 13,
}
SEED = 42

# Official Spider 1.0 zip on Google Drive (linked from the Spider site).
DRIVE_IDS = [
    "1403EGqzIDoHMdQF4c9Bkyl7dZLZ5Wt6J",
    "1iRDVHLr4mX2wQKSgA9J8Pire73Jahh0m",
    "1TqleXec_OykOYFREKKtschzY29dUcVAQ",
]


def _download_drive(file_id: str, dest: Path) -> bool:
    import requests

    session = requests.Session()
    url = "https://drive.usercontent.google.com/download"
    params = {"id": file_id, "export": "download", "confirm": "t"}
    try:
        response = session.get(url, params=params, stream=True, timeout=60)
        if "text/html" in response.headers.get("content-type", ""):
            page = response.text
            form = dict(re.findall(r'name="(\w+)" value="([^"]*)"', page))
            if not form:
                return False
            response = session.get(url, params={**params, **form}, stream=True, timeout=60)
        response.raise_for_status()
        tmp = dest.with_suffix(".part")
        with open(tmp, "wb") as handle:
            for chunk in response.iter_content(1 << 20):
                handle.write(chunk)
        if not zipfile.is_zipfile(tmp):
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dest)
        return True
    except Exception as exc:
        print(f"  download from Google Drive id {file_id} failed: {exc}")
        return False


def find_spider_root() -> Path:
    """Folder that holds dev.json and database/ (after unzipping)."""
    for dev in DATA.rglob("dev.json"):
        if (dev.parent / "database").is_dir():
            return dev.parent
    return None


def get_data() -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    root = find_spider_root()
    if root:
        return root
    zips = sorted(DATA.glob("*.zip"))
    if not zips:
        print("Downloading Spider (about 200 MB, one time only)...")
        dest = DATA / "spider_data.zip"
        if not any(_download_drive(file_id, dest) for file_id in DRIVE_IDS):
            sys.exit("Could not download Spider. Download spider_data.zip from "
                     "https://yale-lily.github.io/spider and put it in evals\\spider_data, then run again.")
        zips = [dest]
    print(f"Unzipping {zips[0].name}...")
    with zipfile.ZipFile(zips[0]) as archive:
        archive.extractall(DATA / "unzipped")
    root = find_spider_root()
    if not root:
        sys.exit("The zip does not contain dev.json and database/. Is it the Spider 1.0 zip?")
    return root


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def main() -> None:
    root = get_data()
    dev = json.loads((root / "dev.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    total = 0
    for db, count in DATABASES.items():
        source = root / "database" / db / f"{db}.sqlite"
        target = DATA / "database" / db / f"{db}.sqlite"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        # one item per distinct gold SQL (Spider lists most questions twice, reworded)
        seen, items = set(), []
        for index, item in enumerate(dev):
            if item["db_id"] == db and _norm(item["query"]) not in seen:
                seen.add(_norm(item["query"]))
                items.append((index, item))
        rng.shuffle(items)

        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        connection.text_factory = lambda b: b.decode("utf-8", errors="replace")
        cases = []
        for index, item in items:
            if len(cases) == count:
                break
            sql = item["query"].strip().rstrip(";")
            try:
                rows = connection.execute(sql).fetchall()
            except sqlite3.Error:
                continue
            if not rows:
                continue
            try:
                level = hardness(sql)
            except Exception:
                continue
            cases.append({
                "id": f"{db}_{index:04d}",
                "difficulty": level,
                "question": item["question"].strip(),
                "expected_sql": sql,
                "order_matters": " order by " in f" {_norm(sql)} ",
                "tags": ["spider", db],
                "verified": True,
            })
        connection.close()
        cases.sort(key=lambda case: case["id"])
        golden = {
            "name": f"spider_{db}",
            "description": f"Spider dev set, database {db}: {len(cases)} questions with the official gold SQL "
                           f"(fixed sample, seed {SEED}). Difficulty by Spider rules.",
            "cases": cases,
        }
        path = OUT / f"{db}.yaml"
        path.write_text(yaml.safe_dump(golden, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")
        levels = {level: sum(c["difficulty"] == level for c in cases) for level in ("easy", "medium", "hard", "extra")}
        print(f"  {db}: {len(cases)} questions {levels}")
        total += len(cases)
    print(f"\nReady: {total} Spider questions in {OUT}")


if __name__ == "__main__":
    main()
