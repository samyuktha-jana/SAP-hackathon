import os
import re
import json
import pandas as pd
from datetime import datetime, date, timedelta

from rapidfuzz import fuzz, process
import streamlit as st
try:
    import importlib
    _vader_mod = importlib.import_module("vaderSentiment.vaderSentiment")
    SentimentIntensityAnalyzer = getattr(_vader_mod, "SentimentIntensityAnalyzer", None)
except Exception:
    SentimentIntensityAnalyzer = None
import google.generativeai as genai
from dotenv import load_dotenv

from utils import notifications_panel
from utils import hydrate_session_from_json, persist_session_to_json

# after login check
hydrate_session_from_json()


# ==================================================
# AUTH / NOTIFICATIONS (top so failures stop early)
# ==================================================
if "user" in st.session_state and st.session_state.user:
    notifications_panel(st.session_state.user)

# --------------------------------------------------
# Page Guard
# --------------------------------------------------
# st.set_page_config(page_title="Role Skill Gap Chatbot", layout="wide")

if "user" not in st.session_state or not st.session_state.user:
    st.warning("⚠️ Please login from the Homepage first.")
    st.stop()

# --------------------------------------------------
# Title
# --------------------------------------------------
st.title("Learning Hub")

# Lightweight UI polish for cards, chips, subtle backgrounds
def _inject_custom_css():
    st.markdown(
        """
        <style>
        .lh-card { border: 1px solid #e6e9ef; border-radius: 10px; padding: 14px 16px; background: #ffffffaa; box-shadow: 0 1px 2px rgba(0,0,0,0.03);} 
        .lh-chip { display:inline-block; padding:4px 10px; margin:4px 6px 0 0; border-radius: 999px; background:#f1f5f9; font-size:12px; color:#334155; border:1px solid #e2e8f0;}
        .lh-chip-strong { background:#eef2ff; color:#3730a3; border-color:#c7d2fe;}
        .lh-section { margin-top: 0.75rem; margin-bottom: 0.25rem; font-weight:600; color:#0f172a; }
        .lh-muted { color:#64748b; font-size: 0.9rem; }
        .lh-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:12px; }
        .lh-pill { display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:8px; background:#f8fafc; border:1px solid #e2e8f0; font-size:12px; color:#0f172a;}
        .lh-divider { height:1px; background:linear-gradient(90deg, #ffffff, #e5e7eb, #ffffff); margin:12px 0; }
        .lh-small { font-size:12px; color:#64748b; }
        </style>
        """,
        unsafe_allow_html=True,
    )

_inject_custom_css()

# ==============================
# QUICK CONFIG FLAGS
# ==============================
HIDE_GAP_DISTRIBUTION_CHART = False                # Hide small bar chart in Gap Summary if True
SHOW_DETAILED_GAP_TABLE_BY_DEFAULT = False         # Gap summary table expander open by default
SHOW_CHECKPOINT_SECTION = False                    # If False, completely hide the old Checkpoints list UI
SHOW_CHECKPOINT_FILTERS = False                    # (Only used if SHOW_CHECKPOINT_SECTION=True)
SHOW_CHECKPOINT_DETAILS = False                    # (Only used if SHOW_CHECKPOINT_SECTION=True)
SHOW_CHECKPOINT_DATES_INLINE = False               # (Only used if SHOW_CHECKPOINT_SECTION=True)
PHASE_COMPLETION_CHECKBOXES_ENABLED = True         # Master switch for phase completion controls
USE_PHASE_COMPLETION_FOR_OVERALL_PROGRESS = True   # If True, overall completion bar is phase-based

# --------------------------------------------------
# Environment & LLM Config
# --------------------------------------------------
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
MODEL_NAME = "gemini-1.5-flash"
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
    except Exception as _e:
        st.warning("Could not configure Gemini API (check key).")

