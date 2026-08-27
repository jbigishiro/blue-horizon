"""
Blue Horizon chat API.

One real endpoint: POST /chat. Everything else (intent classification,
SQL generation, RAG retrieval, booking extraction) already lives in
router.py, sql_agent.py, rag_agent.py, and action_agent.py — this file's
only job is HTTP plumbing and conversation state across requests.

STATE MODEL: session["pending_action"] is either None (no booking/
cancellation in progress) or a dict with a "status" key:
    "pending_confirmation" -> awaiting an explicit yes/no from the guest
    "needs_clarification"  -> awaiting a missing detail (dates, room type, etc.)
    "unavailable"           -> the last specific attempt didn't pan out,
                               but the guest might reasonably retry with
                               a tweak ("how about tomorrow instead?")
In every case, this SAME dict also contains action_agent's DRAFT_FIELDS
(room_number, check_in, check_out, etc.) — decide() in action_agent.py
returns the draft fields alongside status/message, so this one object
doubles as both "what should the guest see next" and "what do we
already know," with no separate tracking needed. This replaced an
earlier design with two different session keys (a proposal dict and a
raw accumulated-text string) that were a real source of bugs.

Run locally:
    cd app
    uvicorn main:app --reload --port 8000

Then:
    curl -X POST http://localhost:8000/chat \\
        -H "Content-Type: application/json" \\
        -d '{"session_id": "test-1", "customer_id": 1, "message": "what time is check-in?"}'
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import action_agent
import router
import session_store

load_dotenv()

app = FastAPI(title="Blue Horizon Concierge API")

# Rate limiting: every /chat request triggers at least one LLM call
# (often several — intent classification, extraction, plus whichever
# agent handles it), so this endpoint is both a real cost surface and
# an abuse vector without a limit. 20/minute per client IP is a
# reasonable starting point for a demo — tune based on real usage.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS: FRONTEND_ORIGIN should be set to the actual deployed Streamlit
# URL in production (e.g. https://your-app.streamlit.app). Defaults to
# "*" only so local development keeps working without extra setup —
# this default is NOT safe to deploy as-is.
_frontend_origin = os.environ.get("FRONTEND_ORIGIN", "*")
if _frontend_origin == "*":
    print(
        "[main] WARNING: FRONTEND_ORIGIN is not set — CORS is wide open (*). "
        "Set FRONTEND_ORIGIN to your deployed frontend's URL before "
        "deploying this anywhere real."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin],
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


def _classify_reply_to_proposal(message: str, proposal: dict) -> str:
    """
    Returns one of: "confirm", "decline", "question", "new_request".

    Takes the full proposal dict (not just its message) so the prompt
    can disambiguate the overloaded word "cancel" — when the PENDING
    PROPOSAL is itself a cancellation, a reply like "I need to cancel
    booking BK050001" is easy to misread as declining ("cancel that")
    when it actually means something different: keep cancelling, just
    a different booking.
    """
    action_type = proposal.get("action_type", "book")
    proposal_message = proposal.get("message", "")

    if action_type == "cancel":
        disambiguation = (
            "IMPORTANT: this proposal is ITSELF a cancellation request. "
            "The word 'cancel' in the guest's reply refers to which "
            "booking they want cancelled, not necessarily to declining "
            "this confirmation. If they mention a different booking ID "
            "or say something like 'a different one' / 'another one', "
            "that's new_request (they still want to cancel — just not "
            "this specific booking). Only classify as decline if they "
            "clearly want to back out entirely (e.g. 'no', 'never mind', "
            "'don't cancel anything')."
        )
    else:
        disambiguation = (
            "If they mention a different room type, different dates, "
            "or ask for something cheaper/different, that's new_request "
            "— they still want to book, just not this specific offer."
        )

    response = _yesno_client.chat.completions.create(
        model=_YESNO_MODEL,
        max_tokens=10,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    f"The guest was just offered this: \"{proposal_message}\" "
                    "and asked to confirm. Classify their reply as exactly one "
                    "of these four words:\n"
                    "confirm — they agree to proceed (yes, sounds good, go ahead)\n"
                    "decline — they want to back out of this entirely, with "
                    "no alternative in mind\n"
                    "question — they're asking something ABOUT this specific "
                    "offer without rejecting it (price, amenities, policy)\n"
                    "new_request — they want something DIFFERENT instead\n\n"
                    f"{disambiguation}\n\n"
                    "Respond with only one of: confirm, decline, question, new_request"
                ),
            },
            {"role": "user", "content": message},
        ],
    )
    result = (response.choices[0].message.content or "").strip().lower()
    valid = {"confirm", "decline", "question", "new_request"}
    return result if result in valid else "question"  # safest default: ask, don't discard or act


def _draft_from(proposal: dict) -> dict:
    """Extracts just the draft fields from a proposal dict, dropping
    status/message/total_price/etc. Used when we need to continue or
    partially reset a draft based on a previous proposal."""
    return {k: proposal.get(k) for k in action_agent.DRAFT_FIELDS}


def _apply_result(result: dict, session: dict) -> tuple[str, str, bool]:
    """
    Given a router action result ({"intent","answer","proposal"}),
    updates session["pending_action"] appropriately and returns
    (intent, answer, needs_confirmation). Shared by every code path
    that produces an action result, so the session-state bookkeeping
    only lives in one place.
    """
    intent = result["intent"]
    answer = result["answer"]
    proposal = result.get("proposal")

    if proposal is None:
        session["pending_action"] = None
        return intent, answer, False

    session["pending_action"] = proposal
    needs_confirmation = proposal.get("status") == "pending_confirmation"
    return intent, answer, needs_confirmation


def _classify_continuation(message: str, pending: dict) -> str:
    """
    Returns "continue" or "abandon". Used only when there's an
    in-progress draft in "needs_clarification" or "unavailable" state —
    i.e. nothing has been formally proposed yet, so there's no yes/no
    to classify, just "is the guest still working on this, or have they
    moved on to something else?"

    Without this gate, EVERY message after a clarification/unavailable
    response — including totally unrelated ones like a greeting or an
    FAQ question — got silently treated as continuing the stale draft,
    since the draft doesn't change when a message doesn't mention any
    of its fields. That's correct behavior for an actual continuation
    ("tomorrow" filling in a missing date) but wrong for an unrelated
    message, which should abandon the draft and go through normal
    intent classification instead.
    """
    response = _yesno_client.chat.completions.create(
        model=_YESNO_MODEL,
        max_tokens=10,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    f"The guest was just asked: \"{pending.get('message', '')}\" "
                    "as part of an in-progress booking/cancellation request "
                    "that isn't finished yet (no offer has been made to "
                    "confirm). Classify their reply as exactly one word:\n"
                    "continue — they're answering that question or still "
                    "working on the same request (giving a date, a "
                    "different room/type, adjusting details, retrying)\n"
                    "abandon — they've moved on to something unrelated "
                    "(a general question, small talk, an unrelated search, "
                    "or anything not about completing this specific "
                    "request)\n"
                    "Respond with only one word: continue or abandon"
                ),
            },
            {"role": "user", "content": message},
        ],
    )
    result = (response.choices[0].message.content or "").strip().lower()
    # Default to "abandon" on a malformed/unexpected response, not
    # "continue" — the observed failure mode when this leans the wrong
    # way is far worse in one direction than the other. Wrongly
    # abandoning just means asking the guest to restart a request that's
    # easy to repeat. Wrongly continuing is what caused the actual bug:
    # every unrelated message (a greeting, an FAQ question) got silently
    # trapped repeating the same stale "unavailable" answer forever.
    return result if result in {"continue", "abandon"} else "abandon"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")
def chat(request: Request, req: ChatRequest):
    session = session_store.get_session(req.session_id)

    customer_id = req.customer_id if req.customer_id is not None else session.get("customer_id")
    session["customer_id"] = customer_id

    session_store.append_turn(session, "user", req.message)

    pending = session.get("pending_action")

    if pending is not None and pending.get("status") == "pending_confirmation":
        decision = _classify_reply_to_proposal(req.message, pending)

        if decision == "confirm":
            result = router.confirm_action(pending)
            session["pending_action"] = None
            answer = result["message"]
            intent = "action"
            needs_confirmation = False

        elif decision == "decline":
            session["pending_action"] = None
            answer = "No problem — let me know if there's anything else I can help with."
            intent = "action"
            needs_confirmation = False

        elif decision == "new_request":
            # Guest abandoned the pending offer for something different.
            # With structured drafts, "abandoning" is precise: clear only
            # the specific field(s) that were being offered, keep
            # everything else (dates, party size, etc.) — no need to
            # re-derive the whole request from scratch, and no risk of
            # losing details that were never in question.
            prior_action_type = pending.get("action_type")
            cleared = _draft_from(pending)

            if prior_action_type == "cancel":
                exclude_id = cleared.get("booking_id")
                cleared["booking_id"] = None
                action_result = router.continue_draft(
                    req.message, customer_id, cleared, exclude_booking_id=exclude_id
                )
            elif prior_action_type == "book":
                cleared["room_number"] = None
                cleared["room_type"] = None
                action_result = router.continue_draft(req.message, customer_id, cleared)
            else:
                action_result = router.continue_draft(
                    req.message, customer_id, dict(action_agent.EMPTY_DRAFT)
                )

            intent, answer, needs_confirmation = _apply_result(action_result, session)

        else:  # "question"
            # Genuinely asking about the current offer — answer it, then
            # remind them the original proposal is still on the table.
            # The proposal itself is left untouched in session state.
            side_answer = router.answer_side_question(req.message, customer_id, pending)
            answer = (
                f"{side_answer}\n\n"
                f"To confirm the earlier request: {pending['message']}"
            )
            intent = "action"
            needs_confirmation = True

    elif pending is not None:
        # status is "needs_clarification" or "unavailable" — an
        # in-progress draft waiting on more information. First check
        # whether the guest is actually continuing it, or has moved on
        # to something else entirely — see _classify_continuation() for
        # why this gate is necessary.
        decision = _classify_continuation(req.message, pending)

        if decision == "continue":
            draft = _draft_from(pending)
            action_result = router.continue_draft(req.message, customer_id, draft)
            intent, answer, needs_confirmation = _apply_result(action_result, session)
        else:  # "abandon"
            session["pending_action"] = None
            action_result = router.handle(req.message, customer_id=customer_id)
            intent, answer, needs_confirmation = _apply_result(action_result, session)

    else:
        action_result = router.handle(req.message, customer_id=customer_id)
        intent, answer, needs_confirmation = _apply_result(action_result, session)

    session_store.append_turn(session, "assistant", answer)
    session_store.save_session(req.session_id, session)

    return ChatResponse(
        session_id=req.session_id,
        intent=intent,
        answer=answer,
        needs_confirmation=needs_confirmation,
    )