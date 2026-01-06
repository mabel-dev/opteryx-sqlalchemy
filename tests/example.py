from __future__ import annotations

import os
import sys

# Make local package importable in editable/test mode (same pattern as tests/plain_script)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import the package so the dialect registers itself in editable/test mode
import sqlalchemy_dialect  # noqa: F401

from sqlalchemy import create_engine, text

# username:token@host:port/database?ssl=true
engine = create_engine("opteryx://username:password@opteryx.app:443/default?ssl=true")

with engine.connect() as conn:
    res = conn.execute(text("SELECT id, name FROM $planets LIMIT 5"))
    print(res.fetchall())
