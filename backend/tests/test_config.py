"""The single-worker startup assertion (invariant 7).

Deliberately in milestone 1: silently splitting a room across workers breaks
a deployment with no visible error, so the guard exists before the server does.
"""

from __future__ import annotations

import pytest

from app.config import WorkerCountError, assert_single_worker


def test_no_worker_env_is_fine() -> None:
    assert_single_worker(env={})


def test_explicit_single_worker_is_fine() -> None:
    assert_single_worker(env={"WEB_CONCURRENCY": "1"})
    assert_single_worker(env={"UVICORN_WORKERS": " 1 "})


@pytest.mark.parametrize("var", ["WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"])
def test_multiple_workers_refuse_to_boot(var: str) -> None:
    with pytest.raises(WorkerCountError, match="single-writer"):
        assert_single_worker(env={var: "4"})


def test_unparseable_worker_count_refuses_to_guess() -> None:
    with pytest.raises(WorkerCountError, match="not an integer"):
        assert_single_worker(env={"WEB_CONCURRENCY": "banana"})


def test_error_message_tells_the_operator_what_to_do() -> None:
    with pytest.raises(WorkerCountError) as exc:
        assert_single_worker(env={"WEB_CONCURRENCY": "2"})
    message = str(exc.value)
    assert "one worker" in message
    assert "room" in message  # explains *why*, not just *what*
