import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage
from agent import agent

st.title("Mentor Match")

# initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# display chat messages
for message in st.session_state.messages:
    if isinstance(message, HumanMessage):
        with st.chat_message("user"):
            st.markdown(message.content)
    elif isinstance(message, AIMessage):
        with st.chat_message("assistant"):
            st.markdown(message.content)

# input bar
prompt = st.chat_input("Ask for a mentor...")

if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
        st.session_state.messages.append(HumanMessage(prompt))

    # run agent
    result = agent.invoke({"input": prompt})
    response = result["output"]

    # ✅ Try parsing structured mentor results
    try:
        mentors = eval(response)  # agent already returns a Python-list-like string
    except Exception:
        mentors = None

    with st.chat_message("assistant"):
        if isinstance(mentors, list) and all("name" in m for m in mentors):
            st.markdown("Here are some mentors you can choose:")

            cols = st.columns(len(mentors))
            for i, mentor in enumerate(mentors):
                with cols[i]:
                    st.subheader(mentor["name"])
                    st.caption(f"{mentor['position']} • {mentor['department']}")
                    st.markdown(f"**Skills:** {mentor['skills']}")
                    st.markdown(f"**Experience:** {mentor['months_experience']} months")
                    
                    # show availability as buttons
                    for slot in mentor.get("availability", []):
                        if st.button(f"📅 {slot}", key=f"{mentor['id']}_{slot}"):
                            st.session_state.messages.append(
                                AIMessage(f"You selected {mentor['name']} at {slot}")
                            )
                            st.success(f"Requested {mentor['name']} at {slot}")

        else:
            # fallback → plain response
            st.markdown(response)

        st.session_state.messages.append(AIMessage(response))
