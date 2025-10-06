# pages/4_MyTickets.py
# Company Ticket Hub (CSV-backed) with login gate + User/Admin view switch

import streamlit as st
import pandas as pd
import os, io
from datetime import datetime, timezone
from dateutil import parser as dtparser

from utils import notifications_panel

# Inside the page (after login check)
if "user" in st.session_state and st.session_state["user"]:
    notifications_panel(st.session_state["user"])



# ---------- Page ----------
#st.set_page_config(page_title="Company Ticket Hub", page_icon="🎫", layout="wide")
st.title("Company Ticket Hub")
st.caption("CSV-backed ticketing with safe writes, chatbot prefill, and role-based views.")

# ---------- Require login first ----------
user = st.session_state.get("user")
if not user or not user.get("email"):
    st.warning("Please login on the main page first.")
    try:
        if st.button("⬅ Back to Chat / Login"):
            st.switch_page("🤖_Homepage.py")
    except Exception:
        st.info("If the button does not work, use the browser Back button.")
    st.stop()

# Now we know we have a logged-in user:
ME_EMAIL = user.get("email", "")
ME_NAME  = user.get("name", "")
ME_ROLE  = str(user.get("role", "EMPLOYEE")).upper()

# ---------- Query params (focus + return path) ----------
qs = {}
try:
    qs = dict(st.query_params)
except Exception:
    pass

focus_id = None
try:
    if qs.get("focus"):
        focus_id = int(qs.get("focus"))
except Exception:
    focus_id = None

from_chat = qs.get("from") == "chat"

# Back to Chat button (top-left)
left = st.columns([1, 6])[0]
with left:
    if from_chat:
        try:
            if st.button("⬅ Back to Chat"):
                st.switch_page("🤖_Homepage.py")
        except Exception:
            st.info("Use your browser Back button to return to chat.")

# ---------- CSV config ----------
EMPLOYEE_CSV   = os.getenv("EMPLOYEE_CSV",   "datasets/ticketingemployees.csv")
TICKETS_CSV    = os.getenv("TICKETS_CSV",    "datasets/tickets.csv")
CATEGORIES_CSV = os.getenv("CATEGORIES_CSV", "datasets/categories.csv")
COMMENTS_CSV   = os.getenv("COMMENTS_CSV",   "datasets/comments.csv")

# Ensure files exist (minimal schema)
SCHEMAS = {
    EMPLOYEE_CSV:   ["email","name","role","team","manager_email","location","phone","date_joined","last_seen_at"],
    TICKETS_CSV:    ["id","title","description","status","priority","category_key","requester_email","assignee_email","created_at","updated_at"],
    CATEGORIES_CSV: ["key","label","default_team"],
    COMMENTS_CSV:   ["id","ticket_id","author_email","body","internal","created_at"],
}
for path, cols in SCHEMAS.items():
    if not os.path.exists(path):
        pd.DataFrame(columns=cols).to_csv(path, index=False)

# ---------- Cached CSV loading & atomic saving ----------
@st.cache_data(ttl=1.0, show_spinner=False)
def read_csv(path: str, mtime: float):
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()

class CsvStore:
    def __init__(self, path: str):
        self.path = path
    @property
    def mtime(self) -> float:
        try:
            return os.path.getmtime(self.path)
        except FileNotFoundError:
            return 0.0
    def load(self) -> pd.DataFrame:
        return read_csv(self.path, self.mtime)
    def save(self, df: pd.DataFrame):
        tmp = self.path + ".tmp"
        df.to_csv(tmp, index=False)
        os.replace(tmp, self.path)

employees_store  = CsvStore(EMPLOYEE_CSV)
tickets_store    = CsvStore(TICKETS_CSV)
categories_store = CsvStore(CATEGORIES_CSV)
comments_store   = CsvStore(COMMENTS_CSV)

# ---------- Load Data ----------
employees  = employees_store.load()
tickets    = tickets_store.load()
categories = categories_store.load()
comments   = comments_store.load()

# Seed default categories if empty
if categories.empty or "key" not in categories.columns:
    categories = pd.DataFrame(
        [{"key":"it","label":"IT","default_team":"Helpdesk"},
         {"key":"hr","label":"Human Resources","default_team":"People Ops"},
         {"key":"ops","label":"Operations","default_team":"Ops"}]
    )
    categories_store.save(categories)

# Normalize dtypes
if "id" in tickets.columns:
    tickets["id"] = pd.to_numeric(tickets["id"], errors="coerce").astype("Int64")
if "ticket_id" in comments.columns:
    comments["ticket_id"] = pd.to_numeric(comments["ticket_id"], errors="coerce").astype("Int64")

