# sqlalchemy-opteryx

SQLAlchemy dialect for Opteryx Cloud (https://opteryx.app) — use On Demand Opteryx wherever SQLAlchemy is supported.

This project packages a SQLAlchemy dialect and lightweight DBAPI 2.0 adapter that talk to Opteryx's HTTP API, enabling read-only SQL access to Opteryx Cloud from SQLAlchemy Core, engines, and downstream tools like pandas and dbt. The library is published on PyPI as `opteryx-sqlalchemy`, so you can `pip install opteryx-sqlalchemy` in any environment.

---

## Features ✅

- Connect to Opteryx Cloud or self-hosted Opteryx instances with a SQLAlchemy connection URL and optional bearer token.
- Read-only analytics with transparent polling of query status and incremental result streaming.
- Lightweight DBAPI implementation that maps Opteryx types to SQLAlchemy while surfacing DatabaseError/OperationalError semantics.
- Compatible with SQLAlchemy 2.x usage patterns, including context-managed engines and `text` queries.
- Work with pandas, dbt, or other tooling that understands SQLAlchemy engines.
- **Comprehensive debug logging** for troubleshooting connection, authentication, and query execution issues.
- Install from PyPI (`pip install opteryx-sqlalchemy`) or lock into editable mode for development.

---

## Connection URL format

Use the following SQLAlchemy URL format:

```
opteryx://[username:token@]host[:port]/[database][?ssl=true&timeout=60]
```

Examples:
- **Opteryx Cloud (with token)**: `opteryx://myusername:mytoken@opteryx.app:443/default?ssl=true`
- **Local Opteryx (no auth)**: `opteryx://localhost:8000/default`
- **Self-hosted (with auth)**: `opteryx://user:token@opteryx.example.com/my_database?ssl=true`

Notes:
- If `ssl=true` or port 443 is used, the driver will use HTTPS. The default port is 8000 for plain HTTP, 443 for HTTPS.
- Pass a token in place of a password for bearer token authentication.

---

## Getting Started

### 1. Create an Opteryx Account

If you don't have an Opteryx account yet, register at: **https://opteryx.app/auth/register.html**

Once registered, you'll receive credentials (username and token) needed to authenticate.

### 2. Install the Package

Install the published package from PyPI in any environment:

```bash
pip install opteryx-sqlalchemy
```

For contributions or debugging, install the local project in editable mode with the SQLAlchemy extras:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[sqlalchemy]'
```

---

## Quickstart — SQLAlchemy Core / Engine

Basic usage with SQLAlchemy 2.x:

```python
from sqlalchemy import create_engine, text

# Connect to Opteryx Cloud with your credentials
engine = create_engine(
    "opteryx://myusername:mytoken@opteryx.app:443/default?ssl=true"
)

with engine.connect() as conn:
    # Run a simple query
    result = conn.execute(text("SELECT * FROM public.examples.users LIMIT 10"))
    for row in result:
        print(row)

    # Parameterized query example
    stmt = text("SELECT * FROM events WHERE user_id = :uid")
    result = conn.execute(stmt, {"uid": 123})
    print(result.fetchall())
```

**Connection String Format:**
- Replace `myusername` with your Opteryx username
- Replace `mytoken` with your Opteryx authentication token
- For Opteryx Cloud, always use `opteryx.app:443` with `ssl=true`

---

## Debug Logging 🔍

The dialect includes comprehensive logging to help troubleshoot issues. Enable it with Python's standard `logging` module:

```python
import logging

# Enable INFO level for query timing and status
logging.basicConfig()
logging.getLogger("sqlalchemy.dialects.opteryx").setLevel(logging.INFO)

# Or enable DEBUG level for detailed request/response information
logging.getLogger("sqlalchemy.dialects.opteryx").setLevel(logging.DEBUG)
```

**What you'll see:**
- **INFO**: Authentication status, query completion times, row counts, long-running query progress
- **DEBUG**: HTTP requests/responses, query text, parameters, state transitions, execution IDs
- **WARNING**: Authentication failures, non-fatal issues
- **ERROR**: Failures with full context including HTTP status codes

---

## Using with pandas

You can use `pandas.read_sql_query` with a SQLAlchemy connection:

```python
import pandas as pd
from sqlalchemy import create_engine

engine = create_engine(
    "opteryx://myusername:mytoken@opteryx.app:443/default?ssl=true"
)
with engine.connect() as conn:
    df = pd.read_sql_query("SELECT * FROM public.examples.users LIMIT 100", conn)
    print(df.head())
```

Note: this requires pandas to be installed in your environment.

---

## Behavior and Limitations ⚠️

- Opteryx is primarily an analytics engine — the dialect treats the service as read-only. Transactional features are effectively no-ops.
- Not all SQLAlchemy reflection/introspection features are available. Some schema introspection operations may return empty results or limited metadata.
- The dialect maps Opteryx native types to SQLAlchemy types as best-effort but does not implement a complete type mapping for every possible backend type.
- If execution fails or times out, the DBAPI will raise an appropriate exception (subclass of DatabaseError/OperationalError).

---

## Testing

Run the tests with pytest:

```bash
python -m pytest -q
```

Tests use mocked HTTP calls for deterministic behavior, so they don't require a running Opteryx server for basic unit tests.

---

## Development & Contributing 💡

Contributions are welcome. To contribute:

1. Fork the repo
2. Create a feature branch
3. Run tests and lint checks
4. Open a pull request with a clear description

- Follow repo formatting and the `ruff`/isort rules in `pyproject.toml`.
- When adding functionality, include tests and documentation for the new behavior.

---

## Reference

- Project package name (pyproject): `opteryx-sqlalchemy`
- Dialect name: `opteryx` (SQLAlchemy dialect entry points are registered in `pyproject.toml`)
- DBAPI module: `sqlalchemy_dialect.dbapi`
- Dialect class: `sqlalchemy_dialect.dialect:OpteryxDialect`

---

## Support

If you find a bug or want to request a feature, please open an issue describing the steps to reproduce and any relevant details.

---

## License

See LICENSE file in the repository for details.

---

Thank you for using `opteryx-sqlalchemy` — bring On Demand Opteryx to your analytics workflows! 🚀