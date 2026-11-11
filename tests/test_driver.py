"""Tests for the Opteryx SQLAlchemy dialect."""

import datetime
import decimal
import time
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import requests
from sqlalchemy_dialect import dbapi
from sqlalchemy_dialect.dialect import OpteryxDialect
from sqlalchemy_dialect.dialect import _quote_identifier


class TestRedactSecret:
    """Tests for the debug-log token redaction helper."""

    def test_redact_secret_keeps_only_trailing_chars(self):
        assert dbapi._redact_secret("eyJhbGciOiJIUzI1NiJ9.abc123") == "***********************c123"

    def test_redact_secret_short_value_fully_masked(self):
        assert dbapi._redact_secret("ab") == "**"

    def test_redact_secret_empty_or_none(self):
        assert dbapi._redact_secret("") == "<empty>"
        assert dbapi._redact_secret(None) == "<empty>"


class TestNumberPlaceholders:
    @pytest.mark.parametrize(
        "sql, expected",
        [
            ("SELECT ? , ?", "SELECT :p0 , :p1"),
            ("SELECT * FROM t WHERE a = '?' AND b = ?", "SELECT * FROM t WHERE a = '?' AND b = :p0"),
            ("SELECT 'it''s ?' , ?", "SELECT 'it''s ?' , :p0"),
            ('SELECT "col?" FROM t WHERE x = ?', 'SELECT "col?" FROM t WHERE x = :p0'),
            ("SELECT `a?` , ?", "SELECT `a?` , :p0"),
            ("SELECT ? -- why?\n, ?", "SELECT :p0 -- why?\n, :p1"),
            ("SELECT /* ? */ ?", "SELECT /* ? */ :p0"),
            ("SELECT 1", "SELECT 1"),
        ],
    )
    def test_only_bare_question_marks_are_numbered(self, sql, expected):
        assert dbapi._number_placeholders(sql) == expected


class TestQuoteIdentifier:
    """Tests for identifier quoting."""

    def test_quote_valid_identifier(self):
        """Test quoting valid identifiers."""
        assert _quote_identifier("my_table") == '"my_table"'
        assert _quote_identifier("MyTable") == '"MyTable"'
        assert _quote_identifier("table123") == '"table123"'
        assert _quote_identifier("_private") == '"_private"'

    def test_quote_invalid_identifiers(self):
        """Test that invalid identifiers raise ValueError."""
        with pytest.raises(ValueError, match="Invalid identifier"):
            _quote_identifier("123table")  # Starts with number
        with pytest.raises(ValueError, match="Invalid identifier"):
            _quote_identifier("my-table")  # Contains hyphen
        with pytest.raises(ValueError, match="Invalid identifier"):
            _quote_identifier("my table")  # Contains space
        with pytest.raises(ValueError, match="Invalid identifier"):
            _quote_identifier("table;DROP")  # SQL injection attempt
        with pytest.raises(ValueError, match="Invalid identifier"):
            _quote_identifier("")  # Empty string


class TestDBAPI:
    """Tests for the DBAPI 2.0 interface."""

    def test_module_globals(self):
        """Test that required DBAPI globals are defined."""
        assert dbapi.apilevel == "2.0"
        assert dbapi.threadsafety == 1
        assert dbapi.paramstyle == "named"

    def test_type_constructors(self):
        """Test DBAPI type constructor functions."""
        assert dbapi.Date(2024, 1, 15) == datetime.date(2024, 1, 15)
        assert dbapi.Time(14, 30, 45) == datetime.time(14, 30, 45)
        assert dbapi.Timestamp(2024, 1, 15, 14, 30, 45) == datetime.datetime(2024, 1, 15, 14, 30, 45)
        assert dbapi.Binary(b"test") == b"test"

    def test_parameters_are_sent_typed(self):
        """Every parameter goes to the jobs API as {"type", "value"}; the engine CASTs it."""
        t = dbapi._typed_parameter
        assert t(None) == {"type": "NULL", "value": None}
        assert t(True) == {"type": "BOOL", "value": True}
        assert t(42) == {"type": "INT64", "value": 42}
        assert t(1.5) == {"type": "FLOAT64", "value": 1.5}
        assert t(float("inf")) == {"type": "FLOAT64", "value": "inf"}
        assert t(decimal.Decimal("123.456")) == {"type": "DECIMAL(6,3)", "value": "123.456"}
        assert t(decimal.Decimal("1E+3")) == {"type": "DECIMAL(4,0)", "value": "1000"}
        assert t(datetime.datetime(2026, 1, 1, 12, 30)) == {"type": "TIMESTAMP", "value": "2026-01-01T12:30:00"}
        assert t(datetime.date(2026, 1, 1)) == {"type": "DATE", "value": "2026-01-01"}
        assert t(datetime.time(12, 30)) == {"type": "TIME", "value": "12:30:00"}
        assert t("x") == {"type": "VARCHAR", "value": "x"}
        with pytest.raises(dbapi.NotSupportedError):
            t(b"bytes")
        with pytest.raises(dbapi.NotSupportedError):
            t(decimal.Decimal("NaN"))

    def test_exception_hierarchy(self):
        """Test that exception classes follow DBAPI hierarchy."""
        assert issubclass(dbapi.Warning, Exception)
        assert issubclass(dbapi.Error, Exception)
        assert issubclass(dbapi.InterfaceError, dbapi.Error)
        assert issubclass(dbapi.DatabaseError, dbapi.Error)
        assert issubclass(dbapi.DataError, dbapi.DatabaseError)
        assert issubclass(dbapi.OperationalError, dbapi.DatabaseError)
        assert issubclass(dbapi.IntegrityError, dbapi.DatabaseError)
        assert issubclass(dbapi.InternalError, dbapi.DatabaseError)
        assert issubclass(dbapi.ProgrammingError, dbapi.DatabaseError)
        assert issubclass(dbapi.NotSupportedError, dbapi.DatabaseError)


