# sqlalchemy-opteryx

SQLAlchemy dialect for Opteryx Cloud (https://opteryx.app) — use On Demand Opteryx wherever SQLAlchemy is supported.

This project packages a SQLAlchemy dialect and lightweight DBAPI 2.0 adapter that talk to Opteryx's HTTP API, enabling read-only SQL access to Opteryx Cloud from SQLAlchemy Core, engines, and downstream tools like pandas. The library is published on PyPI as `opteryx-sqlalchemy`, so you can `pip install opteryx-sqlalchemy` in any environment.

---

## Features ✅

- Connect to Opteryx Cloud or self-hosted Opteryx instances with a SQLAlchemy connection URL and optional bearer token.
- Read-only analytics with transparent polling of query status, and streaming of large results a page at a time.
- Lightweight DBAPI implementation that maps Opteryx types to SQLAlchemy while surfacing DatabaseError/OperationalError semantics.
- Works with SQLAlchemy 1.4 and 2.x, including context-managed engines and `text` queries.
- Schema introspection (`inspect(engine)`) — list schemas, tables, views, columns (with types) and declared foreign keys without running SQL.
- Work with pandas or other tooling that understands SQLAlchemy engines.
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

Sign in (or create an account) at: **https://opteryx.app/login.html**

Once signed in, you'll receive credentials (username and token) needed to authenticate.

### 2. Install the Package

Install the published package from PyPI in any environment:

```bash
pip install opteryx-sqlalchemy
```

The opt-in `result_format="parquet"` execution option needs the `rugo` reader, which is an optional extra (wheels for CPython 3.11+ on Linux and macOS):

```bash
pip install "opteryx-sqlalchemy[parquet]"
```

---

See the [quickstart notebook](https://colab.research.google.com/github/mabel-dev/docs.opteryx.app/blob/main/docs-site/public/notebooks/query-opteryx-app-with-sqlalchemy.ipynb) for a runnable walkthrough covering all of the below (connecting, queries, pandas, execution options, and introspection) in one notebook — opens directly in Google Colab, no install required.

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
    result = conn.execute(text("SELECT id, name FROM public.astronomy.planets LIMIT 10"))
    for row in result:
        print(row)
```

**Connection String Format:**
- Replace `myusername` with your Opteryx username
- Replace `mytoken` with your Opteryx authentication token
- For Opteryx Cloud, always use `opteryx.app:443` with `ssl=true`

> **Bound parameters** are sent typed, and Opteryx binds each one as `CAST(value AS type)`:
> `str` → `VARCHAR`, `int` → `INT64`, `float` → `FLOAT64`, `bool` → `BOOL`,
> `Decimal` → `DECIMAL(p,s)` (taken from the value), `datetime` → `TIMESTAMP`,
> `date` → `DATE`, `time` → `TIME`, and `None` → a plain `NULL`. Other types raise
> `NotSupportedError`, including `bytes` (so the PEP 249 `Binary()` constructor can't be bound),
> lists, `UUID`, and numpy integers such as `np.int64` — convert those with `int(...)` first.

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
    df = pd.read_sql_query("SELECT * FROM public.astronomy.planets LIMIT 100", conn)
    print(df.head())
```

Note: this requires pandas to be installed in your environment.

---

## Streaming large results

By default a query's rows are all downloaded before the first one is returned. To read a large result a page at a time, turn on SQLAlchemy's `stream_results` (or, on SQLAlchemy 2.x, `yield_per`):

```python
from sqlalchemy import text

with engine.connect() as conn:
    result = conn.execution_options(stream_results=True, max_row_buffer=5000).execute(
        text("SELECT * FROM public.astronomy.planets")
    )
    for row in result:
        ...
```

The first page is downloaded when the query finishes, and each later page when you read past the end of the one before. Rows you've already read are dropped, so memory holds about one page. `max_row_buffer` (or the `yield_per` size) sets the page size, which is kept between 100 and 10,000 rows; it defaults to 10,000.

pandas doesn't turn streaming on for `chunksize`, so set it on the connection:

```python
with engine.connect() as conn:
    streaming = conn.execution_options(stream_results=True)
    for chunk in pd.read_sql_query("SELECT * FROM public.astronomy.planets", streaming, chunksize=5000):
        ...
```

---

## Schema Introspection 🔍

Use SQLAlchemy's `inspect()` to browse schemas, tables, views, and columns without writing SQL:

```python
from sqlalchemy import create_engine, inspect

engine = create_engine(
    "opteryx://myusername:mytoken@opteryx.app:443/default?ssl=true"
)

with engine.connect() as conn:
    insp = inspect(conn)

    # List every schema (namespace) visible to this token
    print(insp.get_schema_names())
    # ['benchmarks.tpch', 'personal.myusername', 'public.astronomy', ...]

    # List tables — scoped to a schema, or unscoped for full dotted names
    print(insp.get_table_names(schema="public.astronomy"))
    # ['planets']
    print(insp.get_table_names())
    # ['personal.myusername.customers', 'benchmarks.tpch.customer', ...]

    # Views (including materialized views) are listed separately from tables
    print(insp.get_view_names(schema="personal.myusername"))
    # ['audits_as_at_seven_jan', 'cve_count_by_year', ...]

    # Check whether a table exists
    print(insp.has_table("public.astronomy.planets"))
    # True

    # Get column names and types
    for column in insp.get_columns("planets", schema="public.astronomy"):
        print(column["name"], column["type"], "nullable:", column["nullable"])
    # id BIGINT nullable: False
    # name VARCHAR nullable: True
    # mass FLOAT nullable: True
    # ...
```

Introspection is backed by Opteryx's OData metadata endpoints rather than SQL queries, so it doesn't cost a billed query execution. The first call that needs table schemas (`get_columns`, or `has_table`/`get_table_names` the first time) can take longer than a typical query — full metadata generation is a heavier server-side operation — but the result is cached for the lifetime of the connection, so repeated calls (e.g. reflecting many tables) don't re-pay that cost.

The schema is the dataset's `workspace.collection` prefix. When SQLAlchemy renders a table it quotes each part of that prefix separately, so `table("planets", schema="public.astronomy")` compiles to `public.astronomy.planets`.

---

## Apache Superset

Install the dialect into Superset's Python environment and add a database with this SQLAlchemy URI:

```
opteryx://<client_id>:<client_secret>@opteryx.app:443/?ssl=true
```

Schemas appear as `workspace.collection` and tables as the datasets in it. Superset needs an engine spec for Opteryx to offer time grains (day, week, month and so on) in charts; one is being contributed upstream to Apache Superset.

`sqlalchemy_dialect.functions.FUNCTION_NAMES` lists the functions the engine accepts, for autocomplete without running a query. It is generated from opteryx-core's catalogs with `python scripts/generate_function_names.py <path-to-opteryx-core>`; regenerate it before each release.

---

## Behavior and Limitations ⚠️

- Opteryx is primarily an analytics engine — the dialect treats the service as read-only. Transactional features are effectively no-ops.
- Schema introspection (`has_table`, `get_table_names`, `get_view_names`, `get_schema_names`, `get_columns`, `get_foreign_keys`) is supported.
- `get_foreign_keys` returns the relationships declared in Opteryx. Opteryx doesn't enforce them, and only lists a foreign key when you can see the table it refers to.
- Opteryx has no primary keys or indexes, so `get_pk_constraint` and `get_indexes` always return empty.
- The dialect maps Opteryx native types to SQLAlchemy types as best-effort but does not implement a complete type mapping for every possible backend type.
- Results are typed. Each `cursor.description` entry carries the engine's type name as its type code (`"TIMESTAMP[us]"`, `"DATE"`, `"DECIMAL(15, 2)"`, `"INT64"`, ...), and these compare equal to the PEP 249 `DATETIME`, `NUMBER`, `STRING` and `BINARY` objects. Values come back as Python types:
  - TIMESTAMP becomes a timezone-aware `datetime` in UTC.
  - DATE becomes a `date` and TIME a `time`.
  - DECIMAL becomes a `decimal.Decimal`. The results API sends decimals as JSON numbers, so values with more than 15 significant digits may lose precision.
- The `result_format` execution option picks how pages are fetched. `"json"` (the default) uses the typed `/results` pages. `"ndjson"` uses untyped rows from `/download`; values arrive as JSON delivers them, so timestamps are strings. `"parquet"` also uses `/download` and needs the `[parquet]` extra. If `/results` can't serve a job, the cursor falls back to `/download`.
- Results are fetched in pages. By default every page is downloaded before the first row is returned; with `stream_results` or `yield_per`, pages are downloaded as rows are read (see [Streaming large results](#streaming-large-results)). While streaming, `rowcount` comes from the server's row total, or is -1 if the server doesn't send one.
- If execution fails or times out, the DBAPI will raise an appropriate exception (subclass of DatabaseError/OperationalError).
- `cursor.execute_async(sql)` submits a statement and returns once the job exists; `cursor.statement_handle` is then the job ID, and fetching (or reading `description`/`rowcount`) waits for the job as `execute` would. `cursor.cancel(statement_handle=None)` asks the service to stop a running job (the cursor's own by default) and returns whether it was cancelled; only the client that submitted a job can cancel it. A cursor waiting on a cancelled job raises `OperationalError`.

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
