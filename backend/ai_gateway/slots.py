# backend/ai_gateway/slots.py
#
# A fair, first-come-first-served queue for questions.
#
# Every question shares the same free AI quota. Without a queue, 30 people
# asking at once all get a slice of each minute's quota, every question
# moves a little, and nobody's finishes. Letting a few questions run at
# a time (INSIGHTX_MAX_CONCURRENT_QUESTIONS, 4 by default) and queueing
# the rest in arrival order means the first people get their answer
# quickly and everyone else gets theirs in turn.
#
# A question waits up to INSIGHTX_QUESTION_QUEUE_SECONDS (150 s) for its
# turn; after that it gets a clear "busy, try again" message instead of
# a crash. 0 turns the queue off. A question that already holds a slot
# (nested ai_request) never waits for a second one.

import contextvars
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Iterator, Optional

from backend import telemetry
from backend.ai_gateway.errors import AIUnavailableError

_held: contextvars.ContextVar[bool] = contextvars.ContextVar("insightx_question_slot", default=False)


def _int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


class QuestionSlots:
    def __init__(self, limit: int, timeout_s: float, clock=time.monotonic):
        self.limit = limit
        self.timeout_s = timeout_s
        self._clock = clock
        self._cond = threading.Condition()
        self._active = 0
        self._waiting: deque = deque()

    def status(self) -> dict:
        with self._cond:
            return {"limit": self.limit, "answering": self._active, "waiting": len(self._waiting)}

    @contextmanager
    def hold(self) -> Iterator[None]:
        if self.limit <= 0 or _held.get():
            yield
            return
        ticket = object()
        started = self._clock()
        with self._cond:
            self._waiting.append(ticket)
            deadline = started + self.timeout_s
            while not (self._active < self.limit and self._waiting[0] is ticket):
                remaining = deadline - self._clock()
                if remaining <= 0:
                    ahead = self._waiting.index(ticket)
                    self._waiting.remove(ticket)
                    self._cond.notify_all()
                    raise AIUnavailableError(
                        f"Many people are asking questions right now ({self._active} being answered, "
                        f"{ahead} waiting before you). Please try again in a minute.",
                        wait_seconds=60,
                    )
                self._cond.wait(remaining)
            self._waiting.popleft()
            self._active += 1
            self._cond.notify_all()
        waited = self._clock() - started
        if waited > 0.05:
            telemetry.record_stage("question_queue_wait", waited)
        token = _held.set(True)
        try:
            yield
        finally:
            _held.reset(token)
            with self._cond:
                self._active -= 1
                self._cond.notify_all()


_slots: Optional[QuestionSlots] = None
_slots_lock = threading.Lock()


def get_slots() -> QuestionSlots:
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = QuestionSlots(_int("INSIGHTX_MAX_CONCURRENT_QUESTIONS", 4),
                                   _int("INSIGHTX_QUESTION_QUEUE_SECONDS", 150))
        return _slots


def set_slots(slots: Optional[QuestionSlots]) -> None:
    global _slots
    with _slots_lock:
        _slots = slots
