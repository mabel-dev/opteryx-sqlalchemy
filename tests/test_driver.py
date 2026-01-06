"""Tests for the Opteryx SQLAlchemy dialect."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from sqlalchemy_dialect import dbapi
from sqlalchemy_dialect.dialect import OptetyxDialect
from sqlalchemy_dialect.dialect import _quote_identifier


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
        assert dbapi.Date(2024, 1, 15) == "2024-01-15"
        assert dbapi.Time(14, 30, 45) == "14:30:45"
        assert dbapi.Timestamp(2024, 1, 15, 14, 30, 45) == "2024-01-15 14:30:45"
        assert dbapi.Binary(b"test") == b"test"

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
        assert conn._host == "localhost"
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

        second_get = MagicMock()
        second_get.status_code = 200
        second_get.json.return_value = {
            "execution_id": "handle-321",
            "status": {"state": "SUCCEEDED"},
            "data": [
                {"name": "id", "type": "INTEGER", "values": [3]},
                {"name": "name", "type": "STRING", "values": ["c"]},
            ],
            "total_rows": 3,
        }

        # The first call is the status poll, the next two are paginated results
        mock_get.side_effect = [first_get, first_get, second_get]

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


class TestDialect:
    """Tests for the SQLAlchemy dialect."""

    def test_dialect_name(self):
        """Test dialect name."""
        dialect = OptetyxDialect()
        assert dialect.name == "opteryx"
        assert dialect.driver == "http"

    def test_dialect_dbapi(self):
        """Test that dialect returns correct DBAPI module."""
        assert OptetyxDialect.dbapi() is dbapi
        assert OptetyxDialect.import_dbapi() is dbapi

    def test_create_connect_args_minimal(self):
        """Test create_connect_args with minimal URL."""
        from sqlalchemy.engine.url import make_url

        dialect = OptetyxDialect()
        url = make_url("opteryx://localhost/default")
        args, kwargs = dialect.create_connect_args(url)

        assert args == []
        assert kwargs["host"] == "localhost"
        assert kwargs["port"] == 8000
        assert kwargs["database"] == "default"

    def test_create_connect_args_full(self):
        """Test create_connect_args with full URL."""
        from sqlalchemy.engine.url import make_url

        dialect = OptetyxDialect()
        url = make_url("opteryx://user:token123@opteryx.app:443/mydb?ssl=true&timeout=60")
        args, kwargs = dialect.create_connect_args(url)

        assert args == []
        assert kwargs["host"] == "jobs.opteryx.app"
        assert kwargs["port"] == 443
        assert kwargs["username"] == "user"
        assert kwargs["token"] == "token123"
        assert kwargs["database"] == "mydb"
        assert kwargs["ssl"] is True
        assert kwargs["timeout"] == 60.0

    def test_dialect_capabilities(self):
        """Test dialect capability flags."""
        dialect = OptetyxDialect()
        assert dialect.supports_alter is False
        assert dialect.supports_sequences is False
        assert dialect.supports_native_boolean is True
        assert dialect.supports_statement_cache is False

    def test_get_isolation_level(self):
        """Test isolation level (always AUTOCOMMIT)."""
        dialect = OptetyxDialect()
        assert dialect.get_isolation_level(None) == "AUTOCOMMIT"


class TestConnect:
    """Tests for the connect function."""

    def test_connect_function(self):
        """Test the connect convenience function."""
        conn = dbapi.connect(host="example.com", port=8000, token="test")
        assert isinstance(conn, dbapi.Connection)
        assert conn._host == "example.com"
        assert conn._token == "test"
        conn.close()
