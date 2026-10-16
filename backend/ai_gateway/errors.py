# backend/ai_gateway/errors.py
#
# Turn a provider error into what the gateway should DO about it.
#
#   rate_limit        too many requests right now -> cool down briefly, next route
#   quota             daily free quota or credits used up -> skip until reset
#   unavailable       timeout / overload / network / 5xx -> next route
#   not_found         model retired or renamed -> skip for a day
#   auth              missing or wrong API key -> skip for an hour
#   context_too_long  prompt too big for this model -> next route (bigger context)
#   bad_request       request rejected -> next route; if every route rejects it, fail

import re
from typing import Optional, Tuple

import litellm

_QUOTA_HINTS = re.compile(
    r"per[ _-]?day|perday|daily|quota|insufficient[ _]credits|requires more credits|credits|"
    r"free-models-per-day|billing|exceeded your current",
    re.IGNORECASE,
)
# Gemini words a per-minute limit as "exceeded your current quota ...
# PerMinute"; those must cool down for a minute, not a whole day.
_MINUTE_HINTS = re.compile(r"per[ _-]?minute|perminute|\bRPM\b|\bTPM\b|tokens per min", re.IGNORECASE)
_CONTEXT_HINTS = re.compile(r"context length|context window|too many tokens|maximum context|too long", re.IGNORECASE)


class AIUnavailableError(RuntimeError):
    """No route could answer. `wait_seconds` says when one may be free again."""

    def __init__(self, message: str, wait_seconds: Optional[float] = None, attempts=None):
        super().__init__(message)
        self.wait_seconds = wait_seconds
        self.attempts = attempts or []


class UsageLimitError(RuntimeError):
    """A user or the whole app reached a configured usage limit."""


def _status(exc: Exception) -> Optional[int]:
    for name in ("status_code", "code"):
        value = getattr(exc, name, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    match = re.search(r'"code"\s*:\s*(\d{3})', str(exc))
    return int(match.group(1)) if match else None


def retry_after(exc: Exception) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        return float(headers.get("retry-after", ""))
    except (TypeError, ValueError, AttributeError):
        match = re.search(r"retry(?:Delay| in| after)[^\d]{0,5}(\d+(?:\.\d+)?)\s*s", str(exc), re.IGNORECASE)
        return float(match.group(1)) if match else None


def classify(exc: Exception) -> Tuple[str, str]:
    """(kind, short message) for a provider exception."""
    text = str(exc)
    short = re.sub(r"\s+", " ", text)[:200]
    status = _status(exc)

    if isinstance(exc, (litellm.AuthenticationError, litellm.PermissionDeniedError)) or status in (401, 403):
        return "auth", short
    if isinstance(exc, litellm.NotFoundError) or status == 404:
        return "not_found", short
    if status == 402:
        return "quota", short
    if isinstance(exc, litellm.ContextWindowExceededError) or (status in (400, 413) and _CONTEXT_HINTS.search(text)):
        return "context_too_long", short
    if isinstance(exc, litellm.RateLimitError) or status == 429:
        if _MINUTE_HINTS.search(text):
            return "rate_limit", short
        return ("quota" if _QUOTA_HINTS.search(text) else "rate_limit"), short
    if isinstance(exc, (litellm.Timeout, litellm.APIConnectionError, litellm.ServiceUnavailableError,
                        litellm.InternalServerError, litellm.BadGatewayError)) or (status and status >= 500):
        return "unavailable", short
    if isinstance(exc, litellm.BadRequestError) or status == 400:
        return "bad_request", short
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "unavailable", short
    # Unknown provider errors: treat as a route problem, not ours, so the
    # next route still gets a chance.
    return "unavailable", short
