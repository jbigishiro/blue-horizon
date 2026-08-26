
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel

import router
import session_store

load_dotenv()

app = FastAPI(title="Blue Horizon: AI Concierge for Luxury Hotels")

# Permissive CORS for local development against the future Streamlit
# frontend. Before deploying anywhere real, this should be narrowed to
# the actual frontend origin(s) rather than "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_yesno_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_YESNO_MODEL = os.environ.get("OPENAI_ROUTER_MODEL", "gpt-4o-mini")


class ChatRequest(BaseModel):
    session_id: str
    message: str
    customer_id: int | None = None  # authenticated guest's ID, if known


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    answer: str
    needs_confirmation: bool = False


def _classify_yes_no(message: str) -> str:
    """
    Returns "yes", "no", or "unclear" — used only when a session has a
    pending proposal awaiting confirmation. Kept separate from
    router.classify_intent() because this is a much narrower, different
    question ("did they just confirm THIS specific thing") than general
    intent classification.
    """
    response = _yesno_client.chat.completions.create(
        model=_YESNO_MODEL,
        max_tokens=5,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "The guest was just asked to confirm an action (a booking "
                    "or cancellation). Classify their reply as exactly one "
                    "word: yes, no, or unclear. Respond with only that word."
                ),
            },
            {"role": "user", "content": message},
        ],
    )
    result = (response.choices[0].message.content or "").strip().lower()
    return result if result in {"yes", "no"} else "unclear"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    session = session_store.get_session(req.session_id)

    # A session's customer_id, once known, sticks for the session even if
    # a later request omits it — but an explicitly-passed customer_id on
    # this request always takes precedence (e.g. login happening mid-session).
    customer_id = req.customer_id if req.customer_id is not None else session.get("customer_id")
    session["customer_id"] = customer_id

    session_store.append_turn(session, "user", req.message)

    pending = session.get("pending_proposal")

    if pending is not None:
        decision = _classify_yes_no(req.message)

        if decision == "yes":
            result = router.confirm_action(pending)
            session["pending_proposal"] = None
            answer = result["message"]
            intent = "action"
            needs_confirmation = False

        elif decision == "no":
            session["pending_proposal"] = None
            answer = "No problem — let me know if there's anything else I can help with."
            intent = "action"
            needs_confirmation = False

        else:
            # Didn't clearly answer yes or no — re-ask rather than
            # silently dropping or silently proceeding with the booking.
            answer = pending.get("message", "Should I go ahead with that? (yes/no)")
            intent = "action"
            needs_confirmation = True

    else:
        result = router.handle(req.message, customer_id=customer_id)
        intent = result["intent"]
        answer = result["answer"]

        proposal = result.get("proposal")
        if proposal and proposal.get("status") == "pending_confirmation":
            session["pending_proposal"] = proposal
            needs_confirmation = True
        else:
            session["pending_proposal"] = None
            needs_confirmation = False

    session_store.append_turn(session, "assistant", answer)
    session_store.save_session(req.session_id, session)

    return ChatResponse(
        session_id=req.session_id,
        intent=intent,
        answer=answer,
        needs_confirmation=needs_confirmation,
    )