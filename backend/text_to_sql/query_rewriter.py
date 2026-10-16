import re
from typing import Any, Dict, List, Optional, Tuple, Union

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from backend.llm_service import get_llm


# Small model is sufficient for query rewriting.
llm = get_llm(job="rewrite")


MAX_HISTORY_MESSAGES = 8
MAX_MESSAGE_CHARS = 2500
MAX_CONTEXT_CHARS = 12000

# A rewrite far longer than this means the model ignored the
# "return only the question" instruction (e.g. it answered or
# explained), so the original question is safer.
MAX_REWRITE_CHARS = 2000

# Alternative phrasings requested per question (primary + N queries).
DEFAULT_ALTERNATIVE_COUNT = 4

NO_CONTEXT = "No previous conversation."


# ============================================================
# PROMPTS
# ============================================================
#
# The examples below are abstract patterns with <placeholders>.
# They illustrate the transformation; they are not rules for
# specific questions or domains.

SYSTEM_PROMPT = """
You are the Query Rewriter of InsightX, a conversational analytics
system that answers questions over arbitrary relational databases.

Your output is used for schema retrieval and SQL generation, so it
must state the user's analytical intent explicitly, in plain business
language, as ONE concise, self-contained request with EXACTLY the
same meaning as the user's latest message.

Work through these steps internally and output only the result.

STEP 1 - Understand the intent. Identify:
- the operation: count, total, average, minimum/maximum, ranking,
  comparison, percentage/share/ratio, trend over time, distribution,
  listing/filtering, or lookup
- the measure being computed and the entities it is computed over
- the grouping (per what)
- every filter and condition, including negations
- the time scope
- the ranking direction and any requested number of results

STEP 2 - Resolve conversational references using the conversation.
- Replace references such as "this", "that", "these", "it", "them",
  "the same", "the previous one" with the concrete value they refer
  to. Values stated in earlier answers count as context.
- An elliptical follow-up ("what about <X>?", "and for <Y>?",
  "only the top <N>", "same for last <period>") inherits the
  operation, measure, grouping and filters of the previous request
  and changes only what the new message changes.
- When an answer is followed by a "DATABASE RESULT (exact stored
  values)" line, take referenced values from that line, copied
  exactly (same spelling, case and accents), not from the answer's
  wording. Example: answer "<Display Name>", result
  "<column>=<stored value>" -> use <stored value>.
- If a reference cannot be resolved from the conversation, keep it
  visible instead of guessing.

STEP 3 - Normalize the analytical intent.
- Write an explicit request, preferably starting with a verb such as
  Find, Count, Calculate, List, Compare, or Show.
- Make implicit aggregation explicit, for example:
  "most/least <items>" -> the highest/lowest number of <items>
  "biggest/smallest <measure>" -> the highest/lowest <measure>
  "how many <items>" -> count of <items>
  "what share/percent of <items>" -> percentage of <items> ... out of all <items>
  "how did <measure> change over <period>" -> trend of <measure> over <period>
- Keep a singular question singular ("which <group> ..." -> "the
  <group> with ...") and keep a stated number of results as stated.

STEP 4 - Preserve exactly:
- every measure, entity, filter value, name, number, unit, date and
  time range, comparison target, ranking direction, result limit and
  negation
- literal values verbatim (same spelling and casing)

RULES
- Do NOT add filters, time ranges, limits, units, metrics or
  assumptions that the user did not state or clearly imply.
- Do NOT invent table or column names, and do not use snake_case or
  code identifiers unless the user wrote them.
- Do NOT write SQL. Do NOT answer the question. Do NOT explain.
- If the latest message is already explicit, self-contained and
  canonical, return it unchanged or nearly unchanged. Do not reword
  it just to make it different.
- If the latest message is not an analytical question (for example a
  greeting), return it unchanged.

ALTERNATIVE PHRASINGS
Also write {alternative_count} alternative phrasings of the SAME
request. They are used to search the database schema, so:
- keep exactly the same meaning, filters, values, time range,
  ranking and limit as REWRITTEN_QUERY
- vary the vocabulary: use synonyms and related business terms a
  database might use for the same concepts (<measure> -> a synonym
  of <measure>, <entity> -> another common name for <entity>)
- each one must be a complete, self-contained request
- no SQL, and no table or column names you were not given

PATTERN EXAMPLES (abstract - never copy their wording or domain)
- "which <group> has the most <items>?"
  -> Find the <group> with the highest number of <items>.
- "top <N> <entities> by <measure> in <period>"
  -> List the <N> <entities> with the highest <measure> in <period>.
- "what % of <items> were <status>?"
  -> Calculate the percentage of <items> with <status> out of all <items>.
- previous request "Find the <group> with the highest <measure> in
  <period A>.", latest message "what about <period B>?"
  -> Find the <group> with the highest <measure> in <period B>.

OUTPUT FORMAT - exactly this, nothing else:
CANONICAL: <yes if the latest message was already explicit and
self-contained and you kept it essentially unchanged, otherwise no>
REWRITTEN_QUERY: <the single rewritten request>
ALTERNATIVE_QUERIES:
- <alternative phrasing 1>
- <alternative phrasing 2>
...
"""


