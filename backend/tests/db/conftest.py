"""DB test harness. These tests need a reachable Postgres, named by
TRIVIA_TEST_DATABASE_URL; without it the whole directory skips (CI always
sets it, so the gate holds where it matters).

Schema setup runs once per session via subprocesses (alembic's env.py calls
asyncio.run, which cannot nest inside pytest-asyncio's loop)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parents[2]
TEST_URL = os.environ.get("TRIVIA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.db

RESET_SNIPPET = """
import asyncio, os
import asyncpg

async def main() -> None:
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await conn.close()

asyncio.run(main())
"""


def _run(*argv: str) -> None:
    env = {**os.environ, "DATABASE_URL": TEST_URL}
    subprocess.run(argv, check=True, cwd=BACKEND_DIR, env=env, capture_output=True)


@pytest.fixture(scope="session")
def migrated_db() -> str:
    if not TEST_URL:
        pytest.skip("TRIVIA_TEST_DATABASE_URL not set")
    _run(sys.executable, "-c", RESET_SNIPPET)
    _run(sys.executable, "-m", "alembic", "upgrade", "head")
    _run(sys.executable, "-m", "app.db.seed")
    return TEST_URL
