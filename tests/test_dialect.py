import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pathlib
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from tests import load_dotenv_simple


def _get_engine_from_env() -> Engine:
    env_path = _find_env_file()
    load_dotenv_simple(str(env_path))
    connection_string = os.getenv("OPTERYX_CONNECTION_STRING")
    if not connection_string:
        pytest.skip("OPTERYX_CONNECTION_STRING missing (set in .env or env vars)")
    return create_engine(connection_string)


def _find_env_file() -> pathlib.Path:
    current_dir = pathlib.Path(__file__).resolve().parent
    for directory in (current_dir,) + tuple(current_dir.parents):
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return pathlib.Path(".env")


def test_opteryx_connection():
    engine = _get_engine_from_env()

    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, name FROM $planets LIMIT 10"))
        rows = [row for row in result]
        assert rows, "Query did not return any rows"
        for row in rows:
            print(row)


def test_streaming_query():
    engine = _get_engine_from_env()

    with engine.connect() as conn:
        result = conn.execution_options(stream_results=True, max_row_buffer=500).execute(
            text("SELECT id FROM $planets AS P")
        )
        count = 0
        for _ in result:
            count += 1
        print(f"Total rows fetched: {count}")
        assert count > 0


def test_load_into_pandas():
    import pandas as pd

    engine = _get_engine_from_env()
    # read table data using sql query
    sql_df = pd.read_sql("SELECT * FROM $planets", con=engine)
    assert not sql_df.empty, "DataFrame is empty"
    assert "name" in sql_df.columns, "'name' column not found in DataFrame"


if __name__ == "__main__":
    # test_opteryx_connection()
    test_streaming_query()
    # test_load_into_pandas()

    # pytest.main([__file__])