HUMAN_PROMPT = """
Conversation so far (oldest first):

{conversation_context}

Latest user message:

{question}
"""


RETRY_INSTRUCTION = """
Your previous output did not normalize the latest user message
({reason}).

Redo the rewrite following all steps: state the operation, the
measure, the grouping, every filter, the time scope, the ranking
direction and any result limit explicitly, and resolve references
from the conversation. Keep the meaning exactly the same and do not
add anything the user did not state or clearly imply.

Only if the latest message is already fully explicit and
self-contained may you return it unchanged, with CANONICAL: yes.

Respond in the required output format.
"""


_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", HUMAN_PROMPT),
    ]
)


# ============================================================
# CONTEXT
# ============================================================

def _normalize_for_compare(text: str) -> str:
    """Case-, punctuation- and whitespace-insensitive form."""
    text = re.sub(r"[^\w\s]", " ", str(text).lower())
    return re.sub(r"\s+", " ", text).strip()


def _format_history(
    conversation_history: Optional[list],
    max_messages: int = MAX_HISTORY_MESSAGES,
) -> str:
    """
    Format recent conversation messages for the LLM.

    Only recent messages are included here because older
    information is represented by context_summary.
    """

    if not conversation_history:
        return NO_CONTEXT

    recent_messages = conversation_history[-max_messages:]

    lines = []

    for message in recent_messages:
        if not isinstance(message, dict):
            continue

        role = str(message.get("role", "unknown")).upper()
        content = str(message.get("content", "")).strip()

        if not content:
            continue

        content = content[:MAX_MESSAGE_CHARS]

        evidence = str(message.get("evidence", "")).strip()
        if evidence:
            content += f"\nDATABASE RESULT (exact stored values): {evidence}"

        lines.append(
            f"{role}: {content}"
        )

    if not lines:
        return NO_CONTEXT

    return "\n\n".join(lines)


def _build_context(
    conversation_context: Optional[Union[str, List[Dict[str, str]]]],
    question: str = "",
) -> str:
    """
    Build the conversation context for the prompt.

    Accepts either a pre-formatted string (context_manager.py) or the
    LangGraph conversation_history list. The list already ends with
    the current question, which is removed so it is not presented as
    prior conversation. When trimming, the most recent context wins.
    """

    if isinstance(conversation_context, list):
        history = list(conversation_context)

        if (
            history
            and isinstance(history[-1], dict)
            and str(history[-1].get("role", "")).lower() == "user"
            and _normalize_for_compare(history[-1].get("content", ""))
            == _normalize_for_compare(question)
        ):
            history = history[:-1]

        context = _format_history(history)

    elif conversation_context:
        context = str(conversation_context).strip() or NO_CONTEXT

    else:
        context = NO_CONTEXT

    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]

    return context


# ============================================================
# RESPONSE PARSING
# ============================================================

