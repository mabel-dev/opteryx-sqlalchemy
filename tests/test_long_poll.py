"""The cursor long-polls /results and reads large results in parallel."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import requests

from sqlalchemy_dialect import dbapi


def _response(status_code=200, json_body=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = text
    return resp


def _page(values, total):
    return {"total_rows": total, "data": [{"name": "n", "type": "INT64", "values": values}]}


class _Jobs:
    """A fake jobs service: /results answers 202 for `running` polls, then pages."""

    def __init__(self, running=("SUBMITTED", "EXECUTING"), total=3):
        self.running = list(running)
        self.total = total
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if url.endswith("/status"):
            raise AssertionError("the long-poll path never calls /status")
        if url.endswith("/results"):
            if "wait" in params and self.running:
                state = self.running.pop(0)
                return _response(202, {"execution_id": "h", "status": state})
            offset, num_rows = params["offset"], params["num_rows"]
            values = list(range(offset, min(offset + num_rows, self.total)))
            return _response(200, _page(values, self.total))
        if url.endswith("/download"):
            return _response(200, text="\n".join(f'{{"n": {i}}}' for i in range(self.total)))
        raise AssertionError(url)


def _execute(jobs, sql="SELECT n FROM t", timeout=30.0, **cursor_attrs):
    with (
        patch("requests.Session.post") as post,
        patch("requests.Session.get", side_effect=jobs.get),
        patch("sqlalchemy_dialect.dbapi.time.sleep") as sleep,
    ):
        post.return_value = _response(201, {"execution_id": "h"})
        conn = dbapi.Connection(timeout=timeout)
        cursor = conn.cursor()
        for name, value in cursor_attrs.items():
            setattr(cursor, name, value)
        cursor.execute(sql)
        rows = cursor.fetchall()
        return cursor, rows, sleep


def test_first_page_arrives_with_completion():
    jobs = _Jobs()
    cursor, rows, _ = _execute(jobs)
    assert rows == [(0,), (1,), (2,)]
    assert cursor.description[0][:2] == ("n", "INT64")
    # Two 202 polls and the completing poll - no separate page read, no /status.
    assert len(jobs.calls) == 3
    assert all(params["wait"] == 20.0 for _, params in jobs.calls)


def test_wait_stays_inside_the_request_timeout():
    jobs = _Jobs(running=())
    _execute(jobs, timeout=10.0)
    assert jobs.calls[0][1]["wait"] == 5.0
    jobs = _Jobs(running=())
    _execute(jobs, timeout=3.0)
    assert jobs.calls[0][1]["wait"] == 0.0


def test_state_changes_are_followed_without_sleeping():
    jobs = _Jobs(running=("SUBMITTED", "EXECUTING"))
    _, _, sleep = _execute(jobs)
    sleep.assert_not_called()


def test_an_unheld_unchanged_answer_backs_off():
    """An older server answers at once; the cursor must not spin."""
    jobs = _Jobs(running=("EXECUTING", "EXECUTING", "EXECUTING"))
    _, rows, sleep = _execute(jobs)
    assert rows == [(0,), (1,), (2,)]
    # The first EXECUTING is news; the next two are repeats that were not held.
    assert [c.args[0] for c in sleep.call_args_list] == pytest.approx([0.05, 0.075])


def test_large_results_are_read_in_parallel_and_in_order():
    jobs = _Jobs(running=(), total=1_050)
    cursor, rows, _ = _execute(jobs, _opteryx_max_row_buffer=100)
    assert [r[0] for r in rows] == list(range(1_050))
    assert cursor.rowcount == 1_050
    offsets = sorted(params["offset"] for _, params in jobs.calls)
    assert offsets == list(range(0, 1_050, 100))  # each page read exactly once


def test_streaming_does_not_reread_the_first_page():
    jobs = _Jobs(running=(), total=250)
    with (
        patch("requests.Session.post") as post,
        patch("requests.Session.get", side_effect=jobs.get),
    ):
        post.return_value = _response(201, {"execution_id": "h"})
        cursor = dbapi.Connection().cursor()
        cursor._opteryx_stream_results_requested = True
        cursor._opteryx_max_row_buffer = 100
        cursor.execute("SELECT n FROM t")
        assert len(jobs.calls) == 1
        assert [r[0] for r in cursor.fetchall()] == list(range(250))


def test_other_formats_read_rows_from_download():
    jobs = _Jobs(running=(), total=2)
    _, rows, _ = _execute(jobs, _opteryx_result_format="ndjson")
    assert rows == [(0,), (1,)]
    assert jobs.calls[0][1]["num_rows"] == 100  # the completing page is kept small
    assert jobs.calls[-1][0].endswith("/download")


def test_unknown_statement_is_a_programming_error():
    def get(url, params=None, timeout=None):
        resp = _response(404, {"detail": "job not found"})
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
        return resp

    with patch("requests.Session.post") as post, patch("requests.Session.get", side_effect=get):
        post.return_value = _response(201, {"execution_id": "h"})
        with pytest.raises(dbapi.ProgrammingError):
            dbapi.Connection().cursor().execute("SELECT 1")


def test_failure_reported_by_a_poll_is_raised():
    def get(url, params=None, timeout=None):
        return _response(202, {"status": "FAILED", "error_message": "no such column"})

    with patch("requests.Session.post") as post, patch("requests.Session.get", side_effect=get):
        post.return_value = _response(201, {"execution_id": "h"})
        with pytest.raises(dbapi.ProgrammingError, match="no such column"):
            dbapi.Connection().cursor().execute("SELECT 1")
