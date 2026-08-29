"""DbStore: the Postgres GameStore (spec §06).

Exactly two touches per game — one SELECT at start filling the deck from the
mode's stored query specification, one INSERT batch at the end recording
results. The round loop never sees this module.
"""

from __future__ import annotations

import dataclasses
import logging

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.models import (
    Game,
    GameAnswer,
    GameMode,
    GamePlayer,
    Question,
    QuestionTag,
)
from app.game.errors import GameError
from app.game.state import GameConfig, LoadedQuestion, Option
from app.store import GameRecord, ModeInfo

logger = logging.getLogger(__name__)


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_size=5, pool_pre_ping=True)


class DbStore:
    def __init__(self, engine: AsyncEngine, base_config: GameConfig) -> None:
        self._engine = engine
        self._session = async_sessionmaker(engine, expire_on_commit=False)
        self._base = base_config  # intro/reveal/grace pacing; modes own the rest

    async def list_modes(self) -> list[ModeInfo]:
        async with self._session() as session:
            rows = (await session.scalars(select(GameMode).order_by(GameMode.id))).all()
        return [
            ModeInfo(
                id=m.id,
                slug=m.slug,
                name=m.name,
                blurb=m.blurb,
                question_count=m.question_count,
                seconds_per_q=m.seconds_per_q,
            )
            for m in rows
        ]

    async def load_deck(self, mode_id: int) -> tuple[tuple[LoadedQuestion, ...], GameConfig]:
        """The one SELECT at game start. ORDER BY random() LIMIT n samples
        without replacement in the database itself (R-15)."""
        async with self._session() as session:
            mode = await session.get(GameMode, mode_id)
            if mode is None:
                raise GameError("mode_not_found")

            stmt = (
                select(Question)
                .where(
                    Question.is_active,
                    Question.difficulty.between(mode.difficulty_min, mode.difficulty_max),
                )
                .options(selectinload(Question.options))
            )
            if mode.series_id is not None:
                stmt = stmt.where(Question.series_id == mode.series_id)
            if mode.required_tags:
                # ALL required tags must be present, not any: group + count.
                sub = (
                    select(QuestionTag.question_id)
                    .where(QuestionTag.tag_id.in_(mode.required_tags))
                    .group_by(QuestionTag.question_id)
                    .having(func.count(distinct(QuestionTag.tag_id)) == len(mode.required_tags))
                )
                stmt = stmt.where(Question.id.in_(sub))
            stmt = stmt.order_by(func.random()).limit(mode.question_count)

            questions = (await session.scalars(stmt)).all()

        if len(questions) < mode.question_count:
            # A mode that quietly runs short is the most likely content bug
            # (spec §06); refuse loudly instead of dealing a thin deck.
            raise GameError("deck_too_small")

        deck = tuple(_to_loaded(q) for q in questions)
        config = dataclasses.replace(
            self._base,
            question_count=mode.question_count,
            seconds_per_question=float(mode.seconds_per_q),
        )
        return deck, config

    async def record_game(self, record: GameRecord) -> None:
        """The one INSERT batch at game end. game_answer's composite primary
        key is R-01 in durable form — a double-scored round cannot be
        written, only rejected."""
        async with self._session() as session, session.begin():
            session.add(
                Game(
                    id=record.game_id,
                    room_code=record.room_code,
                    mode_id=record.mode_id,
                    started_at=record.started_at,
                    ended_at=record.ended_at,
                    question_count=record.question_count,
                )
            )
            session.add_all(
                GamePlayer(
                    game_id=record.game_id,
                    player_id=row.player_id,
                    name=row.name,
                    final_score=row.final_score,
                    final_rank=row.final_rank,
                    total_elapsed_ms=row.total_elapsed_ms,
                )
                for row in record.standings
            )
            session.add_all(
                GameAnswer(
                    game_id=record.game_id,
                    round_index=rnd.index,
                    player_id=player_id,
                    question_id=rnd.question_id,
                    option_id=option_id,
                    elapsed_ms=elapsed_ms,
                    is_correct=option_id == rnd.correct_option_id and option_id is not None,
                    points=points,
                )
                for rnd in record.history
                for player_id, (option_id, elapsed_ms, points) in rnd.entries.items()
            )
        logger.info(
            "recorded game %s: %d rounds, %d players",
            record.game_id,
            len(record.history),
            len(record.standings),
        )


def _to_loaded(q: Question) -> LoadedQuestion:
    """DB rows -> the in-memory question. Option ids are the GLOBAL
    answer_option ids from here on (the wire and game_answer both use them);
    the per-question ordinals of the file-backed path disappear."""
    correct = [o.id for o in q.options if o.is_correct]
    if len(correct) != 1:  # unreachable while one_correct_per_question stands
        raise GameError("corrupt_question")
    return LoadedQuestion(
        id=q.id,
        kind=q.kind,
        prompt=q.prompt,
        options=tuple(Option(id=o.id, label=o.label) for o in q.options),
        correct_option_id=correct[0],
        difficulty=q.difficulty,
        media_ref=q.media_ref,
    )
