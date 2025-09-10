import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

CSV_PATH = "Mentor Match/Empolyee Dataset.csv"   # check path & spaces
DB_PATH  = "mentormatch.db"

# -------- 1) Load CSV --------
df = pd.read_csv(CSV_PATH).fillna("")

# Validate expected columns (as they appear in CSV)
required = ["ID","Name","Department","Team","Position","Age", "College", "Salary", "Skills","Experience Period (Months)"]
missing = [c for c in required if c not in df.columns]
if missing:
    raise ValueError(f"Missing column(s): {missing}")

# Normalize column names for DB
df = df.rename(columns={"Experience Period (Months)": "months_experience",
                        "Name": "name",
                        "Department": "department",
                        "Team": "team",
                        "Position": "position",
                        "Skills": "skills",
                        "Age": "age",
                        "College": "college",
                        "Salary": "salary",
                        "ID": "ID"
                        })

# Synthesize emails if you don’t have them
def synth_email(name):
    base = str(name).strip().lower().replace(" ", ".")
    return f"{base}@company.com"

df["email"] = df["name"].apply(synth_email)
df["calendar_email"] = df["email"]

# Ensure months_experience is integer
df["months_experience"] = pd.to_numeric(df["months_experience"], errors="coerce").fillna(0).astype(int)

# Mentor rule: experience > 24 months
df["is_mentor"] = (df["months_experience"] > 24).astype(int)

# Frame for insert
users_df = df[[
    "ID","name","email","position","department","team","skills","months_experience","is_mentor","calendar_email"
]].copy()

# -------- 2) Create DB & tables --------
con = sqlite3.connect(DB_PATH)
con.execute("PRAGMA foreign_keys = ON;")
cur = con.cursor()

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
  calendar_email TEXT
);
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS mentor_profiles(
  mentor_id INTEGER PRIMARY KEY,
  bio TEXT,
  areas_of_help TEXT,
  max_mentees INTEGER DEFAULT 5,
  accepting_new INTEGER DEFAULT 1,
  FOREIGN KEY (mentor_id) REFERENCES users(ID)
);
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mentee_email TEXT NOT NULL,
  mentor_id INTEGER NOT NULL,
  status TEXT NOT NULL,               -- requested/approved/booked/cancelled
  start_utc DATETIME,
  end_utc DATETIME,
  location TEXT,
  graph_event_id TEXT,
  notes TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (mentor_id) REFERENCES users(ID)
);
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS rewards(
  mentor_id INTEGER PRIMARY KEY,
  points_total INTEGER DEFAULT 0,
  last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (mentor_id) REFERENCES users(ID)
);
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS audit_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  action TEXT NOT NULL,
  details_json TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(ID)
);
""")

# -------- 3) Upsert users --------
# Using INSERT OR REPLACE works if ID (the PK) is provided.
cur.executemany("""
INSERT OR REPLACE INTO users
(ID, name, email, position, department, team, skills, months_experience, is_mentor, calendar_email)
VALUES (?,?,?,?,?,?,?,?,?,?)
""", users_df.itertuples(index=False, name=None))

# -------- 4) Initialize rewards for mentors --------
cur.execute("""
INSERT OR IGNORE INTO rewards(mentor_id, points_total)
SELECT ID, 0 FROM users WHERE is_mentor = 1;
""")

con.commit()
con.close()

print(f"Imported {len(users_df)} users into {DB_PATH}.")
print("   sessions/rewards/audit_logs are ready (rewards initialized for mentors).")