class TestConnection:
    """Tests for the Connection class."""

    def test_connection_init_defaults(self):
        """Test connection initialization with defaults."""
        conn = dbapi.Connection()
        assert conn._host == "jobs.opteryx.app"
        assert conn._port == 8000
        assert conn._ssl is False
        assert conn._closed is False
        conn.close()

    def test_connection_init_with_ssl(self):
        """Test connection initialization with SSL."""
        conn = dbapi.Connection(host="jobs.opteryx.app", port=443, ssl=True)
        assert conn._base_url == "https://jobs.opteryx.app"
        conn.close()

    def test_connection_init_with_token(self):
        """Test connection initialization with bearer token."""
        conn = dbapi.Connection(token="test-token")
        assert conn._session.headers["Authorization"] == "Bearer test-token"
        conn.close()

    def test_connection_close(self):
        """Test closing connection."""
        conn = dbapi.Connection()
        conn.close()
        assert conn._closed is True

    def test_connection_cursor(self):
        """Test creating cursor."""
        conn = dbapi.Connection()
        cursor = conn.cursor()
        assert isinstance(cursor, dbapi.Cursor)
        conn.close()

    def test_connection_context_manager(self):
        """Test connection as context manager."""
        with dbapi.Connection() as conn:
            assert not conn._closed
        assert conn._closed

    def test_closed_connection_raises(self):
        """Test that operations on closed connection raise error."""
        conn = dbapi.Connection()
        conn.close()
        with pytest.raises(dbapi.ProgrammingError, match="Connection is closed"):
            conn.cursor()

    def test_commit_noop(self):
        """Test that commit is a no-op."""
        conn = dbapi.Connection()
        conn.commit()  # Should not raise
        conn.close()

    def test_rollback_noop(self):
        """Test that rollback is a no-op."""
        conn = dbapi.Connection()
        conn.rollback()  # Should not raise
        conn.close()

    def test_odata_base_url(self):
        """Test that the odata subdomain is derived the same way as the jobs subdomain."""
        conn = dbapi.Connection(host="opteryx.app", port=443, ssl=True)
        assert conn._odata_base_url() == "https://odata.opteryx.app"
        conn.close()

        conn = dbapi.Connection(host="jobs.opteryx.app", port=443, ssl=True)
        assert conn._odata_base_url() == "https://odata.opteryx.app"
        conn.close()

        conn = dbapi.Connection(host="localhost", port=8000, ssl=False)
        assert conn._odata_base_url() == "http://localhost:8000"
        conn.close()

    @patch("requests.Session.get")
    @patch("requests.Session.post")
    def test_ensure_authenticated_triggers_jwt_flow(self, mock_post, mock_get):
        """A pre-configured token header is not proof of a completed JWT exchange."""
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = {"access_token": "jwt-token", "token_type": "bearer"}
        mock_post.return_value = post_response

        conn = dbapi.Connection(host="opteryx.app", username="user", token="raw-token", ssl=True)
        # Seeded from the raw token before any JWT exchange has happened.
        assert conn._session.headers["Authorization"] == "Bearer raw-token"
        assert conn._jwt_authenticated is False

        conn._ensure_authenticated()

        assert conn._jwt_authenticated is True
        assert conn._session.headers["Authorization"] == "Bearer jwt-token"
        mock_post.assert_called_once()

    @patch("requests.Session.get")
    @patch("requests.Session.post")
    def test_get_odata_service_document(self, mock_post, mock_get):
        """Test fetching and caching the OData service document."""
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = {"access_token": "jwt-token", "token_type": "bearer"}
        mock_post.return_value = post_response

        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "value": [
                {"name": "public.examples.planets", "kind": "EntitySet", "source": "Table"},
                {"name": "public.examples.a_view", "kind": "EntitySet", "source": "View"},
            ]
        }
        mock_get.return_value = get_response

        conn = dbapi.Connection(host="opteryx.app", username="user", token="raw-token", ssl=True)
        entities = conn.get_odata_service_document()
        assert entities == [
            {"name": "public.examples.planets", "kind": "EntitySet", "source": "Table"},
            {"name": "public.examples.a_view", "kind": "EntitySet", "source": "View"},
        ]

        # Second call should be served from cache, not a second HTTP request.
        conn.get_odata_service_document()
        assert mock_get.call_count == 1

    @patch("requests.Session.get")
    @patch("requests.Session.post")
    def test_get_odata_service_document_failure_returns_empty(self, mock_post, mock_get):
        """Test that a failed request degrades to an empty list rather than raising."""
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = {"access_token": "jwt-token", "token_type": "bearer"}
        mock_post.return_value = post_response
        mock_get.side_effect = requests.exceptions.ConnectionError("boom")

        conn = dbapi.Connection(host="opteryx.app", username="user", token="raw-token", ssl=True)
        assert conn.get_odata_service_document() == []

    @patch("requests.Session.get")
    @patch("requests.Session.post")
    def test_get_odata_metadata(self, mock_post, mock_get):
        """Test fetching and parsing the OData $metadata document."""
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = {"access_token": "jwt-token", "token_type": "bearer"}
        mock_post.return_value = post_response

        metadata_xml = """<?xml version="1.0" encoding="UTF-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema Namespace="OData" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <EntityType Name="public_examples_planets">
        <Key><PropertyRef Name="id" /></Key>
        <Property Name="id" Type="Edm.Int64" Nullable="false" />
        <Property Name="name" Type="Edm.String" Nullable="true" />
        <Property Name="mass" Type="Edm.Double" Nullable="true" />
      </EntityType>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>"""
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.content = metadata_xml.encode("utf-8")
        mock_get.return_value = get_response

        conn = dbapi.Connection(host="opteryx.app", username="user", token="raw-token", ssl=True)
        metadata = conn.get_odata_metadata()
        assert metadata == {
            "public_examples_planets": [
                ("id", "Edm.Int64", False),
                ("name", "Edm.String", True),
                ("mass", "Edm.Double", True),
            ]
        }

        # Second call should be served from cache, not a second HTTP request.
        conn.get_odata_metadata()
        assert mock_get.call_count == 1

    @patch("requests.Session.get")
    @patch("requests.Session.post")
    def test_get_odata_metadata_malformed_xml_returns_empty(self, mock_post, mock_get):
        """Test that malformed XML degrades to an empty dict rather than raising."""
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = {"access_token": "jwt-token", "token_type": "bearer"}
        mock_post.return_value = post_response

        get_response = MagicMock()
        get_response.status_code = 200
        get_response.content = b"<not><valid"
        mock_get.return_value = get_response

        conn = dbapi.Connection(host="opteryx.app", username="user", token="raw-token", ssl=True)
        assert conn.get_odata_metadata() == {}


