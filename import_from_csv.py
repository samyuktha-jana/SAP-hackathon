import sqlite3
import pandas as pd

CSV_PATH = "/Users/samyuktha/Desktop/SAP-hackathon/Employee Dataset1.csv"
DB_PATH = "mentormatch.db"

# -------- 1) Load CSV --------
df = pd.read_csv(CSV_PATH).fillna("")

# Validate expected columns
required = [
    "ID", "Name", "Department", "Team", "Position", "Age",
    "College", "Salary", "Skills", "Experience Period (Months)",
    "email", "chat", "timezone", "topics", "office_hours"
]
missing = [c for c in required if c not in df.columns]
if missing:
    raise ValueError(f"Missing column(s): {missing}")

# Normalize column names for DB
df = df.rename(columns={
    "Experience Period (Months)": "months_experience",
    "Name": "name",
    "Department": "department",
    "Team": "team",
    "Position": "position",
    "Skills": "skills",
    "Age": "age",
    "College": "college",
    "Salary": "salary",
    "ID": "ID",
    "email": "email",
    "chat": "chat",
    "timezone": "timezone",
    "topics": "topics",
    "office_hours": "office_hours"
})

# Ensure months_experience is integer
df["months_experience"] = pd.to_numeric(df["months_experience"], errors="coerce").fillna(0).astype(int)

# Mentor rule: experience > 24 months
df["is_mentor"] = (df["months_experience"] > 24).astype(int)

# Frame for insert
users_df = df[[
    "ID", "name", "email", "position", "department", "team", "skills",
    "months_experience", "is_mentor", "chat", "timezone", "topics",
    "office_hours", "college", "age", "salary"
]].copy()

# -------- 2) Create DB & tables --------
con = sqlite3.connect(DB_PATH)
con.execute("PRAGMA foreign_keys = ON;")
cur = con.cursor()

# --- Users ---
cur.execute("""
CREATE TABLE IF NOT EXISTS users(
  ID INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL,
  position TEXT,
  department TEXT,
  team TEXT,
  skills TEXT,
  months_experience INTEGER DEFAULT 0,
  is_mentor INTEGER DEFAULT 0,
  chat TEXT,
  timezone TEXT,
  topics TEXT,
  office_hours TEXT,
  college TEXT,
  age INTEGER,
  salary REAL
);
""")

# --- Sessions ---
cur.execute("""
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mentee_email TEXT NOT NULL,
  mentor_email TEXT NOT NULL,
  mentor_id INTEGER NOT NULL,
  status TEXT NOT NULL,               -- requested/approved/booked/cancelled
  start_utc TEXT,
  end_utc TEXT,
  location TEXT,
  graph_event_id TEXT,
  notes TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (mentor_id) REFERENCES users(ID)
);
""")

# --- Rewards (history-friendly) ---
cur.execute("""
CREATE TABLE IF NOT EXISTS rewards(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mentor_id INTEGER NOT NULL,
  points_total INTEGER DEFAULT 0,
  last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (mentor_id) REFERENCES users(ID)
);
""")

# --- Audit logs ---
cur.execute("""
CREATE TABLE IF NOT EXISTS audit_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  details_json TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(ID)
);
""")

# --- Notifications ---
cur.execute("""
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_email TEXT NOT NULL,
  message TEXT NOT NULL,
  ics_path TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
""")

# --- Chat history ---
cur.execute("""
CREATE TABLE IF NOT EXISTS chat_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_email TEXT NOT NULL,
  role TEXT NOT NULL,  -- 'user' or 'assistant'
  message TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
""")

# -------- 3) Upsert users --------
cur.executemany("""
INSERT OR REPLACE INTO users
(ID, name, email, position, department, team, skills,
 months_experience, is_mentor, chat, timezone, topics,
 office_hours, college, age, salary)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
""", users_df.itertuples(index=False, name=None))

# -------- 4) Initialize rewards for mentors --------
cur.execute("""
INSERT INTO rewards(mentor_id, points_total)
SELECT ID, 0 FROM users WHERE is_mentor = 1
EXCEPT
SELECT mentor_id, 0 FROM rewards;
""")

con.commit()
con.close()

print(f"Imported {len(users_df)} users into {DB_PATH}.")
