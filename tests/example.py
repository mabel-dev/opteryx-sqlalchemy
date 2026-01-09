from __future__ import annotations

import logging
import os
import sys

# Make local package importable in editable/test mode (same pattern as tests/plain_script)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import the package so the dialect registers itself in editable/test mode
import sqlalchemy_dialect  # noqa: F401

username = ""
password = ""

from sqlalchemy import create_engine, text

# Configure logging to see the new debug output
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# Enable INFO level for opteryx dialect to see query execution times
logging.getLogger("sqlalchemy.dialects.opteryx").setLevel(logging.INFO)

# username:token@host:port/database?ssl=true
engine = create_engine(f"opteryx://{username}:{password}@opteryx.app:443/default?ssl=true")

with engine.connect() as conn:
    res = conn.execute(text("SELECT * FROM benchmarks.tpch.lineitem LIMIT 50"))
    print(res.fetchall())
