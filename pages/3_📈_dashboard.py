import streamlit as st
from utils import notifications_panel

# Inside the page (after login check)
if st.session_state.user:
    notifications_panel(st.session_state.user)



# 🚨 Block page if no login
if "user" not in st.session_state or not st.session_state.user:
    st.warning("⚠️ Please login from the Homepage first.")
    st.stop()

    
st.title("DASHBOARD")