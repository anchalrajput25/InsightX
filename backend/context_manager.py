from typing import Dict, List, Optional

from backend.llm_service import get_llm


# ============================================================
# CONFIGURATION
# ============================================================

# Number of latest messages kept directly in the prompt.
MAX_RECENT_MESSAGES = 6

# Compaction starts when enough NEW messages have accumulated.
COMPACTION_TRIGGER_MESSAGES = 10

# Maximum size of compact memory.
MAX_SUMMARY_CHARS = 6000

# Maximum size of one conversation message.
MAX_MESSAGE_CHARS = 2500

# Maximum context sent to the query rewriter.
MAX_CONTEXT_CHARS = 12000


llm = get_llm(job="context")


# ============================================================
# FORMAT MESSAGES
# ============================================================

def _format_messages(
    messages: List[Dict[str, str]],
) -> str:

    if not messages:
        return "No previous conversation."

    lines = []

    for message in messages:

        if not isinstance(message, dict):
            continue

        role = str(
            message.get(
                "role",
                "unknown",
            )
        ).upper()

        content = str(
            message.get(
                "content",
                "",
            )
        ).strip()

        if not content:
            continue

        # Prevent one huge message from consuming the context.
        content = content[:MAX_MESSAGE_CHARS]

        lines.append(
            f"{role}: {content}"
        )

    if not lines:
        return "No previous conversation."

    return "\n\n".join(lines)


# ============================================================
# COMPACT OLD HISTORY
# ============================================================

def compact_conversation(
    old_messages: List[Dict[str, str]],
    existing_summary: str = "",
) -> str:

    if not old_messages:
        return existing_summary or ""

    history_text = _format_messages(
        old_messages
    )

    previous_summary = (
        existing_summary.strip()
        if existing_summary
        else "No previous summary."
    )

    prompt = f"""
You are the conversation memory component of InsightX,
a production conversational business analytics system.

Create a compact factual memory of the previous investigation.

This memory will be used ONLY to resolve future analytical
follow-up questions.

Examples:

- this city
- that product
- those customers
- the previous result
- compare it with...
- why did it...
- what about the same period?
- what about the previous category?

Preserve ONLY information useful for future analytical questions.

Preserve:

- entities discussed
- metrics
- filters
- dates and time periods
- important analytical results
- resolved references
- important comparisons
- user clarifications
- explicit assumptions
- important business/database context established in conversation

Do NOT:

- invent facts
- invent schema
- invent values
- invent relationships
- make new conclusions
- include unnecessary conversational text
- include irrelevant greetings or filler

If an earlier answer contained an important factual database
result, preserve it if it may help resolve a future question.

Previous compact memory:
{previous_summary}

NEWLY COMPACTED CONVERSATION:
{history_text}

Return ONLY a concise factual memory.
"""

    try:

        summary = llm.invoke(
            prompt
        )

        # llm.invoke() returns an AIMessage; str() would give its repr.
        summary = str(
            getattr(summary, "content", summary) or ""
        ).strip()

        if not summary:
            return existing_summary.strip()

        return summary[
            :MAX_SUMMARY_CHARS
        ]

    except Exception as exc:

        print(
            f"[Context Manager] Compaction error: {exc}"
        )

        # If compaction fails, keep the existing memory.
        return existing_summary.strip()


# ============================================================
# BUILD CONTEXT FOR QUERY REWRITER
# ============================================================

def build_relevant_context(
    conversation_history: Optional[
        List[Dict[str, str]]
    ],
    context_summary: str = "",
) -> str:

    history = conversation_history or []

    if not history and not context_summary:
        return "No previous conversation."

    # Keep only the most recent messages directly.
    recent = history[
        -MAX_RECENT_MESSAGES:
    ]

    recent_text = _format_messages(
        recent
    )

    if context_summary:

        context = f"""
COMPACT MEMORY FROM EARLIER CONVERSATION:

{context_summary}

RECENT CONVERSATION:

{recent_text}
""".strip()

    else:

        context = recent_text

    # Final protection against oversized prompts.
    return context[
        :MAX_CONTEXT_CHARS
    ]


# ============================================================
# CONTEXT MANAGER
# ============================================================

def prepare_context(
    conversation_history: Optional[
        List[Dict[str, str]]
    ],
    context_summary: str = "",
    context_compacted_message_count: int = 0,
) -> Dict[str, object]:

    history = conversation_history or []

    total_messages = len(history)

    # --------------------------------------------------------
    # No history
    # --------------------------------------------------------

    if total_messages == 0:

        return {
            "context_summary": (
                context_summary or ""
            ),
            "relevant_context": (
                "No previous conversation."
            ),
            "context_compacted_message_count": (
                context_compacted_message_count
            ),
        }

    # --------------------------------------------------------
    # How many messages are currently NOT compacted?
    # --------------------------------------------------------

    uncompacted_count = (
        total_messages
        - context_compacted_message_count
    )

    # --------------------------------------------------------
    # Compaction not needed yet
    # --------------------------------------------------------

    if (
        uncompacted_count
        <= COMPACTION_TRIGGER_MESSAGES
    ):

        return {
            "context_summary": (
                context_summary or ""
            ),
            "relevant_context": build_relevant_context(
                conversation_history=history,
                context_summary=context_summary,
            ),
            "context_compacted_message_count": (
                context_compacted_message_count
            ),
        }

    # --------------------------------------------------------
    # Keep latest messages directly available.
    #
    # Everything before the recent window can be compacted.
    # --------------------------------------------------------

    target_compaction_end = max(
        0,
        total_messages - MAX_RECENT_MESSAGES,
    )

    # Never compact messages that were already compacted.
    new_messages_start = max(
        0,
        context_compacted_message_count,
    )

    # --------------------------------------------------------
    # Only compact NEW messages.
    #
    # This is the important production fix.
    # --------------------------------------------------------

    if (
        target_compaction_end
        <= new_messages_start
    ):

        return {
            "context_summary": (
                context_summary or ""
            ),
            "relevant_context": build_relevant_context(
                conversation_history=history,
                context_summary=context_summary,
            ),
            "context_compacted_message_count": (
                context_compacted_message_count
            ),
        }

    new_messages_to_compact = history[
        new_messages_start:
        target_compaction_end
    ]

    # --------------------------------------------------------
    # Update compact memory
    # --------------------------------------------------------

    new_summary = compact_conversation(
        old_messages=new_messages_to_compact,
        existing_summary=context_summary,
    )

    # --------------------------------------------------------
    # Mark these messages as compacted.
    # --------------------------------------------------------

    new_compacted_count = (
        target_compaction_end
    )

    # --------------------------------------------------------
    # Build context for Query Rewriter
    # --------------------------------------------------------

    relevant_context = build_relevant_context(
        conversation_history=history,
        context_summary=new_summary,
    )

    return {
        "context_summary": new_summary,
        "relevant_context": relevant_context,
        "context_compacted_message_count": (
            new_compacted_count
        ),
    }