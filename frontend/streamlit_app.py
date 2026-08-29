"""
Blue Horizon concierge — Streamlit frontend.

Thin chat UI over the FastAPI /chat endpoint from Step 6. No business
logic lives here — this file's only job is rendering the conversation
and forwarding messages to the API.

Run:
    cd frontend
    streamlit run streamlit_app.py

Requires the FastAPI backend running separately (Step 6):
    cd app
    uvicorn main:app --reload --port 8000
"""

import os
import uuid

import requests
import streamlit as st

# Backend URL: checks Streamlit secrets first (the standard way to
# configure this on Streamlit Community Cloud — see .streamlit/secrets.toml
# or the app's Secrets settings in the dashboard), then an environment
# variable, then falls back to localhost for local development. Without
# this, a deployed frontend would silently keep trying to reach
# localhost:8000 on ITS OWN server, which is never where the backend
# actually lives once both services are deployed separately.
try:
    _secrets_url = st.secrets.get("API_BASE_URL")
except Exception:
    _secrets_url = None  # no secrets.toml present — normal for local dev

API_BASE_URL = (_secrets_url or os.environ.get("API_BASE_URL", "http://localhost:8000")).rstrip("/")
# .rstrip("/") matters: a trailing slash here (e.g. "...onrender.com/")
# would make every request go to ".../onrender.com//chat" — a double
# slash, which FastAPI treats as a genuinely different, non-existent
# path and returns 404 for. This one invisible character is a very easy
# mistake to make when copy-pasting a URL, so it's stripped here
# defensively rather than relying on it never happening again.

st.set_page_config(page_title="Blue Horizon Concierge", page_icon="🏨")

# --- Session state setup ----------------------------------------------
# session_id ties this browser session to conversation state stored in
# Redis by session_store.py on the backend. customer_id simulates an
# authenticated guest — there's no real login system yet (see the
# security note in action_agent.py), so it's just a sidebar input here.

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "customer_id" not in st.session_state:
    st.session_state.customer_id = None

if "messages" not in st.session_state:
    st.session_state.messages = []  # list of {"role": ..., "content": ...}

if "needs_confirmation" not in st.session_state:
    st.session_state.needs_confirmation = False


# --- Sidebar -------------------------------------------------------------

with st.sidebar:
    st.header("Blue Horizon")

    customer_id_input = st.number_input(
        "Guest ID (simulated login)",
        min_value=0,
        value=st.session_state.customer_id or 0,
        step=1,
        help=(
            "Stands in for a real authentication system, which doesn't "
            "exist yet. Booking or cancelling requires a guest ID here — "
            "0 means 'not logged in'."
        ),
    )
    st.session_state.customer_id = customer_id_input if customer_id_input > 0 else None

    if st.session_state.customer_id is None:
        st.warning("⚠️ Not signed in. Enter a Guest ID above to book or cancel a stay.")
    else:
        st.success(f"✅ Signed in as Guest #{st.session_state.customer_id}")

    st.divider()

    if st.button("🔄 New conversation"):
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.session_state.needs_confirmation = False
        st.rerun()

    st.caption(f"Session: `{st.session_state.session_id[:8]}...`")
    st.caption(f"Backend: `{API_BASE_URL}`")


# --- Main chat area --------------------------------------------------

st.title("🏨 Blue Horizon Concierge")
st.caption("Ask about rooms, amenities, policies — or book a stay.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        # Escape $ before rendering: Streamlit's markdown treats text
        # between two $ signs as LaTeX math, which mangles ordinary
        # dollar amounts (e.g. "$677.65 ... $2032.95" gets parsed as
        # one math expression spanning both numbers, stripping spaces
        # and producing garbled text like "677.65pernightonaverage").
        # This never affected the underlying data — only how it renders.
        st.markdown(msg["content"].replace("$", "\\$"))

if st.session_state.needs_confirmation:
    st.info("💬 Waiting on your confirmation above — reply yes or no.")


def send_message(text: str):
    st.session_state.messages.append({"role": "user", "content": text})

    try:
        response = requests.post(
            f"{API_BASE_URL}/chat",
            json={
                "session_id": st.session_state.session_id,
                "message": text,
                "customer_id": st.session_state.customer_id,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        answer = data["answer"]
        st.session_state.needs_confirmation = data.get("needs_confirmation", False)

    except requests.exceptions.ConnectionError:
        answer = (
            "⚠️ I can't reach the concierge service right now. Is the "
            "backend running? (`uvicorn main:app --reload --port 8000`)"
        )
        st.session_state.needs_confirmation = False
    except requests.exceptions.RequestException as e:
        answer = f"⚠️ Something went wrong talking to the concierge service: {e}"
        st.session_state.needs_confirmation = False

    st.session_state.messages.append({"role": "assistant", "content": answer})


user_input = st.chat_input("Ask about your stay, amenities, or book a room...")
if user_input:
    send_message(user_input)
    st.rerun()