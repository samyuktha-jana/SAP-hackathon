import os
import pandas as pd
import google.generativeai as genai
import json
import sqlite3
import uuid
from datetime import datetime

# -----------------------------
# 1. Setup Gemini API
# -----------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or "AIzaSyBaV5-o3IFRK4u931MGjZSYdVOIRD1hpiY"
genai.configure(api_key=GEMINI_API_KEY)

# Load Gemini model
model = genai.GenerativeModel("gemini-1.5-flash")

# -----------------------------
# 2. Load Onboarding Data
# -----------------------------
# Get the directory where this script is located
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

employee_df = pd.read_csv(os.path.join(BASE_DIR, "Employee Dataset1.csv")).fillna("")
office_df = pd.read_csv(os.path.join(BASE_DIR, "OfficeDetails.csv"), sep="\t").fillna("")
employee_df.columns = employee_df.columns.str.strip()
office_df.columns = office_df.columns.str.strip()

print("Employee data loaded:", len(employee_df), "records")
print("Office details loaded:", len(office_df), "records")

# -----------------------------
# 3. Setup SQLite for Tickets
# -----------------------------
DB_FILE = "tickets.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            TicketID TEXT PRIMARY KEY,
            User TEXT,
            Issue TEXT,
            Status TEXT,
            CreatedAt TEXT
        )
    """)
    conn.commit()
    conn.close()

def create_ticket(user_name, issue_description):
    ticket_id = str(uuid.uuid4())[:8]
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tickets (TicketID, User, Issue, Status, CreatedAt)
        VALUES (?, ?, ?, ?, ?)
    """, (ticket_id, user_name, issue_description, "Open", created_at))
    conn.commit()
    conn.close()
    return ticket_id

# Initialize DB
init_db()

# -----------------------------
# 4. Conversation history
# -----------------------------
conversation_history = []

# -----------------------------
# 5. Helper function for Gemini
# -----------------------------
def query_gemini(user_input, chat_history=None):
    """
    Sends the user input along with CSV data and conversation history to Gemini and returns AI response.
    If the user requests a ticket, create one in SQLite.
    """
    # Use provided chat_history if available, else fallback to local conversation_history
    history = chat_history if chat_history is not None else conversation_history

    # Special case: raising a ticket
    if "raise a ticket" in user_input.lower():
        try:
            # Try to extract issue description (everything after "raise a ticket")
            issue_desc = user_input.split("raise a ticket", 1)[-1].strip()
            if not issue_desc:
                return "Please describe the issue you want to raise a ticket for."

            # Find user name from conversation history if available
            user_name = "Unknown User"
            for entry in history:
                if entry.startswith("You:") and "," in entry:
                    # Example: "You: Clarissa Tan, SAP BTP Development"
                    user_name = entry.split(",")[0].replace("You:", "").strip()
                    break

            ticket_id = create_ticket(user_name, issue_desc)
            return f"✅ Ticket raised successfully!\nTicket ID: {ticket_id}\nIssue: {issue_desc}\nYou can track this in the IT dashboard."
        except Exception as e:
            return f"❌ Failed to raise ticket: {str(e)}"

    # Convert CSVs to JSON for AI processing
    employee_json = employee_df.to_dict(orient="records")
    office_json = office_df.to_dict(orient="records")

    # Include conversation history
    history_text = "\n".join(history)

    # Prepare prompt for Gemini with explicit memory instructions
    prompt = f"""
You are an intelligent onboarding assistant. 
Use the following CSV data to answer user queries:

Employee Data:
{json.dumps(employee_json, indent=2)}

Office Data:
{json.dumps(office_json, indent=2)}

Conversation History:
{history_text}

Instructions:
- Remember any information the user provides during this session, such as their name, team, or position.
- Use this information in future answers to personalize responses.
- If the user asks about their team members, learning modules, or documents, use the remembered team/position info.
- If the requested information is not available, use your own knowledge to answer the question and respond politely. 
- Answer only the specific question the user asked.
- If the user asks for team members, provide just the list of members.
- If the user asks for learning modules, documents, or emails, provide only those.
- Do not add extra information unless the user explicitly requests it.
- Remember any information the user provides for context, but do not generate unrelated onboarding info.

User asked: "{user_input}"
    """

    response = model.generate_content(contents=prompt)
    return response.text.strip()

# -----------------------------
# 6. Run chatbot in terminal
# -----------------------------
def main():
    print("🤖 Onboarding Assistant Chatbot")
    print("Bot: Hello! What can I do for you today?\n")
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ["exit", "quit"]:
            print("Bot: Goodbye! All the best in your onboarding. 👋")
            break
        try:
            # Add user input to conversation history
            conversation_history.append(f"You: {user_input}")

            # Get Gemini response
            response = query_gemini(user_input)

            # Add bot response to conversation history
            conversation_history.append(f"Bot: {response}")

            print("Bot:", response)
        except Exception as e:
            print("Bot: Sorry, I encountered an error:", str(e))

if __name__ == "__main__":
    main()