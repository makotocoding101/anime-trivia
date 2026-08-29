"""Deployment-shape guards, checked before the app serves anything.

Invariant 7: rooms live in process memory, owned by one asyncio
task each. With more than one worker process, each worker holds its own room
registry and players joining the same room code silently land in different
games — no error, just a split room. So we refuse to boot instead.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# Environment variables that supervisors and PaaS dashboards use to scale
# worker counts. uvicorn honours WEB_CONCURRENCY; the others catch common
# gunicorn/uvicorn wrapper setups.
_WORKER_ENV_VARS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS")


class WorkerCountError(RuntimeError):
    """Raised at startup when the environment asks for more than one worker."""


def assert_single_worker(env: Mapping[str, str] | None = None) -> None:
    """Fail fast if the environment requests multiple worker processes.

    Called from the app factory at startup (wired in M2). Detection is via the
    worker-count environment variables — a bare `uvicorn --workers N` on the
    command line should never be used; deployment docs and the Dockerfile CMD
    always go through WEB_CONCURRENCY so this check sees the truth.
    """
    environ = os.environ if env is None else env
    for var in _WORKER_ENV_VARS:
        raw = environ.get(var, "").strip()
        if not raw:
            continue
        try:
            count = int(raw)
        except ValueError as exc:
            raise WorkerCountError(
                f"{var}={raw!r} is not an integer; refusing to guess a worker count."
            ) from exc
        if count > 1:
            raise WorkerCountError(
                f"{var}={count} requests multiple workers, but room state is "
                "in-process and single-writer: each worker would hold its own "
                "room registry and rooms would silently split across processes. "
                "Run exactly one worker (invariant 7). Scale by "
                "adding rooms per process, not processes."
            )
