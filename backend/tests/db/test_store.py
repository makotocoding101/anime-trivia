"""The Postgres store against a real database: schema constraints, the mode
query, and both boundary crossings (deck out, results in)."""

from __future__ import annotations

import datetime
import json
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.models import AnswerOption, Game, GameAnswer, GameMode, GamePlayer, Question
from app.db.seed import check_mode_margins
from app.db.store import DbStore
from app.game.errors import GameError
from app.game.state import GameConfig, PlayerId, RoundRecord
from app.store import GameRecord, StandingRow

pytestmark = pytest.mark.db

BASE = GameConfig()


@pytest.fixture
async def engine(migrated_db: str):
    engine = create_async_engine(migrated_db)
    yield engine
    await engine.dispose()


@pytest.fixture
def store(engine: AsyncEngine) -> DbStore:
    return DbStore(engine, BASE)


async def test_seed_landed(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        questions = await conn.scalar(select(func.count(Question.id)))
        options = await conn.scalar(select(func.count(AnswerOption.id)))
        modes = await conn.scalar(select(func.count(GameMode.id)))
    assert (questions, options, modes) == (10, 40, 2)


async def test_one_correct_per_question_is_unrepresentable(engine: AsyncEngine) -> None:
    """The partial unique index from §06: a second correct answer is an
    IntegrityError, not a quiet data bug."""
    async with engine.connect() as conn:
        question_id = await conn.scalar(
            select(AnswerOption.question_id).where(AnswerOption.is_correct).limit(1)
        )
        assert question_id is not None
        with pytest.raises(IntegrityError, match="one_correct_per_question"):
            await conn.execute(
                AnswerOption.__table__.insert().values(
                    question_id=question_id,
                    ordinal=99,
                    label="also correct?!",
                    is_correct=True,
                )
            )


async def test_modes_have_the_required_margin(engine: AsyncEngine) -> None:
    await check_mode_margins(engine)  # raises if any mode runs short


async def test_list_modes(store: DbStore) -> None:
    modes = await store.list_modes()
    assert [m.slug for m in modes] == ["quick-5", "hard-3"]
    assert modes[0].question_count == 5 and modes[0].seconds_per_q == 20
    assert modes[1].question_count == 3 and modes[1].seconds_per_q == 15


async def test_load_deck_respects_the_mode_spec(store: DbStore) -> None:
    modes = {m.slug: m for m in await store.list_modes()}

    deck, config = await store.load_deck(modes["quick-5"].id)
    assert len(deck) == 5
    assert len({q.id for q in deck}) == 5  # without replacement (R-15)
    assert config.question_count == 5
    assert config.seconds_per_question == 20.0
    assert config.grace_seconds == BASE.grace_seconds  # pacing comes from base
    for q in deck:
        option_ids = {o.id for o in q.options}
        assert q.correct_option_id in option_ids
        assert len(option_ids) == 4
        assert min(option_ids) > 0  # global answer_option ids, not ordinals

    hard_deck, hard_config = await store.load_deck(modes["hard-3"].id)
    assert len(hard_deck) == 3
    assert all(q.difficulty >= 2 for q in hard_deck)
    assert hard_config.seconds_per_question == 15.0


async def test_load_deck_unknown_mode(store: DbStore) -> None:
    with pytest.raises(GameError) as exc:
        await store.load_deck(999)
    assert exc.value.code == "mode_not_found"


async def test_thin_mode_refuses_to_deal(store: DbStore, engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            GameMode.__table__.insert()
            .values(slug=f"thin-{uuid.uuid4().hex[:6]}", name="Thin", question_count=99)
            .returning(GameMode.__table__.c.id)
        )
        thin_id = result.scalar_one()
    with pytest.raises(GameError) as exc:
        await store.load_deck(thin_id)
    assert exc.value.code == "deck_too_small"


def _record(deck_question_id: int, correct_option_id: int) -> GameRecord:
    pid_a: PlayerId = str(uuid.uuid4())
    pid_b: PlayerId = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.UTC)
    return GameRecord(
        game_id=str(uuid.uuid4()),
        room_code="TEST",
        mode_id=1,
        started_at=now - datetime.timedelta(minutes=2),
        ended_at=now,
        question_count=1,
        history=(
            RoundRecord(
                index=0,
                question_id=deck_question_id,
                correct_option_id=correct_option_id,
                entries={
                    pid_a: (correct_option_id, 2000, 145),
                    pid_b: (None, 20000, 0),
                },
            ),
        ),
        standings=(
            StandingRow(pid_a, "aoi", 145, 1, 2000),
            StandingRow(pid_b, "ren", 0, 2, 20000),
        ),
    )


