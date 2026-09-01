"""
Smoke test for the Blue Horizon backend — run this against a freshly
deployed (or freshly restarted local) backend before trusting it.

This is intentionally a SMALL, fast checklist, not a full test suite:
one check per major subsystem (health, each intent type, and the
booking propose/confirm flow), so a real deployment issue (wrong env
var, unreachable DB, wrong Redis URL, etc.) surfaces in seconds rather
than requiring a manual click-through.

Usage:
    python smoke_test.py                          # tests localhost:8000
    python smoke_test.py https://your-api.onrender.com
"""

import sys
import uuid

import requests

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
SESSION_ID = f"smoke-test-{uuid.uuid4()}"
TEST_CUSTOMER_ID = 1  # must exist in your seeded customers table

PASS = "✅"
FAIL = "❌"
results = []


def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    print(f"{status} {name}" + (f" — {detail}" if detail and not condition else ""))
    results.append(condition)


def chat(message: str, customer_id: int | None = TEST_CUSTOMER_ID, session_id: str | None = None) -> dict:
    resp = requests.post(
        f"{BASE_URL}/chat",
        json={"session_id": session_id or SESSION_ID, "message": message, "customer_id": customer_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    print(f"Running smoke test against {BASE_URL}\n")

    # 1. Health check — confirms the service is up at all.
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=10)
        check("Health endpoint responds", resp.status_code == 200, f"status {resp.status_code}")
    except requests.exceptions.RequestException as e:
        check("Health endpoint responds", False, str(e))
        print("\nBackend is unreachable — stopping here, nothing else will work.")
        sys.exit(1)

    # 2. Knowledge query — confirms RAG/Redis vector index is reachable.
    try:
        result = chat("What time is check-in?")
        check(
            "Knowledge query (RAG/Redis) works",
            result["intent"] == "knowledge_query" and len(result["answer"]) > 0,
            f"got intent={result.get('intent')!r}",
        )
    except Exception as e:
        check("Knowledge query (RAG/Redis) works", False, str(e))

    # 3. SQL query — confirms Postgres + read-only role are reachable.
    try:
        result = chat("How many rooms do we have?")
        check(
            "SQL query (Postgres) works",
            result["intent"] == "sql_query" and len(result["answer"]) > 0,
            f"got intent={result.get('intent')!r}",
        )
    except Exception as e:
        check("SQL query (Postgres) works", False, str(e))

    # 4. Chitchat — confirms the OpenAI key / router model work at all.
    try:
        result = chat("hello")
        check(
            "Chitchat (OpenAI connectivity) works",
            result["intent"] == "chitchat" and len(result["answer"]) > 0,
            f"got intent={result.get('intent')!r}",
        )
    except Exception as e:
        check("Chitchat (OpenAI connectivity) works", False, str(e))

    # 5. Action flow: propose without login should be declined, not crash.
    #    IMPORTANT: uses a FRESH session_id, not the shared SESSION_ID —
    #    the shared session already has customer_id=1 attached from
    #    checks 2-4 (a session remembers who's logged in across turns,
    #    which is correct conversational behavior), so reusing it here
    #    wouldn't actually simulate an unauthenticated guest at all.
    try:
        fresh_session = f"smoke-test-unauth-{uuid.uuid4()}"
        result = chat(
            "book me a standard room for tomorrow",
            customer_id=None,
            session_id=fresh_session,
        )
        check(
            "Unauthenticated booking is declined gracefully",
            "sign" in result["answer"].lower() or "log" in result["answer"].lower(),
            f"got: {result['answer'][:80]}",
        )
    except Exception as e:
        check("Unauthenticated booking is declined gracefully", False, str(e))

    # 6. Action flow: a full propose step (not confirming, to avoid
    #    creating real data during a smoke test) should not error.
    try:
        result = chat("book me a standard room for tomorrow, one night")
        check(
            "Booking proposal flow runs without error",
            result["intent"] == "action" and len(result["answer"]) > 0,
            f"got intent={result.get('intent')!r}",
        )
    except Exception as e:
        check("Booking proposal flow runs without error", False, str(e))

    print()
    passed = sum(results)
    total = len(results)
    print(f"{passed}/{total} checks passed.")
    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()