class TestCursor:
    """Tests for the Cursor class."""

    def test_cursor_init(self):
        """Test cursor initialization."""
        conn = dbapi.Connection()
        cursor = conn.cursor()
        assert cursor.description is None
        assert cursor.rowcount == -1
        assert cursor.arraysize == 1
        conn.close()

    def test_cursor_close(self):
        """Test closing cursor."""
        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor.close()
        assert cursor._closed is True
        conn.close()

    def test_closed_cursor_raises(self):
        """Test that operations on closed cursor raise error."""
        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor.close()
        with pytest.raises(dbapi.ProgrammingError, match="Cursor is closed"):
            cursor.fetchone()
        conn.close()

    def test_cursor_iteration(self):
        """Test cursor as iterator."""
        conn = dbapi.Connection()
        cursor = conn.cursor()
        # Set up some fake data
        cursor._rows = [(1, "a"), (2, "b")]
        cursor._row_index = 0

        results = list(cursor)
        assert results == [(1, "a"), (2, "b")]
        conn.close()

    def test_fetchone_empty(self):
        """Test fetchone with no results."""
        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor._rows = []
        cursor._row_index = 0
        assert cursor.fetchone() is None
        conn.close()

    def test_fetchmany(self):
        """Test fetchmany."""
        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor._rows = [(1,), (2,), (3,), (4,), (5,)]
        cursor._row_index = 0
        cursor.arraysize = 2

        result = cursor.fetchmany()
        assert result == [(1,), (2,)]
        result = cursor.fetchmany(3)
        assert result == [(3,), (4,), (5,)]
        conn.close()

    def test_fetchall(self):
        """Test fetchall."""
        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor._rows = [(1,), (2,), (3,)]
        cursor._row_index = 0

        result = cursor.fetchall()
        assert result == [(1,), (2,), (3,)]
        assert cursor._row_index == 3
        conn.close()

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_execute_success(self, mock_get, mock_post):
        """Test successful statement execution."""
        # Mock POST response (statement submission)
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {"execution_id": "handle-123"}
        mock_post.return_value = post_response

        # Mock GET responses (status polling)
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "execution_id": "handle-123",
            "status": {"state": "SUCCEEDED"},
            # Columnar results: each entry contains 'name' and 'values' list
            "data": [
                {"name": "id", "type": "INTEGER", "values": [1]},
                {"name": "value", "type": "STRING", "values": ["test"]},
            ],
            "total_rows": 1,
        }
        mock_get.return_value = get_response

        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM test")

        assert cursor._statement_handle == "handle-123"
        assert cursor.description is not None
        assert len(cursor.description) == 2
        assert cursor._rows == [(1, "test")]
        conn.close()

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_execute_sends_typed_parameters(self, mock_get, mock_post):
        """Positional and named parameters both reach the jobs API typed."""
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {"execution_id": "handle-123"}
        mock_post.return_value = post_response
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "execution_id": "handle-123",
            "status": {"state": "SUCCEEDED"},
            "data": [],
            "total_rows": 0,
        }
        mock_get.return_value = get_response

        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM t WHERE d = ? AND n = ?", [datetime.date(2026, 1, 1), None])
        payload = mock_post.call_args.kwargs["json"]
        assert payload["sql_text"] == "SELECT * FROM t WHERE d = :p0 AND n = :p1"
        assert payload["parameters"] == {
            "p0": {"type": "DATE", "value": "2026-01-01"},
            "p1": {"type": "NULL", "value": None},
        }

        cursor.execute("SELECT * FROM t WHERE price = :price", {"price": decimal.Decimal("1.50")})
        assert mock_post.call_args.kwargs["json"]["parameters"] == {
            "price": {"type": "DECIMAL(3,2)", "value": "1.50"}
        }
        conn.close()

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_fetch_results_columnar_pagination(self, mock_get, mock_post):
        """Test pagination with columnar results using num_rows/offset."""
        # Mock authless POST response
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {"execution_id": "handle-321"}
        mock_post.return_value = post_response

        # Pagination: first status request returns completion, then results pages stream the rows
        first_get = MagicMock()
        first_get.status_code = 200
        first_get.json.return_value = {
            "execution_id": "handle-321",
            "status": {"state": "SUCCEEDED"},
            "data": [
                {"name": "id", "type": "INTEGER", "values": [1, 2]},
                {"name": "name", "type": "STRING", "values": ["a", "b"]},
            ],
            "total_rows": 3,
        }

        # The second page comes from the /results endpoint: column by column,
        # each column carrying its type.
        second_get = MagicMock()
        second_get.status_code = 200
        second_get.json.return_value = {
            "total_rows": 3,
            "data": [
                {"name": "id", "type": "INT64", "values": [3]},
                {"name": "name", "type": "VARCHAR", "values": ["c"]},
            ],
        }

        # The first call is the status poll (whose payload is reused for the
        # first page of rows), the second is the paginated results page
        mock_get.side_effect = [first_get, second_get]

        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor.arraysize = 2
        cursor.execute("SELECT id, name FROM planets")

        assert cursor._rows == [(1, "a"), (2, "b"), (3, "c")]
        assert cursor._rowcount == 3
        conn.close()

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_cursor_auth_retrieves_token(self, mock_get, mock_post):
        """Test that Cursor.__init__ retrieves JWT token using client credentials and stores it on the cursor."""
        # Mock auth POST response (first call)
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.text = '{"access_token":"jwt-123"}'
        auth_response.json.return_value = {"access_token": "jwt-123"}

        # Mock statement POST response (second call) for submit
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {"execution_id": "handle-123"}
        mock_post.side_effect = [auth_response, post_response]

        # Mock GET responses (status polling)
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "execution_id": "handle-123",
            "status": "SUCCEEDED",
            "data": [{"name": "id", "type": "INTEGER", "values": [1]}],
            "total_rows": 1,
        }
        mock_get.return_value = get_response

        conn = dbapi.Connection(
            username="username", token="password", host="jobs.opteryx.app", port=443, ssl=True
        )
        cursor = conn.cursor()
        # Trigger a statement submission so a second POST call is made
        cursor.execute("SELECT * FROM test")

        # Cursor should have its JWT token and connection session had Authorization header set
        assert getattr(cursor, "_jwt_token") == "jwt-123"
        assert conn._session.headers.get("Authorization") == "Bearer jwt-123"

        # Ensure that the auth POST went to the authenticate subdomain and the statement was posted to the jobs subdomain
        assert mock_post.call_count >= 2
        first_call_url = mock_post.call_args_list[0][0][0]
        second_call_url = mock_post.call_args_list[1][0][0]
        assert first_call_url.endswith("authenticate.opteryx.app/token")
        assert second_call_url.endswith("jobs.opteryx.app/api/v1/jobs")
        conn.close()

    @patch("requests.Session.post")
    def test_cursor_auth_reuses_unexpired_token(self, mock_post):
        """A second cursor should not re-authenticate while the cached token is still valid."""
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.text = '{"access_token":"jwt-123","expires_in":3600}'
        auth_response.json.return_value = {"access_token": "jwt-123", "expires_in": 3600}
        mock_post.return_value = auth_response

        conn = dbapi.Connection(username="username", token="password", host="jobs.opteryx.app")

        cursor1 = conn.cursor()
        assert cursor1._jwt_token == "jwt-123"
        assert mock_post.call_count == 1

        # A second cursor within the token's lifetime should reuse it, not re-authenticate.
        cursor2 = conn.cursor()
        assert cursor2._jwt_token == "jwt-123"
        assert mock_post.call_count == 1
        conn.close()

    @patch("requests.Session.post")
    def test_cursor_auth_refreshes_expired_token(self, mock_post):
        """A new cursor should re-authenticate once the cached token has expired."""
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.text = '{"access_token":"jwt-123","expires_in":3600}'
        auth_response.json.return_value = {"access_token": "jwt-123", "expires_in": 3600}
        mock_post.return_value = auth_response

        conn = dbapi.Connection(username="username", token="password", host="jobs.opteryx.app")
        conn.cursor()
        assert mock_post.call_count == 1

        # Simulate the token being past its (skew-adjusted) expiry.
        conn._jwt_token_expiry = time.time() - 1

        refreshed_auth_response = MagicMock()
        refreshed_auth_response.status_code = 200
        refreshed_auth_response.text = '{"access_token":"jwt-456","expires_in":3600}'
        refreshed_auth_response.json.return_value = {"access_token": "jwt-456", "expires_in": 3600}
        mock_post.return_value = refreshed_auth_response

        cursor2 = conn.cursor()
        assert cursor2._jwt_token == "jwt-456"
        assert mock_post.call_count == 2
        conn.close()

    @patch("requests.Session.post")
    def test_submit_statement_retries_once_on_401(self, mock_post):
        """A 401 (e.g. token revoked early) should force re-auth and retry once, then succeed."""
        initial_auth_response = MagicMock()
        initial_auth_response.status_code = 200
        initial_auth_response.text = '{"access_token":"jwt-123"}'
        initial_auth_response.json.return_value = {"access_token": "jwt-123"}

        unauthorized_response = MagicMock()
        unauthorized_response.status_code = 401
        unauthorized_response.json.return_value = {"detail": "token revoked"}
        unauthorized_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=unauthorized_response
        )

        reauth_response = MagicMock()
        reauth_response.status_code = 200
        reauth_response.text = '{"access_token":"jwt-456"}'
        reauth_response.json.return_value = {"access_token": "jwt-456"}

        success_response = MagicMock()
        success_response.status_code = 201
        success_response.json.return_value = {"execution_id": "handle-789"}

        mock_post.side_effect = [
            initial_auth_response,  # conn.cursor() below authenticates
            unauthorized_response,  # first submit attempt -> 401
            reauth_response,  # forced re-auth
            success_response,  # retried submit -> succeeds
        ]

        conn = dbapi.Connection(username="username", token="password", host="jobs.opteryx.app")
        conn.cursor()

        result = conn._submit_statement("SELECT 1")

        assert result == {"execution_id": "handle-789"}
        assert conn._session.headers.get("Authorization") == "Bearer jwt-456"
        assert mock_post.call_count == 4
        conn.close()

    @patch("requests.Session.post")
    def test_submit_statement_401_after_retry_still_raises(self, mock_post):
        """If the retried request also gets a 401, it should raise rather than retry again."""

        def make_401():
            resp = MagicMock()
            resp.status_code = 401
            resp.json.return_value = {"detail": "still unauthorized"}
            resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
            return resp

        initial_auth_response = MagicMock()
        initial_auth_response.status_code = 200
        initial_auth_response.text = '{"access_token":"jwt-123"}'
        initial_auth_response.json.return_value = {"access_token": "jwt-123"}

        reauth_response = MagicMock()
        reauth_response.status_code = 200
        reauth_response.text = '{"access_token":"jwt-456"}'
        reauth_response.json.return_value = {"access_token": "jwt-456"}

        mock_post.side_effect = [
            initial_auth_response,  # conn.cursor() below authenticates
            make_401(),  # first submit attempt -> 401
            reauth_response,  # forced re-auth succeeds
            make_401(),  # retried submit -> 401 again
        ]

        conn = dbapi.Connection(username="username", token="password", host="jobs.opteryx.app")
        conn.cursor()

        with pytest.raises(dbapi.OperationalError, match="still unauthorized"):
            conn._submit_statement("SELECT 1")

        assert mock_post.call_count == 4
        conn.close()

    @patch("requests.Session.post")
    def test_submit_statement_401_no_retry_without_client_credentials(self, mock_post):
        """With a static bearer token (no client_id/secret), there's no token to refresh, so no retry."""
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"detail": "Unauthorized"}
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
        mock_post.return_value = resp

        conn = dbapi.Connection(token="static-token")

        with pytest.raises(dbapi.OperationalError, match="Unauthorized"):
            conn._submit_statement("SELECT 1")

        assert mock_post.call_count == 1
        conn.close()

    @patch("requests.Session.post")
    def test_execute_http_error(self, mock_post):
        """Test execute with HTTP error."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"detail": "Unauthorized"}
        mock_response.raise_for_status.side_effect = __import__("requests").exceptions.HTTPError(
            response=mock_response
        )
        mock_post.return_value = mock_response

        conn = dbapi.Connection()
        cursor = conn.cursor()

        with pytest.raises(dbapi.DatabaseError, match="Unauthorized"):
            cursor.execute("SELECT * FROM test")
        conn.close()

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_execute_failed_statement(self, mock_get, mock_post):
        """Test execute with failed statement."""
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {"execution_id": "handle-456"}
        mock_post.return_value = post_response

        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "status": {"state": "FAILED", "description": "Syntax error"}
        }
        mock_get.return_value = get_response

        conn = dbapi.Connection()
        cursor = conn.cursor()

        with pytest.raises(dbapi.DatabaseError, match="Syntax error"):
            cursor.execute("SELECT * FROM invalid")
        conn.close()

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_execute_no_data_response(self, mock_get, mock_post):
        """If the server returns success but no columns/data, the cursor should still be usable and return empty rows."""
        post_response = MagicMock()
        post_response.status_code = 201
        post_response.json.return_value = {"execution_id": "handle-empty"}
        mock_post.return_value = post_response

        # Status indicates completion but no data payload
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "execution_id": "handle-empty",
            "status": {"state": "SUCCEEDED"},
            "total_rows": 0,
            "data": [],
        }
        mock_get.return_value = get_response

        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        assert cursor._statement_handle == "handle-empty"
        # description should have been set to an empty list to avoid SQLAlchemy closing the result
        assert cursor.description == []
        assert cursor.fetchall() == []
        conn.close()


def _response(status_code=200, json_body=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = text
    return resp


_COMPLETED = {"execution_id": "h", "status": "COMPLETED"}


class TestTypedResults:
    """Result pages from /results carry column types; the cursor uses them."""

    PAGE = {
        "total_rows": 2,
        "data": [
            {
                "name": "launched",
                "type": "TIMESTAMP[us]",
                "values": ["1957-10-04T19:28:00+00:00", None],
            },
            {"name": "day", "type": "DATE", "values": ["1996-01-02", "2024-02-29"]},
            {"name": "price", "type": "DECIMAL(15, 2)", "values": [172799.49, 0.1]},
            {"name": "ratio", "type": "FLOAT64", "values": [2.5, None]},
            {"name": "n", "type": "INT64", "values": [1, 2]},
            {"name": "company", "type": "VARCHAR", "values": ["RVSN USSR", "NASA"]},
            {"name": "tags", "type": "ARRAY", "values": [["a", "b"], []]},
        ],
    }

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_types_and_values(self, mock_get, mock_post):
        import datetime
        import decimal

        mock_post.return_value = _response(json_body={"execution_id": "h"})
        mock_get.side_effect = [_response(json_body=_COMPLETED), _response(json_body=self.PAGE)]

        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")

        assert [d[0] for d in cursor.description] == [
            "launched", "day", "price", "ratio", "n", "company", "tags"
        ]  # fmt: skip
        assert [d[1] for d in cursor.description] == [
            "TIMESTAMP[us]", "DATE", "DECIMAL(15, 2)", "FLOAT64", "INT64", "VARCHAR", "ARRAY"
        ]  # fmt: skip
        assert cursor.description[0][1] == dbapi.DATETIME
        assert cursor.description[2][1] == dbapi.NUMBER
        assert cursor.description[5][1] == dbapi.STRING
        assert cursor.description[5][1] != dbapi.DATETIME

        first, second = cursor.fetchall()
        assert first == (
            datetime.datetime(1957, 10, 4, 19, 28, tzinfo=datetime.timezone.utc),
            datetime.date(1996, 1, 2),
            decimal.Decimal("172799.49"),
            2.5,
            1,
            "RVSN USSR",
            ["a", "b"],
        )
        assert second[0] is None
        assert second[2] == decimal.Decimal("0.1")

        # The page was requested from /results with num_rows inside the API's range.
        url, kwargs = mock_get.call_args_list[1][0][0], mock_get.call_args_list[1][1]
        assert url.endswith("/api/v1/jobs/h/results")
        assert kwargs["params"] == {"num_rows": 10_000, "offset": 0}
        conn.close()

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_empty_result_keeps_columns(self, mock_get, mock_post):
        """An empty result declares its columns without values: no phantom row."""
        mock_post.return_value = _response(json_body={"execution_id": "h"})
        empty = {"total_rows": 0, "data": [{"name": "id", "type": "INT64"}]}
        mock_get.side_effect = [_response(json_body=_COMPLETED), _response(json_body=empty)]

        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM t WHERE FALSE")
        assert cursor.description == [("id", "INT64", None, None, None, None, None)]
        assert cursor.fetchall() == []
        conn.close()

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_falls_back_to_download(self, mock_get, mock_post):
        """If /results can't serve the job, rows come from /download as before (untyped)."""
        import requests as _requests

        mock_post.return_value = _response(json_body={"execution_id": "h"})
        not_found = _response(status_code=404)
        not_found.raise_for_status.side_effect = _requests.exceptions.HTTPError(
            response=not_found
        )
        ndjson = _response(text='{"id": 1, "name": "a"}\n{"id": 2, "name": "b"}')
        mock_get.side_effect = [_response(json_body=_COMPLETED), not_found, ndjson]

        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM t")
        assert cursor.fetchall() == [(1, "a"), (2, "b")]
        assert cursor.description[0] == ("id", None, None, None, None, None, None)
        assert mock_get.call_args_list[2][0][0].endswith("/api/v1/jobs/h/download")
        conn.close()

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_ndjson_format_uses_download(self, mock_get, mock_post):
        mock_post.return_value = _response(json_body={"execution_id": "h"})
        ndjson = _response(text='{"id": 1}')
        mock_get.side_effect = [_response(json_body=_COMPLETED), ndjson]

        conn = dbapi.Connection()
        cursor = conn.cursor()
        cursor._opteryx_result_format = "ndjson"
        cursor.execute("SELECT id FROM t")
        assert cursor.fetchall() == [(1,)]
        url, kwargs = mock_get.call_args_list[1][0][0], mock_get.call_args_list[1][1]
        assert url.endswith("/download")
        assert kwargs["params"]["file_format"] == "json"
        conn.close()

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("2024-01-02T03:04:05+00:00", (2024, 1, 2, 3, 4, 5, 0)),
            ("2024-01-02T03:04:05.123456+00:00", (2024, 1, 2, 3, 4, 5, 123456)),
            ("2024-01-02T03:04:05.123+00:00", (2024, 1, 2, 3, 4, 5, 123000)),
            ("2024-01-02T03:04:05.123456789+00:00", (2024, 1, 2, 3, 4, 5, 123456)),
            ("2024-01-02T03:04:05Z", (2024, 1, 2, 3, 4, 5, 0)),
        ],
    )
    def test_timestamp_parsing(self, text, expected):
        import datetime

        value = dbapi._to_timestamp(text)
        assert value == datetime.datetime(*expected, tzinfo=datetime.timezone.utc)

    def test_unparseable_values_pass_through(self):
        assert dbapi._to_timestamp("not a timestamp") == "not a timestamp"
        assert dbapi._to_date("nope") == "nope"
        assert dbapi._to_decimal(True) is True