# --------------------------------------------------
# Data Loading / Sample Data
# --------------------------------------------------
@st.cache_data
def load_role_skill_data():
    data = [
        {"Role": "Data Analyst", "Skill": "SQL", "Required_Level": 4, "Skill_Category": "Data", "Weight": 1.0, "Description": "Advanced SQL for analytics", "Recommended_Courses": "BI101;BI201"},
        {"Role": "Data Analyst", "Skill": "Power BI", "Required_Level": 3, "Skill_Category": "Visualization", "Weight": 0.8, "Description": "Build dashboards & reports", "Recommended_Courses": "BI102"},
        {"Role": "Data Analyst", "Skill": "Excel", "Required_Level": 4, "Skill_Category": "Data", "Weight": 0.7, "Description": "Data manipulation & pivot tables", "Recommended_Courses": "BI103"},
        {"Role": "Data Analyst", "Skill": "Python", "Required_Level": 3, "Skill_Category": "Programming", "Weight": 0.8, "Description": "Python for data cleaning and analysis", "Recommended_Courses": "BI104"},

        {"Role": "SAP BTP Development Engineer", "Skill": "SAP BTP", "Required_Level": 4, "Skill_Category": "Platform", "Weight": 1.0, "Description": "Develop & deploy on SAP BTP", "Recommended_Courses": "BTP101"},
        {"Role": "SAP BTP Development Engineer", "Skill": "Java", "Required_Level": 3, "Skill_Category": "Programming", "Weight": 0.7, "Description": "Back-end services on BTP", "Recommended_Courses": "BTP102"},
        {"Role": "SAP BTP Development Engineer", "Skill": "CAP (Cloud Application Programming)", "Required_Level": 3, "Skill_Category": "Framework", "Weight": 0.7, "Description": "Model and build apps on SAP BTP", "Recommended_Courses": "BTP103"},
        {"Role": "SAP BTP Development Engineer", "Skill": "Python", "Required_Level": 2, "Skill_Category": "Programming", "Weight": 0.4, "Description": "Basic scripting for automation", "Recommended_Courses": "BTP104"},

        {"Role": "SAP Intelligent ERP Engineer", "Skill": "SAP S/4HANA", "Required_Level": 4, "Skill_Category": "ERP", "Weight": 1.0, "Description": "Configure & customize S/4HANA", "Recommended_Courses": "ERP101"},
        {"Role": "SAP Intelligent ERP Engineer", "Skill": "ABAP", "Required_Level": 3, "Skill_Category": "Programming", "Weight": 0.8, "Description": "Develop custom ERP logic", "Recommended_Courses": "ERP102"},
        {"Role": "SAP Intelligent ERP Engineer", "Skill": "Fiori", "Required_Level": 3, "Skill_Category": "UI", "Weight": 0.7, "Description": "Design Fiori apps for ERP", "Recommended_Courses": "ERP103"},
        {"Role": "SAP Intelligent ERP Engineer", "Skill": "Excel", "Required_Level": 2, "Skill_Category": "Data", "Weight": 0.4, "Description": "Use Excel for reporting and analysis", "Recommended_Courses": "ERP104"},

        {"Role": "CX AI Solutions Engineer", "Skill": "SAP C4C", "Required_Level": 3, "Skill_Category": "CRM", "Weight": 0.8, "Description": "Customer Experience Cloud", "Recommended_Courses": "CX101"},
        {"Role": "CX AI Solutions Engineer", "Skill": "AI/ML", "Required_Level": 3, "Skill_Category": "AI", "Weight": 0.7, "Description": "Embed AI for CX", "Recommended_Courses": "CX102"},
        {"Role": "CX AI Solutions Engineer", "Skill": "Integration", "Required_Level": 3, "Skill_Category": "API", "Weight": 0.6, "Description": "Integrate CX platforms", "Recommended_Courses": "CX103"},
        {"Role": "CX AI Solutions Engineer", "Skill": "Python", "Required_Level": 2, "Skill_Category": "Programming", "Weight": 0.4, "Description": "Basic Python for AI integration", "Recommended_Courses": "CX104"},

        {"Role": "S/4HANA Cloud Engineer", "Skill": "SAP S/4HANA Cloud", "Required_Level": 4, "Skill_Category": "ERP", "Weight": 1.0, "Description": "Cloud ERP configuration", "Recommended_Courses": "S4C101"},
        {"Role": "S/4HANA Cloud Engineer", "Skill": "ABAP", "Required_Level": 3, "Skill_Category": "Programming", "Weight": 0.7, "Description": "Cloud ABAP development", "Recommended_Courses": "S4C102"},
        {"Role": "S/4HANA Cloud Engineer", "Skill": "Cloud Integration", "Required_Level": 3, "Skill_Category": "Integration", "Weight": 0.7, "Description": "Integrate with other cloud apps", "Recommended_Courses": "S4C103"},
        {"Role": "S/4HANA Cloud Engineer", "Skill": "Excel", "Required_Level": 2, "Skill_Category": "Data", "Weight": 0.4, "Description": "Basic reporting with Excel", "Recommended_Courses": "S4C104"},

        {"Role": "SAP Joule Copilot Engineer", "Skill": "SAP Joule", "Required_Level": 4, "Skill_Category": "AI", "Weight": 1.0, "Description": "Develop & configure Joule Copilot", "Recommended_Courses": "Joule101"},
        {"Role": "SAP Joule Copilot Engineer", "Skill": "Conversational AI", "Required_Level": 3, "Skill_Category": "AI", "Weight": 0.8, "Description": "Design dialogue & flows", "Recommended_Courses": "Joule102"},
        {"Role": "SAP Joule Copilot Engineer", "Skill": "API Integration", "Required_Level": 3, "Skill_Category": "API", "Weight": 0.6, "Description": "Integrate Copilot with SAP APIs", "Recommended_Courses": "Joule103"},
        {"Role": "SAP Joule Copilot Engineer", "Skill": "Python", "Required_Level": 2, "Skill_Category": "Programming", "Weight": 0.4, "Description": "Basic Python for conversational AI", "Recommended_Courses": "Joule104"},

        {"Role": "Supply Chain Intelligence Engineer", "Skill": "SAP IBP", "Required_Level": 4, "Skill_Category": "Supply Chain", "Weight": 1.0, "Description": "Integrated Business Planning", "Recommended_Courses": "SC101"},
        {"Role": "Supply Chain Intelligence Engineer", "Skill": "Analytics", "Required_Level": 3, "Skill_Category": "Data", "Weight": 0.8, "Description": "Analyze supply chain data", "Recommended_Courses": "SC102"},
        {"Role": "Supply Chain Intelligence Engineer", "Skill": "Python", "Required_Level": 3, "Skill_Category": "Programming", "Weight": 0.6, "Description": "Automate supply chain tasks", "Recommended_Courses": "SC103"},
        {"Role": "Supply Chain Intelligence Engineer", "Skill": "Excel", "Required_Level": 2, "Skill_Category": "Data", "Weight": 0.4, "Description": "Basic supply chain analysis in Excel", "Recommended_Courses": "SC104"},

        {"Role": "SAP Industry Cloud Engineer", "Skill": "SAP Industry Cloud", "Required_Level": 4, "Skill_Category": "Cloud", "Weight": 1.0, "Description": "Industry-specific cloud solutions", "Recommended_Courses": "IC101"},
        {"Role": "SAP Industry Cloud Engineer", "Skill": "JavaScript", "Required_Level": 3, "Skill_Category": "Programming", "Weight": 0.7, "Description": "Front-end cloud development", "Recommended_Courses": "IC102"},
        {"Role": "SAP Industry Cloud Engineer", "Skill": "Integration", "Required_Level": 3, "Skill_Category": "API", "Weight": 0.6, "Description": "Integrate cloud apps", "Recommended_Courses": "IC103"},
        {"Role": "SAP Industry Cloud Engineer", "Skill": "Python", "Required_Level": 2, "Skill_Category": "Programming", "Weight": 0.4, "Description": "Basic scripting for cloud automation", "Recommended_Courses": "IC104"},

        {"Role": "Integration & API Engineer", "Skill": "SAP CPI", "Required_Level": 4, "Skill_Category": "Integration", "Weight": 1.0, "Description": "Cloud Platform Integration", "Recommended_Courses": "API101"},
        {"Role": "Integration & API Engineer", "Skill": "REST APIs", "Required_Level": 3, "Skill_Category": "API", "Weight": 0.8, "Description": "Design & consume APIs", "Recommended_Courses": "API102"},
        {"Role": "Integration & API Engineer", "Skill": "OData", "Required_Level": 3, "Skill_Category": "API", "Weight": 0.7, "Description": "Build OData services", "Recommended_Courses": "API103"},
        {"Role": "Integration & API Engineer", "Skill": "Python", "Required_Level": 2, "Skill_Category": "Programming", "Weight": 0.4, "Description": "Python for automation and integration", "Recommended_Courses": "API104"},

        {"Role": "IT support / Technician", "Skill": "SAP Basis", "Required_Level": 4, "Skill_Category": "IT Support", "Weight": 1.0, "Description": "System administration & monitoring", "Recommended_Courses": "ITS101"},
        {"Role": "IT support / Technician", "Skill": "Networking", "Required_Level": 3, "Skill_Category": "IT Support", "Weight": 0.8, "Description": "Network troubleshooting", "Recommended_Courses": "ITS102"},
        {"Role": "IT support / Technician", "Skill": "Windows/Linux", "Required_Level": 3, "Skill_Category": "IT Support", "Weight": 0.7, "Description": "OS support & scripting", "Recommended_Courses": "ITS103"},
        {"Role": "IT support / Technician", "Skill": "Python", "Required_Level": 2, "Skill_Category": "Programming", "Weight": 0.4, "Description": "Basic Python for IT automation", "Recommended_Courses": "ITS104"},
        {"Role": "IT support / Technician", "Skill": "Excel", "Required_Level": 2, "Skill_Category": "Data", "Weight": 0.4, "Description": "Excel for IT reporting", "Recommended_Courses": "ITS105"},

        {"Role": "Digital Transformation Analyst", "Skill": "SAP Digital Transformation", "Required_Level": 4, "Skill_Category": "Transformation", "Weight": 1.0, "Description": "Process digitization & change management", "Recommended_Courses": "DTA101"},
        {"Role": "Digital Transformation Analyst", "Skill": "Project Management", "Required_Level": 3, "Skill_Category": "Management", "Weight": 0.8, "Description": "Lead transformation projects", "Recommended_Courses": "DTA102"},
        {"Role": "Digital Transformation Analyst", "Skill": "Business Process Modeling", "Required_Level": 3, "Skill_Category": "Process", "Weight": 0.7, "Description": "Map & optimize business processes", "Recommended_Courses": "DTA103"},
        {"Role": "Digital Transformation Analyst", "Skill": "Python", "Required_Level": 2, "Skill_Category": "Programming", "Weight": 0.4, "Description": "Basic scripting for process automation", "Recommended_Courses": "DTA104"},
        {"Role": "Digital Transformation Analyst", "Skill": "Excel", "Required_Level": 2, "Skill_Category": "Data", "Weight": 0.4, "Description": "Excel for analysis and reporting", "Recommended_Courses": "DTA105"},
    ]
    df = pd.DataFrame(data)
    df.columns = [c.strip() for c in df.columns]
    return df

