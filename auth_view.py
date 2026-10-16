# Sign-in screen, account box in the sidebar, and the HTTP helper that
# sends the session token with every backend call.
#
# The token lives only in this browser tab's Streamlit session: closing
# or refreshing the tab means signing in again.

import hashlib
from typing import Any, Dict, Optional

import requests

TOKEN_KEY = "auth_token"
USER_KEY = "auth_user"
REQUIRED_KEY = "auth_required"
NOTICE_KEY = "auth_notice"


def auth_headers(st) -> Dict[str, str]:
    token = st.session_state.get(TOKEN_KEY)
    return {"Authorization": f"Bearer {token}"} if token else {}


def signed_in_user(st) -> Optional[Dict[str, Any]]:
    return st.session_state.get(USER_KEY)


def user_storage_key(user: Optional[Dict[str, Any]]) -> str:
    """A file-name-safe key per account (used for the chat list)."""
    return hashlib.sha256(user["email"].encode("utf-8")).hexdigest()[:16] if user else ""


def sign_out(st, notice: str = "") -> None:
    """Forget everything in this tab: token, chats, connection, results."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if notice:
        st.session_state[NOTICE_KEY] = notice


class Api:
    """requests.get/post/delete with the session token added; a 401 signs the tab out."""

    def __init__(self, st):
        self.st = st

    def _send(self, method: str, url: str, **kwargs):
        headers = {**auth_headers(self.st), **(kwargs.pop("headers", None) or {})}
        response = requests.request(method, url, headers=headers, **kwargs)
        if response.status_code == 401 and self.st.session_state.get(TOKEN_KEY):
            # Only the token is dropped here (the page is mid-run); the next
            # run shows the sign-in screen, and signing in clears the rest.
            self.st.session_state.pop(TOKEN_KEY, None)
            self.st.session_state.pop(USER_KEY, None)
            self.st.session_state[NOTICE_KEY] = "Your session ended. Please sign in again."
        return response

    def get(self, url: str, **kwargs):
        return self._send("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self._send("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self._send("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self._send("DELETE", url, **kwargs)


def _detail(response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, list):  # FastAPI validation errors
        return "Please fill in every field."
    return str(detail or response.text or f"Error {response.status_code}")


def _sign_in_requests(st, backend_url: str, path: str, payload: Dict[str, str]) -> None:
    try:
        response = requests.post(f"{backend_url}{path}", json=payload, timeout=30)
    except requests.exceptions.RequestException:
        st.error(f"Cannot reach the backend at {backend_url}. Start FastAPI first.")
        return
    if not response.ok:
        st.error(_detail(response))
        return
    data = response.json()
    required = st.session_state.get(REQUIRED_KEY)
    sign_out(st)  # nothing from an earlier account stays in this tab
    st.session_state[REQUIRED_KEY] = required
    st.session_state[TOKEN_KEY] = data["token"]
    st.session_state[USER_KEY] = data["user"]
    st.rerun()


def require_sign_in(st, backend_url: str) -> Optional[Dict[str, Any]]:
    """
    The signed-in user, or None when the backend has sign-in turned off.
    Otherwise shows the sign-in / create-account screen and stops the page.
    """
    if REQUIRED_KEY not in st.session_state:
        try:
            response = requests.get(f"{backend_url}/auth/status", timeout=10)
            response.raise_for_status()
            st.session_state[REQUIRED_KEY] = bool(response.json().get("required"))
        except requests.exceptions.RequestException:
            st.error(f"Cannot reach the backend at {backend_url}. Start FastAPI first, then refresh this page.")
            st.stop()
    if not st.session_state[REQUIRED_KEY]:
        return None
    if st.session_state.get(TOKEN_KEY) and st.session_state.get(USER_KEY):
        return st.session_state[USER_KEY]

    _, middle, _ = st.columns([1, 1.3, 1])
    with middle:
        st.title("🔍 InsightX")
        st.caption("Ask questions about your business data in plain English.")
        notice = st.session_state.pop(NOTICE_KEY, "")
        if notice:
            st.info(notice)
        sign_in_tab, sign_up_tab = st.tabs(["Sign in", "Create account"])
        with sign_in_tab:
            with st.form("sign_in_form"):
                email = st.text_input("Email", key="sign_in_email")
                password = st.text_input("Password", type="password", key="sign_in_password")
                if st.form_submit_button("Sign in", type="primary", use_container_width=True):
                    _sign_in_requests(st, backend_url, "/auth/login", {"email": email, "password": password})
        with sign_up_tab:
            with st.form("sign_up_form"):
                name = st.text_input("Your name", key="sign_up_name")
                email = st.text_input("Email", key="sign_up_email")
                password = st.text_input("Password", type="password", key="sign_up_password",
                                         help="At least 8 characters, with letters and numbers.")
                repeat = st.text_input("Repeat password", type="password", key="sign_up_repeat")
                if st.form_submit_button("Create account", type="primary", use_container_width=True):
                    if password != repeat:
                        st.error("The two passwords are different.")
                    else:
                        _sign_in_requests(st, backend_url, "/auth/signup",
                                          {"email": email, "name": name, "password": password})
    st.stop()


def render_account_box(st, backend_url: str, api: Api) -> None:
    """Sidebar: who is signed in, today's questions, change password, sign out."""
    user = signed_in_user(st)
    if not user:
        return
    st.sidebar.markdown(f"👤 **{user.get('name') or user['email']}**  \n{user['email']}")
    try:
        me = api.get(f"{backend_url}/auth/me", timeout=10)
        if me.ok:
            data = me.json()
            limit = data.get("daily_limit") or 0
            used = data.get("questions_today", 0)
            st.sidebar.caption(f"Questions today: {used} / {limit}" if limit else f"Questions today: {used}")
        elif not st.session_state.get(TOKEN_KEY):
            st.rerun()  # the session ended: back to the sign-in screen
    except requests.exceptions.RequestException:
        pass

    with st.sidebar.expander("Account"):
        with st.form("change_password_form", clear_on_submit=True):
            current = st.text_input("Current password", type="password")
            new = st.text_input("New password", type="password",
                                help="At least 8 characters, with letters and numbers.")
            if st.form_submit_button("Change password"):
                response = api.post(f"{backend_url}/auth/password",
                                    json={"current_password": current, "new_password": new}, timeout=30)
                if response.ok:
                    st.session_state[TOKEN_KEY] = response.json()["token"]
                    st.success("Password changed. You were signed out on other devices.")
                else:
                    st.error(_detail(response))
        if st.button("Sign out on all devices", use_container_width=True):
            api.post(f"{backend_url}/auth/logout-everywhere", timeout=10)
            sign_out(st, "You were signed out on all devices.")
            st.rerun()
    if st.sidebar.button("Sign out", use_container_width=True):
        sign_out(st, "You signed out.")
        st.rerun()
