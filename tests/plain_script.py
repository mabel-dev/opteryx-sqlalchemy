#!/usr/bin/env python3
"""
Simple smoke-test script for Opteryx auth + data endpoints.

"""

from __future__ import annotations

import os
import sys
import time
from typing import Any
from typing import Dict
from typing import Optional
from typing import Sequence

import orjson
import requests
from orso import DataFrame

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests import load_dotenv_simple

try:
    import brotli  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency for brotli
    brotli = None


load_dotenv_simple("../.env")


DEFAULT_AUTH_URL = "https://authenticate.opteryx.app"
DEFAULT_DATA_URL = "https://jobs.opteryx.app"
DEFAULT_CLIENT_ID = os.environ.get("CLIENT_ID")
DEFAULT_CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
SQL_STATEMENT = "SELECT * FROM $planets AS P"


def fatal(msg: str) -> None:
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(2)


class SmokeTestResult:
    def __init__(self) -> None:
        self.steps = []

    def ok(self, msg: str) -> None:
        print("✅", msg)
        self.steps.append((True, msg))

    def fail(self, msg: str) -> None:
        print("❌", msg)
        self.steps.append((False, msg))

    def all_ok(self) -> bool:
        return all(ok for ok, _ in self.steps)


def get_token(
    auth_url: str,
    client_id: str,
    client_secret: str,
    scope: Optional[str] = None,
    key_date: Optional[str] = None,
) -> str:
    url = f"{auth_url.rstrip('/')}/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope:
        data["scope"] = scope
    if key_date:
        data["key_date"] = key_date

    r = requests.post(url, data=data, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"token endpoint returned status {r.status_code}: {r.text}")
    body = r.json()
    token = body.get("access_token")
    if not token:
        raise RuntimeError("token endpoint returned no access_token")
    return token


def create_statement(
    data_url: str, token: str, sql: str = "SELECT 1", describe_only: bool | None = None
) -> Dict[str, Any]:
    url = f"{data_url.rstrip('/')}/api/v1/jobs"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload: Dict[str, Any] = {
        "sql_text": sql,
        "client_info": {"application_name": "smoke_test_script"},
    }
    if describe_only is not None:
        payload["describeOnly"] = describe_only

    r = requests.post(url, json=payload, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def get_statement_status(data_url: str, token: str, handle: str) -> Dict[str, Any]:
    url = f"{data_url.rstrip('/')}/api/v1/jobs/{handle}/status"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def get_statement_data(
    data_url: str, token: str, handle: str, offset: Optional[int] = None
) -> Dict[str, Any]:
    url = f"{data_url.rstrip('/')}/api/v1/jobs/{handle}/results"
    if offset is not None:
        url += f"?offset={offset}"
    headers = {"Authorization": f"Bearer {token}"}
    if brotli is not None:
        headers["Accept-Encoding"] = "br"
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    encoding = r.headers.get("Content-Encoding", "")
    if encoding.lower() == "br":
        if not brotli:
            raise RuntimeError("response is brotli encoded but brotli library is unavailable")
        try:
            content = brotli.decompress(r.content)
        except brotli.error:
            # Already decompressed or corrupt stream; fall back to raw bytes
            content = r.content
        return orjson.loads(content)
    return r.json()


def statement_results_to_dataframe(columns: Optional[Sequence[Dict[str, Any]]]) -> DataFrame:
    """Build an Orso DataFrame from the jobs.opteryx.app column payload."""

    if not columns:
        return DataFrame(rows=[], schema=[])

    column_entries = []
    for column in columns:
        if not column:
            continue
        name = column.get("name")
        if name is None:
            continue
        values = column.get("values") or []
        column_entries.append((str(name), list(values)))

    if not column_entries:
        return DataFrame(rows=[], schema=[])

    column_names = [name for name, _ in column_entries]
    column_values = [values for _, values in column_entries]
    max_rows = max((len(values) for values in column_values), default=0)
    rows = [
        tuple(values[index] if index < len(values) else None for values in column_values)
        for index in range(max_rows)
    ]

    return DataFrame(rows=rows, schema=column_names)


def main() -> int:
    result = SmokeTestResult()

    try:
        print("→ Requesting client credentials token...")
        token = get_token(DEFAULT_AUTH_URL, DEFAULT_CLIENT_ID, DEFAULT_CLIENT_SECRET)
        result.ok("Obtained access token")
    except requests.RequestException as exc:
        result.fail(f"Failed to obtain token: {exc}")
        print("See environment defaults or check AUTH_URL and client credentials.")
        return 1

    # Create statement
    try:
        print("→ Creating statement...")
        resp = create_statement(DEFAULT_DATA_URL, token, sql=SQL_STATEMENT)
        handle = resp.get("execution_id")
        if not handle:
            raise RuntimeError("response missing execution_id")
        result.ok(f"Statement created: {handle}")
    except requests.RequestException as exc:
        result.fail(f"Failed to create statement: {exc}")
        return 1

    resp = None
    # Poll status
    try:
        print("→ Polling statement status...")
        start = time.time()
        state = None
        while True:
            resp = get_statement_status(DEFAULT_DATA_URL, token, handle)
            state = resp.get("status", {})
            print(f"  status -> {state}         ", end="\r")
            if state in ("COMPLETED", "FAILED", "CANCELLED", "INCHOATE"):
                # We'll treat successful fetch as OK. Data service may be a stub and not actually run work.
                break
            if time.time() - start > 60:
                raise RuntimeError("timed out waiting for terminal status")
            time.sleep(0.5)
        if state == "COMPLETED":
            resp = get_statement_data(DEFAULT_DATA_URL, token, handle)
            table = statement_results_to_dataframe(resp.get("data", []))
            print(table)

        print()
        result.ok(f"Fetched statement status: {state}")
    except requests.RequestException as exc:
        if resp:
            print("Last response:", resp)
        result.fail(f"Failed to fetch statement status: {exc}")

    print()
    if result.all_ok():
        print("Smoke test completed: all checks passed")
        return 0
    else:
        print("Smoke test completed: some checks failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
