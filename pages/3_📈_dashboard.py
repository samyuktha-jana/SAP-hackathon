import streamlit as st
import pandas as pd
import os
import sqlite3
import json
from datetime import datetime,timedelta,date
from utils import notifications_panel
from utils import hydrate_session_from_json, persist_session_to_json
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import plotly.figure_factory as ff


# after login check
hydrate_session_from_json()

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="SAP360 Hub Dashboard", layout="wide")

# ---------------- LOGIN CHECK ----------------
if "user" in st.session_state and st.session_state["user"]:
    notifications_panel(st.session_state["user"])


if "user" not in st.session_state or not st.session_state.user:
    st.warning("⚠️ Please login from the Homepage first.")
    st.stop()

user_email = st.session_state.user["email"].strip().lower()

st.title("📊 SAP360 Hub Dashboard")


# ---------------- DB CONNECTION ----------------
DB_PATH = "mentormatch.db"
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def get_confirmed_bookings(mentee_email: str, upcoming_only=True):
    con = _conn()
    now = datetime.utcnow().isoformat()
    query = """
        SELECT s.id, s.start_utc, s.end_utc, s.status, s.location,
               u.name AS mentor_name, u.email AS mentor_email, u.position AS mentor_position
        FROM sessions s
        JOIN users u ON s.mentor_id = u.ID
        WHERE s.mentee_email = ?
          AND s.status IN ('approved', 'booked')
    """
    params = [mentee_email]
    if upcoming_only:
        query += " AND s.start_utc >= ?"
        params.append(now)
    query += " ORDER BY s.start_utc ASC"
    rows = con.execute(query, tuple(params)).fetchall()
    con.close()
    return [dict(r) for r in rows]

# ---------------- EMPLOYEE DATA ----------------
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
employee_path = os.path.join(BASE_DIR, "Employee Dataset1.csv")
progress_path = os.path.join(BASE_DIR, "LearningProgress.csv")

try:
    employee_df = pd.read_csv(employee_path).fillna("")
    employee_df.columns = employee_df.columns.str.strip()
    employee_df["email"] = employee_df["email"].astype(str).str.strip().str.lower()
    user_row = employee_df[employee_df["email"] == user_email]
except Exception:
    user_row = pd.DataFrame()

# ---------------- DASHBOARD LAYOUT ----------------
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "👤 Profile",
    "📚 Onboarding Learning Modules",
    "📅 Mentor Sessions",
    "📈 Learning Hub progress",
    "🚀 Career Progress"
])

# --- TAB 1: Profile ---
with tab1:
    st.subheader("👤 Your Employee Profile")
    if not user_row.empty:
        show_cols = ["Name", "email", "Department", "Team", "Position"]
        user_info = user_row.iloc[0][show_cols].rename({"email": "Email"})
        st.table(pd.DataFrame(user_info).reset_index().rename(columns={"index": "Field", 0: "Value"}))
    else:
        st.info("Profile not found. Please check your login email.")

# --- TAB 2: Learning Modules ---
with tab2:
    st.subheader("📚 Required Learning Modules")
    try:
        if not user_row.empty:
            modules_str = user_row.iloc[0].get("Learning Modules", "")
            modules = [m.strip() for m in str(modules_str).split(",") if m.strip()]
            if modules:
                st.markdown("**Modules assigned to you:**")
                if os.path.exists(progress_path):
                    progress_df = pd.read_csv(progress_path)
                else:
                    progress_df = pd.DataFrame(columns=["email", "module", "completed"])
                progress_df["email"] = progress_df["email"].astype(str).str.strip().str.lower()
                progress_df["module"] = progress_df["module"].astype(str).str.strip()
                user_progress = {
                    row["module"]: bool(row["completed"])
                    for _, row in progress_df[progress_df["email"] == user_email].iterrows()
                }
                updated_progress, completed_count = {}, 0
                for module in modules:
                    completed = user_progress.get(module, False)
                    checked = st.checkbox(module, value=completed, key=f"{user_email}_{module}")
                    updated_progress[module] = checked
                    if checked: completed_count += 1
                for module, checked in updated_progress.items():
                    mask = (progress_df["email"] == user_email) & (progress_df["module"] == module)
                    if mask.any():
                        if progress_df.loc[mask, "completed"].values[0] != checked:
                            progress_df.loc[mask, "completed"] = checked
                    else:
                        progress_df = pd.concat([progress_df, pd.DataFrame([{
                            "email": user_email, "module": module, "completed": checked
                        }])], ignore_index=True)
                progress_df.to_csv(progress_path, index=False)

                # Metrics + Progress Bar
                c1, c2 = st.columns(2)
                c1.metric("Modules Completed", f"{completed_count}/{len(modules)}")
                c2.progress(completed_count / len(modules))
            else:
                st.info("No learning modules found for your profile.")
        else:
            st.info("No learning modules found.")
    except Exception as e:
        st.error(f"Could not load learning modules: {e}")

