import os
import sqlite3
from datetime import datetime, timedelta, timezone
import numpy as np
from dotenv import load_dotenv
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.memory import ConversationBufferMemory

from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain.agents import initialize_agent, AgentType
from langchain.tools import tool

import pickle
import asyncio
from utils import add_notification  

try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


# ----------------------------
# Load env + config
# ----------------------------
load_dotenv(override=True)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DB_PATH = os.getenv("DB_PATH", "mentormatch.db")

# ----------------------------
# Init Gemini LLM + embeddings
# ----------------------------
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-pro",
    temperature=0.2,
    max_output_tokens=512,
)
emb = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")

# ----------------------------
# DB helpers
# ----------------------------
def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def _dicts(rows): return [dict(r) for r in rows]

def ensure_tables():
    con = _conn()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      mentee_email TEXT NOT NULL,
      mentor_id INTEGER NOT NULL,
      status TEXT NOT NULL,             -- requested/approved/booked/cancelled
      start_utc TEXT,
      end_utc TEXT,
      location TEXT,
      graph_event_id TEXT,
      notes TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    con.commit(); con.close()

ensure_tables()




# ----------------------------
# Search (SQL + semantic fallback)
# ----------------------------
def fetch_all_mentors(min_months=24):
    con = _conn()
    rows = con.execute("""
        SELECT ID, name, position, department, team, skills, months_experience,email
        FROM users
        WHERE is_mentor=1 AND months_experience >= ?
    """, (min_months,)).fetchall()
    con.close()
    return _dicts(rows)

