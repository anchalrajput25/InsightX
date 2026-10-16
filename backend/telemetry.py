# backend/telemetry.py
#
# Per-request measurements: LLM calls, tokens, cost and the time spent
# in each pipeline stage. A Collector is bound to the current context
# (contextvars), so it follows one question through the agent without
# being passed around, and parallel questions never mix their numbers.
#
#   with collect() as stats:
#       run_text_to_sql(...)
#   stats.summary()  ->  {"llm_calls": 7, "prompt_tokens": ..., "stages": {...}}
#
# Work handed to threads keeps the collector when submitted with
# in_current_context(fn) (thread pools do not copy context by default).
#
# Nothing here stores prompts, answers or credentials: only counts,
# model names and durations.
#
# A Collector also keeps a timeline of events (pipeline steps, AI calls,
# database queries) with their start time and duration; backend.tracing
# saves it as the question's trace.

import contextvars
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional

MAX_EVENTS = 400

_current: contextvars.ContextVar[Optional["Collector"]] = contextvars.ContextVar("insightx_telemetry", default=None)


@dataclass
class Collector:
    llm_calls: int = 0
    failed_llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    # False once any successful call had no known price (cost is then a
    # lower bound, reported as such).
    cost_complete: bool = True
    llm_seconds: float = 0.0
    cache_hits: int = 0
    by_task: Dict[str, Dict[str, float]] = field(default_factory=dict)
    models: Dict[str, int] = field(default_factory=dict)
    stages: Dict[str, float] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_event(self, kind: str, name: str, seconds: float, **detail: Any) -> None:
        """An event that just finished and took `seconds`."""
        now = time.perf_counter()
        event = {
            "kind": kind,
            "name": name,
            "start": round(max(0.0, now - seconds - self.started), 4),
            "seconds": round(max(0.0, seconds), 4),
            "thread": threading.current_thread().name,
            **detail,
        }
        with self._lock:
            if len(self.events) < MAX_EVENTS:
                self.events.append(event)

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def add_llm(self, task: str, model: str, prompt_tokens: int, completion_tokens: int,
                cost: Optional[float], seconds: float) -> None:
        with self._lock:
            self.llm_calls += 1
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self.llm_seconds += seconds
            if cost is None:
                self.cost_complete = False
            else:
                self.cost_usd += cost
            entry = self.by_task.setdefault(task, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0})
            entry["calls"] += 1
            entry["prompt_tokens"] += prompt_tokens
            entry["completion_tokens"] += completion_tokens
            entry["seconds"] += seconds
            self.models[model] = self.models.get(model, 0) + 1

    def add_cache_hit(self, task: str) -> None:
        with self._lock:
            self.cache_hits += 1
            entry = self.by_task.setdefault(task, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0})
            entry["cache_hits"] = entry.get("cache_hits", 0) + 1

    def add_failure(self) -> None:
        with self._lock:
            self.failed_llm_calls += 1

    def add_stage(self, name: str, seconds: float) -> None:
        with self._lock:
            self.stages[name] = self.stages.get(name, 0.0) + seconds

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "failed_llm_calls": self.failed_llm_calls,
                "cache_hits": self.cache_hits,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
                "cost_usd": round(self.cost_usd, 6),
                "cost_complete": self.cost_complete,
                "llm_seconds": round(self.llm_seconds, 3),
                "by_task": {task: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in data.items()}
                            for task, data in self.by_task.items()},
                "models": dict(self.models),
                "stages": {name: round(seconds, 3) for name, seconds in self.stages.items()},
            }


@contextmanager
def collect() -> Iterator[Collector]:
    collector = Collector()
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)


def current() -> Optional[Collector]:
    return _current.get()


def in_current_context(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap fn so it runs with the caller's context (and collector) in another thread."""
    context = contextvars.copy_context()
    return lambda *args, **kwargs: context.run(fn, *args, **kwargs)


def _usage_number(usage: Any, name: str) -> int:
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


_UNSET = object()


def record_llm(task: str, model: str, response: Any, seconds: float, cost: Any = _UNSET) -> None:
    """cost: pass the known cost (0.0 for free models); omitted, litellm's list price is used."""
    collector = _current.get()
    if collector is None:
        return
    usage = getattr(response, "usage", None) or {}
    collector.add_event("llm", task, seconds, model=model, status="ok",
                        prompt_tokens=_usage_number(usage, "prompt_tokens"),
                        completion_tokens=_usage_number(usage, "completion_tokens"))
    if cost is not _UNSET:
        collector.add_llm(task, model, _usage_number(usage, "prompt_tokens"),
                          _usage_number(usage, "completion_tokens"), cost, seconds)
        return
    cost = None
    try:  # litellm knows list prices for most hosted models
        import litellm

        cost = float(litellm.completion_cost(completion_response=response))
    except Exception:
        hidden = getattr(response, "_hidden_params", None) or {}
        if isinstance(hidden.get("response_cost"), (int, float)):
            cost = float(hidden["response_cost"])
    collector.add_llm(task, model, _usage_number(usage, "prompt_tokens"),
                      _usage_number(usage, "completion_tokens"), cost, seconds)


def record_cache_hit(task: str) -> None:
    collector = _current.get()
    if collector is not None:
        collector.add_cache_hit(task)
        collector.add_event("llm", task, 0.0, model="saved reply", status="cache")


def record_llm_failure(task: str = "", model: str = "", seconds: float = 0.0, error: str = "") -> None:
    collector = _current.get()
    if collector is not None:
        collector.add_failure()
        if task:
            collector.add_event("llm", task, seconds, model=model, status="error", error=error[:300])


def record_stage(name: str, seconds: float, status: str = "ok", error: str = "") -> None:
    collector = _current.get()
    if collector is not None:
        collector.add_stage(name, seconds)
        collector.add_event("step", name, seconds, status=status, **({"error": error[:300]} if error else {}))


def record_db(sql: str, seconds: float, rows: int = 0, ok: bool = True, error: str = "") -> None:
    collector = _current.get()
    if collector is not None:
        collector.add_event("db", "query", seconds, sql=str(sql)[:2000], rows=int(rows or 0),
                            status="ok" if ok else "error", **({"error": error[:300]} if error else {}))
