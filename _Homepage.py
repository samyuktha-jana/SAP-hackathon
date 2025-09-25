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



#NEWLY ADDED BELOW (NEHA) 2/2
# --- Progress Table Creators ---
def ensure_software_progress_table():
    """Create software_progress table if it doesn't exist"""
    con = _conn()
    con.execute("""
        CREATE TABLE IF NOT EXISTS software_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            software TEXT NOT NULL,
            installed BOOLEAN DEFAULT FALSE,
            installed_at DATETIME,
            UNIQUE(user_email, software)
        )
    """)
    con.commit(); con.close()

def ensure_document_progress_table():
    """Create document_progress table if it doesn't exist"""
    con = _conn()
    con.execute("""
        CREATE TABLE IF NOT EXISTS document_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            document TEXT NOT NULL,
            signed BOOLEAN DEFAULT FALSE,
            signed_at DATETIME,
            UNIQUE(user_email, document)
        )
    """)
    con.commit(); con.close()
#NEWLY ADDED ABOVE (NEHA) 2/2

# Ensure all progress tables exist before any DB operations
def ensure_all_progress_tables():
    ensure_tables()
    # Ensure all three progress tables exist
    try:
        ensure_learning_progress_table()
    except Exception:
        # Function may not yet be defined on first import; will be called again after definitions
        pass
    ensure_software_progress_table()
    ensure_document_progress_table()

#NEWLY ADDED BELOW (NEHA) 1/2
# ------------------------ Learning Progress (Chatbot Memory) ----------------------
def ensure_learning_progress_table():
    """Create learning_progress table if it doesn't exist"""
    con = _conn()
    con.execute("""
        CREATE TABLE IF NOT EXISTS learning_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            module TEXT NOT NULL,
            completed BOOLEAN DEFAULT FALSE,
            completed_at DATETIME,
            UNIQUE(user_email, module)
        )
    """)
    con.commit(); con.close()

def ensure_software_progress_table():
    """Create software_progress table if it doesn't exist"""
    con = _conn()
    con.execute("""
        CREATE TABLE IF NOT EXISTS software_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            software TEXT NOT NULL,
            installed BOOLEAN DEFAULT FALSE,
            installed_at DATETIME,
            UNIQUE(user_email, software)
        )
    """)
    con.commit(); con.close()

def ensure_document_progress_table():
    """Create document_progress table if it doesn't exist"""
    con = _conn()
    con.execute("""
        CREATE TABLE IF NOT EXISTS document_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            document TEXT NOT NULL,
            signed BOOLEAN DEFAULT FALSE,
            signed_at DATETIME,
            UNIQUE(user_email, document)
        )
    """)
    con.commit(); con.close()

def load_learning_progress_from_csv():
    """Load learning progress data from CSV into database only if database is empty"""
    # Ensure table exists before loading
    try:
        ensure_learning_progress_table()
    except Exception:
        pass
    if not os.path.exists("LearningProgress.csv"):
        return
    
    try:
        con = _conn()
        # Check if database already has learning progress data
        count = con.execute("SELECT COUNT(*) FROM learning_progress").fetchone()[0]
        if count > 0:
            con.close()
            return  # Database already has data, don't overwrite
        
        df = pd.read_csv("LearningProgress.csv")
        
        for _, row in df.iterrows():
            module_name = str(row['module']).strip()
            completed = row['completed'] in [True, 'True', 'true', 1, '1', 'TRUE', 'Yes', 'yes', 'Y']
            con.execute("""
                INSERT OR REPLACE INTO learning_progress (user_email, module, completed, completed_at)
                VALUES (?, ?, ?, ?)
            """, (row['email'], module_name, completed, 
                  datetime.now(timezone.utc).isoformat() if completed else None))
        
        con.commit(); con.close()
        print("Loaded learning progress from CSV into empty database")
    except Exception as e:
        print(f"Error loading learning progress: {e}")

def get_user_assigned_modules(user_email: str):
    """Get assigned learning modules for a user from Employee Dataset"""
    try:
        df = pd.read_csv("Employee Dataset1.csv")
        user_row = df[df['email'] == user_email]
        
        if user_row.empty:
            return []
        
        modules_str = user_row['Learning Modules'].iloc[0]
        if pd.isna(modules_str):
            return []
        
        # Split by comma and clean up
        modules = [m.strip() for m in str(modules_str).split(',')]
        return modules
        
    except Exception as e:
        print(f"Error getting assigned modules: {e}")
        return []