# --------------------------------------------------
# Utility: Parsing & Matching
# --------------------------------------------------
def parse_user_skills(raw_text):
    if not raw_text:
        return {}
    parts = re.split(r"[,\n;]+", raw_text)
    skill_map = {}
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if ":" in p:
            skill_name, lvl = p.split(":", 1)
            skill_name = skill_name.strip()
            try:
                lvl_val = float(lvl.strip())
            except Exception:
                lvl_val = None
            skill_map[skill_name] = lvl_val
        else:
            skill_map[p] = None
    return skill_map

def fuzzy_match_skill(skill, candidate_skills, threshold=85):
    if not candidate_skills:
        return skill, 0
    match, score, _ = process.extractOne(skill, candidate_skills, scorer=fuzz.WRatio)
    if score >= threshold:
        return match, score
    return skill, score

def compute_skill_gap(df_role, user_skills_dict, fuzzy=True):
    required_rows = df_role.copy()
    canonical_skills = required_rows["Skill"].tolist()
    normalized_user = {}
    for raw_skill, lvl in user_skills_dict.items():
        skill_clean = raw_skill.strip()
        if fuzzy:
            matched, _ = fuzzy_match_skill(skill_clean, canonical_skills)
            normalized_user[matched] = lvl
        else:
            normalized_user[skill_clean] = lvl

    gap = {"missing": [], "underdeveloped": [], "met": [], "extra": []}
    for _, row in required_rows.iterrows():
        req_skill = row["Skill"]
        req_level = row.get("Required_Level", None)
        weight = row.get("Weight", 1.0)
        desc = row.get("Description", "")
        base_courses = row.get("Recommended_Courses", "")
        user_level = normalized_user.get(req_skill, None)

        if user_level is None:
            gap["missing"].append({
                "skill": req_skill, "required_level": req_level, "user_level": None,
                "weight": weight, "description": desc, "base_courses": base_courses
            })
        else:
            if (req_level is not None and isinstance(req_level, (int, float))
                    and user_level is not None):
                if user_level >= req_level:
                    gap["met"].append({
                        "skill": req_skill, "required_level": req_level, "user_level": user_level,
                        "weight": weight, "description": desc, "base_courses": base_courses
                    })
                else:
                    gap["underdeveloped"].append({
                        "skill": req_skill, "required_level": req_level, "user_level": user_level,
                        "gap_value": req_level - user_level, "weight": weight,
                        "description": desc, "base_courses": base_courses
                    })
            else:
                gap["met"].append({
                    "skill": req_skill, "required_level": req_level, "user_level": user_level,
                    "weight": weight, "description": desc, "base_courses": base_courses
                })

    required_set = set(canonical_skills)
    for uskill in normalized_user.keys():
        if uskill not in required_set:
            gap["extra"].append({"skill": uskill, "user_level": normalized_user[uskill]})
    return gap

def summarize_gap_stats(gap):
    total_required = len(gap["missing"]) + len(gap["underdeveloped"]) + len(gap["met"])
    if total_required == 0:
        return {}
    gap_score = 0.0
    total_weight = 0.0
    for item in gap["underdeveloped"]:
        req_level = item.get("required_level")
        user_level = item.get("user_level", 0) or 0
        weight = item.get("weight", 1.0) or 1.0
        if req_level:
            gap_component = ((req_level - user_level) / req_level) * weight
            gap_score += gap_component
            total_weight += weight
    for item in gap["missing"]:
        weight = item.get("weight", 1.0) or 1.0
        gap_score += 1.0 * weight
        total_weight += weight
    normalized_gap = gap_score / total_weight if total_weight else 0
    return {
        "total_required_skills": total_required,
        "met": len(gap["met"]),
        "underdeveloped": len(gap["underdeveloped"]),
        "missing": len(gap["missing"]),
        "extra": len(gap["extra"]),
        "weighted_gap_index": round(normalized_gap, 3)
    }

EXTERNAL_COURSE_INDEX = {
    "Python": ["Coursera: Python for Everybody", "Internal: DS101", "LeetCode practice sets"],
    "SQL": ["Internal: DS102", "Mode Analytics SQL Tutorial", "Coursera: Advanced SQL"],
    "Statistics": ["Internal: DS103", "Khan Academy: Statistics & Probability"],
    "Machine Learning": ["Internal: DS201", "Andrew Ng ML", "Hands-On ML Book"],
    "Airflow": ["Astronomer Academy Core", "Internal: DE201"]
}

def collect_course_suggestions(gap):
    suggestions = {}
    def accumulate(skill_name, base_courses):
        base_list = []
        if base_courses:
            base_list = [c.strip() for c in str(base_courses).split(";") if c.strip()]
        ext_list = EXTERNAL_COURSE_INDEX.get(skill_name, [])
        return list(dict.fromkeys(base_list + ext_list))
    for section in ["missing", "underdeveloped"]:
        for item in gap[section]:
            suggestions[item["skill"]] = accumulate(item["skill"], item.get("base_courses"))
    return suggestions

