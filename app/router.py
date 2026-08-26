import os
 
from dotenv import load_dotenv
from openai import OpenAI
 
import sql_agent
import rag_agent
import action_agent
 
load_dotenv()
 
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ROUTER_MODEL = os.environ.get("OPENAI_ROUTER_MODEL", "gpt-4o-mini")
 
VALID_INTENTS = {"sql_query", "knowledge_query", "action", "chitchat"}
 
CLASSIFIER_PROMPT = """You are an intent classifier for a hotel concierge system. \
Classify the guest's message into exactly one of these four categories:
 
sql_query — Questions answerable from structured hotel data: room \
availability, pricing, existing bookings, staff schedules, payments, \
event space availability, service appointments. Anything that needs a \
lookup in a database table.
Examples: "what rooms are available", "how much is the ocean suite",
"do you have a spa appointment open tomorrow"
 
knowledge_query — Questions about policies, procedures, amenities \
descriptions, or nearby recommendations. Anything answerable from a \
static FAQ, amenity description, or recommendation entry rather than a \
live data lookup.
Examples: "what time is check-in", "what's your cancellation policy",
"do you have a pool", "any good restaurants nearby"
 
action — The guest is trying to DO something that changes data: book a \
room, cancel a reservation, modify a booking, make a payment.
Examples: "book me the ocean suite for Friday", "cancel my reservation",
"I'd like to reserve a spa treatment"
 
chitchat — Greetings, thanks, small talk, or anything not related to \
hotel information or bookings.
Examples: "hello", "thank you", "how are you"
 
Respond with ONLY the category name, nothing else: sql_query, \
knowledge_query, action, or chitchat.
"""
 
 
def classify_intent(question: str) -> str:
    response = client.chat.completions.create(
        model=ROUTER_MODEL,
        max_tokens=10,
        temperature=0,
        messages=[
            {"role": "system", "content": CLASSIFIER_PROMPT},
            {"role": "user", "content": question},
        ],
    )
    intent = (response.choices[0].message.content or "").strip().lower()
 
    if intent not in VALID_INTENTS:
        # Model returned something unexpected — fail toward the safest,
        # cheapest path rather than guessing into a SQL or write-adjacent
        # flow on a malformed classification.
        intent = "chitchat"
 
    return intent
 
 
def _handle_sql_query(question: str) -> dict:
    try:
        result = sql_agent.ask(question)
        if not result["rows"]:
            answer = "I couldn't find anything matching that — could you try rephrasing?"
        else:
            answer = _format_rows_for_guest(result["rows"])
        return {"intent": "sql_query", "answer": answer, "raw": result}
    except (sql_agent.SQLGenerationError, sql_agent.SQLValidationError) as e:
        return {
            "intent": "sql_query",
            "answer": "I wasn't able to look that up — could you rephrase the question?",
            "error": str(e),
        }
 
 
def _handle_knowledge_query(question: str) -> dict:
    result = rag_agent.answer_from_knowledge_base(question)
    return {"intent": "knowledge_query", "answer": result["answer"], "raw": result}
 
 
def _handle_action(question: str, customer_id: int | None) -> dict:
    if customer_id is None:
        # No authenticated guest identity available — can't safely book
        # or cancel anything on anyone's behalf. This should only happen
        # if the calling layer (future FastAPI/Streamlit) hasn't wired
        # up session identity yet.
        return {
            "intent": "action",
            "answer": (
                "I can help with that once you're signed in — please log in "
                "and I'll be glad to check availability or make changes to "
                "your booking."
            ),
        }
 
    proposal = action_agent.propose(question, customer_id)
    # IMPORTANT: propose() never writes to the database. The caller
    # (chat UI / API layer) must get an explicit "yes" from the guest
    # and call action_agent.confirm(proposal) separately — this router
    # does not auto-confirm.
    return {
        "intent": "action",
        "answer": proposal["message"],
        "proposal": proposal,  # caller needs this to call confirm() later
    }
 
 
def _handle_chitchat(question: str) -> dict:
    response = client.chat.completions.create(
        model=ROUTER_MODEL,
        max_tokens=100,
        temperature=0.7,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the Blue Horizon hotel concierge. Respond warmly "
                    "and briefly to small talk or greetings. If the guest seems "
                    "to want hotel information, gently invite them to ask."
                ),
            },
            {"role": "user", "content": question},
        ],
    )
    answer = (response.choices[0].message.content or "").strip()
    return {"intent": "chitchat", "answer": answer}
 
 
def _format_rows_for_guest(rows: list[dict], max_rows: int = 10) -> str:
    """
    Very simple guest-facing formatting of raw SQL rows. This is a
    placeholder — for a polished product you'd likely want another LLM
    pass that turns the rows into natural language, or structured
    formatting per query type. Good enough to verify the pipeline works
    end to end for now.
    """
    preview = rows[:max_rows]
    lines = [", ".join(f"{k}: {v}" for k, v in row.items()) for row in preview]
    suffix = f"\n(...and {len(rows) - max_rows} more)" if len(rows) > max_rows else ""
    return "\n".join(lines) + suffix
 
 
_HANDLERS = {
    "sql_query": lambda q, customer_id: _handle_sql_query(q),
    "knowledge_query": lambda q, customer_id: _handle_knowledge_query(q),
    "action": _handle_action,
    "chitchat": lambda q, customer_id: _handle_chitchat(q),
}
 
 
def handle(question: str, customer_id: int | None = None) -> dict:
    """
    Full pipeline: classify -> dispatch -> return.
    Returns at minimum {"intent": str, "answer": str}.
 
    customer_id: the authenticated guest's ID, if known. Required for
    the "action" intent (booking/cancelling) — passing None means any
    booking/cancellation attempt will be declined rather than guessing
    or trusting an identity claimed in the message text.
    """
    intent = classify_intent(question)
    return _HANDLERS[intent](question, customer_id)
 
 
def confirm_action(proposal: dict) -> dict:
    """
    Executes a previously proposed action after the guest has explicitly
    confirmed it. The caller (chat UI / API layer) is responsible for
    getting that confirmation before calling this — see action_agent.py
    for the full safety rationale.
    """
    return action_agent.confirm(proposal)
 
 
if __name__ == "__main__":
    import sys
 
    q = " ".join(sys.argv[1:]) or "What time is check-in?"
    print(f"Question: {q}\n")
    result = handle(q, customer_id=1)  # test customer_id, matches seeded data
    print(f"Intent: {result['intent']}\n")
    print(f"Answer: {result['answer']}")
    if result.get("proposal", {}).get("status") == "pending_confirmation":
        print("\n(This action needs confirmation — call router.confirm_action(proposal) to execute it.)")
 