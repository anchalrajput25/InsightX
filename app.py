import hashlib
import json
import os
import re
import requests
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

from auth_view import Api, auth_headers, render_account_box, require_sign_in, user_storage_key
from chart_view import render_chart
from investigation_view import render_investigation

BACKEND_URL = os.getenv("INSIGHTX_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
THREAD_REGISTRY_FILE = (
    Path(__file__).resolve().parent
    / ".insightx_threads.json"
)

st.set_page_config(
    page_title="InsightX",
    page_icon="🔍",
    layout="wide",
)

# Sign-in screen (stops here until signed in). None when the backend
# has sign-in turned off (INSIGHTX_AUTH_REQUIRED=0).
AUTH_USER = require_sign_in(st, BACKEND_URL)
# Every backend call goes through api: it sends the session token.
api = Api(st)


def thread_registry_file() -> Path:
    """Each signed-in person has their own chat list."""
    if AUTH_USER is None:
        return THREAD_REGISTRY_FILE
    return THREAD_REGISTRY_FILE.parent / ".insightx_threads" / f"{user_storage_key(AUTH_USER)}.json"


# =========================================================
# CHAT REGISTRY
# =========================================================

def load_thread_registry():
    """The chat list is kept by the backend; the old local file is moved there once."""
    try:
        response = api.get(f"{BACKEND_URL}/chats", timeout=10)
        if response.ok:
            chats = response.json().get("chats") or []
            if not chats:
                local = load_local_thread_registry()
                if local:
                    save_thread_registry(local)
                    chats = local
            return import_chats_from_before_sign_in(chats)
    except requests.exceptions.RequestException:
        pass
    return load_local_thread_registry()


def import_chats_from_before_sign_in(chats):
    """
    Chats made on this computer before sign-in was turned on are kept in
    the shared .insightx_threads.json. The first account that signs in
    here gets them once, so no old chat disappears from the sidebar.
    """
    marker = THREAD_REGISTRY_FILE.with_suffix(".imported")
    if AUTH_USER is None or not THREAD_REGISTRY_FILE.exists() or marker.exists():
        return chats
    try:
        old = json.loads(THREAD_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return chats
    known = {chat.get("thread_id") for chat in chats}
    added = [chat for chat in old if isinstance(chat, dict) and chat.get("thread_id") and chat["thread_id"] not in known]
    merged = sorted(chats + added, key=lambda chat: str(chat.get("updated_at") or ""), reverse=True)
    if added:
        try:
            if not api.put(f"{BACKEND_URL}/chats", json={"chats": merged}, timeout=10).ok:
                return chats
        except requests.exceptions.RequestException:
            return chats
    try:
        marker.write_text(f"Chats from before sign-in were added to {user_storage_key(AUTH_USER)}.\n", encoding="utf-8")
    except OSError:
        pass
    return merged


def load_local_thread_registry():
    if not thread_registry_file().exists():
        return []

    try:
        data = json.loads(
            thread_registry_file().read_text(
                encoding="utf-8"
            )
        )

        return (
            data
            if isinstance(data, list)
            else []
        )

    except Exception:
        return []


def save_thread_registry(threads):
    try:
        if api.put(f"{BACKEND_URL}/chats", json={"chats": threads}, timeout=10).ok:
            return
    except requests.exceptions.RequestException:
        pass
    # Backend not reachable: keep the list on this computer for now.
    thread_registry_file().parent.mkdir(parents=True, exist_ok=True)
    thread_registry_file().write_text(
        json.dumps(
            threads,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def ensure_thread_registry():

    if "thread_registry" not in st.session_state:

        st.session_state[
            "thread_registry"
        ] = load_thread_registry()

    if not st.session_state[
        "thread_registry"
    ]:

        now = datetime.now().isoformat()
        thread_id = str(uuid.uuid4())

        st.session_state[
            "thread_registry"
        ] = [
            {
                "thread_id": thread_id,
                "title": "New Chat",
                "created_at": now,
                "updated_at": now,
            }
        ]

        save_thread_registry(
            st.session_state[
                "thread_registry"
            ]
        )


def create_new_thread():
    """
    Create one new chat.

    One thread_id = one complete conversation.
    """

    now = datetime.now().isoformat()
    thread_id = str(uuid.uuid4())

    thread = {
        "thread_id": thread_id,
        "title": "New Chat",
        "created_at": now,
        "updated_at": now,
    }

    st.session_state[
        "thread_registry"
    ].insert(
        0,
        thread,
    )

    save_thread_registry(
        st.session_state[
            "thread_registry"
        ]
    )

    st.session_state[
        "thread_id"
    ] = thread_id

    st.session_state[
        "chat_messages"
    ] = []

    st.session_state[
        "history_loaded_for"
    ] = thread_id

    st.session_state.pop(
        "last_query_result",
        None,
    )
    st.session_state.pop("last_investigation", None)

    # IMPORTANT:
    # Do not modify current_question here.
    #
    # The current_question widget may already have
    # been instantiated during this Streamlit run.


def rename_thread(
    thread_id: str,
    new_title: str,
):

    new_title = " ".join(
        new_title.strip().split()
    )

    if not new_title:
        return

    for thread in st.session_state[
        "thread_registry"
    ]:

        if thread["thread_id"] == thread_id:

            thread["title"] = new_title[:80]

            thread[
                "updated_at"
            ] = datetime.now().isoformat()

            break

    st.session_state[
        "thread_registry"
    ].sort(
        key=lambda item: item.get(
            "updated_at",
            "",
        ),
        reverse=True,
    )

    save_thread_registry(
        st.session_state[
            "thread_registry"
        ]
    )


def update_thread_metadata(
    thread_id: str,
    question: str,
):
    """
    Automatically name a new chat from its first question.
    """

    for thread in st.session_state[
        "thread_registry"
    ]:

        if thread["thread_id"] == thread_id:

            if thread.get(
                "title"
            ) in {
                "New Chat",
                "New Investigation",
                "",
            }:

                title = " ".join(
                    question.strip().split()
                )

                thread["title"] = (
                    title[:55] + "..."
                    if len(title) > 58
                    else title
                )

            thread[
                "updated_at"
            ] = datetime.now().isoformat()

            break

    st.session_state[
        "thread_registry"
    ].sort(
        key=lambda item: item.get(
            "updated_at",
            "",
        ),
        reverse=True,
    )

    save_thread_registry(
        st.session_state[
            "thread_registry"
        ]
    )


def get_current_thread_title():

    current_id = st.session_state[
        "thread_id"
    ]

    for thread in st.session_state[
        "thread_registry"
    ]:

        if thread["thread_id"] == current_id:

            return thread.get(
                "title",
                "New Chat",
            )

    return "New Chat"


# =========================================================
# DATABASE THREAD RESTORATION
# =========================================================

def restore_thread_from_database(
    thread_id: str,
):
    """
    Load the selected chat's persisted history through the backend.

    The UI never opens its own database connection: the backend owns
    the checkpoint store for the connected database.
    """

    st.session_state[
        "history_loaded_for"
    ] = thread_id

    connection_id = st.session_state.get(
        "connection_id"
    )

    if not connection_id:

        st.session_state[
            "chat_messages"
        ] = []

        return

    def fetch(active_connection_id):
        return api.get(
            f"{BACKEND_URL}/threads/{thread_id}/history",
            params={"connection_id": active_connection_id},
            timeout=120,
        )

    try:

        response = fetch(connection_id)

        if is_missing_backend_session(
            response
        ):

            new_connection_id = reconnect_backend_session()

            if new_connection_id:
                response = fetch(new_connection_id)

        if response.ok:

            st.session_state[
                "chat_messages"
            ] = response.json().get(
                "conversation_history",
                [],
            )

        else:

            show_backend_error(
                response
            )

            st.session_state[
                "chat_messages"
            ] = []

    except requests.exceptions.RequestException as restore_error:

        st.error(
            f"Could not load chat history: {restore_error}"
        )

        st.session_state[
            "chat_messages"
        ] = []


def reset_chat_view():
    """Clear per-chat display state when switching or creating chats."""

    st.session_state.pop(
        "last_query_result",
        None,
    )
    st.session_state.pop("last_investigation", None)

    st.session_state[
        "clear_current_question"
    ] = True


# =========================================================
# INITIAL SESSION STATE
# =========================================================

ensure_thread_registry()


if "thread_id" not in st.session_state:

    st.session_state[
        "thread_id"
    ] = st.session_state[
        "thread_registry"
    ][0]["thread_id"]


if "chat_messages" not in st.session_state:

    st.session_state[
        "chat_messages"
    ] = []


if "clear_current_question" not in st.session_state:

    st.session_state[
        "clear_current_question"
    ] = False


# =========================================================
# SIDEBAR — CHAT LIST
# =========================================================

st.sidebar.title(
    "InsightX"
)

st.sidebar.caption(
    "Business Data Investigations"
)

# =========================================================
# USER (for AI usage tracking)
# =========================================================

def current_user_name():
    if AUTH_USER is not None:
        return AUTH_USER["email"]
    return (st.session_state.get("user_name") or "").strip() or "local"


def ai_headers():
    if AUTH_USER is not None:
        return auth_headers(st)
    return {"X-InsightX-User": current_user_name()}


PAGE_ASK = "💬 Ask & investigate"
PAGE_BENCHMARK = "📏 Benchmark"
PAGE_USAGE = "📊 AI usage"
PAGE_TRACES = "🔍 Traces"
PAGE_HISTORY = "🕘 History"
PAGE_DASHBOARD = "📈 Dashboard"
PAGE_ALERTS = "🚨 Alerts"
if st.session_state.get("go_to_page"):
    # A button elsewhere asked to switch page (set before the radio exists).
    st.session_state["app_page"] = st.session_state.pop("go_to_page")
app_page = st.sidebar.radio(
    "Page",
    [PAGE_ASK, PAGE_DASHBOARD, PAGE_ALERTS, PAGE_HISTORY, PAGE_TRACES, PAGE_BENCHMARK, PAGE_USAGE],
    key="app_page",
    label_visibility="collapsed",
)
# Alerts page (alerts_view.py): the "🚨 N open alerts" line under the page list.
from alerts_view import render_alert_badge  # noqa: E402
render_alert_badge(st, BACKEND_URL, st.session_state.get("connection_id"), headers=ai_headers())

if AUTH_USER is not None:
    render_account_box(st, BACKEND_URL, api)
else:
    # Sign-in is off: the name typed here is sent with every question so
    # AI usage and the daily question limit are per person.
    st.sidebar.text_input(
        "Your name",
        key="user_name",
        placeholder="e.g. anchal",
        help="Used to track AI usage and the daily question limit per person.",
    )


if st.sidebar.button(
    "➕ New Chat",
    use_container_width=True,
    type="primary",
):

    create_new_thread()

    # Set the flag rather than modifying the widget directly.
    st.session_state[
        "clear_current_question"
    ] = True

    st.rerun()


st.sidebar.divider()

st.sidebar.write(
    "**Chats**"
)


for index, thread in enumerate(
    st.session_state[
        "thread_registry"
    ]
):

    thread_id = thread[
        "thread_id"
    ]

    title = (
        thread.get(
            "title",
            "New Chat",
        )
        or "New Chat"
    )

    is_current = (
        thread_id
        == st.session_state[
            "thread_id"
        ]
    )

    # Empty chats (nothing asked yet) are not listed, except the open one.
    if title in ("New Chat", "New Investigation") and not is_current:
        continue

    # -----------------------------------------------------
    # Chat selection row
    # -----------------------------------------------------

    col_chat, col_menu = (
        st.sidebar.columns(
            [5, 1]
        )
    )

    with col_chat:

        label = (
            f"🟢 {title}"
            if is_current
            else title
        )

        if st.button(
            label,
            key=(
                f"chat_select_"
                f"{index}_"
                f"{thread_id}"
            ),
            use_container_width=True,
        ):

            st.session_state[
                "thread_id"
            ] = thread_id

            reset_chat_view()

            # History is loaded by the chat section on the rerun
            # (history_loaded_for no longer matches thread_id).
            st.rerun()

    # -----------------------------------------------------
    # Chat menu
    # -----------------------------------------------------

    with col_menu:

        with st.popover(
            "⋮",
            use_container_width=True,
        ):

            st.caption(
                "Chat options"
            )

            rename_key = (
                f"rename_input_"
                f"{thread_id}"
            )

            new_title = st.text_input(
                "Rename chat",
                value=title,
                key=rename_key,
            )

            if st.button(
                "Rename",
                key=(
                    f"rename_button_"
                    f"{thread_id}"
                ),
                use_container_width=True,
            ):

                rename_thread(
                    thread_id,
                    new_title,
                )

                st.rerun()


# =========================================================
# PAGE HEADER
# =========================================================

st.title(
    "InsightX"
)

st.caption(
    "AI-powered business data investigation platform"
)

if app_page == PAGE_DASHBOARD:
    from dashboard_view import render_dashboard_page

    render_dashboard_page(st, BACKEND_URL, st.session_state.get("connection_id"), headers=ai_headers())
    st.stop()

if app_page == PAGE_ALERTS:
    from alerts_view import render_alerts_page

    def ask_why(question):
        """'Find out why': open the Ask page and investigate."""
        st.session_state["pending_question"] = str(question)
        if st.session_state.get("query_mode") == "Ask a question":
            st.session_state["query_mode"] = "Auto"
        st.session_state["go_to_page"] = PAGE_ASK
        st.rerun()

    render_alerts_page(st, BACKEND_URL, st.session_state.get("connection_id"), headers=ai_headers(), ask=ask_why)
    st.stop()

if app_page == PAGE_HISTORY:
    from history_view import render_history_page

    def ask_from_history(question, kind):
        """Open the Ask page with this question and run it."""
        st.session_state["pending_question"] = str(question)
        if kind == "investigation" and st.session_state.get("query_mode") == "Ask a question":
            st.session_state["query_mode"] = "Auto"
        st.session_state["go_to_page"] = PAGE_ASK
        st.rerun()

    render_history_page(st, BACKEND_URL, headers=ai_headers(), ask=ask_from_history,
                        connected=bool(st.session_state.get("connection_id")))
    st.stop()

if app_page == PAGE_TRACES:
    from trace_view import render_trace_page

    render_trace_page(st, BACKEND_URL, headers=ai_headers())
    st.stop()

if app_page == PAGE_USAGE:
    from usage_view import render_usage_page

    render_usage_page(st, BACKEND_URL, current_user=current_user_name(), headers=ai_headers(),
                      signed_in=AUTH_USER is not None)
    st.stop()

if app_page == PAGE_BENCHMARK:
    from evaluation_view import render_benchmark_page

    _project_root = Path(__file__).resolve().parent
    render_benchmark_page(
        st,
        _project_root / "logs" / "evaluation",
        _project_root / "backend" / "evaluation" / "golden",
    )
    st.stop()


# =========================================================
# BACKEND ERROR
# =========================================================

def show_backend_error(
    response,
):

    try:

        error_data = response.json()

        error_detail = error_data.get(
            "detail",
            error_data,
        )

    except ValueError:

        error_detail = response.text

    if response.status_code == 401:
        st.warning("Your session ended. Please sign in again.")
        if st.button("Go to sign in"):
            st.rerun()
        return

    # 429: the user's daily question limit; 503: no free AI model has
    # capacity right now. Both are expected situations, not crashes.
    if response.status_code in (429, 503):
        st.warning(error_detail)
        return

    st.error(
        f"Backend Error "
        f"({response.status_code}): "
        f"{error_detail}"
    )


# =========================================================
# POSTGRES FORM
# =========================================================

def postgres_form(
    form_key: str,
    default_port: int = 5432,
    user_placeholder: str = "postgres",
):

    col1, col2 = st.columns(2)

    with col1:

        host = st.text_input(
            "Host",
            value="localhost",
            key=f"{form_key}_host",
        )

        port = st.number_input(
            "Port",
            min_value=1,
            max_value=65535,
            value=default_port,
            step=1,
            key=f"{form_key}_port",
        )

        database = st.text_input(
            "Database Name",
            placeholder="data_reliability",
            key=f"{form_key}_database",
        )

    with col2:

        username = st.text_input(
            "Username",
            placeholder=user_placeholder,
            key=f"{form_key}_username",
        )

        password = st.text_input(
            "Password",
            type="password",
            key=f"{form_key}_password",
        )

    return (
        host,
        port,
        database,
        username,
        password,
    )


# =========================================================
# FORM DATA
# =========================================================

def build_form_data(
    host,
    port,
    database,
    username,
    password,
    session_id=None,
):

    form_data = {
        "host": str(host),
        "port": str(port),
        "database": str(database),
        "username": str(username),
        "password": str(password),
    }

    if session_id is not None:

        form_data[
            "session_id"
        ] = str(session_id)

    return {
        key: (
            None,
            value,
        )
        for key, value in form_data.items()
    }


# =========================================================
# DISPLAY SCHEMA
# =========================================================

def display_schema(
    schema,
):

    if not schema:

        st.info(
            "No schema information returned by backend."
        )

        return

    st.divider()

    st.subheader(
        "Database Schema"
    )

    col1, col2, col3 = st.columns(3)

    with col1:

        st.metric(
            "Total Tables",
            schema.get(
                "total_tables",
                0,
            ),
        )

    with col2:

        st.metric(
            "Relationships",
            len(
                schema.get(
                    "relationships",
                    [],
                )
            ),
        )

    with col3:

        total_columns = sum(
            len(
                table.get(
                    "columns",
                    [],
                )
            )
            for table in schema.get(
                "tables",
                [],
            )
        )

        st.metric(
            "Total Columns",
            total_columns,
        )

    tables = schema.get(
        "tables",
        [],
    )

    if not tables:

        st.warning(
            "No tables found in this database."
        )

        return

    st.subheader(
        "Tables"
    )

    for table in tables:

        table_name = table.get(
            "table_name",
            table.get(
                "name",
                "Unknown Table",
            ),
        )

        row_count = table.get(
            "row_count",
            table.get(
                "rows",
                0,
            ),
        )

        with st.expander(
            f"{table_name} | {row_count} rows"
        ):

            st.write(
                f"**Primary Key:** "
                f"{table.get('primary_key', [])}"
            )

            columns = table.get(
                "columns",
                [],
            )

            if columns:

                st.write(
                    "**Columns:**"
                )

                st.dataframe(
                    columns,
                    use_container_width=True,
                    hide_index=True,
                )

            foreign_keys = table.get(
                "foreign_keys",
                [],
            )

            if foreign_keys:

                st.write(
                    "**Foreign Keys:**"
                )

                st.json(
                    foreign_keys
                )

    relationships = schema.get(
        "relationships",
        [],
    )

    if relationships:

        st.subheader(
            "Table Relationships"
        )

        st.dataframe(
            relationships,
            use_container_width=True,
            hide_index=True,
        )


# =========================================================
# SAVE DATABASE CONFIG
# =========================================================

def save_database_config(
    host,
    port,
    database,
    username,
    password,
    db_type="postgresql",
):

    st.session_state[
        "db_config"
    ] = {
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "password": password,
        "db_type": db_type,
    }


def wants_investigation(mode, question):
    if mode == "Investigate a change":
        return True
    if mode == "Ask a question":
        return False
    try:
        response = api.get(f"{BACKEND_URL}/investigate/route", params={"question": question}, timeout=10)
        return bool(response.ok and response.json().get("investigation"))
    except requests.exceptions.RequestException:
        return False


def run_investigation_request(question, fall_back=False):
    """
    POST /investigate (reconnecting once after a backend restart) and keep
    the report. With fall_back (Auto mode), a question that cannot be
    investigated returns False so it is answered as a normal question.
    """

    def send(active_connection_id):
        return api.post(
            f"{BACKEND_URL}/investigate",
            json={"connection_id": active_connection_id, "question": question,
                  "thread_id": st.session_state.get("thread_id")},
            headers=ai_headers(),
            timeout=900,
        )

    try:
        with st.spinner("Investigating: planning, analysing segments and related signals..."):
            response = send(st.session_state.get("connection_id"))
            if is_missing_backend_session(response):
                new_connection_id = reconnect_backend_session()
                if new_connection_id:
                    response = send(new_connection_id)
    except requests.exceptions.ConnectionError:
        st.error(f"Backend is not running at {BACKEND_URL}. Start FastAPI first.")
        return
    except requests.exceptions.Timeout:
        st.error("The investigation timed out. Check the backend terminal.")
        return

    if response.status_code == 422:
        detail = response.json().get("detail")
        if fall_back:
            st.info(f"No change to investigate here ({detail}), so this is answered as a normal question.")
            return False
        st.warning(f"Could not investigate this question: {detail}")
        return True
    if not response.ok:
        show_backend_error(response)
        return

    report = response.json()
    st.session_state["last_investigation"] = report
    st.session_state.pop("last_query_result", None)
    # The question and its answer are now part of this chat.
    if report.get("conversation_history"):
        st.session_state["chat_messages"] = report["conversation_history"]
    if st.session_state.get("thread_id"):
        update_thread_metadata(thread_id=st.session_state["thread_id"], question=question)
    st.session_state["clear_current_question"] = True
    st.rerun()


def is_missing_backend_session(
    response,
) -> bool:
    """
    True when the backend no longer knows this connection_id.

    Connection sessions live in backend memory, so a backend restart
    (including uvicorn --reload after a code change) forgets them.
    """

    if response.status_code != 404:
        return False

    try:
        detail = str(response.json().get("detail", ""))
    except ValueError:
        return False

    return "connection session not found" in detail.lower()


def reconnect_backend_session():
    """
    Re-open the backend connection with the details already held in
    this Streamlit session. Returns the new connection_id, or None.
    """

    db_config = st.session_state.get(
        "db_config"
    )

    if not db_config or db_config.get("db_type") == "sqlite":
        return None  # a SQLite file is reopened by the backend itself

    form_data = build_form_data(
        db_config["host"],
        db_config["port"],
        db_config["database"],
        db_config["username"],
        db_config["password"],
    )
    form_data["db_type"] = (
        None,
        db_config.get("db_type", "postgresql"),
    )

    response = api.post(
        f"{BACKEND_URL}/connect-postgres",
        files=form_data,
        timeout=600,
    )

    if not response.ok:
        show_backend_error(
            response
        )
        return None

    connection_id = response.json().get(
        "connection_id"
    )

    st.session_state[
        "connection_id"
    ] = connection_id

    return connection_id


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~])")


def escape_markdown(
    text: str,
) -> str:
    """Show user text literally (no headings, lists or links)."""

    return _MARKDOWN_SPECIAL.sub(
        r"\\\1",
        str(text),
    )


def render_chat_text(
    content: str,
    role: str,
) -> str:
    """
    Chat text at normal size: user questions are shown literally,
    and markdown headings in answers become bold lines.
    """

    if role == "user":
        return escape_markdown(content)

    return re.sub(
        r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$",
        r"**\1**",
        str(content),
        flags=re.MULTILINE,
    )


# =========================================================
# DISPLAY TEXT-TO-SQL PIPELINE
# =========================================================

TRUST_STYLES = {
    "high": ("#0f7b4f", "#e7f6ee", "High trust", "Answer checked against the data"),
    "medium": ("#9a6700", "#fff6e0", "Medium trust", "Check the reasons before relying on it"),
    "low": ("#b42318", "#fdecea", "Low trust", "Do not rely on this answer without checking"),
}


def _answer_html(answer, claim_check):
    """Answer text with every checked number highlighted (green = found in the result)."""
    import html as html_lib

    text = html_lib.escape(answer or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    status = {}
    for item in (claim_check or {}).get("numbers", []) + (claim_check or {}).get("values", []):
        token = html_lib.escape(item.get("text", ""))
        if token and token not in status:
            status[token] = bool(item.get("verified", False))
    if status:
        # One pass over the original text, so a token is never matched
        # inside markup inserted for another token.
        pattern = re.compile(
            r"(?<!\w)(" + "|".join(re.escape(t) for t in sorted(status, key=len, reverse=True)) + r")(?!\w)"
        )

        def mark(match):
            token = match.group(1)
            verified = status[token]
            color = "#0f7b4f" if verified else "#b42318"
            background = "rgba(15,123,79,.10)" if verified else "rgba(180,35,24,.12)"
            title = "Found in the query result" if verified else "NOT found in the query result"
            return (
                f'<span title="{title}" style="color:{color};background:{background};'
                f'border-bottom:2px solid {color};padding:0 3px;border-radius:3px;font-weight:600">{token}</span>'
            )

        parts = re.split(r"(<[^>]+>)", text)  # leave tags (<b>) untouched
        text = "".join(part if part.startswith("<") else pattern.sub(mark, part) for part in parts)
    return text.replace("\n", "<br>")


def render_trace_note(result):
    """Total time and AI calls, with a way to open the full trace."""
    trace = result.get("trace") or {}
    if not trace.get("id"):
        return
    calls = int(trace.get("llm_calls") or 0)
    text = (
        f"⏱ Took {float(trace.get('seconds') or 0):.1f} s · "
        f"{calls} AI call{'s' if calls != 1 else ''} · "
        f"{int(trace.get('db_queries') or 0)} database queries"
    )
    left, right = st.columns([4, 1])
    with left:
        st.caption(text)
    with right:
        if st.button("🔍 See trace", key=f"trace_{trace['id']}", use_container_width=True):
            st.session_state["open_trace_id"] = trace["id"]
            st.session_state["go_to_page"] = PAGE_TRACES
            st.rerun()


def render_why_button(result):
    """Offer a root-cause investigation of the answer just given."""
    question = str(result.get("question") or "").strip()
    if not question:
        return
    if st.button(
        "🔍 Find out why, and how to improve it",
        key=f"why_{hashlib.sha1(question.encode('utf-8')).hexdigest()[:10]}",
        help="Looks at how this number changed over time, what drove the change, and what to do about it.",
    ):
        st.session_state["investigate_question"] = (
            f"{question} Why did this change recently, and how can it improve?"
        )
        st.rerun()


def ask_follow_up(question):
    """Put a suggested question in the box and run it."""
    st.session_state["pending_question"] = str(question)
    st.rerun()


def render_follow_ups(result):
    """Related questions to ask next, as buttons."""
    follow_ups = [str(q) for q in result.get("follow_ups") or [] if str(q).strip()]
    if not follow_ups:
        return
    st.markdown("**💬 Ask next**")
    asked = hashlib.sha1(str(result.get("question") or "").encode("utf-8")).hexdigest()[:10]
    columns = st.columns(2)
    for number, follow_up in enumerate(follow_ups):
        with columns[number % 2]:
            if st.button(follow_up, key=f"next_{asked}_{number}", use_container_width=True):
                ask_follow_up(follow_up)


def render_cache_note(result):
    """Say when an earlier answer to the same question was reused."""
    cache = result.get("cache") or {}
    if not cache.get("hit"):
        return
    when = ""
    if cache.get("asked_at"):
        when = " on " + datetime.fromtimestamp(float(cache["asked_at"])).strftime("%d %b %Y, %H:%M")
    asked = (
        "the same question"
        if cache.get("match") == "same"
        else f'a similar question ("{escape_markdown(cache.get("question", ""))}")'
    )
    if cache.get("answer_reused"):
        st.info(
            f"⚡ Answered from cache: you asked {asked}{when}. Its SQL was run again on the "
            "current data, gave the same result, so the saved answer was reused. No AI was used."
        )
    else:
        st.info(
            f"⚡ Reused the SQL from {asked}{when}. The data has changed since, "
            "so only the answer text was written again by the AI."
        )


def render_trust_card(result):
    """Trust level, score and the evidence behind it."""
    trust = result.get("trust") or {}
    if not trust:
        return
    color, background, label, subtitle = TRUST_STYLES.get(trust.get("level"), TRUST_STYLES["medium"])
    score = int(trust.get("score") or 0)
    claims = result.get("claim_check") or {}
    numbers = claims.get("numbers") or []
    verified = sum(1 for item in numbers if item.get("verified"))
    selection = result.get("sql_selection") or {}
    executed = selection.get("executed_ok") or 0
    agreeing = max(selection.get("clusters") or [0]) if executed else 0
    fanout = (result.get("sanity") or {}).get("fanout") or []

    def chip(icon, text_value, ok):
        chip_color = "#0f7b4f" if ok else ("#b42318" if ok is False else "#57606a")
        return (
            f'<span style="display:inline-flex;gap:6px;align-items:center;border:1px solid {chip_color}33;'
            f'color:{chip_color};background:#ffffff;border-radius:999px;padding:3px 10px;font-size:12.5px;'
            f'margin:4px 6px 0 0">{icon} {text_value}</span>'
        )

    chips = []
    if numbers:
        chips.append(chip("🔢", f"{verified}/{len(numbers)} numbers found in result", verified == len(numbers)))
    if executed > 1:
        chips.append(chip("🧪", f"{agreeing}/{executed} SQL candidates agree", agreeing == executed))
    chips.append(chip("🔗", "join fan-out detected" if fanout else "no double counting", not fanout))
    corrections = int(result.get("correction_attempts") or 0)
    chips.append(chip("🛠️", f"{corrections} automatic correction(s)" if corrections else "no corrections needed",
                      None if corrections else True))

    st.markdown(
        f"""
<div style="border:1px solid {color}40;background:{background};border-radius:14px;padding:14px 16px;margin:8px 0 4px">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
    <div>
      <div style="font-weight:700;color:{color};font-size:16px">● {label}</div>
      <div style="color:#57606a;font-size:13px">{subtitle}</div>
    </div>
    <div style="min-width:180px;flex:1;max-width:320px">
      <div style="display:flex;justify-content:space-between;font-size:12px;color:#57606a">
        <span>Trust score</span><span style="font-weight:700;color:{color}">{score}/100</span>
      </div>
      <div style="height:8px;background:#ffffff;border-radius:99px;overflow:hidden;border:1px solid {color}26">
        <div style="width:{score}%;height:100%;background:{color};border-radius:99px"></div>
      </div>
    </div>
  </div>
  <div style="margin-top:8px">{''.join(chips)}</div>
</div>
""",
        unsafe_allow_html=True,
    )

    reasons = [reason for reason in trust.get("reasons") or [] if reason.get("effect")]
    if reasons:
        with st.expander("Why this trust level?"):
            for reason in reasons:
                st.markdown(f"- **{reason['effect']:+d}**: {escape_markdown(reason['detail'])}")


def display_pipeline(
    result,
):

    st.divider()

    st.subheader(
        "Text-to-SQL Pipeline"
    )

    status = str(
        result.get(
            "status",
            "UNKNOWN",
        )
    ).upper()

    if status == "SUCCESS":

        st.success(
            "Pipeline completed successfully."
        )

        render_cache_note(result)

        render_trust_card(result)

        render_trace_note(result)

        render_why_button(result)

        render_follow_ups(result)

    elif status == "NEEDS_CLARIFICATION":

        st.warning(
            "Clarification required."
        )

    else:

        st.error(
            "Pipeline did not complete successfully."
        )

        if result.get(
            "error_stage"
        ):

            st.caption(
                f"Failed stage: {result.get('error_stage')}"
            )

        if result.get(
            "error"
        ):

            st.error(
                result.get(
                    "error"
                )
            )

    # -----------------------------------------------------
    # 1. Query Rewriting
    # -----------------------------------------------------

    with st.expander(
        "1. Query Rewriting",
        expanded=True,
    ):

        st.write(
            result.get(
                "rewritten_query",
                "Not available",
            )
        )

        alternatives = (
            result.get(
                "query_variants",
                [],
            )
            or []
        )[1:]

        if alternatives:

            st.caption(
                "Alternative phrasings used to search "
                "the schema:"
            )

            for alternative in alternatives:

                st.markdown(
                    f"- {escape_markdown(alternative)}"
                )

    # -----------------------------------------------------
    # 2. Query Decomposition
    # -----------------------------------------------------

    with st.expander(
        "2. Query Decomposition"
    ):

        st.write(
            result.get(
                "decomposition",
                "Not available",
            )
        )

    # -----------------------------------------------------
    # 3. Hybrid Retrieval
    # -----------------------------------------------------

    with st.expander(
        "3. Hybrid Retrieval"
    ):

        st.json(
            result.get(
                "retrieved_schema",
                [],
            )
        )

    # -----------------------------------------------------
    # 4. GraphRAG
    # -----------------------------------------------------

    with st.expander(
        "4. GraphRAG"
    ):

        st.json(
            result.get(
                "graph_context",
                {},
            )
        )

    # -----------------------------------------------------
    # 5. Schema Linking
    # -----------------------------------------------------

    with st.expander(
        "5. Schema Linking"
    ):

        linked = result.get("linked_schema") or {}
        if linked:
            st.caption(
                f"Status: {linked.get('status', '?')} — "
                f"{len(linked.get('tables') or [])} tables, "
                f"{len(linked.get('joins') or [])} joins"
            )
            if linked.get("corrections"):
                st.markdown("**Corrected by verification**")
                for text in linked["corrections"]:
                    st.markdown(f"- {escape_markdown(text)}")
            if linked.get("issues"):
                st.markdown("**Problems found in the linker output**")
                for text in linked["issues"]:
                    st.markdown(f"- {escape_markdown(text)}")

        st.code(
            str(
                result.get(
                    "schema_linking",
                    "Not available",
                )
            ),
            language="text",
        )

    # -----------------------------------------------------
    # 5b. SQL Candidates
    # -----------------------------------------------------

    candidates = result.get("sql_candidates") or []
    if len(candidates) > 1:
        with st.expander(
            "5b. SQL Candidates"
        ):
            selection = result.get("sql_selection") or {}
            if selection.get("reason"):
                st.caption(selection["reason"])
            for item in candidates:
                outcome = item.get("execution") or item.get("status", "")
                label = f"Candidate {item.get('index')}: {outcome}"
                if item.get("row_count") is not None:
                    label += f", {item['row_count']} rows"
                if item.get("selected"):
                    label += " — selected"
                st.markdown(f"**{escape_markdown(label)}**")
                if item.get("sql"):
                    st.code(item["sql"], language="sql")
                error = item.get("execution_error") or item.get("error")
                if error:
                    st.caption(error)

    # -----------------------------------------------------
    # 6. Generated SQL
    # -----------------------------------------------------

    with st.expander(
        "6. Generated SQL",
        expanded=True,
    ):

        st.code(
            result.get(
                "generated_sql",
                "",
            )
            or result.get(
                "sql",
                "",
            ),
            language="sql",
        )

    # -----------------------------------------------------
    # 7. SQL Validation
    # -----------------------------------------------------

    with st.expander(
        "7. SQL Validation"
    ):

        st.json(
            result.get(
                "validation_result",
                {},
            )
        )

    # -----------------------------------------------------
    # 8. SQL Safety
    # -----------------------------------------------------

    with st.expander(
        "8. SQL Safety"
    ):

        st.json(
            result.get(
                "safety_result",
                {},
            )
        )

    # -----------------------------------------------------
    # 9. SQL Correction
    # -----------------------------------------------------

    if result.get(
        "correction_attempts",
        0,
    ):

        with st.expander(
            "9. SQL Correction"
        ):

            st.write(
                "Correction attempts:",
                result.get(
                    "correction_attempts",
                    0,
                ),
            )

            st.code(
                result.get(
                    "corrected_sql",
                    "",
                ),
                language="sql",
            )

    # -----------------------------------------------------
    # 10. Query Execution
    # -----------------------------------------------------

    execution = result.get(
        "execution_result"
    ) or {}

    with st.expander(
        "10. Query Execution",
        expanded=True,
    ):

        if execution.get(
            "success"
        ):

            st.success(
                f"Executed successfully — "
                f"{execution.get('row_count', 0)} rows, "
                f"{execution.get('execution_time_ms', 0):.2f} ms"
            )

            if execution.get(
                "truncated"
            ):

                st.warning(
                    "The result has more rows than the display limit; "
                    f"showing the first {execution.get('row_count', 0)} rows."
                )

            if execution.get(
                "rows"
            ):

                st.dataframe(
                    execution["rows"],
                    use_container_width=True,
                )

        elif execution.get(
            "error"
        ):

            st.error(
                execution.get(
                    "error"
                )
            )

        else:

            st.info(
                "The query was not executed because "
                "an earlier stage did not complete."
            )

    # -----------------------------------------------------
    # 11. Result Validation
    # -----------------------------------------------------

    with st.expander(
        "11. Result Validation"
    ):

        st.json(
            result.get(
                "result_validation",
                {},
            )
        )

    # -----------------------------------------------------
    # 12. AI Answer
    # -----------------------------------------------------

    st.subheader(
        "12. AI Answer"
    )

    claim_check = result.get("claim_check") or {}
    if claim_check.get("numbers") or claim_check.get("values"):
        st.markdown(
            '<div style="font-size:16px;line-height:1.6">'
            + _answer_html(result.get("answer", ""), claim_check)
            + "</div>",
            unsafe_allow_html=True,
        )
        if claim_check.get("unverified_numbers") or claim_check.get("unverified_values"):
            st.error(
                "Highlighted in red: stated in the answer but not found in the query result. "
                "Treat these as unverified."
            )
        else:
            st.caption("Highlighted in green: every number and value was found in the query result.")
    else:
        st.markdown(
            result.get(
                "answer",
                "No answer generated.",
            )
        )

    # -----------------------------------------------------
    # 13. Chart (picked automatically from the result)
    # -----------------------------------------------------

    if execution.get("success") and execution.get("rows"):
        chart_key = "chart_" + hashlib.md5(
            ((result.get("generated_sql") or "") + str(execution.get("row_count"))).encode()
        ).hexdigest()[:12]
        with st.container():
            st.subheader("13. Chart")
            if not render_chart(
                st,
                execution,
                result.get("question") or "",
                key=chart_key,
                context=result.get("chart_data") or None,
            ):
                st.caption(
                    "No chart: this answer has no numbers to draw "
                    "(for example a list of names or a yes/no answer). "
                    "The table in step 10 shows it."
                )


# =========================================================
# DATA SOURCE
# =========================================================

st.subheader(
    "Add Data Source"
)

source_type = st.radio(
    "Choose how you want to add your data",
    options=[
        "Upload Database Folder",
        "Connect PostgreSQL",
        "Connect MySQL",
        "Connect SQL Server",
        "Upload SQLite File",
    ],
    horizontal=True,
)

st.divider()


# =========================================================
# UPLOAD DATABASE FOLDER
# =========================================================

if source_type == "Upload Database Folder":

    st.subheader(
        "Upload Database Folder"
    )

    st.info(
        "Upload a ZIP file containing your CSV or Excel files. "
        "InsightX will extract the files and show their details "
        "before importing them into PostgreSQL."
    )

    try:
        loader = api.get(f"{BACKEND_URL}/upload/fast-loader", timeout=5).json()
        if loader.get("available"):
            st.success(
                f"⚡ Fast loader is ON (DuckDB {loader.get('version')}): "
                f"CSV files of {loader.get('min_mb')} MB or more load with DuckDB."
            )
        else:
            st.warning(
                "Fast loader is OFF: DuckDB is not installed in the Python that runs the backend. "
                "Stop the backend, run this command, then start the backend again:"
            )
            st.code(f'"{loader.get("python")}" -m pip install duckdb', language="bash")
    except Exception:
        pass

    uploaded_file = st.file_uploader(
        "Upload your database folder as a ZIP",
        type=["zip"],
        key="database_zip",
    )

    if uploaded_file is not None:

        st.success(
            f"Selected file: "
            f"{uploaded_file.name}"
        )

        if st.button(
            "Extract and Inspect Files",
            type="primary",
        ):

            with st.spinner(
                "Extracting and inspecting files..."
            ):

                try:

                    response = api.post(
                        f"{BACKEND_URL}/upload-folder",
                        files={
                            "file": (
                                uploaded_file.name,
                                uploaded_file.getvalue(),
                                "application/zip",
                            )
                        },
                        timeout=600,
                    )

                    if response.status_code == 200:

                        result = response.json()

                        st.session_state[
                            "upload_result"
                        ] = result

                        st.session_state[
                            "upload_session_id"
                        ] = result[
                            "session_id"
                        ]

                        st.success(
                            "ZIP extracted and files inspected successfully."
                        )

                    else:

                        show_backend_error(
                            response
                        )

                except requests.exceptions.ConnectionError:

                    st.error(
                        "Backend is not running. "
                        "Please start FastAPI first."
                    )

                except requests.exceptions.Timeout:

                    st.error(
                        "Request timed out. "
                        "Please check whether the backend is running."
                    )

                except Exception as error:

                    st.error(
                        f"Error: {error}"
                    )

                    st.code(
                        traceback.format_exc()
                    )

    # -----------------------------------------------------
    # Uploaded result
    # -----------------------------------------------------

    if "upload_result" in st.session_state:

        result = st.session_state[
            "upload_result"
        ]

        st.divider()

        st.subheader(
            "Uploaded Database Details"
        )

        file_items = result.get(
            "files",
            [],
        ) or result.get(
            "file_profiles",
            [],
        )

        total_files = result.get(
            "total_files",
            len(file_items),
        )

        st.metric(
            "Files Found",
            total_files,
        )

        for file in file_items:

            if isinstance(
                file, dict
            ):

                file_name = file.get(
                    "file_name",
                    file.get(
                        "file",
                        "Unknown File",
                    ),
                )

                rows = file.get(
                    "rows",
                    0,
                )

                importable = file.get(
                    "importable",
                    not file.get("error"),
                )

                status_label = (
                    f"→ table {file.get('table_name')}"
                    if importable and file.get("table_name")
                    else "⚠ will be skipped"
                    if not importable
                    else ""
                )

                with st.expander(
                    f"{file_name} | {rows} rows {status_label}"
                ):

                    if file.get(
                        "error"
                    ):

                        st.error(
                            f"This file cannot be imported and "
                            f"will be skipped: {file.get('error')}"
                        )

                    for warning in file.get(
                        "warnings",
                        [],
                    ):

                        st.info(warning)

                    col1, col2, col3 = st.columns(
                        3
                    )

                    with col1:

                        st.write(
                            f"**File Type:** "
                            f"{file.get('file_type', 'Unknown')}"
                        )
                        if file.get("reader") == "duckdb":
                            st.caption("⚡ Read with DuckDB (fast loader)")

                    with col2:

                        st.write(
                            f"**Rows:** "
                            f"{file.get('rows', 0)}"
                        )

                    with col3:

                        st.write(
                            f"**Columns:** "
                            f"{file.get('columns', 0)}"
                        )

                    if file.get(
                        "column_types"
                    ):

                        st.write(
                            "**Columns and detected types:**"
                        )

                        st.dataframe(
                            [
                                {
                                    "Source column": column.get("source_name"),
                                    "Table column": column.get("column_name"),
                                    "Type": column.get("data_type"),
                                    "Empty values": column.get("null_count"),
                                }
                                for column in file.get(
                                    "column_types",
                                    [],
                                )
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )

                    else:

                        st.write(
                            "**Column Names:**"
                        )

                        st.write(
                            file.get(
                                "column_names",
                                [],
                            )
                        )

                    st.write(
                        "**Preview:**"
                    )

                    st.dataframe(
                        file.get(
                            "preview",
                            [],
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )

            elif isinstance(
                file, str
            ):

                with st.expander(
                    f"{file} | metadata unavailable"
                ):

                    st.warning(
                        f"Backend returned a filename "
                        f"string instead of inspection "
                        f"metadata: {file}"
                    )

                    st.write(
                        "Metadata unavailable for this file."
                    )

            else:

                with st.expander(
                    "Unknown item | metadata unavailable"
                ):

                    st.warning(
                        f"Unexpected file item type "
                        f"{type(file).__name__}: {file}"
                    )

        st.divider()

        st.subheader(
            "Connect PostgreSQL"
        )

        st.info(
            "Your files have been inspected. "
            "Connect to a PostgreSQL database to import "
            "the data as tables. Existing tables with the "
            "same names in that database will be replaced."
        )

        skipped_uploads = result.get(
            "skipped_files",
            [],
        )

        if skipped_uploads:

            st.warning(
                f"{len(skipped_uploads)} file(s) cannot be imported "
                f"and will be skipped: "
                + ", ".join(
                    item.get("file_name", "?")
                    for item in skipped_uploads
                )
            )

        (
            host,
            port,
            database,
            username,
            password,
        ) = postgres_form(
            "upload_import"
        )

        if st.button(
            "Connect PostgreSQL and Import Data",
            type="primary",
        ):

            session_id = st.session_state.get(
                "upload_session_id"
            )

            if not session_id:

                st.error(
                    "Upload session missing. "
                    "Please upload and inspect the ZIP again."
                )

            elif not database.strip():

                st.error(
                    "Please enter the database name."
                )

            elif not username.strip():

                st.error(
                    "Please enter the PostgreSQL username."
                )

            elif not password:

                st.error(
                    "Please enter the PostgreSQL password."
                )

            else:

                with st.spinner(
                    "Connecting to PostgreSQL and importing data..."
                ):

                    try:

                        form_data = build_form_data(
                            host,
                            port,
                            database,
                            username,
                            password,
                            session_id,
                        )

                        response = api.post(
                            f"{BACKEND_URL}/import-uploaded-files",
                            files=form_data,
                            timeout=1800,
                        )

                        if response.ok:

                            result = response.json()

                            st.session_state[
                                "import_result"
                            ] = result

                            # FIX: persist backend connection_id.
                            # /import-uploaded-files returns it,
                            # but it was previously discarded,
                            # so POST /query could never be built.
                            st.session_state[
                                "connection_id"
                            ] = result.get(
                                "connection_id"
                            )

                            save_database_config(
                                host,
                                port,
                                database,
                                username,
                                password,
                            )

                            st.success(
                                "Data imported into PostgreSQL successfully!"
                            )

                            if result.get(
                                "replaced_tables"
                            ):

                                st.warning(
                                    "Replaced existing tables: "
                                    + ", ".join(
                                        result["replaced_tables"]
                                    )
                                )

                            if result.get(
                                "skipped_files"
                            ):

                                st.warning(
                                    "Skipped files: "
                                    + "; ".join(
                                        f"{item.get('file_name')} ({item.get('reason')})"
                                        for item in result["skipped_files"]
                                    )
                                )

                            st.subheader(
                                "Import Summary"
                            )

                            col1, col2 = st.columns(
                                2
                            )

                            with col1:

                                st.metric(
                                    "Total Tables",
                                    result.get(
                                        "total_tables",
                                        len(
                                            result.get(
                                                "imported_tables",
                                                [],
                                            )
                                        ),
                                    ),
                                )

                            with col2:

                                st.metric(
                                    "Database",
                                    database,
                                )

                            imported_tables = result.get(
                                "imported_tables",
                                [],
                            )

                            if imported_tables:

                                st.subheader(
                                    "Imported Tables"
                                )

                                for table in imported_tables:

                                    if isinstance(
                                        table,
                                        dict,
                                    ):

                                        table_name = table.get(
                                            "table_name",
                                            table.get(
                                                "name",
                                                "Unknown",
                                            ),
                                        )

                                        rows = table.get(
                                            "rows",
                                            table.get(
                                                "row_count",
                                                "N/A",
                                            ),
                                        )

                                        columns = table.get(
                                            "columns",
                                            "N/A",
                                        )

                                        with st.expander(
                                            f"📊 {table_name}"
                                        ):

                                            st.write(
                                                f"**Rows:** {rows}"
                                            )

                                            st.write(
                                                f"**Columns:** {columns}"
                                            )

                                    else:

                                        st.write(
                                            f"• {table}"
                                        )

                            display_schema(
                                result.get(
                                    "schema",
                                    {},
                                )
                            )

                        else:

                            show_backend_error(
                                response
                            )

                    except requests.exceptions.ConnectionError:

                        st.error(
                            "Backend is not running. "
                            "Please start FastAPI first."
                        )

                    except requests.exceptions.Timeout:

                        st.error(
                            "Import timed out. "
                            "Please check the backend terminal."
                        )

                    except Exception as error:

                        st.error(
                            f"Error: {error}"
                        )

                        st.code(
                            traceback.format_exc()
                        )

        if "import_result" in st.session_state:

            display_schema(
                st.session_state[
                    "import_result"
                ].get(
                    "schema",
                    {},
                )
            )


# =========================================================
# SQLITE FILE
# =========================================================

elif source_type == "Upload SQLite File":

    st.subheader(
        "Upload a SQLite Database File"
    )

    st.info(
        "Upload a SQLite file (.db, .sqlite or .sqlite3). "
        "InsightX opens it read-only and fetches its schema."
    )

    sqlite_file = st.file_uploader(
        "SQLite file",
        type=["db", "sqlite", "sqlite3", "db3"],
        key="sqlite_file",
    )

    if st.button(
        "Connect and Fetch Schema",
        type="primary",
        disabled=sqlite_file is None,
        key="connect_sqlite",
    ):

        with st.spinner(
            "Uploading and fetching schema..."
        ):

            try:

                response = api.post(
                    f"{BACKEND_URL}/connect-sqlite",
                    files={"file": (sqlite_file.name, sqlite_file.getvalue())},
                    timeout=600,
                )

                if response.status_code == 200:

                    result = response.json()
                    st.session_state["direct_result"] = result
                    st.session_state["connection_id"] = result.get("connection_id")
                    # Shown in "Database connected: ..."; a SQLite file is
                    # reopened by the backend itself, never re-uploaded.
                    st.session_state["db_config"] = {
                        "database": sqlite_file.name,
                        "db_type": "sqlite",
                    }
                    st.success(result.get("message", "SQLite file connected successfully."))

                else:

                    show_backend_error(
                        response
                    )

            except requests.exceptions.ConnectionError:

                st.error(
                    "Backend is not running. "
                    "Please start FastAPI first."
                )

            except requests.exceptions.Timeout:

                st.error(
                    "Upload timed out. The file may be too large."
                )

    if "direct_result" in st.session_state:

        display_schema(
            st.session_state[
                "direct_result"
            ].get(
                "schema",
                {},
            )
        )


# =========================================================
# DIRECT POSTGRESQL / MYSQL / SQL SERVER
# =========================================================

else:

    # (db_type sent to the backend, label, default port, usual admin user)
    db_type, db_label, default_port, user_placeholder = {
        "Connect MySQL": ("mysql", "MySQL", 3306, "root"),
        "Connect SQL Server": ("mssql", "SQL Server", 1433, "sa"),
    }.get(source_type, ("postgresql", "PostgreSQL", 5432, "postgres"))

    st.subheader(
        f"Connect {db_label} Database"
    )

    st.info(
        f"Connect an existing {db_label} database. "
        "InsightX will fetch its schema dynamically."
        + {
            "mysql": " MySQL and MariaDB are supported (read-only).",
            "mssql": (" Use a login that can only read (for example one with the db_datareader role):"
                      " SQL Server has no read-only mode, so InsightX blocks every change itself."),
        }.get(db_type, "")
    )

    (
        host,
        port,
        database,
        username,
        password,
    ) = postgres_form(
        f"direct_{db_type}",
        default_port=default_port,
        user_placeholder=user_placeholder,
    )

    if st.button(
        "Connect and Fetch Schema",
        type="primary",
    ):

        if not database.strip():

            st.error(
                "Please enter the database name."
            )

        elif not username.strip():

            st.error(
                f"Please enter the {db_label} username."
            )

        elif not password:

            st.error(
                f"Please enter the {db_label} password."
            )

        else:

            with st.spinner(
                "Connecting and fetching schema..."
            ):

                try:

                    form_data = build_form_data(
                        host,
                        port,
                        database,
                        username,
                        password,
                    )
                    form_data["db_type"] = (
                        None,
                        db_type,
                    )

                    response = api.post(
                        f"{BACKEND_URL}/connect-postgres",
                        files=form_data,
                        timeout=120,
                    )

                    if response.status_code == 200:

                        result = response.json()

                        st.session_state[
                            "direct_result"
                        ] = result

                        # FIX: persist backend connection_id.
                        # /connect-postgres returns it, but it
                        # was previously discarded. /query needs
                        # this exact key.
                        st.session_state[
                            "connection_id"
                        ] = result.get(
                            "connection_id"
                        )

                        save_database_config(
                            host,
                            port,
                            database,
                            username,
                            password,
                            db_type,
                        )

                        st.success(
                            f"{db_label} connected successfully."
                        )

                    else:

                        show_backend_error(
                            response
                        )

                except requests.exceptions.ConnectionError:

                    st.error(
                        "Backend is not running. "
                        "Please start FastAPI first."
                    )

                except requests.exceptions.Timeout:

                    st.error(
                        "Connection timed out. "
                        f"Please check {db_label} and FastAPI."
                    )

                except Exception as error:

                    st.error(
                        f"Error: {error}"
                    )

                    st.code(
                        traceback.format_exc()
                    )

    if "direct_result" in st.session_state:

        display_schema(
            st.session_state[
                "direct_result"
            ].get(
                "schema",
                {},
            )
        )


# =========================================================
# ASK YOUR DATA — CURRENT CHAT
# =========================================================

st.divider()

st.caption(
    "This is the selected chat. Its conversation is saved "
    "automatically."
)

db_config = st.session_state.get(
    "db_config"
)

connection_id = st.session_state.get(
    "connection_id"
)

if not connection_id:

    st.error(
        "Pipeline not connected successfully: "
        "connection_id is missing in session state. "
        "Please reconnect your database."
    )

    if db_config:

        st.warning(
            "db_config exists but connection_id does not. "
            "This means /connect-postgres or "
            "/import-uploaded-files succeeded locally but "
            "its connection_id was not stored."
        )

    else:

        st.info(
            "Connect a database or upload your data first. "
            "Then you can ask natural-language questions."
        )

else:

    st.success(
        f"Database connected: "
        f"{(db_config or {}).get('database', '?')}"
    )

    # =====================================================
    # IMPORTANT:
    # Clear widget state BEFORE text_area is instantiated.
    # =====================================================

    if st.session_state.pop(
        "clear_current_question",
        False,
    ):

        st.session_state[
            "current_question"
        ] = ""

    # A clicked "Ask next" question goes in the box and runs.
    pending_question = st.session_state.pop("pending_question", None)
    if pending_question:
        st.session_state["current_question"] = pending_question

    # =====================================================
    # RESTORE SELECTED THREAD
    # =====================================================

    if st.session_state.get(
        "history_loaded_for"
    ) != st.session_state[
        "thread_id"
    ]:

        restore_thread_from_database(
            st.session_state[
                "thread_id"
            ]
        )

    # =====================================================
    # DISPLAY CHAT HISTORY
    # =====================================================

    if st.session_state.get(
        "chat_messages"
    ):

        for message in st.session_state[
            "chat_messages"
        ]:

            role = message.get(
                "role",
                "assistant",
            )

            content = message.get(
                "content",
                "",
            )

            with st.chat_message(
                role
            ):

                st.markdown(
                    render_chat_text(
                        content,
                        role,
                    )
                )

    # =====================================================
    # QUESTION INPUT
    # =====================================================

    question = st.text_area(
        "Ask a question about your data",
        height=100,
        key="current_question",
    )

    # =====================================================
    # RUN QUERY — via backend POST /query
    # =====================================================

    query_mode = st.radio(
        "Mode",
        ["Auto", "Ask a question", "Investigate a change"],
        horizontal=True,
        key="query_mode",
        help=(
            "Auto: questions like 'why did revenue drop?' run a root-cause investigation, "
            "everything else is answered with SQL."
        ),
    )

    use_cache = not st.checkbox(
        "Fresh answer (don't reuse an earlier answer to the same question)",
        value=False,
        key="fresh_answer",
    )

    run_clicked = st.button(
        "Run Query",
        type="primary",
        use_container_width=True,
    ) or bool(pending_question)

    # "🔍 Find out why" under an answer investigates that answer.
    why_question = st.session_state.pop("investigate_question", None)
    if why_question and connection_id:
        run_investigation_request(why_question)

    if run_clicked and connection_id and question.strip() and wants_investigation(query_mode, question.strip()):
        handled = run_investigation_request(question.strip(), fall_back=query_mode == "Auto")
        run_clicked = handled is False

    if run_clicked:

        current_thread_id = st.session_state.get(
            "thread_id"
        )

        if not connection_id:

            st.error(
                "Pipeline not connected successfully: "
                "connection_id missing, cannot POST /query. "
                "Reconnect PostgreSQL first."
            )

        elif not current_thread_id:

            st.error(
                "Cannot POST /query: thread_id missing "
                "in session state."
            )

        elif not question.strip():

            st.warning(
                "Please enter a question."
            )

        else:

            # Exact JSON fields backend/main.py QueryRequest
            # accepts: connection_id, question, thread_id.
            payload = {
                "connection_id": connection_id,
                "question": question.strip(),
                "thread_id": current_thread_id,
                "use_cache": use_cache,
            }

            try:

                with st.spinner(
                    "Running Text-to-SQL agent via backend..."
                ):

                    response = api.post(
                        f"{BACKEND_URL}/query",
                        json=payload,
                        headers=ai_headers(),
                        timeout=600,
                    )

                if is_missing_backend_session(
                    response
                ):

                    # Backend restarted: reconnect once and retry.
                    with st.spinner(
                        "Backend restarted; reconnecting to the database..."
                    ):

                        new_connection_id = reconnect_backend_session()

                    if new_connection_id:

                        payload[
                            "connection_id"
                        ] = new_connection_id

                        with st.spinner(
                            "Running Text-to-SQL agent via backend..."
                        ):

                            response = api.post(
                                f"{BACKEND_URL}/query",
                                json=payload,
                                headers=ai_headers(),
                                timeout=600,
                            )

                if response.status_code != 200:

                    show_backend_error(
                        response
                    )

                else:

                    result = response.json()

                    # -------------------------------------
                    # Update chat history
                    # -------------------------------------

                    st.session_state[
                        "chat_messages"
                    ] = result.get(
                        "conversation_history",
                        st.session_state.get(
                            "chat_messages",
                            [],
                        ),
                    )

                    # -------------------------------------
                    # Update thread metadata
                    # -------------------------------------

                    update_thread_metadata(
                        thread_id=current_thread_id,
                        question=question,
                    )

                    # -------------------------------------
                    # Persist result so st.rerun() does not
                    # wipe the pipeline display (this looked
                    # like "nothing happens").
                    # -------------------------------------

                    st.session_state[
                        "last_query_result"
                    ] = result

                    st.session_state.pop("last_investigation", None)

                    st.session_state[
                        "clear_current_question"
                    ] = True

                    # -------------------------------------
                    # Display pipeline
                    # -------------------------------------

                    display_pipeline(
                        result
                    )

                    st.rerun()

            except requests.exceptions.ConnectionError:

                st.error(
                    "Backend is not running at "
                    f"{BACKEND_URL}. "
                    "Start FastAPI first, then click Run again."
                )

            except requests.exceptions.Timeout:

                st.error(
                    "POST /query timed out. "
                    "Check the backend terminal."
                )

            except Exception as error:

                st.error(
                    f"Text-to-SQL error: {error}"
                )

                st.code(
                    traceback.format_exc()
                )

    # Persist last pipeline across reruns so the result
    # remains visible after st.rerun().
    if st.session_state.get("last_investigation"):

        render_investigation(st, st.session_state["last_investigation"], ask=ask_follow_up)

    elif st.session_state.get(
        "last_query_result"
    ):

        display_pipeline(
            st.session_state[
                "last_query_result"
            ]
        )