# --- TAB 3: Mentor Sessions ---
with tab3:
    st.subheader("📅 My Upcoming Mentor Sessions")
    try:
        bookings = get_confirmed_bookings(user_email, upcoming_only=True)
        if not bookings:
            st.info("You have no upcoming confirmed mentor sessions.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Upcoming Sessions", len(bookings))
            c2.metric("Next Session", bookings[0]["start_utc"].split("T")[0])
            c3.metric("Mentors", len(set(b["mentor_email"] for b in bookings)))

            st.markdown("---")
            for b in bookings:
                try:
                    start = datetime.fromisoformat(b["start_utc"].replace("Z", "")).strftime("%b %d, %Y %I:%M %p")
                    end = datetime.fromisoformat(b["end_utc"].replace("Z", "")).strftime("%I:%M %p")
                except Exception:
                    start, end = b["start_utc"], b["end_utc"]
                st.markdown(
                    f"""
                    **🧑 Mentor:** {b['mentor_name']} ({b['mentor_email']})  
                    **💼 Role:** {b['mentor_position']}  
                    **🕒 Time:** {start} → {end}  
                    **📍 Location:** {b['location'] or "Not specified"}  
                    **✅ Status:** {b['status'].capitalize()}
                    """
                )
                st.divider()
    except Exception as e:
        st.error(f"Could not load mentor bookings: {e}")

# --------------------------------------------------
# TAB 4: Upskilling Modules (Coursera-style layout)
# --------------------------------------------------



def generate_plan_pdf(plan_text: str, role: str, accepted_at: str, overall_pct: int, completed_phases: int, total_phases: int) -> BytesIO:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "Upskilling Plan Report")

    # Metadata
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 80, f"Role: {role}")
    c.drawString(50, height - 100, f"Accepted at: {accepted_at}")
    c.drawString(50, height - 120, f"Overall Progress: {overall_pct}% ({completed_phases}/{total_phases} phases completed)")

    # Content
    text = c.beginText(50, height - 160)
    text.setFont("Helvetica", 11)
    for line in plan_text.splitlines():
        text.textLine(line)
    c.drawText(text)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer



