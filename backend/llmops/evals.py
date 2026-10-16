# backend/llmops/evals.py
#
# Agent evaluations for the Evals page, built on the existing harness
# (python -m backend.evaluation). Nothing in the harness is changed.
#
#   - list and open reports: shared ones in logs/evaluation (from the
#     command line) and your own runs in logs/llmops/evals/<you>/
#   - list golden sets: the built-in ones and the ones you grew from
#     feedback ("promote to golden" on the Quality page)
#   - start a run against the database you are connected to, follow its
#     progress, stop it
#   - a quality gate (minimum accuracy, faithfulness, safety, maximum
#     p95 latency) checked on every report
#   - compare two reports: metric changes, cases that started or
#     stopped passing (regressions)
#   - per-model accuracy from report cases, used by smart routing
#
# The database password is passed to the run in an environment
# variable, never on the command line or in any file.

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import PROJECT_ROOT

SHARED_REPORTS = PROJECT_ROOT / "logs" / "evaluation"
OWN_ROOT = PROJECT_ROOT / "logs" / "llmops"
BUILTIN_GOLDEN = PROJECT_ROOT / "backend" / "evaluation" / "golden"

DEFAULT_GATE = {
    "execution_accuracy": 0.70,
    "answer_faithfulness": 0.85,
    "safety_pass_rate": 1.0,
    "latency_p95_s": 45.0,
}
GATE_DIRECTION = {
    "execution_accuracy": "min",
    "answer_faithfulness": "min",
    "safety_pass_rate": "min",
    "latency_p95_s": "max",
}
COMPARE_METRICS = [
    ("execution_accuracy", "Execution accuracy", "higher"),
    ("strict_accuracy", "Strict accuracy", "higher"),
    ("answer_faithfulness", "Answer faithfulness", "higher"),
    ("safety_pass_rate", "Safety pass rate", "higher"),
    ("abstention_rate", "Abstention rate", "higher"),
    ("mean_retrieval_recall", "Retrieval recall", "higher"),
    ("correction_rate", "Correction rate", "lower"),
    ("latency_p50_s", "Latency p50 (s)", "lower"),
    ("latency_p95_s", "Latency p95 (s)", "lower"),
    ("cost_total_usd", "Cost (USD)", "lower"),
]
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,160}$")
_PROGRESS = re.compile(r"^\[(?P<variant>[^#\]]+) #(?P<repeat>\d+)\] (?P<mark>PASS|FAIL) (?P<id>\S+)")
_HEADER = re.compile(r"(?P<cases>\d+) cases x (?P<repeats>\d+) repeat\(s\) x (?P<variants>\d+) variant")


def user_key(user_id: str) -> str:
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]


