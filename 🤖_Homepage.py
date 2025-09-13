import streamlit as st
import sqlite3
from langchain.agents import AgentExecutor
from langchain_core.messages import HumanMessage, AIMessage
from langchain.memory import ConversationBufferMemory
from agents.mentor_agent import functions_agent, tools, _tool_create_session_request
from utils import notifications_panel
import json
from datetime import datetime

# --- Import onboarding chatbot ---
from onboarding_chatbot import query_gemini

st.set_page_config(
    page_title="SAP360 Hub",
    page_icon="👋",
)

st.title("Welcome to SAP360!")

# --- Initialize session state keys (only once, at the very top) ---
for key, default in {
    "user": None,
    "all_messages": {},
    "memories": {},
    "last_mentors": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

DB_PATH = "mentormatch.db"

# ---------------- DB Helpers ----------------
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def get_user_by_email(email: str):
    con = _conn()
    row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    con.close()
    return dict(row) if row else None

def ensure_tables():
    con = _conn()
    con.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            role TEXT NOT NULL,     -- "user" or "assistant"
            message TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit(); con.close()

ensure_tables()

def save_message(user_email, role, message):
    con = _conn()
    con.execute(
        "INSERT INTO chat_history (user_email, role, message) VALUES (?,?,?)",
        (user_email, role, message),
    )
    con.commit(); con.close()

def load_chat_history(user_email):
    con = _conn()
    rows = con.execute(
        "SELECT role, message FROM chat_history WHERE user_email=? ORDER BY created_at",
        (user_email,),
    ).fetchall()
    con.close()
    return [(r["role"], r["message"]) for r in rows]

# --- NEW: fetch bookings as a mentee ---
def get_bookings_as_mentee(user_email):
    con = _conn()
    rows = con.execute("""
        SELECT s.id, s.start_utc, s.end_utc, s.status, s.location,
               u.name as mentor_name, u.email as mentor_email
        FROM sessions s
        JOIN users u ON u.id = s.mentor_id
        WHERE s.mentee_email=?
        ORDER BY s.start_utc DESC
    """, (user_email,)).fetchall()
    con.close()
    return [dict(r) for r in rows]

# ---------------- Session State ----------------
if "user" not in st.session_state:
    st.session_state.user = None
if "all_messages" not in st.session_state:
    st.session_state.all_messages = {}   # per-user chat store
if "memories" not in st.session_state:
    st.session_state.memories = {}
if "last_mentors" not in st.session_state:
    st.session_state.last_mentors = None

# ---------------- Login ----------------
if not st.session_state.user:
    st.subheader("Login")
    email = st.text_input("Enter your email")
    if st.button("Login"):
        user = get_user_by_email(email)
        if user:
            st.session_state.user = user
            st.success(f"Welcome {user['name']}!")

            user_email = user["email"]

            # --- Load chat history from DB ---
            past = load_chat_history(user_email)
            st.session_state.all_messages[user_email] = []
            for role, msg in past:
                if role == "user":
                    st.session_state.all_messages[user_email].append(HumanMessage(msg))
                else:
                    st.session_state.all_messages[user_email].append(AIMessage(msg))

            # --- Init memory ---
            if user_email not in st.session_state.memories:
                st.session_state.memories[user_email] = ConversationBufferMemory(
                    memory_key="chat_history",
                    return_messages=True,
                )

            st.session_state.user_agent = AgentExecutor(
                agent=functions_agent,
                tools=tools,
                memory=st.session_state.memories[user_email],
                verbose=True,
            )
            st.rerun()
        else:
            st.error("User not found.")

# ---------------- Main App (after login) ----------------
else:
    user_email = st.session_state.user["email"]

    # Sidebar notifications
    notifications_panel(st.session_state.user)

    # Chatbot
    st.subheader(f"Hi {st.session_state.user['name']}, how can I help you today?")

    # --- Clear chat button (UI only) ---
    if st.sidebar.button("🗑️ Clear Chat"):
        user_email = st.session_state.user["email"]
        st.session_state.all_messages[user_email] = []
        st.session_state.memories[user_email].clear()
        st.sidebar.success("Chat cleared from screen (history still saved).")
        st.rerun()

    # Show chat history
    for message in st.session_state.all_messages.get(user_email, []):
        role = "user" if isinstance(message, HumanMessage) else "assistant"
        with st.chat_message(role):
            st.markdown(message.content)

    # Input
    prompt = st.chat_input("Ask me anything (mentorship, learning, onboarding)...")

    if prompt:
        # --- User message ---
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.all_messages[user_email].append(HumanMessage(prompt))
        save_message(user_email, "user", prompt)

        # clear mentors from old query
        st.session_state.last_mentors = None  

        # --- Special case: show mentee bookings ---
        if "my bookings" in prompt.lower() or "past bookings" in prompt.lower():
            bookings = get_bookings_as_mentee(user_email)
            if not bookings:
                final_response = "📭 You have no bookings yet."
            else:
                lines = ["📅 Here are your bookings:"]
                for b in bookings:
                    lines.append(
                        f"- With **{b['mentor_name']}** ({b['mentor_email']}) "
                        f"on {b['start_utc']} → {b['end_utc']} "
                        f"at {b['location']} (Status: {b['status']})"
                    )
                final_response = "\n".join(lines)

            with st.chat_message("assistant"):
                st.markdown(final_response)

            st.session_state.all_messages[user_email].append(AIMessage(final_response))
            save_message(user_email, "assistant", final_response)

        else:
            # --- Routing logic for onboarding vs mentor agent ---
            chat_history = []
            for message in st.session_state.all_messages[user_email]:
                if isinstance(message, HumanMessage):
                    chat_history.append(f"You: {message.content}")
                else:
                    chat_history.append(f"Bot: {message.content}")

            onboarding_response = query_gemini(prompt, chat_history=chat_history)
            modified_prompt = f"(User email: {user_email}) {prompt}"
            result = st.session_state.user_agent.invoke({"input": modified_prompt})
            mentor_response = result["output"]

            try:
                mentors = eval(mentor_response)
            except Exception:
                mentors = None

            fallback_phrases = [
                "I am sorry", "I cannot answer", "I don't know", "not able to", "cannot help"
            ]
            def is_fallback(resp):
                return any(phrase in resp.lower() for phrase in fallback_phrases)

            if mentors and isinstance(mentors, list) and all("name" in m for m in mentors):
                final_response = "Here are some mentors you can choose 👇"
                st.session_state.last_mentors = mentors
            elif not is_fallback(onboarding_response) and onboarding_response.strip() != "":
                final_response = onboarding_response
            else:
                final_response = mentor_response

            with st.chat_message("assistant"):
                st.markdown(final_response)

            st.session_state.all_messages[user_email].append(
                AIMessage(final_response if not mentors else "Mentor options displayed.")
            )
            save_message(user_email, "assistant", final_response)

    # --- Render cached mentors (persist across reruns) ---
    if st.session_state.last_mentors:
        for mentor in st.session_state.last_mentors:
            with st.expander(f"👤 {mentor['name']} – {mentor['position']}"):
                st.caption(f"📍 {mentor['department']} • {mentor['team']}")
                st.markdown(f"**Skills:** {mentor['skills']}")
                st.markdown(f"**Experience:** {mentor['months_experience']} months")

                # Form ensures single click
                with st.form(key=f"form_{mentor['id']}"):
                    slot = st.selectbox(
                        f"Available slots for {mentor['name']}",
                        mentor.get("availability", []),
                        key=f"slot_{mentor['id']}"
                    )
                    location = st.selectbox(
                        "Choose location",
                        ["Level 1 canteen", "Meeting room 3", "Google Meet", "Reception Area", "Pantry Lounge"],
                        key=f"loc_{mentor['id']}"
                    )
                    confirm = st.form_submit_button("✅ Confirm Request")

                    if confirm:
                        if slot:
                            input_str = (
                                f"{user_email}|{mentor['email']}|{mentor['id']}|"
                                f"{slot.split(' → ')[0]}|{slot.split(' → ')[1]}|{location}"
                            )
                            resp = _tool_create_session_request(input_str)

                            st.success(f"Requested {mentor['name']} at {slot} via {location}\n\n{resp}")

                            # log assistant message
                            st.session_state.all_messages[user_email].append(
                                AIMessage(f"✅ Booking request sent to {mentor['name']} for {slot} ({location}).")
                            )
                            save_message(user_email, "assistant",
                                         f"Booking request sent to {mentor['name']} for {slot} ({location}).")

                            st.session_state.last_mentors = None
                            st.rerun()
                        else:
                            st.warning("Please select a slot first.")

