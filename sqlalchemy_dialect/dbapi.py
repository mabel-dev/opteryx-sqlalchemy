"""
DBAPI 2.0 (PEP 249) compliant interface for Opteryx (opteryx.app).

This module implements a minimal DBAPI 2.0 interface that communicates
with the Opteryx data service via HTTP. It provides Connection and Cursor
classes that translate SQL queries into HTTP requests.
"""

from __future__ import annotations

import json
import logging
import time
from importlib.metadata import version
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import Union
from urllib.parse import urljoin

import requests

logger = logging.getLogger("sqlalchemy.dialects.opteryx")

try:
    __version__ = version("opteryx-sqlalchemy")
except Exception:
    __version__ = "unknown"

# Module globals required by PEP 249
apilevel = "2.0"
threadsafety = 1  # Threads may share the module, but not connections
paramstyle = "named"  # Named style: WHERE name=:name


class Error(Exception):
    """Base exception for DBAPI errors."""


class Warning(Exception):  # noqa: A001
    """Warning exception."""


class InterfaceError(Error):
    """Exception for interface errors."""


class DatabaseError(Error):
    """Exception for database errors."""


class DataError(DatabaseError):
    """Exception for data errors."""


class OperationalError(DatabaseError):
    """Exception for operational errors."""


class IntegrityError(DatabaseError):
    """Exception for integrity constraint errors."""


class InternalError(DatabaseError):
    """Exception for internal errors."""


class ProgrammingError(DatabaseError):
    """Exception for programming errors."""


class NotSupportedError(DatabaseError):
    """Exception for not supported operations."""


# Type constructors (required by PEP 249)
def Date(year: int, month: int, day: int) -> str:
    """Construct a date value."""
    return f"{year:04d}-{month:02d}-{day:02d}"


def Time(hour: int, minute: int, second: int) -> str:
    """Construct a time value."""
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def Timestamp(year: int, month: int, day: int, hour: int, minute: int, second: int) -> str:
    """Construct a timestamp value."""
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"


def Binary(string: bytes) -> bytes:
    """Construct a binary value."""
    return string


STRING = str
BINARY = bytes
NUMBER = float
DATETIME = str
ROWID = str


