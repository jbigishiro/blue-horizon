
import os
import re

from openai import OpenAI
from sqlalchemy import text

from db import engine
from schema_context import build_schema_context, build_whitelists

client = OpenAI()  
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

SCHEMA_CONTEXT = build_schema_context()
ALLOWED_TABLES, ALLOWED_COLUMNS = build_whitelists()

DEFAULT_ROW_LIMIT = 200

SYSTEM_PROMPT = f"""You are a SQL generator for the Blue Horizon hotel database (Postgres).
 
Given a guest's or staff member's natural-language question, output exactly
one SQL SELECT statement that answers it. Follow these rules strictly:
 
1. Output ONLY the SQL statement. No explanation, no markdown code fences,
   no preamble — just the raw SQL, ending in a semicolon.
2. Only use SELECT. Never generate INSERT, UPDATE, DELETE, DROP, ALTER,
   TRUNCATE, GRANT, or any other statement that modifies data or schema.
3. Only reference tables and columns that appear in the schema below.
   Never invent a column or table name.
4. Always include a LIMIT clause. If the question doesn't imply a specific
   number of results, use LIMIT {DEFAULT_ROW_LIMIT}.
5. If the question cannot be answered with the available schema, output
   exactly: NO_QUERY_POSSIBLE
6. When filtering on a text/string column (e.g. status, category, type,
   department), use ILIKE instead of = for the comparison, and do not
   assume a specific capitalization. You do not know the exact casing
   used in the stored data (it could be 'Available', 'available', or
   'AVAILABLE'), so ILIKE '%value%' or ILIKE 'value' is safer than a
   case-sensitive = comparison, which will silently return zero rows
   on a case mismatch rather than an error.

Schema:
{SCHEMA_CONTEXT}
"""

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CREATE|"
    r"EXECUTE|CALL|COPY|MERGE)\b",
    re.IGNORECASE,
)


class SQLGenerationError(Exception):
    """Raised when the LLM can't or won't produce a usable query."""


class SQLValidationError(Exception):
    """Raised when generated SQL fails a safety or schema check."""


def generate_sql(question: str) -> str:
    """Calls OpenAI to turn a natural-language question into one SQL SELECT."""
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=500,
        temperature=0,  # deterministic SQL generation, not creative writing
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
    )
    sql = (response.choices[0].message.content or "").strip()

    if sql == "NO_QUERY_POSSIBLE" or not sql:
        raise SQLGenerationError(
            "This question can't be answered with the available data."
        )

    sql = re.sub(r"^```sql\s*|^```\s*|```$", "", sql, flags=re.MULTILINE).strip()

    return sql


def validate_sql(sql: str) -> str:
    """
    Validates generated SQL before execution. Raises SQLValidationError
    with a specific reason on failure. Returns the (possibly LIMIT-added)
    SQL on success.
    """
    stripped = sql.strip().rstrip(";")

    if ";" in stripped:
        raise SQLValidationError("Multiple statements are not allowed.")

    if not re.match(r"^\s*SELECT\b", stripped, re.IGNORECASE):
        raise SQLValidationError("Only SELECT statements are allowed.")

    if _FORBIDDEN_KEYWORDS.search(stripped):
        raise SQLValidationError(
            "Query contains a forbidden keyword (write/DDL operation)."
        )

    referenced_tables = re.findall(
        r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", stripped, re.IGNORECASE
    )
    for t in referenced_tables:
        if t.lower() not in ALLOWED_TABLES:
            raise SQLValidationError(f"Unknown table referenced: '{t}'")

    # Enforce a LIMIT if the model didn't include one.
    if not re.search(r"\bLIMIT\s+\d+", stripped, re.IGNORECASE):
        stripped = f"{stripped} LIMIT {DEFAULT_ROW_LIMIT}"

    return stripped


def execute_sql(sql: str) -> list[dict]:
    """Runs a validated SELECT and returns rows as a list of dicts."""
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        columns = result.keys()
        return [dict(zip(columns, row)) for row in result.fetchall()]


def ask(question: str) -> dict:
    """
    End-to-end: question -> generated SQL -> validated SQL -> rows.

    Returns a dict: {"sql": str, "rows": list[dict]}
    Raises SQLGenerationError or SQLValidationError on failure — callers
    should catch these and show the guest a friendly fallback message
    rather than a raw stack trace.
    """
    raw_sql = generate_sql(question)
    safe_sql = validate_sql(raw_sql)
    rows = execute_sql(safe_sql)
    return {"sql": safe_sql, "rows": rows}


if __name__ == "__main__":
    # Quick manual test: python sql_agent.py
    import sys

    q = " ".join(sys.argv[1:]) or "can you change room RM000366 to booked from today?"
    print(f"Question: {q}\n")
    try:
        result = ask(q)
        print(f"SQL: {result['sql']}\n")
        print(f"Rows returned: {len(result['rows'])}")
        for row in result["rows"][:10]:
            print(row)
    except (SQLGenerationError, SQLValidationError) as e:
        print(f"Could not answer: {e}")