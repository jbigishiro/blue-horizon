"""
Tests for action_agent.merge_draft() — the plain-Python dict merge that
replaced the earlier, buggy "concatenate raw text and re-extract
everything" design. This function is what makes multi-turn booking
state reliable, so it gets thorough coverage of exactly the failure
patterns that were found (and fixed) during real usage:
  - a later message updating one field shouldn't erase others
  - a field not mentioned in the update should be left untouched
  - a genuinely new value should overwrite an old one
"""

import action_agent


class TestMergeDraft:
    def test_new_value_overwrites_existing(self):
        draft = {**action_agent.EMPTY_DRAFT, "room_number": 111}
        update = {**action_agent.EMPTY_DRAFT, "room_number": 9999}
        merged = action_agent.merge_draft(draft, update)
        assert merged["room_number"] == 9999

    def test_null_update_field_preserves_existing_value(self):
        # This is the exact bug class from real usage: a later message
        # that doesn't restate check_in must NOT erase it.
        draft = {**action_agent.EMPTY_DRAFT, "check_in": "2025-01-05"}
        update = {**action_agent.EMPTY_DRAFT, "check_in": None}
        merged = action_agent.merge_draft(draft, update)
        assert merged["check_in"] == "2025-01-05"

    def test_merge_only_touches_fields_present_in_update(self):
        draft = {
            **action_agent.EMPTY_DRAFT,
            "action_type": "book",
            "room_type": "Deluxe",
            "check_in": "2025-01-05",
        }
        update = {**action_agent.EMPTY_DRAFT, "check_out": "2025-01-08"}
        merged = action_agent.merge_draft(draft, update)
        assert merged["action_type"] == "book"
        assert merged["room_type"] == "Deluxe"
        assert merged["check_in"] == "2025-01-05"
        assert merged["check_out"] == "2025-01-08"

    def test_merge_into_empty_draft(self):
        update = {**action_agent.EMPTY_DRAFT, "action_type": "book", "room_type": "Standard"}
        merged = action_agent.merge_draft(dict(action_agent.EMPTY_DRAFT), update)
        assert merged["action_type"] == "book"
        assert merged["room_type"] == "Standard"
        assert merged["room_number"] is None

    def test_merge_does_not_mutate_original_draft(self):
        draft = {**action_agent.EMPTY_DRAFT, "room_number": 111}
        update = {**action_agent.EMPTY_DRAFT, "room_number": 9999}
        action_agent.merge_draft(draft, update)
        assert draft["room_number"] == 111  # original untouched

    def test_all_fields_present_in_merged_result(self):
        merged = action_agent.merge_draft(
            dict(action_agent.EMPTY_DRAFT), dict(action_agent.EMPTY_DRAFT)
        )
        assert set(merged.keys()) == set(action_agent.DRAFT_FIELDS)