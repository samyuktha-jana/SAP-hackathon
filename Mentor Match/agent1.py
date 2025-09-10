import os
import sqlite3
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import initialize_agent, AgentType
from langchain_community.utilities import SQLDatabase
from langchain_community.tools.sql_database.tool import QuerySQLDatabaseTool
import numpy as np
from langchain_google_genai import GoogleGenerativeAIEmbeddings

# ----------------------------
# Load env + config
# ----------------------------
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DB_PATH = os.getenv("DB_PATH", "mentormatch.db")

# ----------------------------
# Init Gemini LLM
# ----------------------------
llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",
    temperature=0.2,
    max_output_tokens=512,
)

# ----------------------------
# Connect SQLite
# ----------------------------
db = SQLDatabase.from_uri(f"sqlite:///{DB_PATH}")
query_tool = QuerySQLDatabaseTool(db=db)


# Initialize Gemini embeddings (text-embedding-004 is good for semantic similarity)
emb = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")

# ----------------------------
# Custom helper functions
# ----------------------------
def fetch_all_mentors(min_months=24):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT ID, name, position, department, team, skills, 
                months_experience, calendar_email
        FROM users
        WHERE is_mentor=1 AND "Experience Period (Months)" >= ?
    """, (min_months,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

def search_mentors(query: str, min_months: int = 24, limit: int = 3):
    """Search mentors: SQL exact match first, then semantic fallback."""
    # 1. Try SQL LIKE
    sql = f"""
    SELECT ID, name, position, department, team, skills, 
            months_experience, calendar_email
    FROM users
    WHERE is_mentor=1
      AND "months_experience" >= {min_months}
      AND (position LIKE '%{query}%' OR skills LIKE '%{query}%' OR team LIKE '%{query}%')
    LIMIT {limit};
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = [dict(r) for r in cur.execute(sql).fetchall()]
    conn.close()

    if len(rows) >= limit:
        return rows

    # 2. Fallback: semantic similarity
    mentors = fetch_all_mentors(min_months)
    if not mentors:
        return []

    query_vec = np.array(emb.embed_query(query))
    reps = [f"{m['position']} {m['skills']} {m['team']} {m['department']}" for m in mentors]
    mentor_vecs = np.array(emb.embed_documents(reps))

    scores = [cosine(query_vec, mentor_vecs[i]) for i in range(len(mentors))]
    top_idx = np.argsort(scores)[::-1][:limit]

    return [mentors[i] | {"score": round(scores[i], 3)} for i in top_idx]

def meetings_in(email: str, days: int):
    """Get meetings in ±days relative to today."""
    sql = f"""
    SELECT mentor_id, mentee_email, start_utc, end_utc, status
    FROM sessions
    WHERE mentee_email='{email}'
      AND date(start_utc) BETWEEN date('now', '{days} day') AND date('now', '{days} day')
    ORDER BY start_utc DESC;
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = cur.execute(sql).fetchall()
    cols = [d[0] for d in cur.description]
    conn.close()
    return [dict(zip(cols, r)) for r in rows]

# ----------------------------
# Tool wrappers for LangChain
# ----------------------------
from langchain.tools import tool

@tool("search_mentors", return_direct=True)
def _search_mentors(input: str) -> str:
    """Search mentors by role/skills/team. Input: string keyword."""
    results = search_mentors(input)
    return str(results)

@tool("meetings_in", return_direct=True)
def _meetings_in(input: str) -> str:
    """
    Find past/future meetings. 
    Input: "mentee_email,days" (days can be -3 for past, +3 for future).
    """
    try:
        email, days = input.split(",")
        days = int(days)
    except:
        return "Format error. Use 'email,days'. Example: 'me@corp.com,-3'"
    results = meetings_in(email.strip(), days)
    return str(results)

# ----------------------------
# Build the agent
# ----------------------------
tools = [_search_mentors, _meetings_in, query_tool]

system_message = """
You are MentorMatch Agent.
- Use tools to fetch data, do not invent.
- Recommend only mentors with is_mentor=1 and Experience Period (Months) ≥ 24.
- For 'who did I meet X days ago' or 'who will I meet in X days', call meetings_in.
- Be concise in answers.
"""

agent = initialize_agent(
    tools,
    llm,
    agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,
    handle_parsing_errors=True,
)

# ----------------------------
# Demo loop
# ----------------------------
if __name__ == "__main__":
    print("MentorMatch Agent (Gemini) ready. Type 'quit' to exit.\n")
    while True:
        q = input("You: ")
        if q.lower() in ["quit", "exit"]:
            break
        try:
            ans = agent.run(system_message + "\nUser: " + q)
            print("Agent:", ans, "\n")
        except Exception as e:
            print("Error:", e)
