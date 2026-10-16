"""Session-based admin authentication for the public Streamlit dashboard."""
from __future__ import annotations

import hmac
import os

import streamlit as st


ADMIN_AUTHENTICATED_KEY = "admin_authenticated"
ADMIN_USERNAME_KEY = "admin_username"


def is_admin() -> bool:
    return bool(st.session_state.get(ADMIN_AUTHENTICATED_KEY, False))


def login(username: str, password: str) -> bool:
    """Authenticate against credentials supplied through server environment."""
    expected_username = os.environ.get("ADMIN_USERNAME", "")
    expected_password = os.environ.get("ADMIN_PASSWORD", "")
    if not expected_username or not expected_password:
        raise RuntimeError("Chưa cấu hình ADMIN_USERNAME và ADMIN_PASSWORD trong .env")
    valid = hmac.compare_digest(username.encode("utf-8"), expected_username.encode("utf-8")) and (
        hmac.compare_digest(password.encode("utf-8"), expected_password.encode("utf-8"))
    )
    if valid:
        st.session_state[ADMIN_AUTHENTICATED_KEY] = True
        st.session_state[ADMIN_USERNAME_KEY] = expected_username
    return valid


def logout() -> None:
    st.session_state.pop(ADMIN_AUTHENTICATED_KEY, None)
    st.session_state.pop(ADMIN_USERNAME_KEY, None)


def require_admin() -> None:
    if not is_admin():
        raise PermissionError("Thao tác này chỉ dành cho admin đã đăng nhập")


def render_admin_login() -> None:
    if is_admin():
        st.success(f"Đã đăng nhập admin: {st.session_state.get(ADMIN_USERNAME_KEY, '')}")
        if st.button("Đăng xuất admin", key="admin_logout"):
            logout()
            st.rerun()
        return

    st.subheader("Đăng nhập admin")
    with st.form("admin_login_form"):
        username = st.text_input("Tài khoản", autocomplete="username")
        password = st.text_input("Mật khẩu", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Đăng nhập")

    if submitted:
        try:
            authenticated = login(username, password)
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            if authenticated:
                st.success("Đăng nhập thành công.")
                st.rerun()
            st.error("Tên đăng nhập hoặc mật khẩu không đúng.")
