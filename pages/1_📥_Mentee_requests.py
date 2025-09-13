import streamlit as st
import sqlite3
from agents.mentor_agent import _tool_approve_session

# 🚨 Block page if no login
if "user" not in st.session_state or not st.session_state.user:
    st.warning("⚠️ Please login from the Homepage first.")
    st.stop()

st.title("Mentee Requests")

DB_PATH = "mentormatch.db"

def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def get_pending_requests(mentor_id: int):
    con = _conn()
    rows = con.execute(
        "SELECT * FROM sessions WHERE mentor_id=? AND status='requested' ORDER BY created_at DESC",
        (mentor_id,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

# ------------------- Mentee Requests Page -------------------

user = st.session_state.get("user")
if not user:
    st.warning("Please log in from Homepage first.")
    st.stop()

if user["is_mentor"]:
    st.subheader("Your Pending Requests")
    requests = get_pending_requests(user["ID"])

    if not requests:
        st.info("No pending requests.")
    else:
        for r in requests:
            with st.form(key=f"form_{r['id']}"):
                st.write(f"📧 {r['mentee_email']} | 🕒 {r['start_utc']} → {r['end_utc']}")
                col1, col2 = st.columns(2)

                with col1:
                    approve = st.form_submit_button("✅ Approve")
                with col2:
                    reject = st.form_submit_button("❌ Reject")

                if approve:
                    input_str = f"{r['id']}|{user['email']}"
                    resp = _tool_approve_session(input_str)
                    st.success(f"Approved!\n\n{resp}")

                if reject:
                    con = _conn()
                    con.execute("UPDATE sessions SET status='cancelled' WHERE id=?", (r["id"],))
                    con.commit()
                    con.close()
                    st.warning("Request rejected.")
