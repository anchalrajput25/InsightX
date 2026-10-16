"""One-time fix for the AI usage log (logs/usage.db).

Before 2026-10-10, AI calls made outside the app (benchmark runs,
scripts) were logged as the user "local", each call as its own
question, so a benchmark run could use up the daily question limit of
whoever uses the app without a name. This moves those calls to the
"system" user. A real question from the app makes several calls under
one request id; a call that is alone in its request came from outside.

    python scripts/fix_usage_labels.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import func, select, update  # noqa: E402

from backend.ai_gateway.context import ANONYMOUS, SYSTEM_USER  # noqa: E402
from backend.ai_gateway.usage_store import ai_calls, get_store  # noqa: E402


def main():
    store = get_store()
    with store.engine.begin() as connection:
        lone = (
            select(ai_calls.c.request_id)
            .where(ai_calls.c.user_id == ANONYMOUS)
            .group_by(ai_calls.c.request_id)
            .having(func.count() == 1)
        )
        moved = connection.execute(
            update(ai_calls)
            .where(ai_calls.c.user_id == ANONYMOUS, ai_calls.c.request_id.in_(lone))
            .values(user_id=SYSTEM_USER)
        ).rowcount
    print(f"Moved {moved} calls from '{ANONYMOUS}' to '{SYSTEM_USER}'.")
    print(f"Questions left on '{ANONYMOUS}' today: {store.questions_today(ANONYMOUS)}")


if __name__ == "__main__":
    main()
