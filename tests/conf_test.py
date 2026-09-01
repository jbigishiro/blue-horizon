"""
Shared pytest fixtures for the Blue Horizon test suite.

Run tests from the app/ directory (or add app/ to PYTHONPATH), since
test modules import project code the same way the app itself does:

    cd app
    pytest ../tests -v

Requires a valid .env (DATABASE_URL etc.) to be loadable, since some
modules under test construct a SQLAlchemy engine at import time —
constructing an engine does NOT open a live connection though, so
these tests do not require the database to actually be reachable
unless a specific test says otherwise (see test_booking_actions.py,
which mocks the connection entirely).
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Allow `import action_agent`, `import sql_agent`, etc. regardless of
# where pytest is invoked from.
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


@pytest.fixture
def empty_draft():
    import action_agent
    return dict(action_agent.EMPTY_DRAFT)


@pytest.fixture
def sample_booking_draft():
    return {
        "action_type": "book",
        "room_type": "Deluxe",
        "room_number": None,
        "check_in": "2025-01-05",
        "check_out": "2025-01-08",
        "num_adults": 2,
        "num_children": 0,
        "special_requests": None,
        "booking_id": None,
    }


@pytest.fixture
def mock_engine_connection():
    """
    Returns a MagicMock standing in for `engine.connect()`'s context
    manager, so DB-dependent functions can be tested without a real
    database. Configure `.execute.return_value.fetchall.return_value`
    on the returned mock in each test to control what rows come back.
    """
    mock_conn = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    mock_cm.__exit__.return_value = False
    return mock_cm, mock_conn