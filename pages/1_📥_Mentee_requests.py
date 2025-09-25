import streamlit as st
import sqlite3
from agents.mentor_agent import _tool_approve_session
from utils import notifications_panel
from datetime import datetime, timezone


DB_PATH = "mentormatch.db"

# ------------------- DB Helpers -------------------
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def ensure_feedback_table():
    """Ensure the feedback table exists with correct schema."""
    con = _conn()
    con.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            user_email TEXT,
            role TEXT,
            takeaway TEXT,
            rating INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()

def auto_update_completed_sessions():
    """Flip booked sessions to completed if end_utc < now()."""
    now = datetime.now(timezone.utc)
    con = _conn()
    con.execute("""
        UPDATE sessions
        SET status='completed'
        WHERE status='booked'
          AND end_utc < ?
    """, (now.isoformat(),))
    con.commit()
    con.close()

def get_pending_requests(mentor_id: int):
    con = _conn()
    rows = con.execute(
        "SELECT * FROM sessions WHERE mentor_id=? AND status='requested' ORDER BY created_at DESC",
        (mentor_id,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

def get_completed_sessions(user_email: str):
    """Return completed sessions for this mentee that do NOT yet have a takeaway."""
    con = _conn()
    rows = con.execute(
        """
        SELECT s.*, u.name AS mentee_name
        FROM sessions s
        JOIN users u ON u.email = s.mentee_email
        WHERE s.mentee_email=? 
          AND s.status='completed'
          AND NOT EXISTS (
              SELECT 1 FROM feedback f 
              WHERE f.session_id = s.id AND f.user_email=?
          )
        ORDER BY s.start_utc DESC
        """,
        (user_email, user_email)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

def save_takeaway(session_id: int, user_email: str, takeaway: str, rating: int):
    con = _conn()
    con.execute(
        "INSERT INTO feedback (session_id, user_email, role, takeaway, rating) VALUES (?,?,?,?,?)",
        (session_id, user_email, "mentee", takeaway, rating),
    )
    con.commit()
    con.close()

def get_my_takeaways(user_email: str):
    """Fetch all takeaways by this mentee."""
    con = _conn()
    rows = con.execute(
        """
        SELECT f.*, s.start_utc, s.end_utc, s.mentor_email
        FROM feedback f
        JOIN sessions s ON f.session_id = s.id
        WHERE f.user_email=?
        ORDER BY f.created_at DESC
        """,
        (user_email,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

# ------------------- Page -------------------
if "user" in st.session_state and st.session_state["user"]:
    notifications_panel(st.session_state["user"])

# 🚨 Block page if no login
if "user" not in st.session_state or not st.session_state.user:
    st.warning("⚠️ Please login from the Homepage first.")
    st.stop()

# ✅ Ensure feedback table + update sessions before UI
ensure_feedback_table()
auto_update_completed_sessions()

st.title("Mentee Requests & Session History")

user = st.session_state.get("user")
if not user:
    st.warning("Please log in from Homepage first.")
    st.stop()

# --- Section 1: Pending Requests (for mentors only) ---
if user.get("is_mentor"):
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

# --- Section 2: Completed Sessions (Takeaway Submission) ---
st.subheader("Completed Sessions- takeaway")

completed_sessions = get_completed_sessions(user["email"])

if not completed_sessions:
    st.info("No sessions waiting for takeaway.")
else:
    for s in completed_sessions:
        with st.form(key=f"takeaway_{s['id']}"):
            st.markdown(
                f"**🧑 Mentor:** {s['mentor_email']} | "
                f"**👤 Mentee:** {s['mentee_email']} | "
                f"🕒 {s['start_utc']} → {s['end_utc']}"
            )
            takeaway = st.text_area("📝 Key Takeaway", key=f"takeaway_text_{s['id']}")
            rating = st.slider("⭐ Rating", 1, 5, 5, key=f"takeaway_rating_{s['id']}")
            submit = st.form_submit_button("Submit Takeaway")

            if submit and takeaway.strip():
                save_takeaway(s["id"], user["email"], takeaway.strip(), rating)
                st.success("✅ Takeaway saved successfully!")
                st.rerun()  # refresh → removed from form, added to pinboard

st.subheader("📌 My Takeaway Pinboard")

my_takeaways = get_my_takeaways(user["email"])
if not my_takeaways:
    st.info("No takeaways yet. Submit after your completed sessions to build your pinboard!")
else:
    # pastel sticky note colors
    colors = ["#FFFACD", "#FFDEAD", "#E6E6FA", "#D8BFD8", "#AFEEEE", "#FFDAB9"]

    # grid container
    st.markdown("""
        <style>
        .pinboard {
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            margin-top: 20px;
        }
        .postit {
            width: 220px;
            height: 220px;
            padding: 15px;
            border-radius: 10px;
            box-shadow: 2px 2px 5px rgba(0,0,0,0.2);
            font-size: 14px;
            overflow-wrap: break-word;
            margin-bottom: 20px; 
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="pinboard">', unsafe_allow_html=True)
    for i, fb in enumerate(my_takeaways):
        color = colors[i % len(colors)]  # cycle colors
        st.markdown(f"""
        <div class="postit" style="background-color:{color};">
            📅 <b>{fb['start_utc']} → {fb['end_utc']}</b><br><br>
            👨‍🏫 <b>Mentor:</b> {fb['mentor_email']}<br>
            ⭐ <b>Rating:</b> {fb['rating']}<br>
            📝 <b>Takeaway:</b> {fb['takeaway']}
        </div>
        """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