async def test_record_game_writes_the_batch(store: DbStore, engine: AsyncEngine) -> None:
    deck, _ = await store.load_deck(1)
    record = _record(deck[0].id, deck[0].correct_option_id)
    await store.record_game(record)

    async with engine.connect() as conn:
        game = (await conn.execute(select(Game).where(Game.id == record.game_id))).one()
        players = (
            await conn.execute(select(GamePlayer).where(GamePlayer.game_id == record.game_id))
        ).all()
        answers = (
            await conn.execute(select(GameAnswer).where(GameAnswer.game_id == record.game_id))
        ).all()
    assert game.room_code == "TEST" and game.question_count == 1
    assert {p.name: p.final_rank for p in players} == {"aoi": 1, "ren": 2}
    by_correct = {a.is_correct: a for a in answers}
    assert by_correct[True].points == 145
    assert by_correct[False].option_id is None and by_correct[False].points == 0


async def test_double_answer_row_is_impossible(store: DbStore, engine: AsyncEngine) -> None:
    """R-01 in durable form: the composite PK rejects a second answer row for
    the same (game, round, player) even if every in-memory guard failed."""
    deck, _ = await store.load_deck(1)
    record = _record(deck[0].id, deck[0].correct_option_id)
    await store.record_game(record)

    duplicated_player = record.standings[0].player_id
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                GameAnswer.__table__.insert().values(
                    game_id=record.game_id,
                    round_index=0,
                    player_id=duplicated_player,
                    question_id=deck[0].id,
                    option_id=None,
                    elapsed_ms=1,
                    is_correct=False,
                    points=999,
                )
            )


def test_full_game_lands_in_postgres(migrated_db: str) -> None:
    """The whole M4 loop over real sockets: play a game through the app with
    the DbStore, then find it in the database."""
    from starlette.testclient import TestClient

    from app.main import create_app

    fast = GameConfig(intro_seconds=0.05, reveal_seconds=0.05, grace_seconds=0.1)
    app = create_app(game_config=fast, database_url=migrated_db)

    with TestClient(app) as client:
        code = client.post("/api/rooms").json()["code"]
        modes = client.get("/api/modes").json()
        hard = next(m for m in modes if m["slug"] == "hard-3")

        with (
            client.websocket_connect(f"/ws/rooms/{code}") as ws1,
            client.websocket_connect(f"/ws/rooms/{code}") as ws2,
        ):
            for ws, name in ((ws1, "aoi"), (ws2, "ren")):
                ws.send_text(json.dumps({"type": "join", "name": name}))
            ws1.send_text(json.dumps({"type": "start_game", "mode_id": hard["id"]}))

            def pump(ws, type_):
                for _ in range(80):
                    frame = json.loads(ws.receive_text())
                    if frame["type"] == type_:
                        return frame
                raise AssertionError(f"no {type_} frame")

            for _ in range(hard["question_count"]):
                q1 = pump(ws1, "question")
                pump(ws2, "question")
                # Answer per the placeholder prompt's own instruction.
                letter = q1["data"]["prompt"].rstrip(".").split()[-1]
                correct = next(
                    o["id"] for o in q1["data"]["options"] if o["label"].endswith(letter)
                )
                for ws, option in ((ws1, correct), (ws2, correct)):
                    ws.send_text(
                        json.dumps(
                            {
                                "type": "submit_answer",
                                "round_seq": q1["data"]["round_seq"],
                                "option_id": option,
                            }
                        )
                    )
            over = pump(ws1, "game_over")
            assert over["data"]["standings"][0]["score"] > 0

    # The lifespan exit disposed the app's engine; inspect with a fresh one.
    import asyncio

    async def fetch_counts() -> tuple[int, int, int]:
        engine = create_async_engine(migrated_db)
        try:
            async with engine.connect() as conn:
                games = await conn.scalar(select(func.count(Game.id)).where(Game.room_code == code))
                answers = await conn.scalar(
                    select(func.count())
                    .select_from(GameAnswer)
                    .join(Game, Game.id == GameAnswer.game_id)
                    .where(Game.room_code == code)
                )
                players = await conn.scalar(
                    select(func.count())
                    .select_from(GamePlayer)
                    .join(Game, Game.id == GamePlayer.game_id)
                    .where(Game.room_code == code)
                )
        finally:
            await engine.dispose()
        return games or 0, answers or 0, players or 0

    games, answers, players = asyncio.run(fetch_counts())
    assert games == 1
    assert players == 2
    assert answers == hard["question_count"] * 2  # every round, both players
