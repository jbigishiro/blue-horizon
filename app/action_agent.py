"""
Action agent: turns a guest's natural-language booking/cancellation
request into a structured "draft" that fills in over multiple turns,
then decides what to do once enough is known.

ARCHITECTURE NOTE — why this replaced the earlier text-accumulation
design: the previous version concatenated raw conversation text turn
after turn and re-extracted the ENTIRE booking from scratch every time.
That put all the "memory" of an in-progress booking on the LLM's
ability to correctly re-parse an ever-growing, unstructured blob —
which produced a string of real bugs: room numbers getting confused
with stale ones still present in the text, dates silently forgotten
because a later message didn't repeat them, etc. Each fix was another
prompt patch, and each patch risked breaking something else (which it
did, more than once).

This version instead keeps a real "draft" dict as explicit state:
    {action_type, room_type, room_number, check_in, check_out,
     num_adults, num_children, special_requests, booking_id}
Each turn, the LLM is asked ONLY "what does THIS message add or
change?" — never "reconstruct everything from scratch." Merging a new
field into the draft (or leaving an old one untouched) happens in
plain Python (merge_draft()), not by hoping the LLM's judgment about
recency holds up across an arbitrarily long conversation.

SECURITY NOTE: customer_id is NEVER extracted from the guest's message.
It must be passed in by the caller, representing an already-authenticated
session — see router.py / main.py for how it's threaded through.
"""

import json
import os
from datetime import date, datetime

from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import text

import booking_actions
from db import engine

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

DRAFT_FIELDS = [
    "action_type", "room_type", "room_number", "check_in", "check_out",
    "num_adults", "num_children", "special_requests", "booking_id",
]

EMPTY_DRAFT = {field: None for field in DRAFT_FIELDS}


class ActionError(Exception):
    """Raised when a request can't be understood or proposed."""


def _reference_today() -> date:
    """
    Returns the date this synthetic dataset should treat as "today" when
    resolving relative phrases like "tomorrow" or "next Friday". NOT
    date.today() — the seeded room_availability data covers a fixed
    historical window unrelated to the real calendar date the app
    happens to run on.
    """
    with engine.connect() as conn:
        return conn.execute(text("SELECT MIN(date) FROM room_availability")).scalar()


def _summarize_draft(draft: dict) -> str:
    known = [f"{k}: {v}" for k, v in draft.items() if v is not None]
    return "\n".join(known) if known else "(nothing established yet)"


UPDATE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "booking_update",
        "schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": ["string", "null"],
                    "enum": ["book", "cancel", None],
                    "description": (
                        "'book' or 'cancel' ONLY if the CURRENT message "
                        "newly states or changes this — e.g. 'I want to "
                        "book a room' is 'book', even with no other "
                        "details yet. If this was already established "
                        "earlier and the current message doesn't change "
                        "it, leave this null (null means 'no change', "
                        "not 'unclear')."
                    ),
                },
                "room_type": {
                    "type": ["string", "null"],
                    "description": "e.g. Standard, Deluxe, Suite, Presidential Suite — ONLY if newly stated/changed in the current message.",
                },
                "room_number": {
                    "type": ["integer", "null"],
                    "description": "A SPECIFIC room number if newly stated in the current message, e.g. 'room 111' -> 111. Different from room_type (a category).",
                },
                "check_in": {
                    "type": ["string", "null"],
                    "description": "ISO date YYYY-MM-DD, ONLY if newly stated/changed in the current message. May resolve a relative phrase ('tomorrow') using the reference date and any already-known dates for context, but only output it if THIS message is what specifies it.",
                },
                "check_out": {
                    "type": ["string", "null"],
                    "description": "ISO date YYYY-MM-DD, ONLY if newly stated/changed in the current message. E.g. '3 nights' or '3 days after' can be resolved using an already-known check_in as an anchor.",
                },
                "num_adults": {"type": ["integer", "null"]},
                "num_children": {"type": ["integer", "null"]},
                "special_requests": {"type": ["string", "null"]},
                "booking_id": {
                    "type": ["string", "null"],
                    "description": "For cancellations, a booking ID if newly stated in the current message, e.g. BK000123.",
                },
            },
            "required": DRAFT_FIELDS,
            "additionalProperties": False,
        },
        "strict": True,
    },
}


