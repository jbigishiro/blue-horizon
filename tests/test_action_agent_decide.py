"""
Tests for action_agent.decide() — the pure decision logic that turns a
merged draft into "ask for more info" / "unavailable" / "propose this
specific booking." All booking_actions.* calls are mocked so these
tests never touch a real database.
"""

from unittest.mock import patch

import action_agent
import booking_actions


class TestDecideActionTypeMissing:
    def test_no_action_type_asks_generic_clarification(self, empty_draft):
        result = action_agent.decide(empty_draft, customer_id=1)
        assert result["status"] == "needs_clarification"
        assert "booking" in result["message"].lower()


class TestDecideBookingFlow:
    def test_no_room_type_or_number_asks_for_room_type(self, empty_draft):
        draft = {**empty_draft, "action_type": "book"}
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "needs_clarification"
        assert "room" in result["message"].lower()

    def test_missing_check_in_is_asked_for_specifically(self, empty_draft):
        draft = {**empty_draft, "action_type": "book", "room_type": "Deluxe"}
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "needs_clarification"
        assert "check-in" in result["message"].lower()

    def test_missing_check_out_is_asked_for_specifically(self, empty_draft):
        draft = {
            **empty_draft, "action_type": "book", "room_type": "Deluxe",
            "check_in": "2025-01-05",
        }
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "needs_clarification"
        assert "check-out" in result["message"].lower()

    def test_check_out_before_check_in_is_rejected(self, empty_draft):
        draft = {
            **empty_draft, "action_type": "book", "room_type": "Deluxe",
            "check_in": "2025-01-08", "check_out": "2025-01-05",
        }
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "needs_clarification"

    @patch.object(booking_actions, "check_room_number_availability")
    def test_specific_room_number_available_proposes_that_room(self, mock_check, empty_draft):
        mock_check.return_value = {"room_number": 111, "room_type": "Standard", "total_price": 500.0}
        draft = {
            **empty_draft, "action_type": "book", "room_number": 111,
            "check_in": "2025-01-05", "check_out": "2025-01-06",
        }
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "pending_confirmation"
        assert result["room_number"] == 111
        assert result["total_price"] == 500.0
        mock_check.assert_called_once()

    @patch.object(booking_actions, "check_room_number_availability")
    def test_specific_room_number_unavailable_reports_unavailable(self, mock_check, empty_draft):
        mock_check.return_value = None
        draft = {
            **empty_draft, "action_type": "book", "room_number": 9999,
            "check_in": "2025-01-05", "check_out": "2025-01-06",
        }
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "unavailable"
        assert "9999" in result["message"]

    @patch.object(booking_actions, "check_availability")
    def test_room_type_search_with_candidates_proposes_cheapest(self, mock_search, empty_draft):
        mock_search.return_value = [
            {"room_number": 220, "total_price": 1000.0},
            {"room_number": 308, "total_price": 1200.0},
        ]
        draft = {
            **empty_draft, "action_type": "book", "room_type": "Standard",
            "check_in": "2025-01-05", "check_out": "2025-01-06",
        }
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "pending_confirmation"
        assert result["room_number"] == 220  # first candidate = cheapest, per check_availability's own sort

    @patch.object(booking_actions, "check_availability")
    def test_room_type_search_no_candidates_reports_unavailable(self, mock_search, empty_draft):
        mock_search.return_value = []
        draft = {
            **empty_draft, "action_type": "book", "room_type": "Presidential Suite",
            "check_in": "2025-01-05", "check_out": "2025-01-06",
        }
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "unavailable"

    @patch.object(booking_actions, "check_room_number_availability")
    def test_room_number_takes_priority_over_room_type_when_both_given(self, mock_check, empty_draft):
        # If a guest specifies both, the specific room number should win
        # — check_availability (type search) should not even be called.
        mock_check.return_value = {"room_number": 111, "room_type": "Standard", "total_price": 500.0}
        draft = {
            **empty_draft, "action_type": "book", "room_type": "Standard", "room_number": 111,
            "check_in": "2025-01-05", "check_out": "2025-01-06",
        }
        with patch.object(booking_actions, "check_availability") as mock_search:
            result = action_agent.decide(draft, customer_id=1)
            mock_search.assert_not_called()
        assert result["status"] == "pending_confirmation"


class TestDecideCancellationFlow:
    def test_explicit_booking_id_proposes_that_cancellation(self, empty_draft):
        draft = {**empty_draft, "action_type": "cancel", "booking_id": "BK000123"}
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "pending_confirmation"
        assert result["booking_id"] == "BK000123"

    @patch.object(booking_actions, "get_recent_bookings")
    def test_no_id_with_one_recent_booking_proposes_it(self, mock_recent, empty_draft):
        mock_recent.return_value = [{
            "booking_id": "BK050006", "room_number": 507,
            "check_in": "2025-01-05", "check_out": "2025-01-08", "total_amount": 2037.23,
        }]
        draft = {**empty_draft, "action_type": "cancel"}
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "pending_confirmation"
        assert result["booking_id"] == "BK050006"

    @patch.object(booking_actions, "get_recent_bookings")
    def test_no_id_with_multiple_bookings_asks_which_one(self, mock_recent, empty_draft):
        mock_recent.return_value = [
            {"booking_id": "BK000001", "room_number": 101, "check_in": "2025-01-01", "check_out": "2025-01-02", "total_amount": 500.0},
            {"booking_id": "BK000002", "room_number": 102, "check_in": "2025-02-01", "check_out": "2025-02-02", "total_amount": 500.0},
        ]
        draft = {**empty_draft, "action_type": "cancel"}
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "needs_clarification"
        assert "BK000001" in result["message"]
        assert "BK000002" in result["message"]

    @patch.object(booking_actions, "get_recent_bookings")
    def test_exclusion_leaving_zero_bookings_says_so_honestly(self, mock_recent, empty_draft):
        # This is the exact bug fixed during real usage: excluding a
        # just-declined booking should never silently re-offer it, and
        # if nothing's left, the guest should be told plainly rather
        # than asked for an ID that doesn't exist.
        mock_recent.return_value = []
        draft = {**empty_draft, "action_type": "cancel"}
        result = action_agent.decide(draft, customer_id=1, exclude_booking_id="BK050006")
        assert result["status"] == "needs_clarification"
        assert "BK050006" in result["message"]
        assert "only" in result["message"].lower()

    @patch.object(booking_actions, "get_recent_bookings")
    def test_exclusion_passed_through_to_lookup(self, mock_recent, empty_draft):
        mock_recent.return_value = []
        draft = {**empty_draft, "action_type": "cancel"}
        action_agent.decide(draft, customer_id=1, exclude_booking_id="BK050006")
        _, kwargs = mock_recent.call_args
        assert kwargs.get("exclude_booking_id") == "BK050006"

    @patch.object(booking_actions, "get_recent_bookings")
    def test_no_bookings_at_all_asks_for_id(self, mock_recent, empty_draft):
        mock_recent.return_value = []
        draft = {**empty_draft, "action_type": "cancel"}
        result = action_agent.decide(draft, customer_id=1)
        assert result["status"] == "needs_clarification"
        assert "confirmation number" in result["message"].lower()


class TestDecideReturnsFullDraft:
    def test_result_always_contains_all_draft_fields(self, empty_draft):
        draft = {**empty_draft, "action_type": "book", "room_type": "Deluxe"}
        result = action_agent.decide(draft, customer_id=1)
        for field in action_agent.DRAFT_FIELDS:
            assert field in result