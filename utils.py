import sqlite3
from pathlib import Path
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import os
import json
from datetime import datetime, date



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
            st.rerun()

    if st.sidebar.button("🚪 Logout"):
        st.session_state.clear()
        st.session_state["user"] = None   # re-initialize so it exists
        st.success("You have been logged out.")
        st.rerun()

        


#learning hub 


USERDATA_DIR = "userdata"
os.makedirs(USERDATA_DIR, exist_ok=True)


def user_json_path(email: str) -> str:
    safe = email.replace("@", "_at_").replace(".", "_dot_")
    return os.path.join(USERDATA_DIR, f"{safe}.json")


def parse_date_safe(val):
    if not isinstance(val, str):
        return val
    try:
        return datetime.fromisoformat(val).date()
    except Exception:
        try:
            return date.fromisoformat(val)
        except Exception:
            return None


def parse_datetime_safe(val):
    if not isinstance(val, str):
        return val
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None


def normalize_dates(data: dict) -> dict:
    pt = data.get("progress_tracker", {})

    if "start_date" in pt:
        pt["start_date"] = parse_date_safe(pt["start_date"])

    for phase, info in pt.get("phase_status", {}).items():
        if "completed_at" in info:
            info["completed_at"] = parse_datetime_safe(info["completed_at"])

    for cp in pt.get("checkpoints", []):
        if "target_date" in cp:
            cp["target_date"] = parse_date_safe(cp["target_date"])
        if "completed_at" in cp:
            cp["completed_at"] = parse_datetime_safe(cp["completed_at"])

    data["progress_tracker"] = pt
    return data


def load_user_data(email: str) -> dict:
    path = user_json_path(email)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return normalize_dates(data)
        except Exception:
            return {}
    return {}


def save_user_data(email: str, data: dict):
    path = user_json_path(email)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def hydrate_session_from_json():
    """Call this once after login to make sure session_state is hydrated from JSON."""
    if "user" not in st.session_state or not st.session_state.user:
        return

    user_email = st.session_state.user["email"].strip().lower()

    if "hydrated_from_json" not in st.session_state:
        user_data = load_user_data(user_email)
        for key in [
            "chosen_upskillingplan",
            "accepted_plan_role",
            "accepted_at",
            "progress_tracker",
            "manager_feedback",
        ]:
            if key in user_data:
                st.session_state[key] = user_data[key]
        st.session_state["hydrated_from_json"] = True


def persist_session_to_json():
    """Call this at the bottom of each page to persist back to JSON."""
    if "user" not in st.session_state or not st.session_state.user:
        return

    user_email = st.session_state.user["email"].strip().lower()
    save_user_data(user_email, {
        "chosen_upskillingplan": st.session_state.get("chosen_upskillingplan"),
        "accepted_plan_role": st.session_state.get("accepted_plan_role"),
        "accepted_at": st.session_state.get("accepted_at"),
        "progress_tracker": st.session_state.get("progress_tracker", {}),
        "manager_feedback": st.session_state.get("manager_feedback")
    })
