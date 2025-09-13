import streamlit as st

# 🚨 Block page if no login
if "user" not in st.session_state or not st.session_state.user:
    st.warning("⚠️ Please login from the Homepage first.")
    st.stop()

    
st.title("Learning Hub")