def _message_text(response: Any) -> str:
    """
    Extract plain text from an LLM response.

    InsightXLLM.invoke() returns an AIMessage. str(AIMessage) is its
    repr ("content='...' additional_kwargs={} ..."), so .content must
    be used. Content may also be a list of content blocks.
    """

    content = getattr(response, "content", response)

    if isinstance(content, list):
        parts = []

        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))

        content = "".join(parts)

    if not isinstance(content, str):
        return ""

    return content


def _clean_rewrite(text: str) -> str:
    """
    Reduce the model output to the bare rewritten question.
    """

    # Reasoning models (e.g. Qwen3) may emit <think>...</think>.
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = text.strip()

    # Drop a leading label such as "Rewrite:" or
    # "Rewritten question:".
    text = re.sub(
        r"^(?:rewritten(?:[\s_]+analytical)?[\s_]+(?:question|query)|rewrite)\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Drop matching surrounding quotes (but keep code fences intact
    # so SQL/code output is still recognizable).
    if (
        len(text) >= 2
        and text[0] == text[-1]
        and text[0] in "\"'`"
        and not text.startswith("```")
    ):
        text = text[1:-1]

    return text.strip()


_CANONICAL_RE = re.compile(
    r"^[ \t*#>-]*CANONICAL[ \t*]*:[ \t*]*(yes|no|true|false)\b",
    flags=re.IGNORECASE | re.MULTILINE,
)

_ALTERNATIVES_LABEL_RE = re.compile(
    r"^[ \t*#>-]*ALTERNATIVE[ _]QUER(?:Y|IES)[ \t*]*:[ \t*]*",
    flags=re.IGNORECASE | re.MULTILINE,
)

_REWRITE_LABEL_RE = re.compile(
    r"^[ \t*#>-]*REWRITTEN[ _]QUERY[ \t*]*:[ \t*]*",
    flags=re.IGNORECASE | re.MULTILINE,
)


def _parse_response(raw: str) -> Tuple[str, Optional[bool]]:
    """
    Parse the two-line output format.

    Returns (rewritten_query, canonical). canonical is None when the
    model did not state it. Output without labels is treated as the
    rewritten query itself.
    """

    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.IGNORECASE | re.DOTALL)

    # Alternative phrasings follow the primary rewrite; they are parsed
    # separately by _parse_alternatives().
    alternatives_label = _ALTERNATIVES_LABEL_RE.search(raw)
    if alternatives_label:
        raw = raw[:alternatives_label.start()]

    canonical = None
    canonical_match = _CANONICAL_RE.search(raw)
    if canonical_match:
        canonical = canonical_match.group(1).lower() in ("yes", "true")

    label = _REWRITE_LABEL_RE.search(raw)
    if label:
        text = raw[label.end():]
        # Stop at a CANONICAL line placed after the query.
        trailing = _CANONICAL_RE.search(text)
        if trailing:
            text = text[:trailing.start()]
    elif canonical_match:
        text = raw[:canonical_match.start()] + raw[canonical_match.end():]
    else:
        text = raw

    # Collapse to one line: the rewrite is a single request.
    text = " ".join(text.split())

    return _clean_rewrite(text), canonical


def _parse_alternatives(raw: str, primary: str, limit: int) -> List[str]:
    """
    Alternative phrasings listed after ALTERNATIVE_QUERIES:, one per
    line (bullets or numbering allowed). Unusable lines, duplicates
    and repeats of the primary rewrite are dropped.
    """
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.IGNORECASE | re.DOTALL)
    label = _ALTERNATIVES_LABEL_RE.search(raw)
    if not label:
        return []

    seen = {_normalize_for_compare(primary)}
    alternatives: List[str] = []

    for line in raw[label.end():].splitlines():
        if _CANONICAL_RE.match(line) or _REWRITE_LABEL_RE.match(line):
            break
        text = re.sub(r"^(?:\s*(?:[-*\u2022]|\d+[.)])\s+)+", "", line)
        text = " ".join(text.split()).strip("*_ ").strip()
        text = _clean_rewrite(text)
        key = _normalize_for_compare(text)
        if not text or key in seen or _invalid_reason(text) is not None:
            continue
        seen.add(key)
        alternatives.append(text)
        if len(alternatives) >= limit:
            break

    return alternatives


