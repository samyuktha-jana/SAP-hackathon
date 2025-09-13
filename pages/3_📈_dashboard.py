import streamlit as st
import pandas as pd
import os

from utils import notifications_panel

# Inside the page (after login check)
if st.session_state.user:
    notifications_panel(st.session_state.user)


# 🚨 Block page if no login
if "user" not in st.session_state or not st.session_state.user:
    st.warning("⚠️ Please login from the Homepage first.")
    st.stop()

st.title("📈 SAP360 Hub Dashboard")

# --- Load onboarding data ---
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
employee_path = os.path.join(BASE_DIR, "/Users/samyuktha/Desktop/SAP-hackathon/Employee Dataset1.csv")
progress_path = os.path.join(BASE_DIR, "LearningProgress.csv")

# --- SAP Summary ---
with st.expander("ℹ️ What is SAP?"):
    st.markdown("""
**SAP** (Systems, Applications, and Products in Data Processing) is a global leader in enterprise software for business operations and customer relations.  
SAP solutions help organizations streamline processes, improve data management, and drive digital transformation across industries.
""")

# --- Show only the logged-in user's employee info ---
try:
    employee_df = pd.read_csv(employee_path).fillna("")
    employee_df.columns = employee_df.columns.str.strip()
    st.subheader("👤 Your Employee Profile")
    user_email = st.session_state.user["email"].strip().lower()

    # Normalize the email column
    employee_df["email"] = employee_df["email"].astype(str).str.strip().str.lower()
    user_row = employee_df[employee_df["email"] == user_email]

    if not user_row.empty:
        show_cols = ["Name", "email", "Department", "Team", "Position"]
        user_info = user_row.iloc[0][show_cols]
        user_info = user_info.rename({"email": "Email"})
        st.table(pd.DataFrame(user_info).reset_index().rename(columns={"index": "Field", 0: "Value"}))
    else:
        st.info("Your employee profile was not found. Please log in with the email used in Employee Dataset.csv.")
        st.write("Logged in email:", user_email)
        st.write("All emails in CSV:", employee_df["email"].tolist())
except Exception as e:
    st.error(f"Could not load employee data: {e}")

# --- Learning Modules Section ---
st.subheader("📚 Learning Modules")
try:
    if not user_row.empty:
        modules_str = user_row.iloc[0]["Learning Modules"]
        modules = [m.strip() for m in str(modules_str).split(",") if m.strip()]
        if modules:
            st.markdown("**Modules assigned to you:**")
            # --- Load or create progress CSV ---
            if os.path.exists(progress_path):
                progress_df = pd.read_csv(progress_path)
            else:
                progress_df = pd.DataFrame(columns=["email", "module", "completed"])
            # Normalize
            progress_df["email"] = progress_df["email"].astype(str).str.strip().str.lower()
            progress_df["module"] = progress_df["module"].astype(str).str.strip()
            # Build a dict for current user
            user_progress = {row["module"]: bool(row["completed"]) for _, row in progress_df[progress_df["email"] == user_email].iterrows()}
            updated_progress = {}
            completed_count = 0
            for module in modules:
                completed = user_progress.get(module, False)
                checked = st.checkbox(module, value=completed, key=f"{user_email}_{module}")
                updated_progress[module] = checked
                if checked:
                    completed_count += 1
            # Save progress if changed
            for module, checked in updated_progress.items():
                mask = (progress_df["email"] == user_email) & (progress_df["module"] == module)
                if mask.any():
                    if progress_df.loc[mask, "completed"].values[0] != checked:
                        progress_df.loc[mask, "completed"] = checked
                else:
                    progress_df = pd.concat([progress_df, pd.DataFrame([{"email": user_email, "module": module, "completed": checked}])], ignore_index=True)
            progress_df.to_csv(progress_path, index=False)
            # Progress chart
            st.markdown("**Progress:**")
            st.progress(completed_count / len(modules), text=f"{completed_count} / {len(modules)} modules completed")
        else:
            st.info("No learning modules found for your profile.")
    else:
        st.info("No learning modules found for your profile.")
except Exception as e:
    st.info("Could not load learning modules.")