def build_llm_prompt(role, gap, stats, course_suggestions):
    def fmt_skill_list(items, show_gap=False):
        lines = []
        for it in items:
            if show_gap:
                lines.append(f"- {it['skill']} (Have: {it.get('user_level')} / Need: {it.get('required_level')} | Weight {it.get('weight')})")
            else:
                lines.append(f"- {it['skill']} (Need Level {it.get('required_level')} | Weight {it.get('weight')})")
        return "\n".join(lines) if lines else "None"

    missing_text = fmt_skill_list(gap["missing"])
    under_text = fmt_skill_list(gap["underdeveloped"], show_gap=True)
    met_text = fmt_skill_list(gap["met"], show_gap=True)

    course_text = []
    for skill, courses in course_suggestions.items():
        if courses:
            course_text.append(f"{skill}: {', '.join(courses)}")
    course_block = "\n".join(course_text) if course_text else "No baseline course suggestions found."

    # Include optional manager feedback context if available in session
    fb = st.session_state.get("manager_feedback", {})
    # Always consider feedback if provided, even if not analyzed via the button
    fb_text = fb.get("text") or st.session_state.get("manager_feedback_input") or ""
    fb_summary = None
    if fb_text:
        fb_summary = f"Manager Feedback Sentiment: {fb.get('sentiment_label','N/A')} (compound {fb.get('compound')})\nHighlights: {fb.get('highlights','') or 'N/A'}\nRaw: {fb_text[:500]}{'...' if len(fb_text)>500 else ''}"

    feedback_block = f"\nManager Feedback Context:\n{fb_summary}\n" if fb_summary else ""

    # Explicit instruction to always incorporate manager feedback into Phase 1 when present
    manager_phase1_rule = """
IMPORTANT REQUIREMENT (if Manager Feedback is provided above):
• Phase 1 must explicitly address the manager feedback items FIRST. For each highlighted item in feedback, include a concrete Phase 1 action (course or mini‑project) that targets it. Make this clear by starting those bullets with "(Manager priority)".
""" if fb_text else ""

    return f"""
You are a professional upskilling advisor.

Role: {role}

Weighted Gap Stats:
- Total Required Skills: {stats.get('total_required_skills')}
- Met: {stats.get('met')}
- Underdeveloped: {stats.get('underdeveloped')}
- Missing: {stats.get('missing')}
- Weighted Gap Index (0 best, 1 worst): {stats.get('weighted_gap_index')}

Missing Skills:
{missing_text}

Underdeveloped Skills:
{under_text}

Met Skills:
{met_text}

Baseline Course Suggestions (raw):
{course_block}

{feedback_block}

TASK:
1. Produce a prioritized learning roadmap (Phase 1 quick wins, Phase 2 core, Phase 3 advanced).
2. For each skill in phases: rationale (1 sentence), 1–2 courses, mini practice project.
3. Suggest timeline (weeks) per phase assuming 5–6 hrs/week.
4. Highlight interdependencies.
5. Provide 3 measurable progress metrics.
{manager_phase1_rule}
"""

def call_gemini(prompt):
    if not GOOGLE_API_KEY:
        return "Gemini API key not configured. Please set GOOGLE_API_KEY."
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        resp = model.generate_content(prompt)
        return resp.text
    except Exception as e:
        return f"Error calling Gemini: {e}"

# --------------------------------------------------
# Gap Summary Renderer
# --------------------------------------------------
def render_gap_summary(stats: dict, show_toggle: bool = True):
    if not stats:
        st.info("No stats available.")
        return
    total = stats["total_required_skills"] or 1

    def pct(v):
        return round(v / total * 100, 1) if total else 0

    gap_index = stats["weighted_gap_index"]
    readiness = round((1 - gap_index) * 100, 1)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Met", f"{stats['met']} ({pct(stats['met'])}%)")
    c2.metric("Underdeveloped", f"{stats['underdeveloped']} ({pct(stats['underdeveloped'])}%)")
    c3.metric("Missing", f"{stats['missing']} ({pct(stats['missing'])}%)")
    c4.metric("Extra", stats['extra'])
    c5.metric("Gap Index", gap_index)

    st.progress(min(int(readiness), 100),
                text=f"Readiness Score: {readiness}% (Higher is better)")

    if not HIDE_GAP_DISTRIBUTION_CHART:
        try:
            import altair as alt
            dist_df = pd.DataFrame([
                {"Metric": "Met", "Share": pct(stats["met"])},
                {"Metric": "Underdeveloped", "Share": pct(stats["underdeveloped"])},
                {"Metric": "Missing", "Share": pct(stats["missing"])},
            ])
            chart = (
                alt.Chart(dist_df)
                .mark_bar()
                .encode(
                    x=alt.X("Share", title="Share (%)", scale=alt.Scale(domain=[0, 100])),
                    y=alt.Y("Metric", sort=None),
                    color=alt.Color(
                        "Metric",
                        legend=None,
                        scale=alt.Scale(
                            domain=["Met", "Underdeveloped", "Missing"],
                            range=["#2e7d32", "#f9a825", "#c62828"]
                        )
                    ),
                    tooltip=["Metric", "Share"]
                )
                .properties(height=120)
            )
            st.altair_chart(chart, use_container_width=True)
        except Exception:
            pass

    if show_toggle:
        with st.expander("Show detailed breakdown table", expanded=SHOW_DETAILED_GAP_TABLE_BY_DEFAULT):
            readiness_score = readiness
            rows = [
                ("Total Required Skills", stats["total_required_skills"], 100.0),
                ("Met", stats["met"], pct(stats["met"])),
                ("Underdeveloped", stats["underdeveloped"], pct(stats["underdeveloped"])),
                ("Missing", stats["missing"], pct(stats["missing"])),
                ("Extra (Not Required)", stats["extra"], None),
                ("Weighted Gap Index (0 best, 1 worst)", gap_index, None),
                ("Readiness Score %", readiness_score, None),
            ]
            st.table(pd.DataFrame(rows, columns=["Metric", "Value", "Share (%)"]))

# --------------------------------------------------
# Progress Tracker Helpers
# --------------------------------------------------
PHASE_PATTERN = re.compile(r"(Phase\s+(\d+))[^0-9]*(\b(\d+)\s*weeks?\b)", re.IGNORECASE)

