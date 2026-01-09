"""SQLAlchemy dialect for Opteryx data service.

This module provides a SQLAlchemy dialect implementation that enables
connecting to the Opteryx data service using SQLAlchemy's engine and
ORM capabilities.

Connection URL format:
    opteryx://[username:token@]host[:port]/[database][?ssl=true]

Examples:
    opteryx://jobs.opteryx.app/default
    opteryx://user:mytoken@jobs.opteryx.app:443/default?ssl=true
    opteryx://localhost:8000/default
"""

from __future__ import annotations

import logging
import re
from typing import Any
from typing import Optional
from typing import Tuple

from sqlalchemy import types as sqltypes
from sqlalchemy.engine import default
from sqlalchemy.engine.interfaces import ExecutionContext
from sqlalchemy.engine.url import URL

from . import dbapi

logger = logging.getLogger("sqlalchemy.dialects.opteryx")


def _quote_identifier(identifier: str) -> str:
    """Safely quote a SQL identifier to prevent SQL injection.

    Args:
        identifier: The identifier (table name, column name, etc.) to quote

    Returns:
        A safely quoted identifier string

    Raises:
        ValueError: If the identifier contains invalid characters
    """
    # Validate identifier format - alphanumeric, underscores, and dots only
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", identifier):
        raise ValueError(f"Invalid identifier: {identifier}")
    # Return double-quoted identifier (standard SQL quoting)
    return f'"{identifier}"'


