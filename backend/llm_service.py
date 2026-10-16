# backend/llm_service.py
#
# Entry point the pipeline uses for AI calls. All the work (choosing a
# free model for the job, failover across Gemini / Groq / OpenRouter,
# free-limit tracking, caching, usage records) happens in
# backend/ai_gateway; this module keeps the small API the rest of the
# code already uses:
#
#   llm = get_llm(job="sql_generate")
#   llm.invoke(prompt) -> AIMessage
#
# `task=` is the older name of the same argument ("small" / "sql").

from pathlib import Path
import os
import logging
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

from backend.ai_gateway.gateway import get_gateway  # noqa: E402  (reads .env at import)

logger = logging.getLogger("insightx.llm")

DEFAULT_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2500"))


def call_llm(
    prompt: str,
    task: str = "small",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """Answer `prompt` with a model suited to the job `task` (see ai_gateway/routes.py)."""
    return get_gateway().complete(
        prompt=str(prompt) if prompt is not None else "",
        job=task,
        temperature=DEFAULT_TEMPERATURE if temperature is None else temperature,
        max_tokens=DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens,
    )


def _prompt_text(prompt) -> str:
    """LangChain prompt values and message lists -> one string."""
    if hasattr(prompt, "to_string"):
        return prompt.to_string()
    if isinstance(prompt, list):
        parts = []
        for message in prompt:
            if hasattr(message, "content"):
                parts.append(str(message.content))
            elif isinstance(message, dict):
                parts.append(str(message.get("content", "")))
            else:
                parts.append(str(message))
        return "\n".join(parts)
    return str(prompt)


class InsightXLLM:
    """LangChain-compatible wrapper: invoke(prompt) -> AIMessage."""

    def __init__(self, task: str = "small", temperature: Optional[float] = None, max_tokens: Optional[int] = None):
        self.task = task
        self.temperature = temperature
        self.max_tokens = max_tokens

    def invoke(self, prompt):
        # Prompts are written for PostgreSQL; adapt them when the question
        # is about a MySQL database.
        from backend import dialects

        return AIMessage(content=call_llm(dialects.adapt_text(_prompt_text(prompt)), task=self.task,
                                          temperature=self.temperature, max_tokens=self.max_tokens))


def get_llm(
    task: str = "small",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    job: Optional[str] = None,
):
    return InsightXLLM(task=job or task, temperature=temperature, max_tokens=max_tokens)
