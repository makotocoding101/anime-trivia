"""DbStore: the Postgres GameStore (spec §06).

Exactly two touches per game — one SELECT at start filling the deck from the
mode's stored query specification, one INSERT batch at the end recording
results. The round loop never sees this module.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging

from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

from app.db.models import (
    Game,
    GameAnswer,
    GameMode,
    GamePlayer,
    PlayerAccount,
    PlayerWallet,
    Question,
    QuestionTag,
)
from app.game.economy import coins_awarded, wallet_key
from app.game.errors import GameError
from app.game.state import GameConfig, LoadedQuestion, Option
from app.store import AccountRow, GameRecord, ModeInfo, RankRow, WalletRow

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
            await self._credit_wallets(session, record)
        logger.info(
            "recorded game %s: %d rounds, %d players",
            record.game_id,
            len(record.history),
            len(record.standings),
        )

    @staticmethod
    async def _credit_wallets(session: AsyncSession, record: GameRecord) -> None:
        """Pay out the game, inside the same transaction that recorded it.

        Sharing the transaction is the point: a payout that can commit while
        the result it was computed from rolls back is a currency that mints
        itself. It also keeps the end-of-game boundary at exactly one write —
        several statements, one commit.

        One upsert per player rather than one batched statement, because two
        players in the same game can normalise to the same wallet (nothing
        stops two people typing "aoi"). Postgres refuses to let a single
        INSERT ... ON CONFLICT touch one row twice; separate statements
        accumulate onto it correctly, which is the behaviour we want — both
        of them did play.
        """
        player_count = len(record.standings)
        for row in record.standings:
            score = max(0, row.final_score)
            coins = coins_awarded(
                final_score=row.final_score,
                final_rank=row.final_rank,
                player_count=player_count,
            )
            won = 1 if row.final_rank == 1 else 0
            stmt = pg_insert(PlayerWallet).values(
                name_key=wallet_key(row.name),
                display_name=row.name,
                coins=coins,
                games_played=1,
                wins=won,
                best_score=score,
                lifetime_score=score,
                updated_at=record.ended_at,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[PlayerWallet.name_key],
                    set_={
                        # Re-stamped every game: the wallet renders with the
                        # capitalisation its owner used most recently.
                        "display_name": stmt.excluded.display_name,
                        "coins": PlayerWallet.coins + coins,
                        "games_played": PlayerWallet.games_played + 1,
                        "wins": PlayerWallet.wins + won,
                        "best_score": func.greatest(PlayerWallet.best_score, score),
                        "lifetime_score": PlayerWallet.lifetime_score + score,
                        "updated_at": record.ended_at,
                    },
                )
            )

    async def list_rankings(self, limit: int = 50) -> list[RankRow]:
        """The global table. Ordered by coins, tie-broken by key so the order
        is total — two wallets on equal coins must not swap places between
        two reads that nothing happened between."""
        async with self._session() as session:
            rows = (
                await session.scalars(
                    select(PlayerWallet)
                    .order_by(PlayerWallet.coins.desc(), PlayerWallet.name_key)
                    .limit(limit)
                )
            ).all()
        return [RankRow(rank=i, wallet=_to_wallet(w)) for i, w in enumerate(rows, start=1)]

    async def get_wallet(self, name: str) -> WalletRow | None:
        async with self._session() as session:
            row = await session.get(PlayerWallet, wallet_key(name))
        return None if row is None else _to_wallet(row)

    async def get_account(self, name: str) -> AccountRow | None:
        async with self._session() as session:
            row = await session.get(PlayerAccount, wallet_key(name))
        return None if row is None else AccountRow(row.display_name, row.password_hash)

    async def claim_account(self, name: str, password_hash: str) -> bool:
        """True if this call is the one that claimed the name.

        ON CONFLICT DO NOTHING ... RETURNING rather than SELECT-then-INSERT:
        two people racing for the same free name must not both be told they
        got it, and the check-then-write version has a window between the two
        statements wide enough to lose that race. The unique index decides,
        and the empty RETURNING is how the loser finds out.
        """
        stmt = (
            pg_insert(PlayerAccount)
            .values(
                name_key=wallet_key(name),
                display_name=name,
                password_hash=password_hash,
                created_at=datetime.datetime.now(datetime.UTC),
            )
            .on_conflict_do_nothing(index_elements=[PlayerAccount.name_key])
            .returning(PlayerAccount.name_key)
        )
        async with self._session() as session, session.begin():
            claimed = (await session.execute(stmt)).scalar_one_or_none()
        return claimed is not None


def _to_wallet(w: PlayerWallet) -> WalletRow:
    return WalletRow(
        name=w.display_name,
        coins=w.coins,
        games_played=w.games_played,
        wins=w.wins,
        best_score=w.best_score,
        lifetime_score=w.lifetime_score,
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
