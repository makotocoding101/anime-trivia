"""App factory. The worker-count assertion runs before anything serves
(invariant 7): with more than one worker, rooms silently split
across processes, so we refuse to boot instead."""

from __future__ import annotations

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

SEED_PATH = Path(__file__).parents[1] / "seed" / "questions.json"


def create_app(
    clock: Clock | None = None,
    game_config: GameConfig | None = None,
) -> FastAPI:
    """`clock` and `game_config` are injection points for tests: a FakeClock
    or a sped-up config turns integration tests from minutes into seconds."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        assert_single_worker()
        yield

    app = FastAPI(title="anime-trivia", lifespan=lifespan)
    app.state.registry = InProcessRegistry(clock or RealClock())
    app.state.question_bank = load_questions(SEED_PATH)
    app.state.game_config = game_config or GameConfig()
    app.include_router(rest.router)
    app.include_router(ws.router)
    return app


app = create_app()