with tab4:
    
    chosen_plan = st.session_state.get("chosen_upskillingplan")
    role = st.session_state.get("accepted_plan_role")
    accepted_at = st.session_state.get("accepted_at")
    pt = st.session_state.get("progress_tracker", {})

    if not chosen_plan:
        st.info("No plan accepted yet. Please generate and accept one in the Learning Hub.")
    else:
        # ---------------------------
        # Plan metadata
        # ---------------------------
        st.markdown("### 🎯 Your Upskilling Plan")
        st.markdown(f"**Role:** {role}")
        st.markdown(f"**Accepted (UTC):** {accepted_at}")

        st.divider()

        phase_weeks = pt.get("phase_weeks", {})
        phase_status = pt.get("phase_status", {})
        start_date = pt.get("start_date")

        # ---------------------------
        # Overall progress
        # ---------------------------
        completed_phases = sum(1 for p in phase_status.values() if p.get("completed"))
        total_phases = len(phase_weeks) or 1
        overall_pct = int(completed_phases / total_phases * 100)

        # Current week since start
        current_week = None
        if start_date:
            if isinstance(start_date, str):
                try:
                    start_date = datetime.fromisoformat(start_date).date()
                except Exception:
                    start_date = None
            if isinstance(start_date, date):
                delta_days = (date.today() - start_date).days
                current_week = delta_days // 7 + 1

        if current_week:
            st.markdown(f"📅 **You are currently in Week {current_week} since start.**")

        

        # ---------------------------
        # Altair Gantt-style chart
        # ---------------------------
        if start_date and isinstance(start_date, date):
            tasks = []
            current_start = start_date

            for phase_no in sorted(phase_weeks.keys(), key=lambda x: int(x)):
                weeks = phase_weeks[phase_no]
                status = phase_status.get(phase_no, {})
                done = status.get("completed", False)

                phase_start = current_start
                phase_end = current_start + timedelta(weeks=weeks)
                current_start = phase_end

                tasks.append(dict(
                    Phase=f"Phase {phase_no}",
                    Start=phase_start,
                    End=phase_end,
                    Weeks=weeks,
                    Completed=done
                ))

            df = pd.DataFrame(tasks)
            today_df = pd.DataFrame([{"Today": date.today()}])

            import altair as alt
            bar_chart = (
                alt.Chart(df)
                .mark_bar(cornerRadius=5)
                .encode(
                    x="Start:T",
                    x2="End:T",
                    y=alt.Y("Phase:N", sort=None),
                    color=alt.condition(
                        alt.datum.Completed,
                        alt.value("#2e7d32"),  # green if completed
                        alt.value("#f9a825")   # yellow if pending
                    ),
                    tooltip=["Phase", "Start", "End", "Weeks", "Completed"]
                )
            )

            today_line = (
                alt.Chart(today_df)
                .mark_rule(color="red", strokeWidth=2)
                .encode(x="Today:T")
            )

            st.altair_chart(bar_chart + today_line, use_container_width=True)
            st.caption("✅ Green = Completed | 🟡 Pending | 🔴 Red line = Today")
            st.divider()
        # ---------------------------
        # Collapsible details per phase
        # ---------------------------
        for phase_no in sorted(phase_weeks.keys(), key=lambda x: int(x)):
            weeks = phase_weeks[phase_no]
            status = phase_status.get(phase_no, {})
            done = status.get("completed", False)

            label = f"Phase {phase_no} ({weeks} weeks)"
            label = f"✅ {label}" if done else f"⏳ {label}"

            with st.expander(label, expanded=False):
                import re
                phase_pattern = re.compile(rf"(?i)phase {phase_no}.*?(?=Phase \d+|$)", re.S)
                match = phase_pattern.search(chosen_plan)
                if match:
                    st.markdown(match.group(0))
                else:
                    st.info("No detailed content found for this phase.")

                if st.button(f"Mark Phase {phase_no} Complete", key=f"tab4_mark_{phase_no}"):
                    status["completed"] = True
                    status["completed_at"] = datetime.utcnow().isoformat()
                    st.session_state["progress_tracker"]["phase_status"][phase_no] = status
                    st.success(f"Phase {phase_no} marked complete!")
                    st.rerun()

        st.divider()

        # ---------------------------
        # Download PDF
        # ---------------------------
        pdf_buffer = generate_plan_pdf(
            chosen_plan, role, accepted_at,
            overall_pct, completed_phases, total_phases
        )
        st.download_button(
            "📥 Download Full Plan (PDF)",
            data=pdf_buffer,
            file_name="upskilling_plan.pdf",
            mime="application/pdf"
        )