class Cursor:
    """DBAPI 2.0 Cursor implementation for Opteryx."""

    def __init__(self, connection: "Connection") -> None:
        self._connection = connection
        self._jwt_token: Optional[str] = None
        self._description: Optional[
            List[Tuple[str, Any, None, None, None, None, Optional[bool]]]
        ] = None
        self._rowcount = -1
        self._rows: List[Tuple[Any, ...]] = []
        self._row_index = 0
        self._arraysize = 1
        self._closed = False
        self._statement_handle: Optional[str] = None

        # Execution option placeholders (may be set by dialect.do_execute)
        self._opteryx_execution_options: dict = {}
        self._opteryx_stream_results_requested: bool = False
        self._opteryx_max_row_buffer: Optional[int] = None

        # Try to authenticate using client credentials (client credentials flow)
        # client_id is connection._username and client_secret is connection._token
        try:
            username = getattr(self._connection, "_username", None)
            secret = getattr(self._connection, "_token", None)
            if username and secret:
                logger.debug("Attempting client credentials authentication for user: %s", username)
                host = getattr(self._connection, "_host", "localhost")
                # Normalize domain and build auth host (auth.domain)
                try:
                    domain = self._connection._normalize_domain(host)
                except Exception as e:
                    logger.debug("Failed to normalize domain '%s': %s", host, e)
                    domain = host
                # Only add auth. prefix when domain looks like a DNS name (not 'localhost')
                if "." in domain and not domain.startswith("localhost"):
                    auth_host = f"authenticate.{domain}"
                else:
                    auth_host = domain
                scheme = "https" if getattr(self._connection, "_ssl", False) else "http"
                auth_url = f"{scheme}://{auth_host}/token"
                logger.debug("Authentication URL: %s", auth_url)

                # Build form-encoded payload
                payload = {
                    "grant_type": "client_credentials",
                    "client_id": username,
                    "client_secret": secret,
                }
                # Use the connection session for auth so auth header set for all subsequent calls
                sess = getattr(self._connection, "_session", requests.Session())
                headers = {
                    "accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                }
                resp = sess.post(
                    auth_url,
                    data=payload,
                    headers=headers,
                    timeout=getattr(self._connection, "_timeout", 30),
                )
                resp.raise_for_status()
                body = resp.json() if resp.text else {}
                token = body.get("access_token") or body.get("token") or body.get("jwt")
                if token:
                    self._jwt_token = token
                    token_type = body.get("token_type", "bearer")
                    # Capitalize token_type properly: "bearer" -> "Bearer"
                    if token_type:
                        token_type = (
                            token_type[0].upper() + token_type[1:].lower()
                            if len(token_type) > 0
                            else "Bearer"
                        )
                    else:
                        token_type = "Bearer"
                    expires_in = body.get("expires_in")
                    refresh_token = body.get("refresh_token")
                    logger.info(
                        "Authentication successful for user: %s (token_type=%s, expires_in=%s)",
                        username,
                        token_type,
                        expires_in,
                    )
                    if refresh_token:
                        logger.debug("Refresh token received for user: %s", username)
                    # Set Authorization header for subsequent requests via the connection session
                    try:
                        auth_header = f"{token_type} {token}"
                        self._connection._session.headers["Authorization"] = auth_header
                        logger.debug("Set Authorization header to: %s ...", auth_header[:50])
                    except Exception as e:
                        logger.warning("Failed to set Authorization header: %s", e)
                else:
                    logger.warning("Authentication response missing token for user: %s", username)
        except requests.exceptions.RequestException as e:
            # Authentication failed — don't raise here; we will attempt queries without the JWT
            logger.warning("Authentication failed for user %s: %s", username, e)
            self._jwt_token = None
        except Exception as e:
            # Any unexpected failure in auth should not crash cursor creation
            logger.error("Unexpected error during authentication: %s", e, exc_info=True)
            self._jwt_token = None

    @property
    def description(
        self,
    ) -> Optional[List[Tuple[str, Any, None, None, None, None, Optional[bool]]]]:
        """Column description as required by PEP 249."""
        return self._description

    @property
    def rowcount(self) -> int:
        """Number of rows affected by the last operation."""
        return self._rowcount

    @property
    def arraysize(self) -> int:
        """Number of rows to fetch at a time."""
        return self._arraysize

    @arraysize.setter
    def arraysize(self, value: int) -> None:
        self._arraysize = value

    def close(self) -> None:
        """Close the cursor."""
        self._closed = True
        self._rows = []
        self._description = None

    def _check_closed(self) -> None:
        """Raise exception if cursor is closed."""
        if self._closed:
            raise ProgrammingError("Cursor is closed")

    def execute(
        self,
        operation: str,
        parameters: Optional[Union[Dict[str, Any], Sequence[Any]]] = None,
    ) -> "Cursor":
        """Execute a SQL statement.

        Args:
            operation: SQL statement to execute
            parameters: Optional parameters for the statement

        Returns:
            Self for method chaining
        """
        self._check_closed()
        self._rows = []
        self._row_index = 0
        self._description = None
        self._rowcount = -1

        # Log query (truncate if too long)
        query_preview = operation[:200] + "..." if len(operation) > 200 else operation
        logger.debug("Executing query: %s", query_preview)
        if parameters:
            logger.debug("Query parameters: %s", parameters)

        # Convert sequence parameters to dict if needed
        params_dict: Optional[Dict[str, Any]] = None
        if parameters is not None:
            if isinstance(parameters, dict):
                params_dict = parameters
            else:
                # Convert positional to named parameters
                params_dict = {f"p{i}": v for i, v in enumerate(parameters)}
                # Replace ? placeholders with :p0, :p1, etc.
                for i in range(len(parameters)):
                    operation = operation.replace("?", f":p{i}", 1)

        # Submit the statement
        start_time = time.time()
        response = self._connection._submit_statement(operation, params_dict)
        self._statement_handle = response.get("execution_id")

        if not self._statement_handle:
            logger.error("No execution ID in response: %s", response)
            raise DatabaseError("No statement handle returned from server")

        logger.debug("Statement submitted with execution_id: %s", self._statement_handle)

        # Poll for completion
        self._poll_for_results()

        elapsed = time.time() - start_time
        logger.info("Query completed in %.2fs, returned %d rows", elapsed, self._rowcount)

        # Ensure description is not None so SQLAlchemy treats this as a rows-capable result.
        # Some Opteryx responses may delay column metadata; setting an empty description here
        # prevents SQLAlchemy from closing the result object immediately.
        if self._description is None:
            self._description = []

        return self

    def _poll_for_results(self) -> None:
        """Poll the server until statement execution completes."""
        if not self._statement_handle:
            return

        max_wait = 300  # Maximum wait time in seconds
        poll_interval = 0.5  # Initial poll interval in seconds
        elapsed = 0.0
        last_log_time = 0.0  # Track when we last logged progress
        log_interval = 5.0  # Log progress every 5 seconds

        logger.debug("Polling for execution_id: %s", self._statement_handle)

        while elapsed < max_wait:
            status = self._connection._get_statement_status(self._statement_handle)
            raw_state = status.get("status")

            if isinstance(raw_state, dict):
                state_value = raw_state.get("state")
                status_details = raw_state
            else:
                state_value = raw_state
                status_details = {}

            if not state_value:
                state_value = status.get("state")

            normalized_state = (state_value or "UNKNOWN").upper()

            # Log progress periodically for long-running queries
            if elapsed - last_log_time >= log_interval:
                logger.info(
                    "Query still executing (state=%s, elapsed=%.1fs)", normalized_state, elapsed
                )
                last_log_time = elapsed

            if normalized_state in ("COMPLETED", "SUCCEEDED", "INCHOATE"):
                logger.debug("Query execution completed with state: %s", normalized_state)
                self._fetch_results()
                return
            if normalized_state in ("FAILED", "CANCELLED"):
                error_message = (
                    status.get("error_message")
                    or status_details.get("description")
                    or status.get("description")
                    or status.get("detail")
                    or "Unknown error"
                )
                logger.error("Query failed with state %s: %s", normalized_state, error_message)
                raise ProgrammingError(error_message)
            if normalized_state in ("UNKNOWN", "SUBMITTED", "EXECUTING", "RUNNING"):
                logger.debug("Query state: %s (elapsed: %.1fs)", normalized_state, elapsed)
                time.sleep(poll_interval)
                elapsed += poll_interval
                poll_interval = min(poll_interval * 1.5, 2.5)
                continue

            logger.error("Unexpected statement state: %s", state_value)
            raise DatabaseError(f"Unknown statement state: {state_value}")

        logger.error("Query execution timed out after %.1fs", elapsed)
        raise OperationalError("Statement execution timed out")

    @staticmethod
    def _rows_from_columnar_data(column_data: Sequence[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
        """Convert column-oriented payloads into row tuples."""
        column_values: List[List[Any]] = []
        for column in column_data:
            if not isinstance(column, dict):
                continue
            values = column.get("values") or []
            column_values.append(list(values))
        if not column_values:
            return []
        max_rows = max((len(values) for values in column_values), default=0)
        return [
            tuple(
                column_values[col_index][row_index]
                if row_index < len(column_values[col_index])
                else None
                for col_index in range(len(column_values))
            )
            for row_index in range(max_rows)
        ]

    def _fetch_results(self) -> None:
        """Fetch results from a completed statement."""
        if not self._statement_handle:
            return

        page_size = max(self._opteryx_max_row_buffer or 10, self._arraysize)
        offset = 0
        has_description = False
        rows: List[Tuple[Any, ...]] = []
        total_rows: Optional[int] = None

        def process_result_page(result: Dict[str, Any]) -> int:
            nonlocal has_description, total_rows
            new_rows = 0
            if total_rows is None and "total_rows" in result:
                try:
                    total_rows = int(result.get("total_rows", 0))
                except (TypeError, ValueError):
                    total_rows = None

            columns_meta = result.get("columns", [])
            if columns_meta and not has_description:
                self._description = [
                    (col.get("name", f"col{i}"), None, None, None, None, None, None)
                    for i, col in enumerate(columns_meta)
                ]
                has_description = True

            data = result.get("data", [])
            if data:
                if isinstance(data[0], dict) and "values" in data[0]:
                    # Columnar format: each dict has {name: ..., values: [...]}
                    if not has_description:
                        self._description = [
                            (col.get("name", f"col{i}"), None, None, None, None, None, None)
                            for i, col in enumerate(data)
                        ]
                        has_description = True
                    column_rows = self._rows_from_columnar_data(data)
                    rows.extend(column_rows)
                    new_rows = len(column_rows)
                elif isinstance(data[0], dict):
                    # Row format: each dict is a row with {col1: val1, col2: val2, ...}
                    if not has_description and data:
                        # Extract column names from first row
                        col_names = list(data[0].keys())
                        self._description = [
                            (col_name, None, None, None, None, None, None) for col_name in col_names
                        ]
                        has_description = True
                    # Convert each row dict to a tuple in the correct column order
                    col_order = [col[0] for col in (self._description or [])]
                    for row_dict in data:
                        row_tuple = tuple(row_dict.get(col) for col in col_order)
                        rows.append(row_tuple)
                    new_rows = len(data)
                else:
                    # List/tuple format
                    for row in data:
                        rows.append(tuple(row))
                    new_rows = len(data)

            return new_rows

        status_result = self._connection._get_statement_status(self._statement_handle)  # pylint: disable=protected-access
        process_result_page(status_result)
        offset = len(rows)

        while True:
            if total_rows is not None and offset >= total_rows:
                break

            result = self._connection._get_statement_results(  # pylint: disable=protected-access
                self._statement_handle, num_rows=page_size, offset=offset
            )

            fetched_this_page = process_result_page(result)
            if fetched_this_page <= 0:
                break

            offset += fetched_this_page
            if fetched_this_page < page_size:
                break

        # Finalize rows and counts
        self._rows = rows
        self._rowcount = len(self._rows)

    def executemany(
        self,
        operation: str,
        seq_of_parameters: Sequence[Union[Dict[str, Any], Sequence[Any]]],
    ) -> "Cursor":
        """Execute a SQL statement multiple times with different parameters."""
        self._check_closed()
        for parameters in seq_of_parameters:
            self.execute(operation, parameters)
        return self

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        """Fetch the next row of a query result set."""
        self._check_closed()
        if self._row_index >= len(self._rows):
            return None
        row = self._rows[self._row_index]
        self._row_index += 1
        return row

    def fetchmany(self, size: Optional[int] = None) -> List[Tuple[Any, ...]]:
        """Fetch the next set of rows."""
        self._check_closed()
        if size is None:
            size = self._arraysize
        rows = self._rows[self._row_index : self._row_index + size]
        self._row_index += len(rows)
        return rows

    def fetchall(self) -> List[Tuple[Any, ...]]:
        """Fetch all remaining rows."""
        self._check_closed()
        rows = self._rows[self._row_index :]
        self._row_index = len(self._rows)
        return rows

    def setinputsizes(self, sizes: Sequence[Any]) -> None:
        """Set input sizes (no-op, but required by PEP 249)."""
        _ = sizes
        return None

    def setoutputsize(self, size: int, column: Optional[int] = None) -> None:
        """Set output size (no-op, but required by PEP 249)."""
        _ = size
        _ = column
        return None

    def __iter__(self) -> "Cursor":
        """Make cursor iterable."""
        return self

    def __next__(self) -> Tuple[Any, ...]:
        """Get next row."""
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class Connection:
    """DBAPI 2.0 Connection implementation for Opteryx.

    Manages HTTP connections to the Opteryx data service.
    """

    def __init__(
        self,
        host: str = "jobs.opteryx.app",
        port: int = 8000,
        username: Optional[str] = None,
        token: Optional[str] = None,
        database: Optional[str] = None,
        ssl: bool = False,
        timeout: float = 30.0,
    ) -> None:
        """Initialize connection to Opteryx data service.

        Args:
            host: Hostname of the Opteryx data service
            port: Port number
            username: Username for authentication (optional)
            token: Bearer token for authentication
            database: Database/schema name (optional)
            ssl: Whether to use HTTPS
            timeout: Request timeout in seconds
        """
        self._host = host
        self._port = port
        self._username = username
        self._token = token
        self._database = database
        self._ssl = ssl
        self._timeout = timeout
        self._closed = False

        # Build base URL
        scheme = "https" if ssl else "http"
        if (ssl and port == 443) or (not ssl and port == 80):
            self._base_url = f"{scheme}://{host}"
        else:
            self._base_url = f"{scheme}://{host}:{port}"

        logger.debug(
            "Creating connection to %s (ssl=%s, timeout=%.1fs)", self._base_url, ssl, timeout
        )

        # Create session for connection pooling
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"
            logger.debug("Using pre-configured token for authentication")
        self._session.headers["Content-Type"] = "application/json"

    def _normalize_domain(self, host: str) -> str:
        """Return the base domain for the given host by stripping known subdomain prefixes.

        Examples:
            'jobs.opteryx.app' -> 'opteryx.app'
            'authenticate.opteryx.app' -> 'opteryx.app'
            'opteryx.app' -> 'opteryx.app'
            'localhost' -> 'localhost'
        """
        domain = host
        for p in ("jobs.", "authenticate."):
            if domain.startswith(p):
                domain = domain[len(p) :]
        return domain

    def _data_base_url(self) -> str:
        """Construct a base URL that targets the 'data' subdomain for API requests."""
        scheme = "https" if self._ssl else "http"
        domain = self._normalize_domain(self._host)
        # Only add subdomain prefix for DNS-style hosts (e.g. example.com), not for localhost or IPs
        if "." in domain and not domain.startswith("localhost"):
            data_host = f"jobs.{domain}"
        else:
            data_host = domain
        if (self._ssl and self._port == 443) or (not self._ssl and self._port == 80):
            return f"{scheme}://{data_host}"
        return f"{scheme}://{data_host}:{self._port}"

    def _check_closed(self) -> None:
        """Raise exception if connection is closed."""
        if self._closed:
            raise ProgrammingError("Connection is closed")

    def _submit_statement(
        self, sql: str, parameters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Submit a SQL statement to the data service."""
        self._check_closed()

        url = urljoin(self._data_base_url() + "/", "api/v1/jobs")
        payload: Dict[str, Any] = {
            "sql_text": sql,
            "client_info": {
                "application_name": "opteryx-sqlalchemy",
                "application_version": __version__,
            },
        }
        if parameters:
            payload["parameters"] = parameters

        logger.debug("Submitting statement to %s", url)

        try:
            response = self._session.post(url, json=payload, timeout=self._timeout)
            response.raise_for_status()
            result = response.json()
            logger.debug(
                "Statement submitted successfully, execution_id: %s", result.get("execution_id")
            )
            return result
        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                status_code = e.response.status_code
                # Authentication/authorization errors should raise OperationalError
                if status_code in (401, 403):
                    try:
                        detail = e.response.json().get("detail", str(e))
                    except (ValueError, json.JSONDecodeError):
                        detail = e.response.text or str(e)
                    logger.error("Authentication error (HTTP %d): %s", status_code, detail)
                    raise OperationalError(f"Authentication error: {detail}") from e
                try:
                    detail = e.response.json().get("detail", str(e))
                except (ValueError, json.JSONDecodeError):
                    detail = e.response.text or str(e)
                logger.error("HTTP error %d submitting statement: %s", status_code, detail)
                raise DatabaseError(f"HTTP error: {detail}") from e
            logger.error("HTTP error submitting statement: %s", e)
            raise DatabaseError(f"HTTP error: {e}") from e
        except requests.exceptions.RequestException as e:
            logger.error("Connection error submitting statement: %s", e)
            raise OperationalError(f"Connection error: {e}") from e

    def _get_statement_status(self, statement_handle: str) -> Dict[str, Any]:
        """Get the status of a submitted statement."""
        self._check_closed()

        url = urljoin(self._data_base_url() + "/", f"api/v1/jobs/{statement_handle}/status")

        logger.debug("Checking status for execution_id: %s", statement_handle)

        try:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
            result = response.json()
            logger.debug("Status check response: %s", result.get("status") or result.get("state"))
            return result
        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                status_code = e.response.status_code
                # Authentication/authorization errors should raise OperationalError
                if status_code in (401, 403):
                    try:
                        detail = e.response.json().get("detail", str(e))
                    except (ValueError, json.JSONDecodeError):
                        detail = e.response.text or str(e)
                    logger.error(
                        "Authentication error (HTTP %d) checking status: %s", status_code, detail
                    )
                    raise OperationalError(f"Authentication error: {detail}") from e
                if status_code == 404:
                    logger.error("Statement not found: %s", statement_handle)
                    raise ProgrammingError("Statement not found") from e
                try:
                    detail = e.response.json().get("detail", str(e))
                except (ValueError, json.JSONDecodeError):
                    detail = e.response.text or str(e)
                logger.error("HTTP error %d checking status: %s", status_code, detail)
                raise DatabaseError(f"HTTP error: {detail}") from e
            logger.error("HTTP error checking status: %s", e)
            raise DatabaseError(f"HTTP error: {e}") from e
        except requests.exceptions.RequestException as e:
            logger.error("Connection error checking status: %s", e)
            raise OperationalError(f"Connection error: {e}") from e

    def _get_statement_results(
        self, statement_handle: str, num_rows: Optional[int] = None, offset: Optional[int] = None
    ) -> Dict[str, Any]:
        """Get results for a completed statement using the /download endpoint.

        Args:
            statement_handle: The execution ID returned by submit
            num_rows: Maximum number of rows to return (maps to 'limit' param)
            offset: Row offset for pagination

        Returns:
            Dictionary containing the result data in a format compatible with process_result_page.
            The download endpoint returns NDJSON (newline-delimited JSON), so we parse it and
            convert to the expected format.
        """
        url = urljoin(self._data_base_url() + "/", f"api/v1/jobs/{statement_handle}/download")
        params: Dict[str, Any] = {"file_format": "json"}
        if num_rows is not None:
            params["limit"] = int(num_rows)
        if offset is not None:
            params["offset"] = int(offset)

        logger.debug(
            "Fetching results for execution_id: %s (limit=%s, offset=%s)",
            statement_handle,
            num_rows,
            offset,
        )

        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
            response.raise_for_status()

            # The download endpoint returns NDJSON (newline-delimited JSON)
            # Parse each line as a separate JSON object (row)
            rows = []
            columns = None
            for line in response.text.strip().split("\n"):
                if line:
                    row_dict = json.loads(line)
                    if columns is None:
                        # Extract column names from first row
                        columns = list(row_dict.keys())
                    rows.append(row_dict)

            # Convert to the format expected by process_result_page
            result = {"data": rows, "columns": [{"name": col} for col in (columns or [])]}

            logger.debug("Fetched %d rows from download endpoint", len(rows))
            return result
        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                # Authentication/authorization errors should raise OperationalError
                if e.response.status_code in (401, 403):
                    try:
                        detail = e.response.json().get("detail", str(e))
                    except (ValueError, json.JSONDecodeError):
                        detail = e.response.text or str(e)
                    logger.error("Authentication error fetching results: %s", detail)
                    raise OperationalError(f"Authentication error: {detail}") from e
            # For other HTTP errors, fall back to status endpoint
            logger.debug("Download endpoint unavailable, falling back to status endpoint")
        except requests.exceptions.RequestException as e:
            logger.debug("Error fetching from download endpoint: %s, falling back", e)

        # Fallback to status endpoint if dedicated download endpoint is unavailable
        return self._get_statement_status(statement_handle)

    def close(self) -> None:
        """Close the connection."""
        if not self._closed:
            logger.debug("Closing connection to %s", self._base_url)
            self._session.close()
            self._closed = True

    def commit(self) -> None:
        """Commit transaction (no-op for Opteryx as it's read-only)."""
        self._check_closed()

    def rollback(self) -> None:
        """Rollback transaction (no-op for Opteryx as it's read-only)."""
        self._check_closed()

    def cursor(self) -> Cursor:
        """Create a new cursor object."""
        self._check_closed()
        return Cursor(self)

    def __enter__(self) -> "Connection":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()


def connect(
    host: str = "localhost",
    port: int = 8000,
    username: Optional[str] = None,
    token: Optional[str] = None,
    database: Optional[str] = None,
    ssl: bool = False,
    timeout: float = 30.0,
) -> Connection:
    """Create a new connection to the Opteryx data service.

    Args:
        host: Hostname of the Opteryx data service
        port: Port number
        username: Username for authentication (optional)
        token: Bearer token for authentication
        database: Database/schema name (optional)
        ssl: Whether to use HTTPS
        timeout: Request timeout in seconds

    Returns:
        A new Connection object
    """
    return Connection(
        host=host,
        port=port,
        username=username,
        token=token,
        database=database,
        ssl=ssl,
        timeout=timeout,
    )