def extract_update(message: str, current_draft: dict) -> dict:
    """
    Extracts ONLY what the current message adds or changes, using the
    existing draft purely as context to resolve relative references
    (e.g. "3 nights" needs a known check_in to compute check_out from).
    Fields not mentioned in THIS message come back null — that null
    means "no change," and merge_draft() below is what actually
    preserves the old value; the LLM is never asked to reproduce state
    it isn't actively changing.
    """
    ref_date = _reference_today()
    prompt = (
        f"You are tracking a hotel booking/cancellation request being "
        f"built up across multiple conversation turns. Today's date is "
        f"{ref_date.isoformat()} — resolve relative dates against this.\n\n"
        f"Here is what's ALREADY been established so far:\n"
        f"{_summarize_draft(current_draft)}\n\n"
        f"Extract ONLY the new information the guest's CURRENT message "
        f"below adds or changes. Leave a field null if this message "
        f"doesn't mention or change it — even if it was already filled "
        f"in earlier. Leaving a field null here means 'unchanged', not "
        f"'clear it'. You may use the already-established details above "
        f"ONLY to resolve relative references in the current message "
        f"(e.g. computing check_out from an already-known check_in) — "
        f"but still only output a field if the CURRENT message is what "
        f"is actually stating or changing it.\n\n"
        f"If the current message contradicts an established value (e.g. "
        f"a different room number than before), output the NEW value — "
        f"that's a genuine change, not something to leave null.\n\n"
        f"Do not invent a room type, date, or booking ID that wasn't "
        f"stated or clearly implied."
    )

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": message},
        ],
        response_format=UPDATE_SCHEMA,
    )
    return json.loads(response.choices[0].message.content)


def merge_draft(draft: dict, update: dict) -> dict:
    """Plain Python merge: a non-null field in `update` overwrites the
    draft; a null field leaves the existing draft value untouched. This
    is the actual fix for the recurring memory bugs — correctness here
    doesn't depend on the LLM's judgment at all, just a dict update."""
    merged = dict(draft)
    for key, value in update.items():
        if value is not None:
            merged[key] = value
    return merged


def _safe_parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def decide(draft: dict, customer_id: int, exclude_booking_id: str | None = None) -> dict:
    """
    Given a (merged) draft, decides what response to give: ask for more
    info, report unavailability, or propose a specific booking/
    cancellation for confirmation. The returned dict always includes
    every DRAFT_FIELDS key (so it doubles as the draft state for the
    caller to persist and continue from next turn) plus "status" and
    "message", and for a pending_confirmation, "customer_id" and
    "total_price".
    """
    base = {k: draft.get(k) for k in DRAFT_FIELDS}
    action_type = draft.get("action_type")

    if action_type is None:
        return {
            **base,
            "status": "needs_clarification",
            "message": (
                "I'd like to help with that — could you tell me a bit more? "
                "For a booking: what room type and which dates? For a "
                "cancellation: your booking confirmation number."
            ),
        }

    if action_type == "cancel":
        booking_id = draft.get("booking_id")

        if booking_id:
            return {
                **base,
                "status": "pending_confirmation",
                "customer_id": customer_id,
                "message": f"Just to confirm — you'd like to cancel booking {booking_id}?",
            }

        recent = booking_actions.get_recent_bookings(
            customer_id, limit=2, exclude_booking_id=exclude_booking_id
        )
        if len(recent) == 1:
            b = recent[0]
            return {
                **base,
                "status": "pending_confirmation",
                "customer_id": customer_id,
                "booking_id": b["booking_id"],
                "message": (
                    f"I found your booking {b['booking_id']} — room "
                    f"{b['room_number']}, {b['check_in']} to "
                    f"{b['check_out']}, ${b['total_amount']:.2f}. "
                    f"Should I cancel this one?"
                ),
            }
        elif len(recent) > 1:
            options = "; ".join(
                f"{b['booking_id']} (room {b['room_number']}, {b['check_in']} to {b['check_out']})"
                for b in recent
            )
            return {
                **base,
                "status": "needs_clarification",
                "message": (
                    f"You have a few recent bookings — which one did you "
                    f"mean? {options}. Just give me the confirmation number."
                ),
            }
        elif exclude_booking_id:
            return {
                **base,
                "status": "needs_clarification",
                "message": (
                    f"Booking {exclude_booking_id} is actually your only "
                    f"current confirmed booking — there isn't another one "
                    f"on file. Would you like to keep it, or is there "
                    f"something else I can help with?"
                ),
            }
        else:
            return {
                **base,
                "status": "needs_clarification",
                "message": "What's the booking confirmation number you'd like to cancel?",
            }

    # action_type == "book"
    room_number = draft.get("room_number")
    room_type = draft.get("room_type")

    if not room_number and not room_type:
        return {
            **base,
            "status": "needs_clarification",
            "message": "What type of room would you like — Standard, Deluxe, Suite, or Presidential Suite?",
        }

    check_in = _safe_parse_date(draft.get("check_in"))
    check_out = _safe_parse_date(draft.get("check_out"))

    if check_in is None:
        return {**base, "status": "needs_clarification",
                "message": "I need a check-in date to proceed — could you specify one?"}
    if check_out is None:
        return {**base, "status": "needs_clarification",
                "message": "I need a check-out date to proceed — could you specify one?"}
    if check_out <= check_in:
        return {**base, "status": "needs_clarification",
                "message": "Check-out needs to be after check-in — could you double check the dates?"}

    num_adults = draft.get("num_adults") or 1
    num_children = draft.get("num_children") or 0

    if room_number:
        # A specific room was named directly — check exactly that room,
        # not a type-based search.
        try:
            result = booking_actions.check_room_number_availability(room_number, check_in, check_out)
        except booking_actions.BookingError as e:
            return {**base, "status": "needs_clarification", "message": str(e)}

        if result is None:
            return {
                **base,
                "status": "unavailable",
                "message": (
                    f"I'm sorry, room {room_number} isn't available for "
                    f"{check_in} to {check_out} (or that room number "
                    f"doesn't exist). Would you like to try different "
                    f"dates or a different room?"
                ),
            }

        return {
            **base,
            "status": "pending_confirmation",
            "customer_id": customer_id,
            "room_type": result["room_type"],
            "num_adults": num_adults,
            "num_children": num_children,
            "total_price": result["total_price"],
            "message": (
                f"Room {result['room_number']} ({result['room_type']}) is "
                f"available from {check_in} to {check_out} for "
                f"${result['total_price']:.2f} total. Shall I book it?"
            ),
        }

    # room_type search (no specific room number given)
    try:
        candidates = booking_actions.check_availability(room_type, check_in, check_out)
    except booking_actions.BookingError as e:
        return {**base, "status": "needs_clarification", "message": str(e)}

    if not candidates:
        return {
            **base,
            "status": "unavailable",
            "message": (
                f"I'm sorry, I don't see any {room_type} rooms available "
                f"from {check_in} to {check_out}. Would you like to try "
                f"different dates or a different room type?"
            ),
        }

    best = candidates[0]
    return {
        **base,
        "status": "pending_confirmation",
        "customer_id": customer_id,
        "room_number": best["room_number"],
        "num_adults": num_adults,
        "num_children": num_children,
        "total_price": best["total_price"],
        "message": (
            f"I found room {best['room_number']} ({room_type}) available "
            f"from {check_in} to {check_out} for ${best['total_price']:.2f} "
            f"total. Shall I book it?"
        ),
    }


