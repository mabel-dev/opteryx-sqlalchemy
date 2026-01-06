"""
SQLAlchemy dialect for Opteryx (opteryx.app).

This module provides a SQLAlchemy dialect that connects to the Opteryx
data service via its HTTP API, enabling use of SQLAlchemy's ORM and
SQL expression language with Opteryx.

Usage:
    from sqlalchemy import create_engine

    # Connect to Opteryx data service
    engine = create_engine(
        "opteryx://user:password@jobs.opteryx.app:443/default?ssl=true"
    )
"""

from .dialect import OptetyxDialect

__all__ = ["OptetyxDialect"]
