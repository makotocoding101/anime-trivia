"""App factory. The worker-count assertion runs before anything serves
(invariant 7): with more than one worker, rooms silently split
across processes, so we refuse to boot instead."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api import rest, ws
from app.config import assert_single_worker
from app.game.clock import Clock, RealClock
from app.game.deck import load_questions
from app.game.state import GameConfig
from app.rooms.registry import InProcessRegistry
from app.store import GameStore, SeedStore

SEED_PATH = Path(__file__).parents[1] / "seed" / "questions.json"


def create_app(
    clock: Clock | None = None,
    game_config: GameConfig | None = None,
    database_url: str | None = None,
) -> FastAPI:
    """`clock` and `game_config` are injection points for tests. The store is
    chosen by `database_url` (default: the DATABASE_URL env var): Postgres
    when present, the seed file when not — same GameStore seam either way."""
    base_config = game_config or GameConfig()
    url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        assert_single_worker()
        store: GameStore
        if url:
            # Imported lazily so the no-database path has no sqlalchemy need.
            from app.db.store import DbStore, make_engine

            engine = make_engine(url)
            store = DbStore(engine, base_config)
        else:
            engine = None
            store = SeedStore(load_questions(SEED_PATH), base_config)
        app.state.store = store
        registry = InProcessRegistry(clock or RealClock(), store=store)
        app.state.registry = registry
        try:
            yield
        finally:
            # A game that ended moments ago may still be writing its results;
            # finish those before tearing the engine down.
            await registry.drain_persistence()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(title="anime-trivia", lifespan=lifespan)
    app.include_router(rest.router)
    app.include_router(ws.router)
    return app


app = create_app()
