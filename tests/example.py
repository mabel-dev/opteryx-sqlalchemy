from __future__ import annotations

import logging
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy import text

# Make local package importable in editable/test mode (same pattern as tests/plain_script)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests import load_dotenv_simple
import sqlalchemy_dialect  # noqa: F401

load_dotenv_simple("../.env")

DEFAULT_CLIENT_ID = os.environ.get("CLIENT_ID")
DEFAULT_CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

# Configure logging to see the new debug output
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# Enable INFO level for opteryx dialect to see query execution times
logging.getLogger("sqlalchemy.dialects.opteryx").setLevel(logging.INFO)

# username:token@host:port/database?ssl=true
engine = create_engine(
    f"opteryx://{DEFAULT_CLIENT_ID}:{DEFAULT_CLIENT_SECRET}@opteryx.app:443/default?ssl=true"
)

with engine.connect() as conn:
    res = conn.execute(text("SELECT * FROM public.examples.planets LIMIT 50"))
    print(res.fetchall())
