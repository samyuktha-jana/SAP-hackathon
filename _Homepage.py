import streamlit as st
import sqlite3
from langchain.agents import AgentExecutor
from langchain_core.messages import HumanMessage, AIMessage
from langchain.memory import ConversationBufferMemory
from agents.mentor_agent import functions_agent, tools, _tool_create_session_request
from utils import notifications_panel
import json
from datetime import datetime,timezone
import re
import pandas as pd
import os


# --- Import onboarding chatbot ---
from agents.onboarding_chatbot import query_gemini

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

# ---------------------- Ticket intent detect --------------------

TICKETS_CSV = os.getenv("TICKETS_CSV", "tickets.csv")
TICKET_ROLE_COL = "role"
TICKET_KEYWORDS = [
    "ticket", "ticket id", "helpdesk", "service desk",
    "hr help", "hr ticket", "it ticket", "my ticket",
    "mytickets", "support ticket", "raise a ticket", "create ticket"]


def detect_ticket_intent(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    if any(k in t for k in TICKET_KEYWORDS):
        return True
    # SR1234 / INC-9999 / or bare 4+ digits
    return bool(re.search(r"(?:INC|SR|TCK|REQ|CASE|IT|HR)[-_]?\d{3,}|\b\d{4,}\b", text, flags=re.I))

def extract_ticket_id(text: str):
    m = re.search(r"(?:INC|SR|TCK|REQ|CASE|IT|HR)[-_]?(\d{3,})", text, flags=re.I) or re.search(r"\b(\d{4,})\b", text)
    return int(m.group(1)) if m else None

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

# ------------------------ SQLite Tickets (sidebar) --------------
def ensure_ticket_tables():
    con = _conn()
    con.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',   -- open | in_progress | resolved | closed
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit(); con.close()

ensure_ticket_tables()

def get_tickets(user_email: str):
    con = _conn()
    rows = con.execute(
        "SELECT id, title, status, created_at, updated_at FROM tickets WHERE user_email=? ORDER BY created_at DESC",
        (user_email,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

def get_ticket_counts(user_email: str):
    con = _conn()
    rows = con.execute("""
        SELECT status, COUNT(*) AS cnt
        FROM tickets
        WHERE user_email=?
        GROUP BY status
    """, (user_email,)).fetchall()
    con.close()
    counts = {r["status"]: r["cnt"] for r in rows}
    counts.setdefault("open", 0)
    counts.setdefault("in_progress", 0)
    counts.setdefault("resolved", 0)
    counts.setdefault("closed", 0)
    return counts

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

# ------------------------- Navigation helper --------------------
def open_mytickets_page(focus_id: int | None = None, from_chat: bool = True):
    """Navigate to MyTickets page, passing context via query params."""
    try:
        qp = dict(st.query_params)
        if focus_id:
            qp["focus"] = str(focus_id)
        if from_chat:
            qp["from"] = "chat"
        st.query_params.update(qp)
    except Exception:
        pass
    try:
        st.switch_page("pages/4_MyTickets.py")
    except Exception:
        st.markdown("➡️ [Open MyTickets](pages/4_MyTickets.py)")
        st.stop()

# ------------------------- CSV helpers (NEW) --------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _ensure_tickets_csv():
    if not os.path.exists(TICKETS_CSV):
        pd.DataFrame(columns=[
            "id","title","description","status","priority","category_key",
            "requester_email","assignee_email","created_at","updated_at", TICKET_ROLE_COL
        ]).to_csv(TICKETS_CSV, index=False)

def _load_tickets_csv() -> pd.DataFrame:
    _ensure_tickets_csv()
    try:
        df = pd.read_csv(TICKETS_CSV)
    except Exception:
        df = pd.DataFrame(columns=[
            "id","title","description","status","priority","category_key",
            "requester_email","assignee_email","created_at","updated_at", TICKET_ROLE_COL
        ])
    if "id" in df.columns:
        df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    if TICKET_ROLE_COL not in df.columns:
        df[TICKET_ROLE_COL] = ""
    return df

def _save_tickets_csv(df: pd.DataFrame):
    tmp = TICKETS_CSV + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, TICKETS_CSV)

def _next_ticket_id(df: pd.DataFrame) -> int:
    return (int(df["id"].max()) + 1) if (not df.empty and "id" in df.columns) else 1

def create_ticket_via_chat(*, requester_email: str, title: str, description: str,
                           category_key: str, priority: str, assignee_email: str | None,
                           requester_role: str | None) -> int:
    df = _load_tickets_csv()
    new_id = _next_ticket_id(df)
    row = {
        "id": new_id,
        "title": title.strip(),
        "description": description.strip(),
        "status": "NEW",
        "priority": priority.strip().upper(),
        "category_key": category_key.strip().lower(),
        "requester_email": requester_email,
        "assignee_email": (assignee_email or "").strip(),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        TICKET_ROLE_COL: (requester_role or "EMPLOYEE").upper()
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _save_tickets_csv(df)
    return new_id

# ------------------------- Intake flow (NEW) --------------------
def start_ticket_intake():
    st.session_state.ticket_intake_active = True
    st.session_state.ticket_stage = "category"
    st.session_state.ticket_data = {
        "category_key": None,
        "priority": None,
        "title": None,
        "description": None,
        "assignee_email": None,
    }
    with st.chat_message("assistant"):
        st.markdown(
            "Got it — let's create a ticket.\n\n"
            "1) **Which category does this issue fall under?** (e.g., **IT**, **HR**, **Operations**)"
        )

def handle_ticket_intake_input(user_text: str, requester_email: str, requester_role: str):
    data = st.session_state.ticket_data
    stage = st.session_state.ticket_stage
    text = (user_text or "").strip()

    def ask(msg: str):
        with st.chat_message("assistant"):
            st.markdown(msg)

    # Allow cancel at any time
    if text.lower() in {"cancel", "stop", "quit"}:
        st.session_state.ticket_intake_active = False
        st.session_state.ticket_stage = None
        st.session_state.ticket_data = {}
        ask("Okay, cancelled the ticket creation.")
        return

    if stage == "category":
       data["category_key"] = text.strip().lower() if text else "it"
       st.session_state.ticket_stage = "priority"
       ask(
        "2) **What priority should we use?**\n\n"
        "- **P1 (Critical):** Major outage/security issue; blocks many people.\n"
        "- **P2 (High):** Severely impacts your work; no reasonable workaround.\n"
        "- **P3 (Normal):** Affects productivity; workaround exists. *(default)*\n"
        "- **P4 (Low):** Minor issue or general request/enhancement.\n\n"
        "Type `P1`, `P2`, `P3`, or `P4` (press Enter to accept **P3**)."
       )
       return


    if stage == "priority":
        p = text.upper().replace(" ", "")
        if p not in {"P1","P2","P3","P4",""}:
            ask("Please enter one of `P1`, `P2`, `P3`, `P4` (or leave blank for `P3`).")
            return
        data["priority"] = p if p else "P3"
        st.session_state.ticket_stage = "title"
        ask("3) **Title**? (short summary)")
        return

    if stage == "title":
        if len(text) < 3:
            ask("Title looks too short — give me a brief summary (≥ 3 chars).")
            return
        data["title"] = text
        st.session_state.ticket_stage = "description"
        ask("4) **Description**? (steps to reproduce, expected vs actual, errors)")
        return

    if stage == "description":
        if len(text) < 5:
            ask("Please add a bit more detail (≥ 5 chars).")
            return
        data["description"] = text
        st.session_state.ticket_stage = "assignee"
        ask("5) **Assignee email** (optional) — or type `skip`.")
        return

    if stage == "assignee":
        data["assignee_email"] = "" if text.lower() in {"", "skip", "none"} else text
        # Create ticket now
        tid = create_ticket_via_chat(
            requester_email=requester_email,
            title=data["title"],
            description=data["description"],
            category_key=data["category_key"],
            priority=data["priority"],
            assignee_email=data["assignee_email"],
            requester_role=requester_role,
        )
        st.session_state.ticket_intake_active = False
        st.session_state.ticket_stage = None
        st.session_state.ticket_data = {}

        with st.chat_message("assistant"):
            st.markdown(f"✅ Ticket **#{tid}** created. Opening **MyTickets** so you can review/edit.")
        # Jump to the MyTickets page focused on this ticket
        open_mytickets_page(focus_id=tid, from_chat=True)
        st.stop()

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
if "ticket_intake_active" not in st.session_state:
    st.session_state.ticket_intake_active = False
if "ticket_stage" not in st.session_state:
    st.session_state.ticket_stage = None
if "ticket_data" not in st.session_state:
    st.session_state.ticket_data = {}

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

        # --- Ticket flow: YES/NO confirm (no inline ticket UI) ---
        # If we were already waiting for a YES/NO and the user typed it:
        if st.session_state.get("pending_ticket_open"):
            want = prompt.strip().lower()
            if want in {"yes", "y", "yeah", "yep", "open", "ok", "okay", "sure"}:
                fid = st.session_state.get("focus_ticket_id")
                st.session_state["pending_ticket_open"] = False
                st.session_state["focus_ticket_id"] = None
                open_mytickets_page(focus_id=fid, from_chat=True)
                st.stop()
            elif want in {"no", "n", "nope", "not now", "cancel"}:
                st.session_state["pending_ticket_open"] = False
                st.session_state["focus_ticket_id"] = None
                with st.chat_message("assistant"):
                    st.markdown("Okay, I’ll stay here.")

        # Fresh detection on this message:
        if detect_ticket_intent(prompt):
            st.session_state["pending_ticket_open"] = True
            st.session_state["focus_ticket_id"] = extract_ticket_id(prompt)

            with st.chat_message("assistant"):
                fid = st.session_state["focus_ticket_id"]
                if fid:
                    st.markdown(
                        f"I noticed ticket **#{fid}**. Open your **MyTickets** page?"
                    )
                else:
                    st.markdown(
                        "You mentioned tickets/helpdesk. Open your **MyTickets** page?"
                    )
            # NOTE: Do not call agents on this turn—let confirm buttons render.

        else:        

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

            # --- Learning module completion auto-update ---
            # Look for phrases like "I have completed <module>"
            match = re.search(r"i have completed (.+)", prompt.strip(), re.IGNORECASE)
            if match:
                completed_module = match.group(1).strip().rstrip(".")
                # Path to progress CSV (must match dashboard)
                BASE_DIR = os.path.dirname(os.path.abspath(__file__))
                progress_path = os.path.join(BASE_DIR, "LearningProgress.csv")
                # Load or create progress CSV
                if os.path.exists(progress_path):
                    progress_df = pd.read_csv(progress_path)
                else:
                    progress_df = pd.DataFrame(columns=["email", "module", "completed"])
                # Normalize
                progress_df["email"] = progress_df["email"].astype(str).str.strip().str.lower()
                progress_df["module"] = progress_df["module"].astype(str).str.strip()
                # Update or add the completed module for this user
                mask = (progress_df["email"] == user_email) & (progress_df["module"].str.lower() == completed_module.lower())
                if mask.any():
                    progress_df.loc[mask, "completed"] = True
                else:
                    progress_df = pd.concat([
                        progress_df,
                        pd.DataFrame([{"email": user_email, "module": completed_module, "completed": True}])
                    ], ignore_index=True)
                progress_df.to_csv(progress_path, index=False) 

    # --- Confirm buttons (render even when there's no new input) ---
    if st.session_state.get("pending_ticket_open"):
        c1, c2 = st.columns(2)
        if c1.button("✅ Yes, open MyTickets"):
            fid = st.session_state.get("focus_ticket_id")
            st.session_state["pending_ticket_open"] = False
            st.session_state["focus_ticket_id"] = None
            open_mytickets_page(focus_id=fid, from_chat=True)
            st.stop()
        if c2.button("❌ No, stay here"):
            st.session_state["pending_ticket_open"] = False
            st.session_state["focus_ticket_id"] = None
            st.rerun()         

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