class TestDialect:
    """Tests for the SQLAlchemy dialect."""

    def test_dialect_name(self):
        """Test dialect name."""
        dialect = OpteryxDialect()
        assert dialect.name == "opteryx"
        assert dialect.driver == "http"

    def test_dialect_dbapi(self):
        """Test that dialect returns correct DBAPI module."""
        assert OpteryxDialect.dbapi() is dbapi
        assert OpteryxDialect.import_dbapi() is dbapi

    def test_create_connect_args_minimal(self):
        """Test create_connect_args with minimal URL."""
        from sqlalchemy.engine.url import make_url

        dialect = OpteryxDialect()
        url = make_url("opteryx://localhost/default")
        args, kwargs = dialect.create_connect_args(url)

        assert args == []
        assert kwargs["host"] == "localhost"
        assert kwargs["port"] == 8000
        assert kwargs["database"] == "default"

    def test_create_connect_args_full(self):
        """Test create_connect_args with full URL."""
        from sqlalchemy.engine.url import make_url

        dialect = OpteryxDialect()
        url = make_url("opteryx://user:token123@opteryx.app:443/mydb?ssl=true&timeout=60")
        args, kwargs = dialect.create_connect_args(url)

        assert args == []
        assert kwargs["host"] == "opteryx.app"
        assert kwargs["port"] == 443
        assert kwargs["username"] == "user"
        assert kwargs["token"] == "token123"
        assert kwargs["database"] == "mydb"
        assert kwargs["ssl"] is True
        assert kwargs["timeout"] == 60.0

    def test_dialect_capabilities(self):
        """Test dialect capability flags."""
        dialect = OpteryxDialect()
        assert dialect.supports_alter is False
        assert dialect.supports_sequences is False
        assert dialect.supports_native_boolean is True
        assert dialect.supports_statement_cache is False

    def test_get_isolation_level(self):
        """Test isolation level (always AUTOCOMMIT)."""
        dialect = OpteryxDialect()
        assert dialect.get_isolation_level(None) == "AUTOCOMMIT"

    def test_create_connect_args_port_443_implies_ssl(self):
        """Port 443 without an ssl query parameter means HTTPS."""
        from sqlalchemy.engine.url import make_url

        dialect = OpteryxDialect()
        _, kwargs = dialect.create_connect_args(make_url("opteryx://id:secret@opteryx.app:443/"))
        assert kwargs["ssl"] is True

    def test_create_connect_args_explicit_ssl_false_wins(self):
        """An explicit ssl=false is respected even on port 443."""
        from sqlalchemy.engine.url import make_url

        dialect = OpteryxDialect()
        _, kwargs = dialect.create_connect_args(make_url("opteryx://localhost:443/?ssl=false"))
        assert kwargs["ssl"] is False

    def test_dotted_schema_is_quoted_per_part(self):
        """A workspace.collection schema renders as dotted parts, not one quoted name."""
        from sqlalchemy import column
        from sqlalchemy import select
        from sqlalchemy import table

        dialect = OpteryxDialect()
        plain = table("countries", column("iso_alpha2"), schema="demo.geopolitics")
        sql = str(select(plain.c.iso_alpha2).compile(dialect=dialect))
        assert "FROM demo.geopolitics.countries" in sql

        mixed = table("Planets", column("Name"), schema="demo.Space Data")
        sql = str(select(mixed.c.Name).compile(dialect=dialect))
        assert 'FROM demo."Space Data"."Planets"' in sql


