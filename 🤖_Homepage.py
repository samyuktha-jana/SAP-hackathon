import streamlit as st
import sqlite3
from langchain.agents import AgentExecutor
from langchain_core.messages import HumanMessage, AIMessage
from langchain.memory import ConversationBufferMemory
from agents.mentor_agent import functions_agent, tools, _tool_create_session_request
from utils import notifications_panel   # shared sidebar
import json

st.set_page_config(
    page_title="SAP360 Hub",
    page_icon="👋",
)

st.title("Welcome to SAP360!")

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
        else:
            st.error("User not found.")

# ---------------- Main App (after login) ----------------
else:
    user_email = st.session_state.user["email"]

    # Sidebar notifications
    notifications_panel(st.session_state.user)

    if st.sidebar.button("🚪 Logout"):
        st.session_state.clear()
        st.experimental_rerun()

    # Chatbot
    st.subheader(f"Hi {st.session_state.user['name']}, how can I help you today?")

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

        # --- Agent response ---
        modified_prompt = f"(User email: {user_email}) {prompt}"
        result = st.session_state.user_agent.invoke({"input": modified_prompt})
        response = result["output"]

        try:
            mentors = eval(response)  # Expecting list[dict]
        except Exception:
            mentors = None

        with st.chat_message("assistant"):
            if isinstance(mentors, list) and all("name" in m for m in mentors):
                st.markdown("Here are some mentors you can choose 👇")
                st.session_state.last_mentors = mentors
            else:
                st.markdown(response)

        st.session_state.all_messages[user_email].append(
            AIMessage(response if not mentors else "Mentor options displayed.")
        )
        save_message(user_email, "assistant", response)

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
                            input_str = f"{user_email}|{mentor['id']}|{slot.split(' → ')[0]}|{slot.split(' → ')[1]}|{location}"
                            resp = _tool_create_session_request(input_str)
                            st.success(f"Requested {mentor['name']} at {slot} via {location}\n\n{resp}")

                            # log assistant message
                            st.session_state.all_messages[user_email].append(
                                AIMessage(f"✅ Booking request sent to {mentor['name']} for {slot} ({location}).")
                            )
                            save_message(user_email, "assistant",
                                         f"Booking request sent to {mentor['name']} for {slot} ({location}).")

                            # clear mentors + rerun
                            st.session_state.last_mentors = None
                            st.rerun()
                        else:
                            st.warning("Please select a slot first.")