def parse_phase_durations(plan_markdown: str):
    results = {}
    if not plan_markdown:
        return results
    for match in PHASE_PATTERN.finditer(plan_markdown):
        try:
            phase_no = int(match.group(2))
            weeks = int(match.group(4))
            if weeks > 0:
                results[phase_no] = weeks
        except Exception:
            continue
    return results

def init_progress_state():
    if "progress_tracker" not in st.session_state:
        st.session_state["progress_tracker"] = {
            "start_date": None,
            "weekly_hours": 5,
            "phase_weeks": {},
            "checkpoints": [],
            "phase_status": {},
            "created_at": datetime.utcnow().isoformat(),
        }

def build_default_checkpoints(phase_weeks: dict, start_date: date):
    checkpoints = []
    current_start = start_date
    for phase_no in sorted(phase_weeks.keys()):
        weeks = phase_weeks[phase_no]
        label = f"Phase {phase_no}"
        checkpoints.append({
            "id": f"phase{phase_no}_start",
            "label": f"{label} Start",
            "phase": phase_no,
            "target_date": current_start.isoformat(),
            "completed": False,
            "completed_at": None
        })
        if weeks > 2:
            mid_date = current_start + timedelta(weeks=weeks / 2)
            checkpoints.append({
                "id": f"phase{phase_no}_mid",
                "label": f"{label} Midpoint",
                "phase": phase_no,
                "target_date": mid_date.isoformat(),
                "completed": False,
                "completed_at": None
            })
        end_date = current_start + timedelta(weeks=weeks)
        checkpoints.append({
            "id": f"phase{phase_no}_end",
            "label": f"{label} Complete",
            "phase": phase_no,
            "target_date": end_date.isoformat(),
            "completed": False,
            "completed_at": None
        })
        current_start = end_date
    return checkpoints

def rebuild_checkpoints(force=False):
    pt = st.session_state["progress_tracker"]
    if not pt["phase_weeks"] or pt["start_date"] is None:
        return
    if force or not pt["checkpoints"]:
        pt["checkpoints"] = build_default_checkpoints(pt["phase_weeks"], pt["start_date"])

def add_custom_checkpoint(label, target_date: date | None, phase_no=None):
    pt = st.session_state["progress_tracker"]
    cp_id = f"cp_{len(pt['checkpoints'])+1}_{int(datetime.utcnow().timestamp())}"
    pt["checkpoints"].append({
        "id": cp_id,
        "label": label,
        "phase": phase_no,
        "target_date": target_date.isoformat() if target_date else None,
        "completed": False,
        "completed_at": None
    })

def toggle_checkpoint(cp_id, new_value: bool):
    pt = st.session_state["progress_tracker"]
    for c in pt["checkpoints"]:
        if c["id"] == cp_id:
            c["completed"] = new_value
            c["completed_at"] = datetime.utcnow().isoformat() if new_value else None
            break

def mark_phase_complete(phase_no: int):
    pt = st.session_state["progress_tracker"]
    for c in pt["checkpoints"]:
        if c.get("phase") == phase_no:
            c["completed"] = True
            c["completed_at"] = datetime.utcnow().isoformat()
    ensure_phase_status()
    if phase_no in pt["phase_status"]:
        pt["phase_status"][phase_no]["completed"] = True
        pt["phase_status"][phase_no]["completed_at"] = datetime.utcnow().isoformat()

def compute_progress_metrics():
    pt = st.session_state["progress_tracker"]
    cps = pt.get("checkpoints", [])
    if not cps:
        return {"overall_pct": 0.0, "phase_pct": {}, "total": 0, "completed": 0}
    total = len(cps)
    done = sum(1 for c in cps if c["completed"])
    phase_groups = {}
    for c in cps:
        phase_no = c.get("phase")
        phase_groups.setdefault(phase_no, {"total": 0, "done": 0})
        phase_groups[phase_no]["total"] += 1
        if c["completed"]:
            phase_groups[phase_no]["done"] += 1
    phase_pct = {
        p: (g["done"] / g["total"] * 100 if g["total"] else 0)
        for p, g in phase_groups.items()
        if p is not None
    }
    return {"overall_pct": done / total * 100, "phase_pct": phase_pct, "total": total, "completed": done}

def weeks_elapsed_since(start_date: date):
    if not start_date:
        return 0
    delta = date.today() - start_date
    return round(delta.days / 7, 1)

