# 🌐 SAP360 – AI-Powered Onboarding, Mentorship, and Upskilling Platform

### 🚀 Overview
**SAP360** transforms new hire onboarding and continuous upskilling by combining:
- 🤖 an **AI chatbot** for instant support(Onboarding,Mentormatch and Tickets agent),
- 🧭 **MentorMatch** for early mentor connections,
- 📚 a **Learning Hub** for skill development and readiness tracking,
- 🎟️ **MyTickets** for streamlined employee support.

The solution integrates **AI-driven learning personalization**, **mentor engagement**, and **data visualization dashboards** into one unified employee experience.

---

## 🧩 Key Modules & Features

| **Page / Module** | **Key Features** |
|--------------------|------------------|
| **Homepage – SAP-powered Intelligent Chatbot** | 1️⃣ AI chatbot for onboarding and SAP-knowledge Q&A.<br>2️⃣ **MentorMatch Agent** for mentor discovery, availability, and booking. <br> 3️⃣ **Tickets** for easy ticket raising 
| **Mentee Requests** | 1️⃣ Approve or decline mentorship session requests.<br>2️⃣ Automatically generate **ICS invites** to add sessions to both mentee and mentor calendars.<br> 3️⃣ Write Takeaways after a session - this takeaway is also linked to learninghub, to give course sugesstions. 
| **Learning Hub** | 1️⃣ Analyze skill gaps for current or target role.<br>2️⃣ Generate **Gemini-driven phased learning plans**.<br>3️⃣ Track progress through phases, checkpoints, and readiness metrics. |
| **MyTickets** | 1️⃣ Create support tickets with category, priority, and description.<br>2️⃣ View and update assigned or created tickets. |
| **Dashboard** | **Tabs include:**<br>• **Profile:** Manage employee details.<br>• **Onboarding Learning Modules:** Track onboarding and assigned learning tasks.<br>• **Mentor Sessions:** View upcoming mentorship interactions.<br>• **Learning Hub Progress:** Visualize phase progress and completion.<br>• **Career Progress:** Display readiness scores and trajectory indicators. |

---

## ⚙️ Technology Stack

| **Category** | **Technologies Used** |
|---------------|-----------------------|
| **UI** | Streamlit, custom HTML/CSS for dashboard, Altair (for visualization) |
| **Chatbot / AI** | Google Gemini (LLM), LangChain (for orchestration & memory management) |
| **Database / Storage** | JSON, SQLite, CSV |
| **Learning Hub** | Gemini-powered skill analysis & plan generation |
| **Integration / Scheduling** | ICS calendar invites for mentorship sessions |

---


## 🧰 Installation & Setup

1. **Clone the Repository**
  
3. **Install Requirements**
   ```bash
   pip install -r requirements.txt

3. **Create database with SQlite**
   ```bash
   Run create_db.py

4. **Insert your own gemini API key inside .env**

5. **Run the Application**
   
  Streamlit run _Homepage.py 

6. **Login**

For login, you can use any email inside main employee dataset called "Employee Dataset1.csv"
 eg: clarissa.tan@company.com 

Some sample prompts for chatbot:
- Find a mentor who knows python
- What documents do i need to sign
- what are the learning modules i need to complete
- Raise a ticket 
