"""
Tests for booking_actions.py's read-side logic (check_availability,
check_room_number_availability), with the database connection mocked
out entirely — no real Postgres connection is used. This tests the
LOGIC (grouping rows by room, filtering out rooms with incomplete date
coverage) rather than the actual SQL execution.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import booking_actions


def _row(**kwargs):
    """Builds a mock row object supporting attribute access, e.g. row.price."""
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


class TestCheckAvailability:
    @patch("booking_actions.engine")
    def test_groups_rows_by_room_and_keeps_full_coverage_only(self, mock_engine):
        # Room 101 has both nights covered; room 102 is missing one —
        # only 101 should come back as a valid candidate.
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = [
            _row(room_number=101, date=date(2025, 1, 5), price=500.0),
            _row(room_number=101, date=date(2025, 1, 6), price=500.0),
            _row(room_number=102, date=date(2025, 1, 5), price=450.0),
            # room 102 missing the second night on purpose
        ]

        result = booking_actions.check_availability(
            "Standard", date(2025, 1, 5), date(2025, 1, 7)
        )

        room_numbers = [r["room_number"] for r in result]
        assert 101 in room_numbers
        assert 102 not in room_numbers

    @patch("booking_actions.engine")
    def test_sorts_candidates_by_total_price_ascending(self, mock_engine):
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = [
            _row(room_number=201, date=date(2025, 1, 5), price=900.0),
            _row(room_number=202, date=date(2025, 1, 5), price=400.0),
        ]

        result = booking_actions.check_availability(
            "Deluxe", date(2025, 1, 5), date(2025, 1, 6)
        )

        assert result[0]["room_number"] == 202  # cheaper one first
        assert result[1]["room_number"] == 201

    @patch("booking_actions.engine")
    def test_no_matching_rows_returns_empty_list(self, mock_engine):
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = []

        result = booking_actions.check_availability(
            "Presidential Suite", date(2025, 1, 5), date(2025, 1, 6)
        )
        assert result == []

    def test_check_out_before_check_in_raises(self):
        with pytest.raises(booking_actions.BookingError):
            booking_actions.check_availability(
                "Standard", date(2025, 1, 10), date(2025, 1, 5)
            )

    def test_check_out_equal_check_in_raises(self):
        with pytest.raises(booking_actions.BookingError):
            booking_actions.check_availability(
                "Standard", date(2025, 1, 5), date(2025, 1, 5)
            )


class TestCheckRoomNumberAvailability:
    @patch("booking_actions.engine")
    def test_nonexistent_room_number_returns_none(self, mock_engine):
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        # First call (room type lookup) returns None -> room doesn't exist
        mock_conn.execute.return_value.scalar.return_value = None

        result = booking_actions.check_room_number_availability(
            999999, date(2025, 1, 5), date(2025, 1, 6)
        )
        assert result is None

    @patch("booking_actions.engine")
    def test_partial_availability_returns_none(self, mock_engine):
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.side_effect = [
            MagicMock(scalar=MagicMock(return_value="Standard")),  # room type lookup
            MagicMock(fetchall=MagicMock(return_value=[_row(price=500.0)])),  # only 1 of 2 nights
        ]

        result = booking_actions.check_room_number_availability(
            111, date(2025, 1, 5), date(2025, 1, 7)  # 2 nights requested
        )
        assert result is None

    @patch("booking_actions.engine")
    def test_full_availability_returns_total_price(self, mock_engine):
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.side_effect = [
            MagicMock(scalar=MagicMock(return_value="Standard")),
            MagicMock(fetchall=MagicMock(return_value=[
                _row(price=500.0), _row(price=450.0),
            ])),
        ]

        result = booking_actions.check_room_number_availability(
            111, date(2025, 1, 5), date(2025, 1, 7)
        )
        assert result is not None
        assert result["total_price"] == 950.0
        assert result["room_type"] == "Standard"