# ---------- Sidebar: identity + View mode ----------
with st.sidebar:
    st.header("You")
    st.write(f"**{ME_NAME or ME_EMAIL}**")
    st.caption(f"Role: **{ME_ROLE}**")

    # View switcher (User vs Admin)
    view_choice = st.radio(
        "View mode",
        ["User", "Admin"],
        index=0 if ME_ROLE not in ("ADMIN","AGENT") else (1 if st.session_state.get("tickets_admin_mode", False) else 0),
        horizontal=True
    )
    # Enforce permission
    if view_choice == "Admin" and ME_ROLE not in ("ADMIN","AGENT"):
        st.warning("You don't have permission for Admin view. Showing User view.")
        view_choice = "User"
    st.session_state["tickets_admin_mode"] = (view_choice == "Admin")

    st.divider()
    # Navigation per view
    if view_choice == "User":
        page = st.radio("Go to", ["Raise Ticket","My Tickets"], index=0)
    else:
        page = st.radio("Go to", ["Queues","Admin","Metrics","Raise Ticket","My Tickets"], index=0)

# ---------- Helpers ----------
STATUS   = ["NEW","TRIAGED","IN_PROGRESS","WAITING_ON_USER","RESOLVED","CLOSED"]
PRIORITY = ["P1","P2","P3","P4"]

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def next_id(df: pd.DataFrame, col: str = "id") -> int:
    if df.empty or col not in df.columns:
        return 1
    return int(pd.to_numeric(df[col], errors="coerce").fillna(0).max()) + 1

def ensure_category(cat_key: str, label: str | None = None):
    global categories
    if "key" not in categories.columns:
        return
    keys = categories["key"].tolist()
    if cat_key not in keys:
        add = pd.DataFrame([{"key":cat_key, "label":label or cat_key.upper(), "default_team":""}])
        categories = pd.concat([categories, add], ignore_index=True)
        categories_store.save(categories)