def get_user_required_software(user_email: str):
    """Get required software installations for a user from Employee Dataset"""
    try:
        df = pd.read_csv("Employee Dataset1.csv")
        user_row = df[df['email'] == user_email]
        
        if user_row.empty:
            return []
        
        software_str = user_row['To Install'].iloc[0]
        if pd.isna(software_str):
            return []
        
        # Split by comma and clean up
        software = [s.strip() for s in str(software_str).split(',')]
        return software
        
    except Exception as e:
        print(f"Error getting required software: {e}")
        return []

def get_user_required_documents(user_email: str):
    """Get required documents to be signed for a user from Employee Dataset"""
    try:
        df = pd.read_csv("Employee Dataset1.csv")
        user_row = df[df['email'] == user_email]
        
        if user_row.empty:
            return []
        
        # Note: Column name has trailing space
        documents_str = user_row['Documents to be signed '].iloc[0]
        if pd.isna(documents_str):
            return []
        
        # Split by comma and clean up
        documents = [d.strip() for d in str(documents_str).split(',')]
        return documents
        
    except Exception as e:
        print(f"Error getting required documents: {e}")
        return []

def get_user_learning_progress(user_email: str):
    """Get learning progress from database"""
    con = _conn()
    rows = con.execute("""
        SELECT module, completed, completed_at
        FROM learning_progress
        WHERE user_email = ?
        ORDER BY module
    """, (user_email,)).fetchall()
    con.close()
    # Normalize module names to avoid whitespace mismatches
    return {str(row['module']).strip(): bool(row['completed']) for row in rows}

def get_user_software_progress(user_email: str):
    """Get software installation progress from database"""
    con = _conn()
    rows = con.execute("""
        SELECT software, installed, installed_at
        FROM software_progress
        WHERE user_email = ?
        ORDER BY software
    """, (user_email,)).fetchall()
    con.close()
    # Normalize software names
    return {str(row['software']).strip(): bool(row['installed']) for row in rows}

def get_user_document_progress(user_email: str):
    """Get document signing progress from database"""
    con = _conn()
    rows = con.execute("""
        SELECT document, signed, signed_at
        FROM document_progress
        WHERE user_email = ?
        ORDER BY document
    """, (user_email,)).fetchall()
    con.close()
    # Normalize document names
    return {str(row['document']).strip(): bool(row['signed']) for row in rows}