# --- TAB 5: Career Progress ---
with tab5:
    st.subheader("🚗 Career Journey Tracker")

    # --- Learning Modules Progress ---
    try:
        total_modules = len(modules)
        completed_modules = completed_count
        modules_pct = completed_modules / total_modules if total_modules else 0
    except Exception:
        modules_pct = 0

    # --- Upskilling Plan Progress ---
    plan_tracker = st.session_state.get("progress_tracker", {})
    phases = plan_tracker.get("phase_status", {})
    completed_phases = sum(1 for p in phases.values() if p.get("completed"))
    total_phases = len(phases) if phases else 0
    plan_pct = completed_phases / total_phases if total_phases else 0

    # --- Mentor Engagement ---
    con = _conn()
    past_sessions = con.execute(
        "SELECT COUNT(*) FROM sessions WHERE mentee_email=? AND status IN ('approved','booked') AND end_utc < ?",
        (user_email, datetime.utcnow().isoformat())
    ).fetchone()[0]
    total_sessions = con.execute(
        "SELECT COUNT(*) FROM sessions WHERE mentee_email=? AND status IN ('approved','booked')",
        (user_email,)
    ).fetchone()[0]
    con.close()
    mentor_pct = past_sessions / total_sessions if total_sessions else 0

    # --- Weighted Score ---
    career_score = (modules_pct * 0.4 + plan_pct * 0.4 + mentor_pct * 0.2) * 100

    # -------------------------
    # Realistic Speedometer Gauges
    # -------------------------
    import streamlit.components.v1 as components
    import math

    def gauge(label, value, color):
        angle = 180 - (value * 180 / 100)
        x = 60 + 40 * math.cos(math.radians(angle))
        y = 70 - 40 * math.sin(math.radians(angle))

        ticks = ""
        for t in range(0, 101, 20):
            t_angle = 180 - (t * 180 / 100)
            tx1 = 60 + 45 * math.cos(math.radians(t_angle))
            ty1 = 70 - 45 * math.sin(math.radians(t_angle))
            tx2 = 60 + 55 * math.cos(math.radians(t_angle))
            ty2 = 70 - 55 * math.sin(math.radians(t_angle))
            ticks += f'<line x1="{tx1:.1f}" y1="{ty1:.1f}" x2="{tx2:.1f}" y2="{ty2:.1f}" stroke="#333" stroke-width="2"/>'

        return f"""
        <div style="text-align:center; margin:10px; display:inline-block; font-family:'Poppins', sans-serif;font-weight: bold;">
        <svg width="120" height="80" viewBox="0 0 120 80">
            <path d="M10 70 A50 50 0 0 1 110 70" fill="none" stroke="#ccc" stroke-width="10"/>
            {ticks}
            <line x1="60" y1="70" x2="{x:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="3"/>
            <circle cx="60" cy="70" r="5" fill="#000"/>
        </svg>
        <p style="margin:0; font-size:14px;">{label}<br><b>{value:.0f}%</b></p>
        </div>
        """

    gauges_html = f"""
    <div style="display:flex; justify-content:center;">
        {gauge("Modules", modules_pct*100, "#2e7d32")}
        {gauge("Upskilling Plan", plan_pct*100, "#1565c0")}
        {gauge("Mentoring", mentor_pct*100, "#f9a825")}
    </div>
    """
    components.html(gauges_html, height=150)

    

    st.markdown("---")

    # -------------------------
    # Road + Car Progress
    # -------------------------
    # Car X position (move between 30 and 430 as score goes 0 → 100)
    car_x = 30 + (career_score / 100) * 400

    road_html = f"""
        <div style="text-align:center; margin-top:20px;">
        <svg width="100%" height="120" viewBox="0 0 500 120">
            <!-- road -->
            <rect x="20" y="60" width="460" height="30" fill="#555"/>
            <line x1="30" y1="75" x2="470" y2="75" stroke="yellow" stroke-width="3" stroke-dasharray="15,15"/>
            
            <!-- car body -->
            <rect x="{car_x}" y="15" width="70" height="40" rx="5" ry="5" fill="red"/>

            <!-- car windows -->
            <rect x="{car_x + 8}" y="20" width="22" height="18"
                fill="lightblue" stroke="black" stroke-width="1" rx="3" ry="3"/>
            <rect x="{car_x + 40}" y="20" width="22" height="18"
                fill="lightblue" stroke="black" stroke-width="1" rx="3" ry="3"/>
            
            <!-- wheels -->
            <circle cx="{car_x + 15}" cy="60" r="10" fill="black"/>
            <circle cx="{car_x + 55}" cy="60" r="10" fill="black"/>
        </svg>
        </div>
        """

    components.html(road_html, height=160)

    # -------------------------
    # Stage message
    # -------------------------
    if career_score < 25:
        stage = "🚦 Just starting — Keep moving!"
    elif 25 <= career_score < 50:
        stage = "🏍️ Gaining speed — You're on your way!"
    elif 50>career_score< 75:
        stage = "🏎️ Halfway there — Making progress!"
    elif 75>career_score < 99:
        stage = "🚗 Almost there — Stay consistent!"
    else:
        stage = "🏁 Almost at the finish line!"
    
    st.success(stage)


persist_session_to_json()
