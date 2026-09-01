"""
Tests for sql_agent.validate_sql() — the guardrail that decides whether
LLM-generated SQL is safe to execute. Given this is the main defense
against a bad/malicious SQL generation, it gets thorough coverage.

NOTE: importing sql_agent triggers build_schema_context() and
build_whitelists(), which construct a SQLAlchemy engine from
DATABASE_URL/READONLY_DATABASE_URL. This does NOT require a live
connection (engines are lazy), but it DOES require those env vars to
be set to a validly-formatted connection string — run with your
project's .env loaded (e.g. via pytest-dotenv, or `python-dotenv run`,
or just running from a shell where .env has already been sourced).
"""

import pytest

import app.sql_agent


class TestValidateSQL:
    def test_allows_simple_select(self):
        sql = "SELECT room_id, room_number FROM rooms LIMIT 10"
        result = sql_agent.validate_sql(sql)
        assert result.strip().upper().startswith("SELECT")

    def test_adds_limit_when_missing(self):
        sql = "SELECT * FROM customers"
        result = sql_agent.validate_sql(sql)
        assert "LIMIT" in result.upper()

    def test_preserves_existing_limit(self):
        sql = "SELECT * FROM customers LIMIT 5"
        result = sql_agent.validate_sql(sql)
        # should not double up or override an explicit limit
        assert result.upper().count("LIMIT") == 1
        assert "LIMIT 5" in result

    @pytest.mark.parametrize("statement", [
        "INSERT INTO customers (customer_id) VALUES (1)",
        "UPDATE rooms SET status = 'Available'",
        "DELETE FROM room_bookings",
        "DROP TABLE customers",
        "TRUNCATE TABLE rooms",
        "ALTER TABLE customers ADD COLUMN test INT",
        "GRANT ALL ON customers TO public",
        "CREATE TABLE evil (id INT)",
    ])
    def test_rejects_write_and_ddl_statements(self, statement):
        with pytest.raises(sql_agent.SQLValidationError):
            sql_agent.validate_sql(statement)

    def test_rejects_non_select_leading_statement(self):
        with pytest.raises(sql_agent.SQLValidationError):
            sql_agent.validate_sql("EXPLAIN SELECT * FROM rooms")

    def test_rejects_multiple_statements(self):
        sql = "SELECT * FROM rooms; DROP TABLE rooms;"
        with pytest.raises(sql_agent.SQLValidationError):
            sql_agent.validate_sql(sql)

    def test_rejects_write_keyword_inside_subquery(self):
        # A SELECT that starts clean but smuggles a write keyword in a
        # subquery should still be caught — the keyword check is not
        # anchored only to the start of the statement.
        sql = "SELECT * FROM rooms WHERE room_id IN (DELETE FROM rooms)"
        with pytest.raises(sql_agent.SQLValidationError):
            sql_agent.validate_sql(sql)

    def test_rejects_unknown_table(self):
        sql = "SELECT * FROM totally_made_up_table LIMIT 10"
        with pytest.raises(sql_agent.SQLValidationError):
            sql_agent.validate_sql(sql)

    def test_allows_known_table_case_insensitively(self):
        sql = "select * from ROOMS limit 10"
        result = sql_agent.validate_sql(sql)
        assert result  # should not raise

    def test_allows_join_across_known_tables(self):
        sql = (
            "SELECT r.room_number, ra.date FROM rooms r "
            "JOIN room_availability ra ON ra.room_id = r.room_id LIMIT 10"
        )
        result = sql_agent.validate_sql(sql)
        assert result

    def test_allows_min_date_subquery_pattern(self):
        # This exact pattern is what the system prompt instructs the
        # model to use for "today" within the synthetic dataset — make
        # sure validate_sql doesn't choke on the subquery.
        sql = (
            "SELECT room_number FROM room_availability "
            "WHERE date = (SELECT MIN(date) FROM room_availability) LIMIT 10"
        )
        result = sql_agent.validate_sql(sql)
        assert result

    def test_case_insensitive_forbidden_keyword(self):
        with pytest.raises(sql_agent.SQLValidationError):
            sql_agent.validate_sql("DeLeTe FROM rooms")

    def test_empty_string_rejected(self):
        with pytest.raises(sql_agent.SQLValidationError):
            sql_agent.validate_sql("")

    def test_strips_trailing_semicolon_without_erroring(self):
        sql = "SELECT * FROM rooms LIMIT 5;"
        result = sql_agent.validate_sql(sql)
        assert result  # should not raise on a single trailing semicolon