class OptetyxDialect(default.DefaultDialect):
    """SQLAlchemy dialect for Opteryx data service.

    This dialect communicates with the Opteryx data service via HTTP,
    translating SQLAlchemy operations into API calls.
    """

    name = "opteryx"
    driver = "http"

    # Capabilities
    supports_alter = False
    supports_pk_autoincrement = False
    supports_default_values = False
    supports_empty_insert = False
    supports_sequences = False
    sequences_optional = True
    supports_native_boolean = True
    supports_native_decimal = True
    supports_statement_cache = False
    postfetch_lastrowid = False

    # Opteryx is read-only (analytics engine)
    supports_sane_rowcount = False
    supports_sane_multi_rowcount = False

    # Default SQL features
    default_paramstyle = "named"
    supports_native_enum = False
    supports_simple_order_by_label = True
    supports_comments = False
    inline_comments = False

    # Required for SQLAlchemy
    preexecute_autoincrement_sequences = False
    implicit_returning = False
    full_returning = False

    # Type mapping
    colspecs = {}
    ischema_names = {
        "VARCHAR": sqltypes.String,
        "STRING": sqltypes.String,
        "TEXT": sqltypes.Text,
        "INTEGER": sqltypes.Integer,
        "INT": sqltypes.Integer,
        "BIGINT": sqltypes.BigInteger,
        "SMALLINT": sqltypes.SmallInteger,
        "FLOAT": sqltypes.Float,
        "DOUBLE": sqltypes.Float,
        "REAL": sqltypes.Float,
        "DECIMAL": sqltypes.Numeric,
        "NUMERIC": sqltypes.Numeric,
        "BOOLEAN": sqltypes.Boolean,
        "BOOL": sqltypes.Boolean,
        "DATE": sqltypes.Date,
        "TIME": sqltypes.Time,
        "TIMESTAMP": sqltypes.DateTime,
        "DATETIME": sqltypes.DateTime,
        "BLOB": sqltypes.LargeBinary,
        "VARBINARY": sqltypes.LargeBinary,
        "BINARY": sqltypes.LargeBinary,
    }

    @classmethod
    def dbapi(cls) -> Any:
        """Return the DBAPI module."""
        return dbapi

    @classmethod
    def import_dbapi(cls) -> Any:
        """Import and return the DBAPI module."""
        return dbapi

    def create_connect_args(self, url: URL) -> Tuple[list, dict]:
        """Create connection arguments from SQLAlchemy URL.

        Args:
            url: SQLAlchemy URL object

        Returns:
            Tuple of (positional args, keyword args) for dbapi.connect()
        """
        opts = {}

        # Host
        if url.host:
            opts["host"] = url.host

        # Port
        if url.port:
            opts["port"] = url.port
        else:
            # Default ports based on SSL setting
            query = dict(url.query) if url.query else {}
            ssl = query.get("ssl", "").lower() in ("true", "1", "yes")
            opts["port"] = 443 if ssl else 8000

        # Username and token (password field used for token)
        if url.username:
            opts["username"] = url.username
        if url.password:
            opts["token"] = url.password

        # Database
        if url.database:
            opts["database"] = url.database

        # Query parameters
        if url.query:
            query = dict(url.query)
            if "ssl" in query:
                opts["ssl"] = query["ssl"].lower() in ("true", "1", "yes")
            if "timeout" in query:
                try:
                    opts["timeout"] = float(query["timeout"])
                except ValueError:
                    pass

        return ([], opts)

    def do_execute(
        self,
        cursor: Any,
        statement: str,
        parameters: Optional[Any],
        context: Optional[ExecutionContext] = None,
    ) -> Any:
        """Propagate execution options so downstream code can react to them."""
        execution_options = getattr(context, "execution_options", {}) if context is not None else {}
        streaming_requested = bool(execution_options.get("stream_results"))
        max_row_buffer = execution_options.get("max_row_buffer")

        # Attach the parsed streaming hints to the DBAPI cursor for later use.
        cursor._opteryx_execution_options = dict(execution_options)
        cursor._opteryx_stream_results_requested = streaming_requested
        cursor._opteryx_max_row_buffer = max_row_buffer

        return super().do_execute(cursor, statement, parameters, context=context)

    def do_ping(self, dbapi_connection: Any) -> bool:
        """Check if the connection is still alive.

        Args:
            dbapi_connection: The DBAPI connection object

        Returns:
            True if the connection is alive, False otherwise
        """
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            logger.debug("Connection ping successful")
            return True
        except Exception as e:
            logger.warning("Connection ping failed: %s", e)
            return False

    def get_isolation_level(self, dbapi_connection: Any) -> str:
        """Return the isolation level.

        Opteryx doesn't support transactions, so we return a nominal value.
        """
        return "AUTOCOMMIT"

    def has_table(
        self, connection: Any, table_name: str, schema: Optional[str] = None, **kw: Any
    ) -> bool:
        """Check if a table exists.

        Args:
            connection: SQLAlchemy connection
            table_name: Name of the table
            schema: Optional schema name

        Returns:
            True if the table exists
        """
        # Try to query the table with a limit of 0 to check existence
        try:
            # Safely quote identifiers to prevent SQL injection
            quoted_table = _quote_identifier(table_name)
            if schema:
                quoted_schema = _quote_identifier(schema)
                full_name = f"{quoted_schema}.{quoted_table}"
            else:
                full_name = quoted_table
            logger.debug("Checking if table exists: %s", full_name)
            result = connection.execute(f"SELECT * FROM {full_name} LIMIT 0")
            result.close()
            logger.debug("Table exists: %s", full_name)
            return True
        except (ValueError, Exception) as e:
            # ValueError from invalid identifier, or database error
            logger.debug("Table does not exist or error checking: %s - %s", table_name, e)
            return False

    def get_columns(
        self,
        connection: Any,
        table_name: str,
        schema: Optional[str] = None,
        **kw: Any,
    ) -> list:
        """Get column information for a table.

        Returns an empty list as Opteryx may not support full introspection.
        """
        return []

    def get_pk_constraint(
        self,
        connection: Any,
        table_name: str,
        schema: Optional[str] = None,
        **kw: Any,
    ) -> dict:
        """Get primary key constraint.

        Opteryx doesn't have primary keys in the traditional sense.
        """
        return {"constrained_columns": [], "name": None}

    def get_foreign_keys(
        self,
        connection: Any,
        table_name: str,
        schema: Optional[str] = None,
        **kw: Any,
    ) -> list:
        """Get foreign key information.

        Opteryx doesn't support foreign keys.
        """
        return []

    def get_indexes(
        self,
        connection: Any,
        table_name: str,
        schema: Optional[str] = None,
        **kw: Any,
    ) -> list:
        """Get index information.

        Opteryx doesn't expose index information.
        """
        return []

    def get_table_names(self, connection: Any, schema: Optional[str] = None, **kw: Any) -> list:
        """Get list of table names.

        Attempts to query Opteryx for available tables.
        """
        try:
            result = connection.execute("SHOW TABLES")
            tables = [row[0] for row in result.fetchall()]
            result.close()
            return tables
        except Exception:
            return []

    def get_view_names(self, connection: Any, schema: Optional[str] = None, **kw: Any) -> list:
        """Get list of view names.

        Opteryx may not distinguish between tables and views.
        """
        return []

    def get_schema_names(self, connection: Any, **kw: Any) -> list:
        """Get list of schema names."""
        try:
            result = connection.execute("SHOW SCHEMAS")
            schemas = [row[0] for row in result.fetchall()]
            result.close()
            return schemas
        except Exception:
            return ["default"]

    def _get_server_version_info(self, connection: Any) -> Tuple[int, ...]:
        """Get server version information."""
        return (0, 26, 1)  # Match Opteryx version in pyproject.toml

    def _check_unicode_returns(
        self, connection: Any, additional_tests: Optional[list] = None
    ) -> bool:
        """Check if the connection returns unicode strings."""
        return True

    def _check_unicode_description(self, connection: Any) -> bool:
        """Check if column descriptions are unicode."""
        return True


# Register the dialect
def register_dialect() -> None:
    """Register the Opteryx dialect with SQLAlchemy.

    This function is a convenience for development/editable installs where
    the package's entry points may not be present. Calling it (or importing
    this module, which calls it automatically) ensures SQLAlchemy can find
    the dialect when `create_engine("opteryx://...")` is used.
    """
    from sqlalchemy.dialects import registry

    # Register using the correct module path for the installed package
    registry.register("opteryx", "sqlalchemy_dialect.dialect", "OptetyxDialect")
    registry.register("opteryx.http", "sqlalchemy_dialect.dialect", "OptetyxDialect")


# Ensure the dialect is registered on import so it works in editable/test mode
try:
    register_dialect()
except Exception:
    # Best-effort registration; failures here shouldn't break imports
    pass