# SQL structure, not just the words: an English rewrite such as
# "Select the customers from <city> ..." must not be rejected.
_SQL_LIKE_RE = re.compile(
    r"```"
    r"|(?-i:\bSELECT\b[\s\S]*\bFROM\b)"
    r"|^\s*select\b[\s\S]*\bfrom\b[\s\S]*?(?:[*;=(]|\bgroup\s+by\b|\border\s+by\b|\blimit\s+\d)"
    r"|^\s*with\s+\w+\s+as\s*\("
    r"|^\s*(?:insert\s+into|update\s+\w+\s+set|delete\s+from)\b",
    flags=re.IGNORECASE,
)


def _invalid_reason(text: str) -> Optional[str]:
    """Return why a rewrite is unusable, or None if it is usable."""
    if not text:
        return "it was empty"
    if len(text) > MAX_REWRITE_CHARS:
        return "it was far too long"
    if _SQL_LIKE_RE.search(text):
        return "it contained SQL or code instead of a question"
    return None


def _is_echo(text: str, question: str) -> bool:
    return _normalize_for_compare(text) == _normalize_for_compare(question)


# ============================================================
# MAIN FUNCTION
# ============================================================

def rewrite_query_variants(
    question: str,
    conversation_context: Optional[Union[str, List[Dict[str, str]]]] = None,
    alternative_count: int = DEFAULT_ALTERNATIVE_COUNT,
) -> Dict[str, Any]:
    """
    Rewrite the latest user question and produce alternative phrasings.

    Returns {"primary": str, "alternatives": [str, ...]}.

    - primary: one self-contained, explicit analytical request with
      the same intent (drives schema linking and SQL generation).
    - alternatives: up to alternative_count phrasings of the same
      request with different vocabulary (used to widen schema
      retrieval). May be empty; never contains the primary.

    One retry with a stronger instruction is made when the model
    echoes the question without declaring it canonical, or returns an
    unusable rewrite. Any failure falls back to the original question
    with no alternatives.
    """

    question = str(question).strip()

    if not question:
        return {"primary": "", "alternatives": []}

    messages = _PROMPT.format_messages(
        conversation_context=_build_context(conversation_context, question),
        question=question,
        alternative_count=alternative_count,
    )

    try:
        # InsightXLLM.invoke() returns an AIMessage.
        first_raw = _message_text(llm.invoke(messages))
    except Exception as exc:
        print(f"[Query Rewriter] Error: {exc}")
        return {"primary": question, "alternatives": []}

    first, canonical = _parse_response(first_raw)
    reason = _invalid_reason(first)

    if reason is None:
        if not _is_echo(first, question) or canonical is True:
            return {
                "primary": first,
                "alternatives": _parse_alternatives(first_raw, first, alternative_count),
            }
        reason = "it repeated the message without stating it was already canonical"

    print(f"[Query Rewriter] Retrying: {reason}.")

    retry_messages = messages + [
        AIMessage(content=first_raw),
        HumanMessage(content=RETRY_INSTRUCTION.format(reason=reason)),
    ]

    try:
        retry_raw = _message_text(llm.invoke(retry_messages))
        retry, _ = _parse_response(retry_raw)
    except Exception as exc:
        print(f"[Query Rewriter] Retry error: {exc}")
        retry_raw, retry = "", ""

    if _invalid_reason(retry) is None:
        return {
            "primary": retry,
            "alternatives": _parse_alternatives(retry_raw, retry, alternative_count),
        }

    # An echo is still a faithful rewrite; anything else unusable
    # falls back to the original question.
    if _invalid_reason(first) is None:
        return {
            "primary": first,
            "alternatives": _parse_alternatives(first_raw, first, alternative_count),
        }

    print("[Query Rewriter] No usable rewrite; using original question.")
    return {"primary": question, "alternatives": []}


def rewrite_query(
    question: str,
    conversation_context: Optional[Union[str, List[Dict[str, str]]]] = None,
) -> str:
    """Primary rewrite only (see rewrite_query_variants)."""
    return rewrite_query_variants(question, conversation_context)["primary"]