def mark_module_completed(user_email: str, module: str):
    """Mark a learning module as completed for a user"""
    con = _conn()
    con.execute("""
        INSERT OR REPLACE INTO learning_progress (user_email, module, completed, completed_at)
        VALUES (?, ?, ?, ?)
    """, (user_email, module, True, datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()
    
    # Also update CSV for dashboard compatibility
    try:
        if os.path.exists("LearningProgress.csv"):
            df = pd.read_csv("LearningProgress.csv")
        else:
            df = pd.DataFrame(columns=["email", "module", "completed"])
        
        # Update or add the completed module
        mask = (df["email"] == user_email) & (df["module"] == module)
        if mask.any():
            df.loc[mask, "completed"] = True
        else:
            new_row = pd.DataFrame([{"email": user_email, "module": module, "completed": True}])
            df = pd.concat([df, new_row], ignore_index=True)
        
        df.to_csv("LearningProgress.csv", index=False)
    except Exception as e:
        print(f"Error updating CSV: {e}")

def mark_software_installed(user_email: str, software: str, installed: bool = True):
    """Mark a software as installed/uninstalled for a user"""
    con = _conn()
    con.execute("""
        INSERT OR REPLACE INTO software_progress (user_email, software, installed, installed_at)
        VALUES (?, ?, ?, ?)
    """, (user_email, software, installed, 
          datetime.now(timezone.utc).isoformat() if installed else None))
    con.commit(); con.close()
    # print(f"[DEBUG] mark_software_installed: {user_email}, {software}, {installed}")  # Debug only

def mark_document_signed(user_email: str, document: str, signed: bool = True):
    """Mark a document as signed/unsigned for a user"""
    con = _conn()
    con.execute("""
        INSERT OR REPLACE INTO document_progress (user_email, document, signed, signed_at)
        VALUES (?, ?, ?, ?)
    """, (user_email, document, signed,
          datetime.now(timezone.utc).isoformat() if signed else None))
    con.commit(); con.close()
    # print(f"[DEBUG] mark_document_signed: {user_email}, {document}, {signed}")  # Debug only

def format_learning_progress_response(user_email: str):
    """Format learning progress with interactive checkboxes and Update button"""
    assigned_modules = get_user_assigned_modules(user_email)
    progress_data = get_user_learning_progress(user_email)
    
    if not assigned_modules:
        st.markdown("No learning modules found for your profile.")
        return
    
    st.markdown("## 📚 Your Learning Modules")
    
    # Use a form to prevent immediate refresh
    with st.form(key="learning_form"):
        module_checkboxes = {}
        st.markdown("### 📚 **Learning Module Progress:**")
        for i, module in enumerate(assigned_modules, 1):
            current_status = progress_data.get(module, False)
            col1, col2 = st.columns([0.1, 0.9])
            with col1:
                safe_module = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(module).strip())[:100]
                checkbox_key = f"{user_email}_learning_{i}_{safe_module}"
                st.checkbox("✓", value=current_status, key=checkbox_key, label_visibility="hidden")
                module_checkboxes[module] = checkbox_key  # Store key instead of value
            with col2:
                status_icon = "✅" if current_status else "⬜"
                st.write(f"{i}. {status_icon} 📖 {module}")
        submitted = st.form_submit_button("🔄 Update Learning Progress", use_container_width=True)
        if submitted:
            changes_made = []
            for module, checkbox_key in module_checkboxes.items():
                # Get the actual checkbox state from session state
                new_state = st.session_state.get(checkbox_key, False)
                # Always write the current state to the DB
                if new_state:
                    mark_module_completed(user_email, module)
                    # Also update CSV for dashboard
                    try:
                        csv_path = "LearningProgress.csv"
                        if os.path.exists(csv_path):
                            df = pd.read_csv(csv_path)
                        else:
                            df = pd.DataFrame(columns=["email", "module", "completed"])
                        mask = (df["email"] == user_email) & (df["module"] == module)
                        if mask.any():
                            df.loc[mask, "completed"] = True
                        else:
                            new_row = pd.DataFrame([{"email": user_email, "module": module, "completed": True}])
                            df = pd.concat([df, new_row], ignore_index=True)
                        df.to_csv(csv_path, index=False)
                    except Exception as e:
                        print(f"Error updating CSV: {e}")
                    changes_made.append(f"✅ Completed: {module}")
                else:
                    con = _conn()
                    con.execute("""
                        INSERT OR REPLACE INTO learning_progress (user_email, module, completed, completed_at)
                        VALUES (?, ?, ?, ?)
                    """, (user_email, module, False, None))
                    con.commit(); con.close()
                    try:
                        csv_path = "LearningProgress.csv"
                        if os.path.exists(csv_path):
                            df = pd.read_csv(csv_path)
                        else:
                            df = pd.DataFrame(columns=["email", "module", "completed"])
                        mask = (df["email"] == user_email) & (df["module"] == module)
                        if mask.any():
                            df.loc[mask, "completed"] = False
                        else:
                            new_row = pd.DataFrame([{"email": user_email, "module": module, "completed": False}])
                            df = pd.concat([df, new_row], ignore_index=True)
                        df.to_csv(csv_path, index=False)
                    except Exception as e:
                        print(f"Error updating CSV: {e}")
                    changes_made.append(f"⬜ Unmarked: {module}")
            updated_progress = get_user_learning_progress(user_email)
            completed_count = sum(1 for m in assigned_modules if updated_progress.get(m, False))
            total_modules = len(assigned_modules)
            progress_percentage = (completed_count / total_modules) * 100 if total_modules > 0 else 0
            st.success("Progress updated successfully!")
            for change in changes_made[:3]:
                st.write(f"• {change}")
            if len(changes_made) > 3:
                st.write(f"• ... and {len(changes_made) - 3} more changes")
            st.markdown(f"📊 **Progress Summary:**")
            st.markdown(f"- **{completed_count}/{total_modules}** modules completed ({progress_percentage:.1f}%)")
            if completed_count < total_modules:
                st.markdown(f"- **{total_modules - completed_count}** modules remaining")
                st.markdown(f"💡 *Check the boxes above and click 'Update Progress' to save your changes!*")
            else:
                st.markdown(f"🎉 **Congratulations! You've completed all your learning modules!**")
            # Clear checkbox keys to prevent stale values on rerun
            for k in module_checkboxes.values():
                if k in st.session_state:
                    del st.session_state[k]
            st.rerun()
    # Progress summary (outside the form, for first render)
    updated_progress = get_user_learning_progress(user_email)
    completed_count = sum(1 for m in assigned_modules if updated_progress.get(m, False))
    total_modules = len(assigned_modules)
    progress_percentage = (completed_count / total_modules) * 100 if total_modules > 0 else 0
    st.markdown(f"📊 **Progress Summary:**")
    st.markdown(f"- **{completed_count}/{total_modules}** modules completed ({progress_percentage:.1f}%)")
    if completed_count < total_modules:
        st.markdown(f"- **{total_modules - completed_count}** modules remaining")
        st.markdown(f"💡 *Check the boxes above and click 'Update Progress' to save your changes!*")
    else:
        st.markdown(f"🎉 **Congratulations! You've completed all your learning modules!**")

def format_required_software_response(user_email: str):
    """Format required software installations with interactive checkboxes and Update button"""
    required_software = get_user_required_software(user_email)
    progress_data = get_user_software_progress(user_email)
    
    if not required_software:
        st.markdown("No required software installations found for your profile.")
        return
    
    st.markdown("## 💻 Required Software & Tools")
    
    with st.form(key="software_form"):
        software_checkboxes = {}
        st.markdown("### 📦 **Software Installation Progress:**")
        for i, software in enumerate(required_software, 1):
            current_status = progress_data.get(software, False)
            col1, col2 = st.columns([0.1, 0.9])
            with col1:
                safe_soft = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(software).strip())[:100]
                checkbox_key = f"{user_email}_software_{i}_{safe_soft}"
                st.checkbox("✓", value=current_status, key=checkbox_key, label_visibility="hidden")
                software_checkboxes[software] = checkbox_key
            with col2:
                status_icon = "✅" if current_status else "⬜"
                st.write(f"{i}. {status_icon} 🔧 {software}")
        submitted = st.form_submit_button("🔄 Update Installation Status", use_container_width=True)
        if submitted:
            changes_made = []
            for software, checkbox_key in software_checkboxes.items():
                new_state = st.session_state.get(checkbox_key, False)
                mark_software_installed(user_email, software, new_state)
                # --- CSV sync for dashboard ---
                try:
                    csv_path = "SoftwareProgress.csv"
                    if os.path.exists(csv_path):
                        df = pd.read_csv(csv_path)
                    else:
                        df = pd.DataFrame(columns=["email", "software", "installed"])
                    mask = (df["email"] == user_email) & (df["software"] == software)
                    if mask.any():
                        df.loc[mask, "installed"] = new_state
                    else:
                        new_row = pd.DataFrame([{"email": user_email, "software": software, "installed": new_state}])
                        df = pd.concat([df, new_row], ignore_index=True)
                    df.to_csv(csv_path, index=False)
                except Exception as e:
                    print(f"Error updating SoftwareProgress.csv: {e}")
                if new_state:
                    changes_made.append(f"✅ Installed: {software}")
                else:
                    changes_made.append(f"⬜ Uninstalled: {software}")
            updated_progress = get_user_software_progress(user_email)
            installed_count = sum(1 for s in required_software if updated_progress.get(s, False))
            total_software = len(required_software)
            progress_percentage = (installed_count / total_software) * 100 if total_software > 0 else 0
            st.success("Software installation status updated successfully!")
            for change in changes_made[:3]:
                st.write(f"• {change}")
            if len(changes_made) > 3:
                st.write(f"• ... and {len(changes_made) - 3} more changes")
            st.markdown(f"📊 **Summary:**")
            st.markdown(f"- **{installed_count}/{total_software}** software packages installed ({progress_percentage:.1f}%)")
            if installed_count < total_software:
                st.markdown(f"- **{total_software - installed_count}** software packages remaining")
                st.markdown(f"💡 *Check the boxes above and click 'Update Installation Status' to save your changes! Contact IT support if you need help.*")
            else:
                st.markdown(f"🎉 **Great! You've installed all required software!**")
            # Clear checkbox keys to prevent stale values on rerun
            for k in software_checkboxes.values():
                if k in st.session_state:
                    del st.session_state[k]
            st.rerun()
    updated_progress = get_user_software_progress(user_email)
    installed_count = sum(1 for s in required_software if updated_progress.get(s, False))
    total_software = len(required_software)
    progress_percentage = (installed_count / total_software) * 100 if total_software > 0 else 0
    st.markdown(f"📊 **Summary:**")
    st.markdown(f"- **{installed_count}/{total_software}** software packages installed ({progress_percentage:.1f}%)")
    if installed_count < total_software:
        st.markdown(f"- **{total_software - installed_count}** software packages remaining")
        st.markdown(f"💡 *Check the boxes above and click 'Update Installation Status' to save your changes! Contact IT support if you need help.*")
    else:
        st.markdown(f"🎉 **Great! You've installed all required software!**")