def own_dir(user_id: str, kind: str) -> Path:
    path = OWN_ROOT / kind / user_key(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------- reports
def _report_files(user_id: str) -> List[Dict[str, Any]]:
    files = []
    for scope, folder in (("shared", SHARED_REPORTS), ("mine", own_dir(user_id, "evals"))):
        if folder.is_dir():
            for path in folder.glob("*.json"):
                files.append({"scope": scope, "path": path})
    return files


def _get(summary: Dict[str, Any], key: str) -> Any:
    if key == "cost_total_usd":
        for name in ("cost_total_usd", "cost_usd", "total_cost_usd"):
            if summary.get(name) is not None:
                return summary[name]
        return None
    return summary.get(key)


def list_reports(user_id: str) -> List[Dict[str, Any]]:
    gate = get_gate()
    items = []
    for entry in _report_files(user_id):
        path = entry["path"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        summary = data.get("summary") or {}
        items.append({
            "id": f"{entry['scope']}:{path.name}",
            "scope": entry["scope"],
            "name": path.stem,
            "golden_set": data.get("golden_set") or path.stem,
            "database": data.get("database") or "",
            "run_at": data.get("run_at") or "",
            "modified": path.stat().st_mtime,
            "case_count": data.get("case_count") or len(data.get("cases") or []),
            "variants": [v.get("name") for v in data.get("variants") or []],
            "stopped": data.get("stopped") or "",
            "execution_accuracy": summary.get("execution_accuracy"),
            "answer_faithfulness": summary.get("answer_faithfulness"),
            "latency_p95_s": summary.get("latency_p95_s"),
            "gate": check_gate(summary, gate),
        })
    items.sort(key=lambda r: r["modified"], reverse=True)
    return items


def _resolve_report(user_id: str, report_id: str) -> Path:
    scope, _, name = str(report_id).partition(":")
    if not _SAFE_NAME.match(name) or not name.endswith(".json"):
        raise FileNotFoundError("Unknown report.")
    folder = SHARED_REPORTS if scope == "shared" else own_dir(user_id, "evals") if scope == "mine" else None
    if folder is None:
        raise FileNotFoundError("Unknown report.")
    path = folder / name
    if not path.is_file():
        raise FileNotFoundError("Report not found.")
    return path


def read_report(user_id: str, report_id: str) -> Dict[str, Any]:
    data = json.loads(_resolve_report(user_id, report_id).read_text(encoding="utf-8"))
    data["id"] = report_id
    data["gate"] = check_gate(data.get("summary") or {}, get_gate())
    return data


# ------------------------------------------------------------ quality gate
def get_gate() -> Dict[str, float]:
    from backend.llmops.store import get_store

    saved = get_store().get_setting("eval_gate", {}) or {}
    return {**DEFAULT_GATE, **{k: float(v) for k, v in saved.items() if k in DEFAULT_GATE}}


def set_gate(values: Dict[str, Any]) -> Dict[str, float]:
    from backend.llmops.store import get_store

    clean = {}
    for key, value in (values or {}).items():
        if key in DEFAULT_GATE and value is not None:
            number = float(value)
            if number < 0 or (GATE_DIRECTION[key] == "min" and number > 1):
                raise ValueError(f"{key} is out of range.")
            clean[key] = number
    get_store().set_setting("eval_gate", {**get_gate(), **clean})
    return get_gate()


def check_gate(summary: Dict[str, Any], gate: Dict[str, float]) -> Dict[str, Any]:
    checks = []
    for key, target in gate.items():
        value = summary.get(key)
        if value is None:
            checks.append({"metric": key, "value": None, "target": target, "passed": None})
            continue
        passed = value >= target if GATE_DIRECTION[key] == "min" else value <= target
        checks.append({"metric": key, "value": value, "target": target, "passed": bool(passed)})
    known = [c for c in checks if c["passed"] is not None]
    return {"passed": bool(known) and all(c["passed"] for c in known), "checks": checks}


# ----------------------------------------------------------------- compare
def compare(user_id: str, base_id: str, other_id: str) -> Dict[str, Any]:
    base, other = read_report(user_id, base_id), read_report(user_id, other_id)
    sa, sb = base.get("summary") or {}, other.get("summary") or {}
    metrics = []
    for key, label, better in COMPARE_METRICS:
        a, b = _get(sa, key), _get(sb, key)
        delta = round(b - a, 4) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
        improved = None if delta in (None, 0) else (delta > 0) == (better == "higher")
        metrics.append({"key": key, "label": label, "base": a, "other": b, "delta": delta, "improved": improved})

    def outcome(report):
        result: Dict[str, Dict[str, Any]] = {}
        for case in report.get("cases") or []:
            item = result.setdefault(case["id"], {"passed": 0, "runs": 0, "question": case.get("question", ""),
                                                  "difficulty": case.get("difficulty", "")})
            item["runs"] += 1
            item["passed"] += bool(case.get("passed"))
        return result

    oa, ob = outcome(base), outcome(other)
    regressions, fixes = [], []
    for case_id in sorted(set(oa) & set(ob)):
        was = oa[case_id]["passed"] / oa[case_id]["runs"]
        now = ob[case_id]["passed"] / ob[case_id]["runs"]
        row = {"id": case_id, "question": ob[case_id]["question"], "difficulty": ob[case_id]["difficulty"],
               "base": round(was, 3), "other": round(now, 3)}
        if now < was:
            regressions.append(row)
        elif now > was:
            fixes.append(row)
    return {
        "base": {"id": base_id, "golden_set": base.get("golden_set"), "run_at": base.get("run_at")},
        "other": {"id": other_id, "golden_set": other.get("golden_set"), "run_at": other.get("run_at")},
        "metrics": metrics,
        "regressions": regressions,
        "fixes": fixes,
        "only_in_base": sorted(set(oa) - set(ob)),
        "only_in_other": sorted(set(ob) - set(oa)),
    }


# ------------------------------------------------------- model accuracy
_accuracy_cache: Dict[str, Any] = {"at": 0.0, "value": {}}


def route_accuracy(max_reports: int = 20) -> Dict[str, Dict[str, float]]:
    """
    From shared reports: for each model route, the share of cases it
    took part in that passed. Used by smart routing as its accuracy prior.
    """
    if time.time() - _accuracy_cache["at"] < 300:
        return _accuracy_cache["value"]
    stats: Dict[str, Dict[str, float]] = {}
    paths = sorted(SHARED_REPORTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:max_reports] \
        if SHARED_REPORTS.is_dir() else []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for case in data.get("cases") or []:
            if case.get("expect", "answer") != "answer":
                continue
            for route in (case.get("models") or {}):
                item = stats.setdefault(route, {"cases": 0, "passed": 0})
                item["cases"] += 1
                item["passed"] += bool(case.get("passed"))
    value = {route: {"cases": s["cases"], "accuracy": round(s["passed"] / s["cases"], 4)}
             for route, s in stats.items() if s["cases"]}
    _accuracy_cache.update(at=time.time(), value=value)
    return value


# ------------------------------------------------------------ golden sets
def list_golden(user_id: str) -> List[Dict[str, Any]]:
    from backend.evaluation.golden import GoldenSetError, load_golden_set

    items = []
    for scope, folder in (("builtin", BUILTIN_GOLDEN), ("mine", own_dir(user_id, "golden"))):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yaml")):
            try:
                golden = load_golden_set(path)
            except (GoldenSetError, OSError) as exc:
                items.append({"id": f"{scope}:{path.name}", "scope": scope, "name": path.stem, "error": str(exc)})
                continue
            by_difficulty: Dict[str, int] = {}
            for case in golden.cases:
                by_difficulty[case.difficulty] = by_difficulty.get(case.difficulty, 0) + 1
            items.append({
                "id": f"{scope}:{path.name}", "scope": scope, "name": golden.name,
                "description": golden.description, "cases": len(golden.cases),
                "verified": sum(1 for c in golden.cases if c.verified), "by_difficulty": by_difficulty,
                "tags": sorted({t for c in golden.cases for t in c.tags})[:40],
            })
    return items


def golden_path(user_id: str, golden_id: str) -> Path:
    scope, _, name = str(golden_id).partition(":")
    if not _SAFE_NAME.match(name) or not name.endswith(".yaml"):
        raise FileNotFoundError("Unknown golden set.")
    folder = BUILTIN_GOLDEN if scope == "builtin" else own_dir(user_id, "golden") if scope == "mine" else None
    if folder is None:
        raise FileNotFoundError("Unknown golden set.")
    path = folder / name
    if not path.is_file():
        raise FileNotFoundError("Golden set not found.")
    return path


def read_golden(user_id: str, golden_id: str) -> Dict[str, Any]:
    from backend.evaluation.golden import load_golden_set

    golden = load_golden_set(golden_path(user_id, golden_id))
    return {"id": golden_id, "name": golden.name, "description": golden.description,
            "cases": [dict(c.to_dict(), id=c.id, difficulty=c.difficulty, verified=c.verified) for c in golden.cases]}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40] or "database"


def add_feedback_case(user_id: str, database_label: str, question: str, sql: str, note: str = "",
                      difficulty: str = "medium") -> Dict[str, Any]:
    """Add one checked question + SQL to your feedback golden set for this database."""
    from backend.evaluation.golden import DIFFICULTIES, GoldenCase, GoldenSet, load_golden_set, save_golden_set

    name = f"feedback-{_slug(database_label)}"
    path = own_dir(user_id, "golden") / f"{name}.yaml"
    if path.is_file():
        golden = load_golden_set(path)
    else:
        golden = GoldenSet(name=name, cases=[],
                           description=f"Questions promoted from feedback on {database_label}.")
    for case in golden.cases:
        if case.question.strip().lower() == question.strip().lower():
            case.expected_sql = sql
            case.notes = note or case.notes
            save_golden_set(golden, path)
            return {"golden_id": f"mine:{path.name}", "case_id": case.id, "updated": True}
    case_id = f"fb-{len(golden.cases) + 1:03d}-{uuid.uuid4().hex[:4]}"
    golden.cases.append(GoldenCase(
        id=case_id, question=question.strip(), expected_sql=sql.strip(), tags=["feedback"],
        notes=note, difficulty=difficulty if difficulty in DIFFICULTIES else "medium", verified=True,
    ))
    save_golden_set(golden, path)
    return {"golden_id": f"mine:{path.name}", "case_id": case_id, "updated": False}


# ------------------------------------------------------------------- runs
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()
RUNNABLE_DIALECTS = ("postgresql", "mysql")


def _parse_log(path: Path) -> Dict[str, Any]:
    total, done, passed, last = None, 0, 0, []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        header = _HEADER.search(line)
        if header and total is None:
            total = int(header["cases"]) * int(header["repeats"]) * int(header["variants"])
        match = _PROGRESS.match(line)
        if match:
            done += 1
            passed += match["mark"] == "PASS"
            last.append({"id": match["id"], "passed": match["mark"] == "PASS", "line": line[:240]})
    report = None
    for line in reversed(lines):
        if line.startswith("Data:"):
            report = Path(line.split("Data:", 1)[1].split("(")[0].strip()).name
            break
    return {"total": total, "done": done, "passed": passed, "recent": last[-12:], "report_file": report,
            "tail": [l[:240] for l in lines[-6:]]}


def start_run(user_id: str, database_url: str, database_label: str, golden_id: str,
              repeats: int = 1, workers: int = 1, difficulty: str = "", tags: str = "",
              ids: str = "", ablation: bool = False, max_cost: Optional[float] = None,
              include_unverified: bool = False) -> Dict[str, Any]:
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    if not any(url.drivername.startswith(name) for name in RUNNABLE_DIALECTS):
        raise ValueError("Evaluation runs need a PostgreSQL or MySQL connection.")
    with _jobs_lock:
        for job in _jobs.values():
            if job["user_id"] == user_id and job["state"] == "running":
                raise ValueError("You already have an evaluation running. Stop it or wait for it to finish.")

    golden = golden_path(user_id, golden_id)
    out = own_dir(user_id, "evals")
    job_id = uuid.uuid4().hex[:12]
    log_path = out / f"run-{job_id}.log"
    args = [sys.executable, "-m", "backend.evaluation", "run", "--golden", str(golden),
            "--repeats", str(max(1, min(int(repeats), 5))), "--workers", str(max(1, min(int(workers), 4))),
            "--out", str(out)]
    for flag, value in (("--difficulty", difficulty), ("--tags", tags), ("--ids", ids)):
        if value and re.fullmatch(r"[A-Za-z0-9_,\- ]{1,400}", value):
            args += [flag, value.replace(" ", "")]
    if ablation:
        args.append("--ablation")
    if max_cost:
        args += ["--max-cost", str(float(max_cost))]
    if include_unverified:
        args.append("--include-unverified")

    env = dict(os.environ)
    env["INSIGHTX_EVAL_DATABASE_URL"] = url.render_as_string(hide_password=False)
    env["PYTHONUNBUFFERED"] = "1"
    log = open(log_path, "w", encoding="utf-8")
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(args, cwd=str(PROJECT_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, **kwargs)
    job = {
        "id": job_id, "user_id": user_id, "state": "running", "started": time.time(), "ended": None,
        "golden_id": golden_id, "database": database_label, "log": str(log_path), "pid": process.pid,
        "options": {"repeats": repeats, "workers": workers, "difficulty": difficulty, "tags": tags,
                    "ablation": ablation, "max_cost": max_cost},
    }
    with _jobs_lock:
        _jobs[job_id] = {**job, "_process": process, "_log": log}
    threading.Thread(target=_wait, args=(job_id,), daemon=True, name=f"llmops-eval-{job_id}").start()
    return public_job(job_id)


def _wait(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return
    code = job["_process"].wait()
    try:
        job["_log"].close()
    except Exception:
        pass
    with _jobs_lock:
        if job["state"] == "running":
            job["state"] = "finished" if code == 0 else "failed"
        job["exit_code"] = code
        job["ended"] = time.time()


def public_job(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = dict(_jobs.get(job_id) or {})
    if not job:
        raise KeyError(job_id)
    progress = _parse_log(Path(job["log"]))
    report = progress.pop("report_file", None)
    return {
        **{k: v for k, v in job.items() if not k.startswith("_") and k not in ("log", "user_id", "pid")},
        "progress": progress,
        "report_id": f"mine:{report}" if report else None,
    }


def list_jobs(user_id: str) -> List[Dict[str, Any]]:
    with _jobs_lock:
        ids = [job_id for job_id, job in _jobs.items() if job["user_id"] == user_id]
    jobs = [public_job(job_id) for job_id in ids]
    jobs.sort(key=lambda j: j["started"], reverse=True)
    return jobs[:20]


def get_job(user_id: str, job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job["user_id"] != user_id:
            raise KeyError(job_id)
    return public_job(job_id)


def stop_job(user_id: str, job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job["user_id"] != user_id:
            raise KeyError(job_id)
        if job["state"] != "running":
            return public_job(job_id)
        job["state"] = "stopped"
        process = job["_process"]
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=15)
    except Exception:
        process.kill()
    return public_job(job_id)
