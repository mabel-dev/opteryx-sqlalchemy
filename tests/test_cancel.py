"""Tests for submitting without waiting (execute_async) and cancelling jobs."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import requests
from sqlalchemy_dialect import dbapi


def _response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
    return response


_DONE = {
    "status": {"state": "SUCCEEDED"},
    "data": [{"name": "id", "type": "INTEGER", "values": [1, 2]}],
    "total_rows": 2,
}


@patch("requests.Session.post")
@patch("requests.Session.get")
def test_execute_async_returns_after_submit(mock_get, mock_post):
    mock_post.return_value = _response({"execution_id": "20261108120000-abcdefghijklmnop"})
    mock_get.return_value = _response(_DONE)

    cursor = dbapi.Connection().cursor()
    assert cursor.statement_handle is None

    assert cursor.execute_async("SELECT id FROM t") is cursor
    assert cursor.statement_handle == "20261108120000-abcdefghijklmnop"
    mock_get.assert_not_called()  # not polled yet

    # Fetching waits for the job, as execute would have.
    assert cursor.fetchall() == [(1,), (2,)]
    assert [column[0] for column in cursor.description] == ["id"]
    assert cursor.rowcount == 2
    assert mock_get.called


@pytest.mark.parametrize("attribute", ["description", "rowcount"])
@patch("requests.Session.post")
@patch("requests.Session.get")
def test_execute_async_description_and_rowcount_wait(mock_get, mock_post, attribute):
    mock_post.return_value = _response({"execution_id": "job-1"})
    mock_get.return_value = _response(_DONE)

    cursor = dbapi.Connection().cursor()
    cursor.execute_async("SELECT id FROM t")
    getattr(cursor, attribute)

    assert cursor.fetchall() == [(1,), (2,)]


@patch("requests.Session.post")
def test_cancel_posts_to_the_jobs_api(mock_post):
    mock_post.return_value = _response(
        {"execution_id": "job-1", "cancelled": True, "status": "CANCELLED"}
    )
    connection = dbapi.Connection()

    assert connection.cursor().cancel("job-1") is True
    assert mock_post.call_args.args[0].endswith("/api/v1/jobs/job-1/cancel")


@patch("requests.Session.post")
@patch("requests.Session.get")
def test_cancel_defaults_to_the_cursors_own_job(mock_get, mock_post):
    mock_post.side_effect = [
        _response({"execution_id": "job-2"}),
        _response({"execution_id": "job-2", "cancelled": True, "status": "CANCELLED"}),
    ]

    cursor = dbapi.Connection().cursor()
    cursor.execute_async("SELECT id FROM t")

    assert cursor.cancel() is True
    assert mock_post.call_args.args[0].endswith("/api/v1/jobs/job-2/cancel")


def test_cancel_without_a_statement_is_false():
    assert dbapi.Connection().cursor().cancel() is False


@patch("requests.Session.post")
def test_cancel_unknown_job_is_false(mock_post):
    mock_post.return_value = _response({"detail": "job not found"}, status_code=404)

    assert dbapi.Connection().cursor().cancel("nope") is False


@patch("requests.Session.post")
def test_cancel_someone_elses_job_raises(mock_post):
    mock_post.return_value = _response({"detail": "forbidden"}, status_code=403)

    with pytest.raises(dbapi.OperationalError, match="Authentication error: forbidden"):
        dbapi.Connection().cursor().cancel("job-3")


@patch("requests.Session.post")
@patch("requests.Session.get")
def test_waiting_on_a_cancelled_job_raises_operational_error(mock_get, mock_post):
    mock_post.return_value = _response({"execution_id": "job-4"})
    mock_get.return_value = _response({"status": {"state": "CANCELLED"}})

    cursor = dbapi.Connection().cursor()
    cursor.execute_async("SELECT id FROM t")

    with pytest.raises(dbapi.OperationalError, match="job-4 was cancelled"):
        cursor.fetchall()