# ---------- Pages ----------
if page == "Raise Ticket":
    st.subheader("Create a new ticket")
    pre_cat   = qs.get("category")
    pre_title = qs.get("title")

    cat_options = categories["key"].tolist() if "key" in categories.columns else []
    c1, c2 = st.columns(2)
    with c1:
        idx = (cat_options.index(pre_cat) if (pre_cat in cat_options) else 0) if cat_options else None
        category_key   = st.selectbox("Category", cat_options, index=idx, placeholder="Add categories in Admin")
        priority       = st.selectbox("Priority", PRIORITY, index=2)
        title          = st.text_input("Title", value=pre_title or "", placeholder="e.g., VPN not connecting on office Wi-Fi")
    with c2:
        requester      = ME_EMAIL
        st.text_input("Your email (requester)", value=requester, disabled=True)
        assignee_email = st.text_input("Assignee (optional email)", value="")
    description = st.text_area("Description", placeholder="Steps to reproduce / expected vs actual / error codes.", height=160)

    create = st.button("Create ticket", type="primary", disabled=not (title and requester and category_key))
    if create:
        ensure_category(category_key)
        new = {
            "id": next_id(tickets, "id"),
            "title": title.strip(),
            "description": description.strip(),
            "status": "NEW",
            "priority": priority,
            "category_key": category_key,
            "requester_email": requester,
            "assignee_email": assignee_email.strip(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        tickets = pd.concat([tickets, pd.DataFrame([new])], ignore_index=True)
        tickets_store.save(tickets)
        st.success(f"Ticket #{new['id']} created")
        st.balloons()

elif page == "My Tickets":
    st.subheader("Your tickets")
    mine = tickets.query("requester_email == @ME_EMAIL") if not tickets.empty else tickets
    if "created_at" in mine.columns:
        mine = mine.sort_values("created_at", ascending=False)
    st.dataframe(mine, use_container_width=True)
    st.divider()
    st.caption("Update a ticket you created")
    default_id = int(focus_id or 0)
    edit_id    = st.number_input("Ticket ID", min_value=0, value=default_id, step=1)
    new_status = st.selectbox("New status", [""] + STATUS)
    if st.button("Update status", disabled=not (edit_id and new_status)):
        if (tickets["id"] == edit_id).any():
            tickets.loc[tickets["id"]==edit_id, ["status","updated_at"]] = [new_status, now_iso()]
            tickets_store.save(tickets)
            st.success(f"Ticket #{edit_id} updated to {new_status}")
        else:
            st.error("Ticket ID not found.")

elif page == "Queues":
    st.subheader("Team queues")
    if st.session_state.get("tickets_admin_mode") is not True:
        st.info("Switch to Admin view in the sidebar to access queues.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            cat = st.selectbox("Category filter", [""] + (categories["key"].tolist() if "key" in categories.columns else []))
        with c2:
            status = st.selectbox("Status filter", [""] + STATUS)
        with c3:
            mine_only = st.checkbox("Only my assignees", value=False)

        view = tickets.copy()
        if cat:
            view = view[view["category_key"]==cat]
        if status:
            view = view[view["status"]==status]
        if mine_only and ME_EMAIL:
            view = view[view["assignee_email"]==ME_EMAIL]

        def age_hours(ts):
            try:
                dt = dtparser.parse(str(ts))
                return round((datetime.now(timezone.utc) - dt).total_seconds()/3600, 1)
            except Exception:
                return None

        if "created_at" in view.columns:
            view = view.assign(age_h=view["created_at"].apply(age_hours))
            view = view.sort_values("created_at", ascending=False)

        st.dataframe(view, use_container_width=True)

        st.divider()
        st.caption("Quick triage / assign")
        edit_id    = st.number_input("Ticket ID", min_value=0, value=0, step=1)
        assign_to  = st.selectbox("Assign to", [""] + (employees["email"].dropna().tolist() if "email" in employees.columns else []))
        set_status = st.selectbox("Set status", [""] + STATUS)
        if st.button("Apply changes", disabled=not edit_id):
            if (tickets["id"] == edit_id).any():
                if assign_to:
                    tickets.loc[tickets["id"]==edit_id, "assignee_email"] = assign_to
                if set_status:
                    tickets.loc[tickets["id"]==edit_id, "status"] = set_status
                tickets.loc[tickets["id"]==edit_id, "updated_at"] = now_iso()
                tickets_store.save(tickets)
                st.success(f"Ticket #{edit_id} updated.")
            else:
                st.error("Ticket ID not found.")

elif page == "Admin":
    st.subheader("Admin")
    if st.session_state.get("tickets_admin_mode") is not True:
        st.info("Switch to Admin view in the sidebar to access Admin tools.")
    else:
        st.markdown("#### Categories")
        st.dataframe(categories, use_container_width=True)
        with st.expander("Add category"):
            key = st.text_input("Key (e.g., it, hr, ops)")
            label = st.text_input("Label")
            default_team = st.text_input("Default team (optional)")
            if st.button("Add category", disabled=not (key and label)):
                if "key" in categories.columns and key in categories["key"].tolist():
                    st.error("Key already exists")
                else:
                    add = pd.DataFrame([{"key":key, "label":label, "default_team":default_team}])
                    categories = pd.concat([categories, add], ignore_index=True)
                    categories_store.save(categories)
                    st.success("Category added.")

        st.divider()
        st.markdown("#### Users (Employees)")
        st.dataframe(employees, use_container_width=True)
        with st.expander("Add user"):
            u_email = st.text_input("Email")
            u_name  = st.text_input("Name")
            u_role  = st.selectbox("Role", ["EMPLOYEE","AGENT","ADMIN"], index=0)
            u_team  = st.text_input("Team")
            if st.button("Add user", disabled=not (u_email and u_name)):
                add = pd.DataFrame([{"email":u_email, "name":u_name, "role":u_role, "team":u_team}])
                employees = pd.concat([employees, add], ignore_index=True)
                employees_store.save(employees)
                st.success("User added.")

elif page == "Metrics":
    st.subheader("Metrics")
    if st.session_state.get("tickets_admin_mode") is not True:
        st.info("Switch to Admin view in the sidebar to view metrics.")
    else:
        total = len(tickets)
        open_states = ["NEW","TRIAGED","IN_PROGRESS","WAITING_ON_USER"]
        open_count = int(tickets["status"].isin(open_states).sum()) if "status" in tickets.columns and not tickets.empty else 0
        resolved   = int(tickets["status"].isin(["RESOLVED","CLOSED"]).sum()) if "status" in tickets.columns and not tickets.empty else 0
        st.metric("Total tickets", total)
        st.metric("Open", open_count)
        st.metric("Resolved/Closed", resolved)

        if "created_at" in tickets.columns and not tickets.empty:
            try:
                tmp = tickets.copy()
                tmp["created_dt"] = pd.to_datetime(tmp["created_at"], errors="coerce").dt.date
                trend = tmp.groupby("created_dt").size().reset_index(name="count").tail(14)
                st.line_chart(trend.set_index("created_dt"))
            except Exception:
                st.info("Not enough data for trend yet.")

# ---------- Footer: freshness ----------
st.divider()
mtimes = {
    "employees":  datetime.fromtimestamp(employees_store.mtime).isoformat()  if employees_store.mtime  else "—",
    "tickets":    datetime.fromtimestamp(tickets_store.mtime).isoformat()    if tickets_store.mtime    else "—",
    "categories": datetime.fromtimestamp(categories_store.mtime).isoformat() if categories_store.mtime else "—",
    "comments":   datetime.fromtimestamp(comments_store.mtime).isoformat()   if comments_store.mtime   else "—",
}
st.caption(f"Data freshness • employees: {mtimes['employees']} | tickets: {mtimes['tickets']} | categories: {mtimes['categories']} | comments: {mtimes['comments']}")
