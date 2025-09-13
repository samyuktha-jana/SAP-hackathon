import sqlite3
from pathlib import Path
import streamlit as st

DB_PATH = "mentormatch.db"

def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def add_notification(user_email: str, message: str, ics_path: str = None):
    con = _conn()
    con.execute(
        """
        INSERT INTO notifications (user_email, message, ics_path, created_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (user_email, message, ics_path)
    )
    con.commit()
    con.close()


def get_notifications(user_email: str):
    con = _conn()
    rows = con.execute(
        "SELECT * FROM notifications WHERE user_email=? ORDER BY created_at DESC",
        (user_email,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

def clear_notifications(user_email: str):
    con = _conn()
    con.execute("DELETE FROM notifications WHERE user_email=?", (user_email,))
    con.commit()
    con.close()

def notifications_panel(user):
    """Sidebar notifications for logged-in user"""
    st.sidebar.header("🔔 Notifications")
    notifs = get_notifications(user["email"])
    if not notifs:
        st.sidebar.info("No notifications yet.")
    else:
        for n in notifs:
            st.sidebar.write(f"- {n['message']}")
            if n.get("ics_path"):
                ics_file = Path(n["ics_path"])
                if ics_file.exists():
                    with open(ics_file, "rb") as f:
                        st.sidebar.download_button(
                            "📥 Download Invite",
                            f,
                            file_name=ics_file.name,
                            key=f"dl_{n['id']}"
                        )
        if st.sidebar.button("Clear All"):
            clear_notifications(user["email"])
            st.sidebar.success("Notifications cleared!")