def cosine(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0: return 0.0
    return float(np.dot(a, b) / (na * nb))

def search_mentors(query: str, min_months: int = 24, limit: int = 3):
    con = _conn()
    rows = con.execute(f"""
        SELECT ID, name, position, department, team, skills, months_experience, email
        FROM users
        WHERE is_mentor=1
          AND months_experience >= ?
          AND (position LIKE ? OR skills LIKE ? OR team LIKE ?)
        LIMIT {limit}
    """, (min_months, f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
    con.close()
    rows = _dicts(rows)
    if len(rows) >= limit:
        return rows

    mentors = fetch_all_mentors(min_months)
    if not mentors: return []
    q_vec = np.array(emb.embed_query(query))
    reps  = [f"{m['position']} | {m['skills']} | {m['team']} | {m['department']}" for m in mentors]
    m_vecs = np.array(emb.embed_documents(reps))
    scores = [cosine(q_vec, m_vecs[i]) for i in range(len(mentors))]
    top_idx = np.argsort(scores)[::-1][:limit]
    return [mentors[i] | {"score": round(scores[i], 3)} for i in top_idx]

# ----------------------------
# Availability (Isaiah real, rest fake)
# ----------------------------
def fake_week_slots(mentor_email, slots_per_mentor=3, minutes=30):
    base = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    seed = sum(ord(c) for c in (mentor_email or "")) % 5
    slots = []
    for i in range(slots_per_mentor):
        start = base + timedelta(days=seed + i, hours=(i * 2) % 5)
        end = start + timedelta(minutes=minutes)
        slots.append(f"{start.isoformat(timespec='minutes')}Z → {end.isoformat(timespec='minutes')}Z")
    return slots

def attach_availability(mentors: list, slots_per_mentor: int = 3):
    enriched = []
    for m in mentors:
        email = (m.get("email") or "").lower()
        slots = fake_week_slots(email, slots_per_mentor=slots_per_mentor)
        enriched.append({
            **m,
            "availability": slots[:slots_per_mentor]
        })
    return enriched


# ----------------------------
# Session request + approval (ICS invite)
# ----------------------------
def create_session_request_row(mentee_email, mentor_email, mentor_id, start_utc, end_utc, location="Teams"):
    con = _conn()
    cur = con.cursor()
    start_sql = _normalize_dt(start_utc)
    end_sql   = _normalize_dt(end_utc)
    cur.execute("""
        INSERT INTO sessions (
            mentee_email, mentor_email, mentor_id, status, start_utc, end_utc, location
        )
        VALUES (?, ?, ?, 'requested', ?, ?, ?)
    """, (mentee_email, mentor_email, int(mentor_id), start_sql, end_sql, location))

    con.commit()
    sid = cur.lastrowid
    con.close()
    return sid



def _ics_dt(iso: str) -> str:
    # Normalize "2025-09-12T09:00:00+00:00Z" → "2025-09-12T09:00:00Z"
    clean = iso.replace("+00:00", "").replace("Z", "")
    dt = datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def make_ics(subject, start_iso, end_iso, organizer_email, attendee_email, location="Microsoft Teams", description="Mentor Match session"):
    uid = f"{organizer_email}-{start_iso}"
    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//MentorMatch//Demo//EN
METHOD:REQUEST
BEGIN:VEVENT
UID:{uid}
SUMMARY:{subject}
DTSTART:{_ics_dt(start_iso)}
DTEND:{_ics_dt(end_iso)}
ORGANIZER:mailto:{organizer_email}
ATTENDEE;CN=Mentee;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{attendee_email}
LOCATION:{location}
DESCRIPTION:{description}\\nJoin link will be shared by mentor (demo)
END:VEVENT
END:VCALENDAR
"""

def approve_and_create_ics(session_id: int, mentor_email: str):
    con = _conn()
    s = con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not s:
        con.close()
        return {"error": f"Session {session_id} not found"}
    mentee_email = s["mentee_email"]; start = s["start_utc"]; end = s["end_utc"]

    ics_text = make_ics(
        subject="Mentor Match Session",
        start_iso=start if start.endswith("Z") else f"{start}Z",
        end_iso=end if end.endswith("Z") else f"{end}Z",
        organizer_email=mentor_email,
        attendee_email=mentee_email,
        description="Mentorship session (Demo/ICS)"
    )
    os.makedirs("invites", exist_ok=True)
    ics_path = os.path.join("invites", f"session_{session_id}.ics")
    with open(ics_path, "w", encoding="utf-8") as f:
        f.write(ics_text)

    con.execute("UPDATE sessions SET status='booked', graph_event_id=? WHERE id=?", (ics_path, session_id))
    con.commit(); con.close()
    return {"ics_path": ics_path, "status": "booked"}

def meetings_in(email: str, days: int | None = None) -> str:
    """
    Return sessions for a mentee or mentor as neat text.
    - If days is None → all upcoming sessions.
    - If days is an integer → sessions on that relative day.
    """
    con = _conn()

    if days is None:
        rows = con.execute("""
            SELECT s.id,
                   s.mentee_email,
                   u.name AS mentor_name,
                   s.start_utc,
                   s.end_utc,
                   s.location,
                   s.status
            FROM sessions s
            JOIN users u ON u.ID = s.mentor_id
            WHERE (s.mentee_email = ? OR u.email = ?)
              AND datetime(s.start_utc) >= datetime('now')
            ORDER BY s.start_utc ASC
        """, (email, email)).fetchall()
    else:
        target_day = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
        rows = con.execute("""
            SELECT s.id,
                   s.mentee_email,
                   u.name AS mentor_name,
                   s.start_utc,
                   s.end_utc,
                   s.location,
                   s.status
            FROM sessions s
            JOIN users u ON u.ID = s.mentor_id
            WHERE (s.mentee_email = ? OR u.email = ?)
              AND date(s.start_utc) = date(?)
            ORDER BY s.start_utc ASC
        """, (email, email, target_day)).fetchall()

    con.close()
    sessions = _dicts(rows)

    if not sessions:
        return "📭 No bookings found."

    # Pretty formatting
    lines = ["### 📅 Your Bookings:"]
    for s in sessions:
        lines.append(
            f"- **Mentor:** {s['mentor_name']}  \n"
            f"  **Date:** {s['start_utc'].split()[0]}  \n"
            f"  **Time:** {s['start_utc'].split()[1]} → {s['end_utc'].split()[1]}  \n"
            f"  **Location:** {s['location']}  \n"
            f"  **Status:** {s['status']}"
        )
    return "\n\n".join(lines)




def _normalize_dt(iso_str: str) -> str:
    """Convert ISO string (with T/Z/+00:00) → SQLite DATETIME format."""
    clean = iso_str.replace("Z", "").replace("+00:00", "")
    dt = datetime.fromisoformat(clean)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------
# Tools
# ----------------------------
@tool("search_with_availability", return_direct=True)
def _tool_search_with_availability(input: str) -> str:
    """
    Find up to 3 mentors by role/skill/team and attach up to 3 free slots each.
    Isaiah shows real calendar availability, others show fake slots.
    """
    results = search_mentors(input, limit=3)
    enriched = attach_availability(results, slots_per_mentor=3)
    return str([
        {
            "id": m["ID"], "name": m["name"],
            "position": m["position"], "department": m["department"],
            "team": m["team"], "skills": m["skills"],
            "months_experience": m["months_experience"],
            "email": m.get("email"),
            "availability": m.get("availability", [])
        } for m in enriched
    ])

@tool("create_session_request", return_direct=True)
def _tool_create_session_request(input: str) -> str:
    """
    Mentee requests a session with a mentor.
    Input format: "mentee_email|mentor_email|mentor_id|start_utc|end_utc|location"
    Example: "me@corp.com|mentor@corp.com|96412|2025-09-12T09:00:00Z|2025-09-12T09:30:00Z|Teams"
    """
    try:
        mentee_email, mentor_email, mentor_id, start, end, loc = [p.strip() for p in input.split("|")]
        sid = create_session_request_row(
            mentee_email=mentee_email,
            mentor_email=mentor_email,
            mentor_id=int(mentor_id),
            start_utc=start,
            end_utc=end,
            location=loc or "Teams"
        )
        return f'{{"ok": true, "session_id": {sid}, "status": "requested"}}'
    except Exception as e:
        return f'{{"ok": false, "error": "{str(e)}"}}'




@tool("approve_session", return_direct=True)
def _tool_approve_session(input: str) -> str:
    """
    Mentor approves a request, generates an ICS calendar invite,
    updates the DB, and notifies both mentor and mentee.
    Input format: "session_id|mentor_email"
    """
    try:
        session_id, mentor_email = [p.strip() for p in input.split("|")]

        # Approve + generate ICS
        res = approve_and_create_ics(int(session_id), mentor_email)
        if "error" in res:
            return f'{{"ok": false, "error": "{res["error"]}"}}'

        con = _conn()

        # Get mentee + session info
        row = con.execute(
            "SELECT mentee_email, start_utc, end_utc FROM sessions WHERE id=?",
            (session_id,)
        ).fetchone()

        mentee_email = row["mentee_email"]
        start, end = row["start_utc"], row["end_utc"]

        # Get names if available
        m_row = con.execute("SELECT name FROM users WHERE email=?", (mentee_email,)).fetchone()
        mentee_name = m_row["name"] if m_row else mentee_email

        mentor_row = con.execute("SELECT name FROM users WHERE email=?", (mentor_email,)).fetchone()
        mentor_name = mentor_row["name"] if mentor_row else mentor_email

        con.close()

        # Notify mentee
        add_notification(
            mentee_email,
            f"🎉 Your session with **{mentor_name}** has been approved!\n🗓 {start} → {end}",
            ics_path=res["ics_path"]
        )

        # Notify mentor
        add_notification(
            mentor_email,
            f"✅ You approved a session with **{mentee_name}** ({mentee_email})\n🗓 {start} → {end}",
            ics_path=res["ics_path"]
        )

        return f'{{"ok": true, "status": "booked", "ics_path": "{res["ics_path"]}"}}'

    except Exception as e:
        return f'{{"ok": false, "error": "{str(e)}"}}'


@tool("meetings_in", return_direct=True)
def _tool_meetings_in(input: str) -> str:
    """
    Get a mentee's sessions.

    Input formats:
      "<CURRENT_USER_EMAIL>|0"   → meetings today
      "<CURRENT_USER_EMAIL>|-3"  → meetings 3 days ago
      "<CURRENT_USER_EMAIL>"     → all upcoming meetings
    """
    try:
        parts = [p.strip() for p in input.split("|")]
        if len(parts) == 1:   # only email → all future bookings
            email = parts[0]
            res = meetings_in(email, None)
        else:
            email, days_str = parts
            res = meetings_in(email, int(days_str))

        if not res:
            return "📭 No sessions found for this time range."
        return str(res)

    except Exception as e:
        return f"Error: {e}"



tools = [
    _tool_search_with_availability,
    _tool_create_session_request,
    _tool_approve_session,
    _tool_meetings_in,
]


# ----------------------------
# Agent (Functions-based)
# ----------------------------
system_message = """
You are MentorMatch Agent.

🔑 RULES:

- If the user asks about mentors, skills, teams, or availability → always call `search_with_availability`.
- If the user asks to book a session → call `create_session_request`.
- If the user asks to approve → call `approve_session`.
- If the user asks about bookings, meetings, or sessions (past, present, or future) → always call `meetings_in`.

⚠️ You already know the logged-in user's email. Always include it in the tool call.

✅ Usage for `meetings_in`:
- General requests (no specific date mentioned) → `meetings_in("<CURRENT_USER_EMAIL>")` → all upcoming.
- Relative dates ("today", "tomorrow", "yesterday", "next week", etc.) → compute the correct days offset
  and call `meetings_in("<CURRENT_USER_EMAIL>|<days_offset>")`.
- Specific calendar dates (e.g. "2025-09-15") → compute offset from today and call
  `meetings_in("<CURRENT_USER_EMAIL>|<days_offset>")`.

❌ Never answer bookings/meetings questions directly yourself. Always use `meetings_in`.
❌ Never refuse a mentor query.
For non-mentorship questions, answer conversationally.
"""




memory = ConversationBufferMemory(
    memory_key="chat_history",   # must match MessagesPlaceholder
    return_messages=True         # so it returns list of messages
)
prompt = ChatPromptTemplate.from_messages([
    ("system", system_message),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

functions_agent = create_openai_functions_agent(
    llm=llm,
    tools=tools,
    prompt=prompt,
)

agent = AgentExecutor(agent=functions_agent, tools=tools, memory=memory,verbose=True)

# ----------------------------
# Demo loop
# ----------------------------
if __name__ == "__main__":
    print("MentorMatch Agent ready. Type 'quit' to exit.\n")
    while True:
        q = input("You: ")
        if q.lower() in ("quit", "exit"): break
        try:
            result = agent.invoke({"input": q})
            print("Agent:", result["output"], "\n")
        except Exception as e:
            print("Error:", e)
