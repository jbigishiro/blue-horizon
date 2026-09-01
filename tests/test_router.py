"""
Tests for router.classify_intent(), with the OpenAI client mocked so
these tests run instantly, deterministically, and without spending
real API credits on every test run.
"""

from unittest.mock import MagicMock, patch

import router


def _mock_openai_response(content: str):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=content))]
    return mock_response


class TestClassifyIntent:
    @patch.object(router, "client")
    def test_returns_the_models_answer_when_valid(self, mock_client):
        mock_client.chat.completions.create.return_value = _mock_openai_response("sql_query")
        result = router.classify_intent("what rooms are available?")
        assert result == "sql_query"

    @patch.object(router, "client")
    def test_falls_back_to_chitchat_on_malformed_response(self, mock_client):
        # Defensive behavior: an unexpected/garbled response from the
        # classifier should never crash the router or route somewhere
        # dangerous — it should fail toward the cheapest, safest intent.
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            "this is not a valid intent label"
        )
        result = router.classify_intent("anything")
        assert result == "chitchat"

    @patch.object(router, "client")
    def test_strips_whitespace_and_lowercases(self, mock_client):
        mock_client.chat.completions.create.return_value = _mock_openai_response("  Knowledge_Query  \n")
        result = router.classify_intent("what time is check-in?")
        assert result == "knowledge_query"


class TestAnswerSideQuestionDeterministicPricing:
    def test_per_night_price_computed_without_llm_call(self):
        # This specific case should be answered with pure arithmetic on
        # the proposal's own data — no LLM call needed or made, which
        # also means it can never be wrong about which room it's
        # describing (the exact bug this replaced).
        proposal = {
            "action_type": "book",
            "room_number": 1003,
            "check_in": "2025-01-05",
            "check_out": "2025-01-08",
            "total_price": 2037.23,
        }
        with patch.object(router, "classify_intent") as mock_classify:
            answer = router.answer_side_question(
                "what is the cost of a single night?", 1, proposal
            )
            mock_classify.assert_not_called()  # confirms the shortcut path was taken

        assert "1003" in answer
        assert "679.08" in answer  # 2037.23 / 3, rounded