def format_required_documents_response(user_email: str):
    """Format required documents to be signed with interactive checkboxes and Update button"""
    required_documents = get_user_required_documents(user_email)
    progress_data = get_user_document_progress(user_email)
    
    if not required_documents:
        st.markdown("No required documents found for your profile.")
        return
    
    st.markdown("## 📄 Required Documents to Sign")
    
    with st.form(key="documents_form"):
        document_checkboxes = {}
        st.markdown("### ✍️ **Document Signing Progress:**")
        for i, document in enumerate(required_documents, 1):
            current_status = progress_data.get(document, False)
            col1, col2 = st.columns([0.1, 0.9])
            with col1:
                safe_doc = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(document).strip())[:100]
                checkbox_key = f"{user_email}_document_{i}_{safe_doc}"
                st.checkbox("✓", value=current_status, key=checkbox_key, label_visibility="hidden")
                document_checkboxes[document] = checkbox_key
            with col2:
                status_icon = "✅" if current_status else "⬜"
                st.write(f"{i}. {status_icon} 📋 {document}")
        submitted = st.form_submit_button("🔄 Update Signing Status", use_container_width=True)
        if submitted:
            changes_made = []
            for document, checkbox_key in document_checkboxes.items():
                new_state = st.session_state.get(checkbox_key, False)
                mark_document_signed(user_email, document, new_state)
                # --- CSV sync for dashboard ---
                try:
                    csv_path = "DocumentProgress.csv"
                    if os.path.exists(csv_path):
                        df = pd.read_csv(csv_path)
                    else:
                        df = pd.DataFrame(columns=["email", "document", "signed"])
                    mask = (df["email"] == user_email) & (df["document"] == document)
                    if mask.any():
                        df.loc[mask, "signed"] = new_state
                    else:
                        new_row = pd.DataFrame([{"email": user_email, "document": document, "signed": new_state}])
                        df = pd.concat([df, new_row], ignore_index=True)
                    df.to_csv(csv_path, index=False)
                except Exception as e:
                    print(f"Error updating DocumentProgress.csv: {e}")
                if new_state:
                    changes_made.append(f"✅ Signed: {document}")
                else:
                    changes_made.append(f"⬜ Unsigned: {document}")
            updated_progress = get_user_document_progress(user_email)
            signed_count = sum(1 for d in required_documents if updated_progress.get(d, False))
            total_documents = len(required_documents)
            progress_percentage = (signed_count / total_documents) * 100 if total_documents > 0 else 0
            st.success("Document signing status updated successfully!")
            for change in changes_made[:3]:
                st.write(f"• {change}")
            if len(changes_made) > 3:
                st.write(f"• ... and {len(changes_made) - 3} more changes")
            st.markdown(f"📊 **Summary:**")
            st.markdown(f"- **{signed_count}/{total_documents}** documents signed ({progress_percentage:.1f}%)")
            if signed_count < total_documents:
                st.markdown(f"- **{total_documents - signed_count}** documents need to be signed")
                st.markdown(f"💡 *Check the boxes above and click 'Update Signing Status' to save your changes! Contact HR if you need help with the signing process.*")
            else:
                st.markdown(f"🎉 **Excellent! You've signed all required documents!**")
            
            # Update the chat history with the correct count after form submission
            updated_progress = get_user_document_progress(user_email)
            signed_count = sum(1 for d in required_documents if updated_progress.get(d, False))
            total_documents = len(required_documents)
            updated_summary = f"Showed interactive documents signing list: {signed_count}/{total_documents} signed"
            st.session_state.all_messages[user_email].append(AIMessage(updated_summary))
            save_message(user_email, "assistant", updated_summary)
            # Clear checkbox keys to prevent stale values on rerun
            for k in document_checkboxes.values():
                if k in st.session_state:
                    del st.session_state[k]

            st.rerun()
    updated_progress = get_user_document_progress(user_email)
    signed_count = sum(1 for d in required_documents if updated_progress.get(d, False))
    total_documents = len(required_documents)
    progress_percentage = (signed_count / total_documents) * 100 if total_documents > 0 else 0
    st.markdown(f"📊 **Summary:**")
    st.markdown(f"- **{signed_count}/{total_documents}** documents signed ({progress_percentage:.1f}%)")
    if signed_count < total_documents:
        st.markdown(f"- **{total_documents - signed_count}** documents need to be signed")
        st.markdown(f"💡 *Check the boxes above and click 'Update Signing Status' to save your changes! Contact HR if you need help with the signing process.*")
    else:
        st.markdown(f"🎉 **Excellent! You've signed all required documents!**")

    # Save summary message to chat history only if form wasn't submitted
    if 'submitted' not in locals() or not submitted:
        summary_response = f"Showed interactive documents signing list: {signed_count}/{total_documents} signed"
        st.session_state.all_messages[user_email].append(AIMessage(summary_response))
        save_message(user_email, "assistant", summary_response)


