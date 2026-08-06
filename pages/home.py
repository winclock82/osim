import streamlit as st
from auth import verify_login, login, is_logged_in, current_user, logout

st.title("OSIM App")

if is_logged_in():
    st.success(f"Logged in as **{current_user()}**. Use the sidebar to navigate.")
    if st.button("Logout"):
        logout()
        st.rerun()
else:
    st.subheader("Login")
    user_id  = st.text_input("User ID")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if not user_id or not password:
            st.warning("Please enter both User ID and password.")
        elif verify_login(user_id, password):
            login(user_id)
            if user_id.lower().startswith("admin"):
                st.switch_page("pages/admin.py")
            elif user_id.lower().startswith("user"):
                st.switch_page("pages/user.py")
        else:
            st.error("Invalid User ID or password.")

st.divider()
st.caption(
    "⚠️ **IMPORTANT NOTICE:** This web application is a proof-of-concept prototype for the "
    "Operational System Information Manager (OSIM). The job information and Q&A responses "
    "provided here are NOT intended for production use and should not be relied upon for "
    "operational decisions or incident management.\n\n"
    "The AI assistant may generate inaccurate or incomplete information about job schedules, "
    "frequencies, or system details. You assume full responsibility for how you use any generated output.\n\n"
    "Always verify critical job information against official system documentation and consult "
    "with your operations team for accurate and authoritative guidance."
)
