# 🏨 Blue Horizon — AI Concierge for Luxury Hotels

An AI-powered concierge system for a fictional luxury hotel. Guests can ask about
amenities and policies, check room availability and pricing, and actually book or
cancel a stay — all through natural conversation.

Built as an end-to-end learning project covering schema design, text-to-SQL,
retrieval-augmented generation (RAG), structured multi-turn agent design, and
production deployment.

## What it does

- **Answers policy/amenity questions** by retrieving from a vector index of the
  hotel's FAQs, amenities, and local recommendations (RAG)
- **Answers data questions** ("what rooms are available?", "how many bookings do we
  have today?") by generating and safely executing SQL against a real Postgres
  database (text-to-SQL)
- **Books and cancels rooms** through a structured, multi-turn conversation with an
  explicit confirm-before-acting step — no reservation is ever created or cancelled
  without the guest saying yes first
- **Routes each message automatically** to whichever of the above actually applies,
  or just responds to small talk

## Architecture

```mermaid
flowchart TD
    UI["Streamlit chat UI"] -->|HTTP| API["FastAPI /chat endpoint"]
    API --> Router["Intent Router"]
    Router -->|knowledge question| RAG["RAG Agent<br/>(LlamaIndex + Redis)"]
    Router -->|data question| SQL["Text-to-SQL Agent<br/>(validated, read-only)"]
    Router -->|booking/cancellation| Action["Action Agent<br/>(structured slot-filling)"]
    Router -->|small talk| Chat["Chitchat"]
    RAG --> Redis[(Redis<br/>vector index)]
    SQL --> PGRead[(Postgres<br/>read-only role)]
    Action --> PGWrite[(Postgres<br/>full-access role)]
    API --> Session[(Redis<br/>session state)]
```

Two data stores, deliberately separated by access level:
- **Postgres (Neon)** — structured hotel data across 19 tables (rooms, bookings,
  staff, payments, feedback, etc.). The SQL agent connects through a **dedicated
  read-only role** so a bad SQL generation can never modify data, regardless of
  what the application-level validation catches.
- **Redis** — both the vector index for RAG (FAQs/amenities/recommendations
  embedded via OpenAI) and per-session conversation state (what's being booked,
  what's awaiting confirmation).

## Why a structured booking agent, not just prompting

Early versions tried to track an in-progress booking by concatenating raw
conversation text and re-extracting the whole request from scratch on every turn.
This produced real, recurring bugs: a later message could silently erase an
earlier answer, or the model would latch onto a stale value still sitting
somewhere in the growing text blob.

The current design keeps a real structured "draft" (`action_type`, `room_type`,
`room_number`, `check_in`, `check_out`, etc.) as explicit state. Each turn, the
LLM is only asked *"what does this one message add or change?"* — merging that
into the draft happens in plain Python, not by hoping the model's judgment about
recency holds up indefinitely. This is the difference between "the AI remembers
correctly" and "the AI can't *not* remember correctly."

## Tech stack

| Layer | Technology |
|---|---|
| LLM | OpenAI (GPT-4o / GPT-4o-mini) |
| Structured data | Neon Postgres + SQLAlchemy |
| Vector search / session state | Redis + LlamaIndex |
| Backend API | FastAPI + slowapi (rate limiting) |
| Frontend | Streamlit |
| Testing | pytest |
| Deployment | Render (backend) + Streamlit Community Cloud (frontend) |

## Project structure

```
blue-horizon/
├── app/
│   ├── main.py              # FastAPI app, session state, /chat endpoint
│   ├── router.py            # Intent classification + dispatch
│   ├── sql_agent.py         # Text-to-SQL: generate, validate, execute
│   ├── rag_agent.py         # RAG query against the Redis vector index
│   ├── build_rag_index.py   # Ingests FAQ/amenities/recommendations into Redis
│   ├── rag_schema.py        # Shared Redis index schema
│   ├── action_agent.py      # Structured slot-filling booking/cancellation logic
│   ├── booking_actions.py   # The only module allowed to write bookings
│   ├── schema_context.py    # Auto-generates DB schema description for the LLM
│   ├── session_store.py     # Redis-backed per-session conversation state
│   ├── models.py            # SQLAlchemy models (19 tables)
│   ├── db.py                # Database engine/session setup
│   ├── seed_db.py           # Loads CSV data into Postgres
│   └── requirements.txt
├── frontend/
│   └── streamlit_app.py     # Chat UI
├── data/                    # Source CSVs (19 tables)
├── scripts/
│   ├── smoke_test.py        # End-to-end health check against a live deployment
│   └── create_readonly_role.sql
├── tests/                   # pytest suite (mocked DB/LLM calls)
├── render.yaml
└── .env                     # Not committed — see Setup below
```

## Setup

### 1. Prerequisites
- Python 3.12
- A [Neon](https://neon.tech) Postgres database
- A Redis instance with the Search/Query capability enabled (Redis Cloud or Upstash)
- An OpenAI API key

### 2. Environment variables
Create `.env` in the project root:

```dotenv
DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
READONLY_DATABASE_URL=postgresql://readonly_user:pass@host/db?sslmode=require
REDIS_URL=redis://default:pass@host:port
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o                 # optional, this is the default
OPENAI_ROUTER_MODEL=gpt-4o-mini     # optional, this is the default
```

`READONLY_DATABASE_URL` is created by running `scripts/create_readonly_role.sql`
against your database once — see the comments in that file.

### 3. Install and seed
```bash
python -m venv venv
source venv/bin/activate
pip install -r app/requirements.txt

cd app
python seed_db.py --reset       # loads data/*.csv into Postgres
python build_rag_index.py --reset  # embeds FAQs/amenities into Redis
```

### 4. Run locally
```bash
# terminal 1 — backend
cd app
uvicorn main:app --reload --port 8000

# terminal 2 — frontend
cd frontend
streamlit run streamlit_app.py
```

## Testing

```bash
pip install pytest pytest-mock
pytest -v
```

The suite covers the SQL validation guardrail, the draft-merge logic, the booking
decision engine, and availability search — all with database/LLM calls mocked, so
it runs fast and doesn't touch real data or spend API credits. It does **not**
cover the quality of the LLM extraction prompts themselves (that needs eval-style
testing against the live API, not unit tests).

For a live smoke test against a deployed instance:
```bash
python scripts/smoke_test.py https://your-backend-url
```

## Deployment

Currently deployed on:
- **Backend**: Render (`render.yaml` included)
- **Frontend**: Streamlit Community Cloud, configured via `API_BASE_URL` in that
  app's Secrets

See `render.yaml` for required environment variables. CORS is restricted via
`FRONTEND_ORIGIN` once both services are live.

## Known limitations

Being upfront about what this is and isn't:

- **No real authentication.** The "Guest ID" field is a plain number input
  simulating a logged-in session — anyone can act as any customer ID. Not
  suitable for real guest data without a proper auth layer.
- **Conversation history isn't used for cross-topic context.** The SQL and RAG
  agents each see only the current message — a follow-up like "what about a
  cheaper one?" right after an unrelated question won't have context. The
  booking agent's structured draft is the one place multi-turn memory is fully
  reliable.
- **SQL date-filtering depends on the model following its system prompt**, not a
  hard guarantee — `room_availability` has one row per room per date, and asking
  for "available rooms" without a date filter can still occasionally return
  duplicate-looking results if the model doesn't apply the filtering rule.
- **The synthetic dataset covers a fixed historical date range** (not tied to the
  real calendar), so relative dates like "tomorrow" are resolved against the
  dataset's own earliest date, not `today()`. This is a deliberate workaround for
  a demo dataset, not something to carry into a real deployment.