# Initialize learning progress system (ensure tables then conditionally load CSV)
try:
    ensure_all_progress_tables()
except Exception:
    pass
load_learning_progress_from_csv()
#NEWLY ADDED ABOVE (NEHA) 1/2

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

            # --- Special case: learning modules/progress query ---
            learning_keywords = [
                "learning modules", "my modules", "modules to complete", "learning progress",
                "what modules", "which modules", "modules i need", "my learning",
                "training modules", "course modules", "modules assigned"
            ]
            if any(keyword in prompt.lower() for keyword in learning_keywords):
                with st.chat_message("assistant"):
                    format_learning_progress_response(user_email)

                # Save a text summary for chat history
                assigned_modules = get_user_assigned_modules(user_email)
                progress_data = get_user_learning_progress(user_email)
                completed_count = sum(1 for m in assigned_modules if progress_data.get(m, False))
                summary_response = f"Showed interactive learning modules list: {completed_count}/{len(assigned_modules)} completed"
                st.session_state.all_messages[user_email].append(AIMessage(summary_response))
                save_message(user_email, "assistant", summary_response)
            
            # --- Special case: required software query ---
            elif any(keyword in prompt.lower() for keyword in [
                "required software", "software to install", "what software", "which software",
                "software needed", "installations", "tools to install", "required tools",
                "software requirements", "install software", "software list"
            ]):
                with st.chat_message("assistant"):
                    format_required_software_response(user_email)

                # Save a text summary for chat history
                required_software = get_user_required_software(user_email)
                progress_data = get_user_software_progress(user_email)
                installed_count = sum(1 for s in required_software if progress_data.get(s, False))
                summary_response = f"Showed interactive software installation list: {installed_count}/{len(required_software)} installed"
                st.session_state.all_messages[user_email].append(AIMessage(summary_response))
                save_message(user_email, "assistant", summary_response)
            
            # --- Special case: required documents query ---
            elif any(keyword in prompt.lower() for keyword in [
                "required documents", "documents to sign", "what documents", "which documents",
                "documents needed", "paperwork", "forms to sign", "required paperwork",
                "document requirements", "sign documents", "document list"
            ]):
                with st.chat_message("assistant"):
                    format_required_documents_response(user_email)

                # Don't save summary message here - let the form update handle it
                # The summary will be saved after form submission with correct count
                
            # --- Special case: show mentee bookings ---
            elif "my bookings" in prompt.lower() or "past bookings" in prompt.lower():
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
                
                # Mark module as completed using our new function
                mark_module_completed(user_email, completed_module)
                
                # Show confirmation message
                confirmation_msg = f"✅ Great! I've marked '{completed_module}' as completed. Keep up the great work!"
                with st.chat_message("assistant"):
                    st.markdown(confirmation_msg)
                st.session_state.all_messages[user_email].append(AIMessage(confirmation_msg))
                save_message(user_email, "assistant", confirmation_msg) 

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

