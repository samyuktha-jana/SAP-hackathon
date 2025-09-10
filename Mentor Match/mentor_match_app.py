import streamlit as st
import sqlite3
from langchain_core.messages import HumanMessage, AIMessage
from agent import agent, _tool_create_session_request, _tool_approve_session  # reuse backend functions

DB_PATH = "mentormatch.db"

# -------------------------
# DB helpers
# -------------------------
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def get_user_by_email(email: str):
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    con.close()
    return dict(row) if row else None

def get_pending_requests(mentor_id: int):
    con = _conn()
    cur = con.cursor()
    cur.execute("""
        SELECT s.id, s.mentee_email, s.start_utc, s.end_utc, s.status
        FROM sessions s
        WHERE s.mentor_id=? AND s.status='requested'
        ORDER BY s.created_at DESC
    """, (mentor_id,))
    rows = cur.fetchall()
    con.close()
    return [dict(r) for r in rows]

# -------------------------
# Mentee Dashboard
# -------------------------
def mentee_dashboard(user):
    st.subheader(f"Welcome, {user['name']} (Mentee)")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        if isinstance(message, HumanMessage):
            with st.chat_message("user"):
                st.markdown(message.content)
        elif isinstance(message, AIMessage):
            with st.chat_message("assistant"):
                st.markdown(message.content)

    prompt = st.chat_input("Ask for a mentor...")

    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
            st.session_state.messages.append(HumanMessage(prompt))

        result = agent.invoke({"input": prompt})
        response = result["output"]

        try:
            mentors = eval(response)  # backend returns list-like str
        except Exception:
            mentors = None

        with st.chat_message("assistant"):
            if isinstance(mentors, list) and all("name" in m for m in mentors):
                st.markdown("Here are some mentors you can choose:")
                cols = st.columns(len(mentors))

                for i, mentor in enumerate(mentors):
                    with cols[i]:
                        st.subheader(mentor["name"])
                        st.caption(f"{mentor['position']} • {mentor['department']}")
                        st.markdown(f"**Skills:** {mentor['skills']}")
                        st.markdown(f"**Experience:** {mentor['months_experience']} months")

                        for slot in mentor.get("availability", []):
                            if st.button(f"📅 {slot}", key=f"{mentor['id']}_{slot}"):
                                # request session in DB
                                input_str = f"{user['email']}|{mentor['id']}|{slot.split(' → ')[0]}|{slot.split(' → ')[1]}|Teams"
                                resp = _tool_create_session_request(input_str)
                                st.success(f"Requested {mentor['name']} at {slot}\n\n{resp}")
            else:
                st.markdown(response)

            st.session_state.messages.append(AIMessage(response))

# -------------------------
# Mentor Dashboard
# -------------------------
def mentor_dashboard(user):
    st.subheader(f"Welcome, {user['name']} (Mentor)")
    st.markdown("Here are your pending session requests:")

    requests = get_pending_requests(user["ID"])
    if not requests:
        st.info("No pending requests right now.")
    else:
        for r in requests:
            with st.container():
                st.write(f"📧 Mentee: {r['mentee_email']}")
                st.write(f"🕒 {r['start_utc']} → {r['end_utc']}")
                st.write(f"Status: {r['status']}")

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ Approve", key=f"approve_{r['id']}"):
                        input_str = f"{r['id']}|{user['email']}"
                        resp = _tool_approve_session(input_str)
                        st.success(f"Approved! Invite sent.\n\n{resp}")
                with col2:
                    if st.button("❌ Reject", key=f"reject_{r['id']}"):
                        # Simple reject (DB update only)
                        con = _conn()
                        cur = con.cursor()
                        cur.execute("UPDATE sessions SET status='cancelled' WHERE id=?", (r['id'],))
                        con.commit(); con.close()
                        st.warning("Request rejected.")

# -------------------------
# Main App
# -------------------------
st.title("MentorMatch Platform")

if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    st.subheader("Login")
    email = st.text_input("Enter your email")
    if st.button("Login"):
        user = get_user_by_email(email)
        if user:
            st.session_state.user = user
            st.success(f"Welcome {user['name']}!")
        else:
            st.error("User not found. Please try again.")
else:
    if st.session_state.user["is_mentor"]:
        mentor_dashboard(st.session_state.user)
    else:
        mentee_dashboard(st.session_state.user)
