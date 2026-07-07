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


_EDM_TYPE_MAP = {
    "Edm.String": sqltypes.String,
    "Edm.Boolean": sqltypes.Boolean,
    "Edm.Byte": sqltypes.SmallInteger,
    "Edm.SByte": sqltypes.SmallInteger,
    "Edm.Int16": sqltypes.SmallInteger,
    "Edm.Int32": sqltypes.Integer,
    "Edm.Int64": sqltypes.BigInteger,
    "Edm.Single": sqltypes.Float,
    "Edm.Double": sqltypes.Float,
    "Edm.Decimal": sqltypes.Numeric,
    "Edm.Date": sqltypes.Date,
    "Edm.TimeOfDay": sqltypes.Time,
    "Edm.DateTimeOffset": sqltypes.DateTime,
    "Edm.Binary": sqltypes.LargeBinary,
}


def _edm_type_to_sqlalchemy(edm_type: str) -> Any:
    """Map an OData Edm.* primitive type name to a SQLAlchemy type."""
    return _EDM_TYPE_MAP.get(edm_type, sqltypes.String)


def _dbapi_connection(connection: Any) -> Any:
    """Get the underlying opteryx dbapi Connection from a SQLAlchemy Connection.

    Reuses the connection's already-authenticated HTTP session rather than
    opening a new one, so introspection doesn't repeat the auth handshake.
    """
    return connection.connection.dbapi_connection


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


class OpteryxDialect(default.DefaultDialect):
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
        result_format = execution_options.get("result_format", "json")

        # Attach the parsed streaming hints to the DBAPI cursor for later use.
        cursor._opteryx_execution_options = dict(execution_options)
        cursor._opteryx_stream_results_requested = streaming_requested
        cursor._opteryx_max_row_buffer = max_row_buffer
        cursor._opteryx_result_format = result_format

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

        Backed by the OData service document rather than a SQL query, so this
        costs a metadata lookup instead of a billed query execution.

        Args:
            connection: SQLAlchemy connection
            table_name: Name of the table
            schema: Optional schema name

        Returns:
            True if the table exists
        """
        full_name = f"{schema}.{table_name}" if schema else table_name
        entities = _dbapi_connection(connection).get_odata_service_document()
        return any(e.get("name") == full_name for e in entities)

    def get_columns(
        self,
        connection: Any,
        table_name: str,
        schema: Optional[str] = None,
        **kw: Any,
    ) -> list:
        """Get column information for a table from the OData $metadata document."""
        full_name = f"{schema}.{table_name}" if schema else table_name
        entity_type_name = full_name.replace(".", "_")
        metadata = _dbapi_connection(connection).get_odata_metadata()
        properties = metadata.get(entity_type_name)
        if not properties:
            return []
        return [
            {
                "name": prop_name,
                "type": _edm_type_to_sqlalchemy(edm_type),
                "nullable": nullable,
                "default": None,
            }
            for prop_name, edm_type, nullable in properties
        ]

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

    def _get_entity_names(self, connection: Any, schema: Optional[str], source: str) -> list:
        """Shared helper: entity names of a given OData `source` ("Table" or "View").

        If `schema` is given, names are scoped to that dotted prefix and
        returned with the prefix stripped; otherwise the full dotted name is
        returned (Opteryx datasets are commonly addressed in dotted form,
        e.g. "public.examples.planets").
        """
        entities = _dbapi_connection(connection).get_odata_service_document()
        prefix = f"{schema}." if schema else None
        names = []
        for entity in entities:
            if entity.get("kind") != "EntitySet" or entity.get("source") != source:
                continue
            full_name = entity.get("name", "")
            if prefix:
                if not full_name.startswith(prefix):
                    continue
                names.append(full_name[len(prefix) :])
            else:
                names.append(full_name)
        return names

    def get_table_names(self, connection: Any, schema: Optional[str] = None, **kw: Any) -> list:
        """Get list of table names from the OData service document."""
        return self._get_entity_names(connection, schema, "Table")

    def get_view_names(self, connection: Any, schema: Optional[str] = None, **kw: Any) -> list:
        """Get list of view names from the OData service document."""
        return self._get_entity_names(connection, schema, "View")

    def get_schema_names(self, connection: Any, **kw: Any) -> list:
        """Get list of schema names, derived from the dotted prefixes of known entities."""
        entities = _dbapi_connection(connection).get_odata_service_document()
        schemas = set()
        for entity in entities:
            full_name = entity.get("name", "")
            if "." in full_name:
                schemas.add(full_name.rsplit(".", 1)[0])
        return sorted(schemas)

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
    registry.register("opteryx", "sqlalchemy_dialect.dialect", "OpteryxDialect")
    registry.register("opteryx.http", "sqlalchemy_dialect.dialect", "OpteryxDialect")


# Ensure the dialect is registered on import so it works in editable/test mode
try:
    register_dialect()
except Exception:
    # Best-effort registration; failures here shouldn't break imports
    pass