def process_turn(message: str, customer_id: int, current_draft: dict,
                  exclude_booking_id: str | None = None) -> dict:
    """
    One turn of the slot-filling flow: extract what's new, merge it
    into the draft, decide what to do. Returns the same shape as
    decide() — draft fields + status + message (+ customer_id/
    total_price when proposing).
    """
    update = extract_update(message, current_draft)
    merged = merge_draft(current_draft, update)
    return decide(merged, customer_id, exclude_booking_id=exclude_booking_id)


def confirm(proposal: dict) -> dict:
    """
    Executes a proposal that was already shown to and approved by the
    guest. Only call this after explicit confirmation — never
    automatically after process_turn().
    """
    if proposal.get("status") != "pending_confirmation":
        raise ActionError("This proposal isn't in a confirmable state.")

    try:
        if proposal["action_type"] == "book":
            result = booking_actions.create_booking(
                customer_id=proposal["customer_id"],
                room_number=proposal["room_number"],
                check_in=datetime.strptime(proposal["check_in"], "%Y-%m-%d").date(),
                check_out=datetime.strptime(proposal["check_out"], "%Y-%m-%d").date(),
                num_adults=proposal.get("num_adults") or 1,
                num_children=proposal.get("num_children") or 0,
                special_requests=proposal.get("special_requests"),
            )
            return {
                "status": "confirmed",
                "message": (
                    f"You're all set! Booking {result['booking_id']} for room "
                    f"{result['room_number']}, {result['check_in']} to "
                    f"{result['check_out']}, total ${result['total_price']:.2f}. "
                    f"You earned {result['points_earned']} loyalty points."
                ),
                "result": result,
            }

        elif proposal["action_type"] == "cancel":
            result = booking_actions.cancel_booking(
                booking_id=proposal["booking_id"],
                customer_id=proposal["customer_id"],
            )
            return {
                "status": "confirmed",
                "message": f"Booking {result['booking_id']} has been cancelled.",
                "result": result,
            }

        else:
            raise ActionError(f"Unknown action_type: {proposal['action_type']}")

    except booking_actions.BookingError as e:
        return {"status": "failed", "message": str(e)}


if __name__ == "__main__":
    # Manual step-through test:
    #   python action_agent.py "<message>" ["<message>" ...]
    # Feeds each argument as a separate turn against a running draft.
    import sys

    draft = dict(EMPTY_DRAFT)
    customer_id = 1
    for msg in sys.argv[1:]:
        print(f"> {msg}")
        result = process_turn(msg, customer_id, draft)
        draft = {k: result.get(k) for k in DRAFT_FIELDS}
        print(f"  status: {result['status']}")
        print(f"  answer: {result['message']}")
        print(f"  draft:  {draft}\n")