def export_progress_json():
    def _ser(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return str(o)
    pt = st.session_state["progress_tracker"].copy()
    return json.dumps(pt, indent=2, default=_ser)

def status_badge(cp):
    if cp["completed"]:
        return "✅"
    if cp.get("target_date"):
        try:
            tdate = datetime.fromisoformat(cp["target_date"]).date()
            if date.today() > tdate:
                return "⚠️"
        except Exception:
            pass
    return "⏳"

def ensure_phase_status():
    pt = st.session_state["progress_tracker"]
    if "phase_status" not in pt:
        pt["phase_status"] = {}
    for p in pt["phase_weeks"].keys():
        pt["phase_status"].setdefault(p, {"completed": False, "completed_at": None})
    for p in list(pt["phase_status"].keys()):
        if p not in pt["phase_weeks"]:
            del pt["phase_status"][p]

def sync_checkpoints_with_phase(phase_no: int, complete: bool):
    pt = st.session_state["progress_tracker"]
    for c in pt.get("checkpoints", []):
        if c.get("phase") == phase_no:
            c["completed"] = complete
            c["completed_at"] = datetime.utcnow().isoformat() if complete else None

def compute_phase_completion_pct():
    pt = st.session_state["progress_tracker"]
    ensure_phase_status()
    ps = pt["phase_status"]
    if not ps:
        return 0.0
    total = len(ps)
    done = sum(1 for v in ps.values() if v["completed"])
    return done / total * 100

def compute_weighted_phase_completion():
    pt = st.session_state["progress_tracker"]
    ensure_phase_status()
    if not pt["phase_weeks"]:
        return 0.0
    total_weight = sum(pt["phase_weeks"].values()) or 1
    acc = 0
    for ph, weeks in pt["phase_weeks"].items():
        if pt["phase_status"].get(ph, {}).get("completed"):
            acc += weeks
    return acc / total_weight * 100

def render_phase_completion_controls():
    pt = st.session_state["progress_tracker"]
    ensure_phase_status()
    if not pt["phase_weeks"]:
        st.info("Define phase durations (parse or set) to enable phase completion toggles.")
        return
    st.markdown("### Phase Completion")
    cols = st.columns(min(len(pt["phase_weeks"]), 4) or 1)
    changed = False
    for idx, phase_no in enumerate(sorted(pt["phase_weeks"].keys())):
        with cols[idx % len(cols)]:
            info = pt["phase_status"][phase_no]
            label = f"Phase {phase_no} ({pt['phase_weeks'][phase_no]} wk)"
            new_val = st.checkbox(label, value=info["completed"], key=f"phase_complete_{phase_no}")
            if new_val != info["completed"]:
                info["completed"] = new_val
                info["completed_at"] = datetime.utcnow().isoformat() if new_val else None
                sync_checkpoints_with_phase(phase_no, new_val)
                changed = True
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Mark All Phases Complete"):
            for p, info in pt["phase_status"].items():
                if not info["completed"]:
                    info["completed"] = True
                    info["completed_at"] = datetime.utcnow().isoformat()
                    sync_checkpoints_with_phase(p, True)
            changed = True
    with bcol2:
        if st.button("Reset All Phases"):
            for p, info in pt["phase_status"].items():
                info["completed"] = False
                info["completed_at"] = None
                sync_checkpoints_with_phase(p, False)
            changed = True
    if changed:
        st.rerun()

# --------------------------------------------------
# Tabs
# --------------------------------------------------
tabs = st.tabs(["Upskill Analysis & Plan", "Chosen Plan", "Progress Tracker"])

# --------------------------------------------------
# TAB 1: Analysis & Plan (Option 1 Implementation)
# --------------------------------------------------
with tabs[0]:
    with st.expander("Instructions", expanded=False):
        st.markdown("""
        1. Select the target role (table updates immediately).
        2. Enter your current skills (Skill or Skill:Level).
        3. Click Analyze to compute gaps and generate AI plan.
        4. Accept the plan to enable tracking in Progress Tracker tab.
        """)

    df = load_role_skill_data()
    if df is None or "Role" not in df.columns:
        st.error("Failed to load role skill data.")
        st.stop()

    roles = sorted(df["Role"].dropna().unique().tolist())

    # Reactive Role Selection OUTSIDE form
    selected_role = st.selectbox("Select Role", roles, key="role_select")

    role_df_view = df[df["Role"] == selected_role][["Skill", "Required_Level", "Skill_Category", "Weight"]]

    st.caption("Role Requirements (updates immediately when role changes)")
    # Top chips: total skills and category distribution
    total_skills = len(role_df_view)
    cat_counts = role_df_view["Skill_Category"].value_counts(dropna=False).to_dict()
    chips_html = [f'<span class="lh-chip-strong lh-chip">🎯 Total Skills: {total_skills}</span>']
    for cat, cnt in cat_counts.items():
        label = str(cat) if pd.notna(cat) else "Uncategorized"
        chips_html.append(f'<span class="lh-chip">{label}: {cnt}</span>')
    st.markdown(" ".join(chips_html), unsafe_allow_html=True)

    st.dataframe(
        role_df_view,
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # Manager Feedback Section with Sentiment Analysis
    st.subheader("Manager Feedback (optional)")
    st.caption("Paste or write summarized feedback from your manager. We'll analyze sentiment and factor it into the AI plan.")
    if "_vader" not in st.session_state:
        try:
            st.session_state["_vader"] = SentimentIntensityAnalyzer()
        except Exception:
            st.session_state["_vader"] = None

    # Provide quick mock feedback examples
    mock_cols = st.columns(3)
    with mock_cols[0]:
        if st.button("Insert Positive Mock"):
            st.session_state["manager_feedback_input"] = (
                "Great progress on Python and SAP BTP tasks. Keep up the pace; your SQL still needs polishing."
            )
    with mock_cols[1]:
        if st.button("Insert Mixed Mock"):
            st.session_state["manager_feedback_input"] = (
                "You're proactive and collaborate well. However, dashboards in Power BI lack depth and need more advanced features."
            )
    with mock_cols[2]:
        if st.button("Insert Negative Mock"):
            st.session_state["manager_feedback_input"] = (
                "Delivery has been inconsistent and the last integration had multiple defects. SQL and API design must improve quickly."
            )

    fb_text = st.text_area(
        "Manager Feedback",
        key="manager_feedback_input",
        height=100,
        placeholder="E.g., Strong collaboration and Python skills, but needs improvement in SQL and Power BI visuals...",
    )

    fb_analyzed = False
    if st.button("Analyze Feedback Sentiment"):
        if not fb_text:
            st.warning("Please enter feedback text first.")
        elif st.session_state.get("_vader") is None:
            st.error("Sentiment analyzer not available. Install vaderSentiment and reload.")
        else:
            scores = st.session_state["_vader"].polarity_scores(fb_text)
            comp = round(scores.get("compound", 0.0), 3)
            if comp >= 0.05:
                label = "Positive"
            elif comp <= -0.05:
                label = "Negative"
            else:
                label = "Neutral"
            # simple heuristic highlights: extract skill words present in role_df_view
            skills = set(role_df_view["Skill"].tolist())
            found = [s for s in skills if re.search(rf"\b{re.escape(s)}\b", fb_text, re.IGNORECASE)]
            st.session_state["manager_feedback"] = {
                "text": fb_text,
                "scores": scores,
                "compound": comp,
                "sentiment_label": label,
                "highlights": ", ".join(found) if found else None,
                "analyzed_at": datetime.utcnow().isoformat(),
            }
            fb_analyzed = True

    # Show current feedback summary if present
    cur_fb = st.session_state.get("manager_feedback")
    if cur_fb:
        st.markdown(
            f"""
            <div class="lh-card">
                <div class="lh-section">Current Feedback Snapshot</div>
                <div class="lh-small lh-muted">Sentiment: <b>{cur_fb.get('sentiment_label')}</b> (compound {cur_fb.get('compound')})</div>
                <div class="lh-small">Highlights: {cur_fb.get('highlights') or '—'}</div>
                <details style="margin-top:6px;"><summary class="lh-small">Show full text</summary>
                <pre style="white-space:pre-wrap">{cur_fb.get('text')}</pre>
                </details>
            </div>
            """,
            unsafe_allow_html=True,
        )
        cfb1, cfb2 = st.columns(2)
        with cfb1:
            if st.button("Clear Feedback"):
                st.session_state.pop("manager_feedback", None)
                st.session_state.pop("manager_feedback_input", None)
                st.rerun()
        with cfb2:
            st.caption("This feedback will be considered when generating the plan.")

    # Form for skill input + analyze action
    with st.form("analysis_form", clear_on_submit=False):
        user_skill_input = st.text_area(
            "Your Current Skills",
            height=110,
            help="Separate by commas. Use Skill:Level for proficiency (e.g. Python:3, SQL:2)."
        )
        submitted = st.form_submit_button("Analyze Skill Gap & Generate Plan")

    # If role changed after last plan generation, optionally inform user
    if st.session_state.get("latest_role") and st.session_state["latest_role"] != selected_role:
        st.info("Role changed since last generated plan. Run analysis again to refresh gap & plan.")

    if submitted:
        user_skills_dict = parse_user_skills(user_skill_input)
        role_df = df[df["Role"] == selected_role]

        if role_df.empty:
            st.error("No data for that role.")
        else:
            gap = compute_skill_gap(role_df, user_skills_dict)
            stats = summarize_gap_stats(gap)
            course_suggestions = collect_course_suggestions(gap)

            st.session_state["latest_gap"] = gap
            st.session_state["latest_stats"] = stats
            st.session_state["latest_course_suggestions"] = course_suggestions
            st.session_state["latest_role"] = selected_role

            st.subheader("Gap Summary")
            render_gap_summary(stats)

            gap_tabs = st.tabs(["Missing", "Underdeveloped", "Met", "Extra"])
            with gap_tabs[0]:
                if gap["missing"]:
                    st.dataframe(pd.DataFrame(gap["missing"]), use_container_width=True, hide_index=True)
                else:
                    st.success("No missing skills.")
            with gap_tabs[1]:
                if gap["underdeveloped"]:
                    st.dataframe(pd.DataFrame(gap["underdeveloped"]), use_container_width=True, hide_index=True)
                else:
                    st.success("No underdeveloped skills.")
            with gap_tabs[2]:
                if gap["met"]:
                    st.dataframe(pd.DataFrame(gap["met"]), use_container_width=True, hide_index=True)
                else:
                    st.info("None met yet.")
            with gap_tabs[3]:
                if gap["extra"]:
                    st.dataframe(pd.DataFrame(gap["extra"]), use_container_width=True, hide_index=True)
                else:
                    st.info("No extra skills.")

            st.subheader("Course Suggestions")
            if course_suggestions:
                # Render as responsive cards
                cols = st.columns(2)
                idx = 0
                for skill, courses in course_suggestions.items():
                    with cols[idx % 2]:
                        st.markdown(
                            f"""
                            <div class="lh-card">
                                <div class="lh-section">{skill}</div>
                                <div class="lh-small lh-muted">Recommended</div>
                                <div style="margin-top:6px;">
                                    {' '.join(f'<span class="lh-pill">📘 {c}</span>' for c in courses)}
                                </div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                    idx += 1
            else:
                st.write("None available.")

            st.divider()
            st.subheader("Gemini Learning Plan")
            with st.spinner("Generating plan..."):
                prompt = build_llm_prompt(selected_role, gap, stats, course_suggestions)
                llm_output = call_gemini(prompt)

            st.session_state["latest_prompt"] = prompt
            st.session_state["latest_plan"] = llm_output

            # IMPORTANT: Render plan as-is to preserve exact format
            st.markdown(llm_output)
            with st.expander("Prompt Debug"):
                st.code(prompt, language="markdown")

    # Post-generation action buttons
    if st.session_state.get("latest_plan"):
        st.divider()
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("🔁 Regenerate Plan"):
                if all(k in st.session_state for k in ["latest_gap", "latest_stats", "latest_course_suggestions", "latest_role"]):
                    with st.spinner("Regenerating..."):
                        new_prompt = build_llm_prompt(
                            st.session_state["latest_role"],
                            st.session_state["latest_gap"],
                            st.session_state["latest_stats"],
                            st.session_state["latest_course_suggestions"]
                        )
                        new_plan = call_gemini(new_prompt)
                    st.session_state["latest_prompt"] = new_prompt
                    st.session_state["latest_plan"] = new_plan
                    st.rerun()
                else:
                    st.warning("Missing context to regenerate.")
        with c2:
            if st.button("✅ Accept Plan"):
                st.session_state["chosen_upskillingplan"] = st.session_state["latest_plan"]
                st.session_state["accepted_plan_role"] = st.session_state.get("latest_role")
                st.session_state["accepted_at"] = datetime.utcnow().isoformat()
                st.success("Plan accepted. Track it in the Progress Tracker tab.")
        with c3:
            if st.button("🧹 Clear Generated Plan"):
                for key in [
                    "latest_gap", "latest_stats", "latest_course_suggestions",
                    "latest_role", "latest_prompt", "latest_plan"
                ]:
                    st.session_state.pop(key, None)
                st.info("Cleared.")
                st.rerun()

# --------------------------------------------------
# TAB 2: Chosen Plan
# --------------------------------------------------
with tabs[1]:
    st.header("Your Accepted Plan")
    chosen_plan = st.session_state.get("chosen_upskillingplan")
    if chosen_plan:
        st.markdown(f"**Role:** {st.session_state.get('accepted_plan_role','N/A')}")
        st.markdown(f"**Accepted (UTC):** {st.session_state.get('accepted_at','N/A')}")
        cur_fb = st.session_state.get("manager_feedback")
        if cur_fb:
            st.info(f"Manager Feedback: {cur_fb.get('sentiment_label')} (compound {cur_fb.get('compound')}). Highlights: {cur_fb.get('highlights') or '—'}")
        st.divider()
        st.markdown(chosen_plan)
    else:
        st.info("No plan accepted yet.")

# --------------------------------------------------
# TAB 3: Progress Tracker
# --------------------------------------------------
with tabs[2]:
    st.header("📈 Progress Tracker")

    init_progress_state()
    pt = st.session_state["progress_tracker"]

    if not st.session_state.get("chosen_upskillingplan"):
        st.info("Accept a learning plan first in the 'Upskill Analysis & Plan' tab.")
        st.stop()

    ensure_phase_status()

    metrics = compute_progress_metrics()
    total_weeks = sum(pt["phase_weeks"].values()) if pt["phase_weeks"] else 0
    elapsed_weeks = weeks_elapsed_since(pt["start_date"]) if pt["start_date"] else 0
    time_progress_pct = (elapsed_weeks / total_weeks * 100) if total_weeks else 0

    phase_pct_val_simple = compute_phase_completion_pct()
    phase_pct_val_weighted = compute_weighted_phase_completion()

    if USE_PHASE_COMPLETION_FOR_OVERALL_PROGRESS:
        overall_label = "Phase Completion"
        overall_pct = phase_pct_val_weighted
    else:
        overall_label = "Checkpoint Completion"
        overall_pct = metrics["overall_pct"]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(overall_label, f"{overall_pct:.1f}%")
    m2.metric("Simple Phase %", f"{phase_pct_val_simple:.1f}%")
    m3.metric("Time Progress", f"{time_progress_pct:.1f}%")
    m4.metric("Weeks Elapsed", f"{elapsed_weeks}")
    m5.metric("Planned Weeks", f"{total_weeks}")

    # Surface feedback sentiment as context
    cur_fb = st.session_state.get("manager_feedback")
    if cur_fb:
        st.caption(f"Manager Feedback sentiment: {cur_fb.get('sentiment_label')} (compound {cur_fb.get('compound')}).")

    st.progress(min(int(overall_pct), 100))

    if PHASE_COMPLETION_CHECKBOXES_ENABLED:
        render_phase_completion_controls()

    if metrics["phase_pct"] and SHOW_CHECKPOINT_SECTION:
        with st.expander("Phase Progress (Checkpoint-Based)", expanded=False):
            phase_bar_cols = st.columns(len(metrics["phase_pct"]))
            for i, (p, pct) in enumerate(sorted(metrics["phase_pct"].items())):
                with phase_bar_cols[i]:
                    st.write(f"Phase {p}")
                    st.progress(min(int(pct), 100))
                    if st.button(f"Mark Phase {p} Complete", key=f"markphase_{p}"):
                        mark_phase_complete(p)
                        st.rerun()

    if SHOW_CHECKPOINT_SECTION:
        st.markdown("### Checkpoints")
        if not pt["checkpoints"] and pt["phase_weeks"] and pt["start_date"]:
            if st.button("Generate Default Checkpoints"):
                rebuild_checkpoints(force=True)
                st.rerun()
        elif pt["checkpoints"]:
            filtered = pt["checkpoints"]
            for c in filtered:
                label_parts = [status_badge(c), c['label']]
                if SHOW_CHECKPOINT_DATES_INLINE and c.get("target_date"):
                    label_parts.append(f"({c['target_date']})")
                if c.get("phase") is not None:
                    label_parts.append(f"[Phase {c['phase']}]")
                label = " ".join(label_parts)
                checked = st.checkbox(label, value=c["completed"], key=f"cpbox_{c['id']}")
                if checked != c["completed"]:
                    toggle_checkpoint(c["id"], checked)
                if SHOW_CHECKPOINT_DETAILS:
                    st.caption(f"Target: {c.get('target_date') or '—'} | Done: {c.get('completed_at') or '—'}")

        if st.button("Rebuild Default Checkpoints (Overwrite)", key="rebuild_cp_overwrite_top"):
            rebuild_checkpoints(force=True)
            st.success("Default checkpoints rebuilt.")
            st.rerun()

        with st.expander("Add Custom Checkpoint", expanded=False):
            with st.form("add_cp_form"):
                col_acp = st.columns([2, 1, 1, 1])
                with col_acp[0]:
                    cp_label = st.text_input("Label", "")
                with col_acp[1]:
                    cp_phase = st.selectbox("Phase (optional)", ["None"] + [p for p in sorted(pt["phase_weeks"].keys())])
                    phase_val = None if cp_phase == "None" else cp_phase
                with col_acp[2]:
                    cp_date_enable = st.checkbox("Set Target Date?", value=False)
                with col_acp[3]:
                    cp_date = st.date_input("Target", value=date.today()) if cp_date_enable else None
                submit_cp = st.form_submit_button("Add")
                if submit_cp and cp_label.strip():
                    add_custom_checkpoint(cp_label.strip(), cp_date, phase_no=phase_val)
                    st.success("Checkpoint added.")
                    st.rerun()

    st.markdown("### Export / Backup")
    exp_json = export_progress_json()
    st.download_button(
        "Download Progress JSON",
        data=exp_json,
        file_name="progress_tracker.json",
        mime="application/json"
    )

    with st.expander("Advanced / Debug State"):
        st.code(exp_json, language="json")
        cols_dbg = st.columns(3)
        with cols_dbg[0]:
            if st.button("Full Reset Progress Tracker"):
                st.session_state.pop("progress_tracker", None)
                st.success("Progress tracker reset.")
                st.rerun()
        if SHOW_CHECKPOINT_SECTION:
            with cols_dbg[1]:
                if st.button("Clear All Checkpoints"):
                    pt["checkpoints"] = []
                    st.warning("Checkpoints cleared.")
                    st.rerun()
            with cols_dbg[2]:
                if st.button("Rebuild Checkpoints (Force)"):
                    rebuild_checkpoints(force=True)
                    st.success("Rebuilt.")
                    st.rerun()

    with st.expander("Setup (Start Date, Hours, Parse Phases)", expanded=False):
        setup_cols = st.columns([1, 1, 1])
        with setup_cols[0]:
            start_val = pt["start_date"] if pt["start_date"] else date.today()
            chosen_start = st.date_input("Start Date", value=start_val)
            if pt["start_date"] != chosen_start:
                pt["start_date"] = chosen_start
        with setup_cols[1]:
            pt["weekly_hours"] = st.number_input(
                "Weekly Study Hours",
                min_value=1, max_value=80,
                value=pt.get("weekly_hours", 5),
            )
        with setup_cols[2]:
            if st.button("Auto-Parse Phases from Plan"):
                parsed = parse_phase_durations(st.session_state["chosen_upskillingplan"])
                if parsed:
                    pt["phase_weeks"] = parsed
                    ensure_phase_status()
                    st.success(f"Parsed phases: {parsed}")
                else:
                    st.warning("Could not detect numeric phase durations in plan.")
        if pt["start_date"] and pt["phase_weeks"] and SHOW_CHECKPOINT_SECTION and not pt["checkpoints"]:
            if st.button("Generate Default Checkpoints"):
                rebuild_checkpoints(force=True)
                st.success("Default checkpoints created.")
                st.rerun()

    with st.expander("Manage Phase Durations", expanded=False):
        if not pt["phase_weeks"]:
            st.info("No phase durations defined yet. Use Auto-Parse above or manually set below.")
        else:
            editable_phases = {}
            phase_cols = st.columns(min(len(pt["phase_weeks"]), 4) or 1)
            for idx, phase_no in enumerate(sorted(pt["phase_weeks"].keys())):
                with phase_cols[idx % len(phase_cols)]:
                    weeks_val = st.number_input(
                        f"Phase {phase_no} Weeks",
                        min_value=1,
                        value=int(pt["phase_weeks"][phase_no]),
                        key=f"phaseweeks_{phase_no}"
                    )
                    editable_phases[phase_no] = weeks_val
            if st.button("Apply Phase Changes"):
                pt["phase_weeks"] = editable_phases
                ensure_phase_status()
                st.success("Phase durations updated.")

        if pt["phase_weeks"] and SHOW_CHECKPOINT_SECTION:
            if st.button("Rebuild Default Checkpoints (Overwrite)", key="rebuild_cp_overwrite_bottom"):
                rebuild_checkpoints(force=True)
                st.success("Default checkpoints rebuilt.")
                st.rerun()


persist_session_to_json()
