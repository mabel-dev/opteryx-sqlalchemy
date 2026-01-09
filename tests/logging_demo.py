"""
Demonstration of different logging levels in opteryx-sqlalchemy.

Run this script to see how different logging levels affect output.
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import sqlalchemy_dialect  # noqa: F401

from sqlalchemy import create_engine, text


def demo_info_level():
    """Demo INFO level logging - shows important operational events."""
    print("\n" + "=" * 80)
    print("INFO LEVEL - Shows query timing and authentication")
    print("=" * 80 + "\n")

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s %(message)s", force=True
    )
    logging.getLogger("sqlalchemy.dialects.opteryx").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    engine = create_engine("opteryx://bastian:12Monkeys@opteryx.app:443/default?ssl=true")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM benchmarks.tpch.lineitem LIMIT 5"))
        rows = result.fetchall()
        print(f"\nFetched {len(rows)} rows\n")


def demo_debug_level():
    """Demo DEBUG level logging - shows detailed diagnostic information."""
    print("\n" + "=" * 80)
    print("DEBUG LEVEL - Shows detailed request/response information")
    print("=" * 80 + "\n")

    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s %(levelname)-8s %(name)s %(message)s", force=True
    )
    logging.getLogger("sqlalchemy.dialects.opteryx").setLevel(logging.DEBUG)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    engine = create_engine("opteryx://bastian:12Monkeys@opteryx.app:443/default?ssl=true")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM benchmarks.tpch.lineitem LIMIT 5"))
        rows = result.fetchall()
        print(f"\nFetched {len(rows)} rows\n")


def demo_warning_level():
    """Demo WARNING level logging - only shows issues."""
    print("\n" + "=" * 80)
    print("WARNING LEVEL - Only shows warnings and errors (quiet)")
    print("=" * 80 + "\n")

    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)-8s %(name)s %(message)s", force=True
    )
    logging.getLogger("sqlalchemy.dialects.opteryx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    engine = create_engine("opteryx://bastian:12Monkeys@opteryx.app:443/default?ssl=true")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM benchmarks.tpch.lineitem LIMIT 5"))
        rows = result.fetchall()
        print(f"\nFetched {len(rows)} rows (no logs unless there's a problem)\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Demo opteryx-sqlalchemy logging")
    parser.add_argument(
        "--level",
        choices=["info", "debug", "warning", "all"],
        default="info",
        help="Logging level to demonstrate",
    )

    args = parser.parse_args()

    if args.level == "all":
        demo_warning_level()
        demo_info_level()
        demo_debug_level()
    elif args.level == "debug":
        demo_debug_level()
    elif args.level == "info":
        demo_info_level()
    elif args.level == "warning":
        demo_warning_level()
