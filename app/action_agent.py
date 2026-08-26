
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
 
 
def _reference_today() -> date:
    """
    Returns the date this synthetic dataset should treat as "today" when
    resolving relative phrases like "tomorrow" or "next Friday".
 
    This is NOT date.today() — the seeded room_availability data covers a
    fixed historical window (roughly all of 2025) that has no relation to
    the real calendar date the app happens to run on. Using the real
    date.today() here caused "book me a room for tomorrow" to resolve to
    a 2026 date that doesn't exist anywhere in the availability data,
    which silently returned "no rooms available" instead of an honest
    error. Anchoring to the dataset's own earliest date keeps relative
    dates meaningful against the data that actually exists.
    """
    with engine.connect() as conn:
        return conn.execute(text("SELECT MIN(date) FROM room_availability")).scalar()
 
EXTRACTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "booking_request",
        "schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["book", "cancel", "unclear"],
                },
                "room_type": {
                    "type": ["string", "null"],
                    "description": "e.g. Standard, Deluxe, Suite, Presidential Suite. Null if not mentioned or action is cancel.",
                },
                "check_in": {
                    "type": ["string", "null"],
                    "description": "ISO date YYYY-MM-DD, null if not mentioned or unparseable.",
                },
                "check_out": {
                    "type": ["string", "null"],
                    "description": "ISO date YYYY-MM-DD, null if not mentioned or unparseable.",
                },
                "num_adults": {"type": ["integer", "null"]},
                "num_children": {"type": ["integer", "null"]},
                "special_requests": {"type": ["string", "null"]},
                "booking_id": {
                    "type": ["string", "null"],
                    "description": "For cancellations, the booking ID if the guest mentioned one, e.g. BK000123.",
                },
            },
            "required": [
                "action_type", "room_type", "check_in", "check_out",
                "num_adults", "num_children", "special_requests", "booking_id",
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
}
 
def _extraction_prompt() -> str:
    ref_date = _reference_today()
    return (
        f"Extract structured booking details from the guest's message. "
        f"Today's date is {ref_date.isoformat()} — resolve relative dates "
        f'("next Friday", "in two weeks", "tomorrow") against this date, '
        f"not any other date you might otherwise assume. If a date can't "
        f"be resolved confidently, leave it null rather than guessing. Do "
        f"not invent a room type, date, or booking ID that wasn't stated "
        f"or clearly implied."
    )
 
 
class ActionError(Exception):
    """Raised when a request can't be understood or proposed."""
 
 
def _extract(question: str) -> dict:
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": _extraction_prompt()},
            {"role": "user", "content": question},
        ],
        response_format=EXTRACTION_SCHEMA,
    )
    return json.loads(response.choices[0].message.content)
 
 
def _parse_date(value: str | None, field_name: str) -> date:
    if not value:
        raise ActionError(f"I need a {field_name} to proceed — could you specify one?")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ActionError(f"I couldn't understand the {field_name} you gave.")
 
 
def propose(question: str, customer_id: int) -> dict:
    """
    Extracts intent and, for a booking, checks real availability —
    but does NOT write anything to the database. Returns a proposal
    the caller should present to the guest for explicit confirmation.
    """
    extracted = _extract(question)
    action_type = extracted["action_type"]
 
    if action_type == "unclear":
        return {
            "status": "needs_clarification",
            "message": (
                "I'd like to help with that — could you tell me a bit more? "
                "For a booking: what room type and which dates? For a "
                "cancellation: your booking confirmation number."
            ),
        }
 
    if action_type == "cancel":
        booking_id = extracted.get("booking_id")
        if not booking_id:
            return {
                "status": "needs_clarification",
                "message": "What's the booking confirmation number you'd like to cancel?",
            }
        return {
            "status": "pending_confirmation",
            "action_type": "cancel",
            "customer_id": customer_id,
            "booking_id": booking_id,
            "message": f"Just to confirm — you'd like to cancel booking {booking_id}?",
        }
 
    # action_type == "book"
    if not extracted.get("room_type"):
        return {
            "status": "needs_clarification",
            "message": "What type of room would you like — Standard, Deluxe, Suite, or Presidential Suite?",
        }
 
    check_in = _parse_date(extracted.get("check_in"), "check-in date")
    check_out = _parse_date(extracted.get("check_out"), "check-out date")
 
    candidates = booking_actions.check_availability(
        extracted["room_type"], check_in, check_out
    )
    if not candidates:
        return {
            "status": "unavailable",
            "message": (
                f"I'm sorry, I don't see any {extracted['room_type']} rooms "
                f"available from {check_in} to {check_out}. Would you like "
                f"to try different dates or a different room type?"
            ),
        }
 
    best = candidates[0]
    return {
        "status": "pending_confirmation",
        "action_type": "book",
        "customer_id": customer_id,
        "room_number": best["room_number"],
        "room_type": extracted["room_type"],
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "num_adults": extracted.get("num_adults") or 1,
        "num_children": extracted.get("num_children") or 0,
        "special_requests": extracted.get("special_requests"),
        "total_price": best["total_price"],
        "message": (
            f"I found room {best['room_number']} ({extracted['room_type']}) "
            f"available from {check_in} to {check_out} for "
            f"${best['total_price']:.2f} total. Shall I book it?"
        ),
    }
 
 
def confirm(proposal: dict) -> dict:
    """
    Executes a proposal that was already shown to and approved by the
    guest. Only call this after explicit confirmation — never
    automatically after propose().
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
                num_adults=proposal["num_adults"],
                num_children=proposal["num_children"],
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
    # Manual test walkthrough:
    #   python action_agent.py propose <customer_id> "<question>"
    import sys
 
    if len(sys.argv) < 2:
        print("Usage: python action_agent.py propose <customer_id> \"<question>\"")
        sys.exit(1)
 
    mode = sys.argv[1]
    if mode == "propose":
        cid = int(sys.argv[2])
        q = " ".join(sys.argv[3:])
        result = propose(q, cid)
        print(json.dumps(result, indent=2))