class TestIntrospection:
    """Tests for OData-backed schema introspection (has_table, get_columns, etc)."""

    SERVICE_DOCUMENT = [
        {"name": "public.examples.planets", "kind": "EntitySet", "source": "Table"},
        {"name": "public.examples.moons", "kind": "EntitySet", "source": "Table"},
        {"name": "public.examples.a_view", "kind": "EntitySet", "source": "View"},
        {"name": "public.examples.an_mv", "kind": "EntitySet", "source": "MaterializedView"},
        {"name": "personal.bastian.customers", "kind": "EntitySet", "source": "Table"},
    ]

    def test_get_view_names_includes_materialized_views(self):
        """Materialized views are listed as views (they'd otherwise be invisible)."""
        sa_connection, _ = self._fake_sa_connection(self.SERVICE_DOCUMENT)
        dialect = OpteryxDialect()
        assert dialect.get_view_names(sa_connection, schema="public.examples") == [
            "a_view",
            "an_mv",
        ]
        assert dialect.get_materialized_view_names(sa_connection, schema="public.examples") == [
            "an_mv"
        ]
        assert "an_mv" not in dialect.get_table_names(sa_connection, schema="public.examples")

    def test_schema_scope_is_exact(self):
        """A schema matches its own datasets only, not those of a longer dotted prefix."""
        document = [
            {"name": "ws.coll", "kind": "EntitySet", "source": "Table"},
            {"name": "ws.coll.ds", "kind": "EntitySet", "source": "Table"},
        ]
        sa_connection, _ = self._fake_sa_connection(document)
        dialect = OpteryxDialect()
        assert dialect.get_table_names(sa_connection, schema="ws") == ["coll"]
        assert dialect.get_table_names(sa_connection, schema="ws.coll") == ["ds"]

    METADATA = {
        "public_examples_planets": [
            ("id", "Edm.Int64", False),
            ("name", "Edm.String", True),
            ("mass", "Edm.Double", True),
        ],
    }

    @staticmethod
    def _fake_sa_connection(service_document=None, metadata=None):
        """Build a stand-in for the SQLAlchemy Connection dialect methods receive.

        Only the `.connection.dbapi_connection` access path used by
        `_dbapi_connection()` needs to work.
        """
        from sqlalchemy_dialect import dialect as dialect_module

        raw_connection = MagicMock()
        raw_connection.get_odata_service_document.return_value = service_document or []
        raw_connection.get_odata_metadata.return_value = metadata or {}
        sa_connection = MagicMock()
        sa_connection.connection.dbapi_connection = raw_connection
        return sa_connection, dialect_module

    def test_dbapi_connection_helper(self):
        """Test that _dbapi_connection reaches through to the raw driver connection."""
        from sqlalchemy_dialect.dialect import _dbapi_connection

        sa_connection, _ = self._fake_sa_connection()
        assert _dbapi_connection(sa_connection) is sa_connection.connection.dbapi_connection

    def test_edm_type_mapping(self):
        """Test Edm.* -> SQLAlchemy type mapping, including an unknown fallback."""
        from sqlalchemy import types as sqltypes
        from sqlalchemy_dialect.dialect import _edm_type_to_sqlalchemy

        assert _edm_type_to_sqlalchemy("Edm.Int64") is sqltypes.BigInteger
        assert _edm_type_to_sqlalchemy("Edm.String") is sqltypes.String
        assert _edm_type_to_sqlalchemy("Edm.Double") is sqltypes.Float
        assert _edm_type_to_sqlalchemy("Edm.Boolean") is sqltypes.Boolean
        # Unknown/unmapped Edm types fall back to String rather than raising.
        assert _edm_type_to_sqlalchemy("Edm.SomeFutureType") is sqltypes.String

    def test_has_table_true(self):
        """Test has_table for an entity present in the service document."""
        sa_connection, dialect_module = self._fake_sa_connection(self.SERVICE_DOCUMENT)
        dialect = OpteryxDialect()
        assert dialect.has_table(sa_connection, "planets", schema="public.examples") is True

    def test_has_table_false(self):
        """Test has_table for an entity absent from the service document."""
        sa_connection, _ = self._fake_sa_connection(self.SERVICE_DOCUMENT)
        dialect = OpteryxDialect()
        assert dialect.has_table(sa_connection, "does_not_exist", schema="public.examples") is False

    def test_has_table_no_schema(self):
        """Test has_table using a fully-dotted table_name with no separate schema."""
        sa_connection, _ = self._fake_sa_connection(self.SERVICE_DOCUMENT)
        dialect = OpteryxDialect()
        assert dialect.has_table(sa_connection, "public.examples.planets") is True

    def test_get_table_names_scoped(self):
        """Test get_table_names filters by schema and strips the dotted prefix."""
        sa_connection, _ = self._fake_sa_connection(self.SERVICE_DOCUMENT)
        dialect = OpteryxDialect()
        tables = dialect.get_table_names(sa_connection, schema="public.examples")
        assert tables == ["planets", "moons"]

    def test_get_table_names_unscoped(self):
        """Test get_table_names with no schema returns full dotted names, tables only."""
        sa_connection, _ = self._fake_sa_connection(self.SERVICE_DOCUMENT)
        dialect = OpteryxDialect()
        tables = dialect.get_table_names(sa_connection)
        assert tables == ["public.examples.planets", "public.examples.moons", "personal.bastian.customers"]

    def test_get_view_names(self):
        """Test get_view_names returns views and materialized views, never tables."""
        sa_connection, _ = self._fake_sa_connection(self.SERVICE_DOCUMENT)
        dialect = OpteryxDialect()
        views = dialect.get_view_names(sa_connection, schema="public.examples")
        assert views == ["a_view", "an_mv"]

    def test_get_schema_names(self):
        """Test get_schema_names derives distinct dotted prefixes."""
        sa_connection, _ = self._fake_sa_connection(self.SERVICE_DOCUMENT)
        dialect = OpteryxDialect()
        schemas = dialect.get_schema_names(sa_connection)
        assert schemas == ["personal.bastian", "public.examples"]

    def test_get_columns(self):
        """Test get_columns parses $metadata into SQLAlchemy column dicts."""
        from sqlalchemy import types as sqltypes

        sa_connection, _ = self._fake_sa_connection(metadata=self.METADATA)
        dialect = OpteryxDialect()
        columns = dialect.get_columns(sa_connection, "planets", schema="public.examples")

        assert [c["name"] for c in columns] == ["id", "name", "mass"]
        assert columns[0]["type"] is sqltypes.BigInteger
        assert columns[0]["nullable"] is False
        assert columns[1]["type"] is sqltypes.String
        assert columns[1]["nullable"] is True
        assert columns[2]["type"] is sqltypes.Float

    def test_get_columns_unknown_table(self):
        """Test get_columns returns an empty list for a table absent from $metadata."""
        sa_connection, _ = self._fake_sa_connection(metadata=self.METADATA)
        dialect = OpteryxDialect()
        assert dialect.get_columns(sa_connection, "does_not_exist", schema="public.examples") == []


class TestConnect:
    """Tests for the connect function."""

    def test_connect_function(self):
        """Test the connect convenience function."""
        conn = dbapi.connect(host="example.com", port=8000, token="test")
        assert isinstance(conn, dbapi.Connection)
        assert conn._host == "example.com"
        assert conn._token == "test"
        conn.close()
