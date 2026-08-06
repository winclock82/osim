import bcrypt
import streamlit as st

def verify_login(user_id: str, password: str) -> bool:
    users = st.secrets.get("passwords", {})
    key = user_id.lower()
    if key not in users:
        return False
    return bcrypt.checkpw(password.encode(), users[key].encode())

def login(user_id: str):
    st.session_state["user_id"] = user_id

def logout():
    st.session_state.clear()

def is_logged_in() -> bool:
    return "user_id" in st.session_state

def current_user() -> str:
    return st.session_state.get("user_id", "")
