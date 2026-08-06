import streamlit as st
from auth import verify_login, login, is_logged_in, current_user

pg = st.navigation([
    st.Page("pages/home.py",           title="Home"),
    st.Page("pages/admin.py",          title="Admin"),
    st.Page("pages/user.py",           title="User"),
    st.Page("pages/about.py",          title="About Us"),
    st.Page("pages/methodology.py",    title="Methodology"),
])
pg.run()
