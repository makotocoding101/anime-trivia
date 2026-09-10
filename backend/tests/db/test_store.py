"""The Postgres store against a real database: schema constraints, the mode
query, and both boundary crossings (deck out, results in)."""

from __future__ import annotations

import datetime
import json
import pathlib
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


def _seed_file(name: str) -> dict:
    path = pathlib.Path(__file__).parents[2] / "seed" / name
    return json.loads(path.read_text(encoding="utf-8"))


async def test_seed_landed(engine: AsyncEngine) -> None:
    # Counted against the seed files rather than against literals: the bank is
    # content, and a test that has to be edited every time a question is added
    # stops being read and starts being updated reflexively.
    expected_questions = _seed_file("questions.json")["questions"]
    expected_modes = _seed_file("modes.json")["modes"]
    async with engine.connect() as conn:
        questions = await conn.scalar(select(func.count(Question.id)))
        options = await conn.scalar(select(func.count(AnswerOption.id)))
        modes = await conn.scalar(select(func.count(GameMode.id)))
    assert questions == len(expected_questions)
    assert options == sum(len(q["options"]) for q in expected_questions)
    assert modes == len(expected_modes)


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
    expected = _seed_file("modes.json")["modes"]
    modes = await store.list_modes()
    assert [m.slug for m in modes] == [m["slug"] for m in expected]
    assert [m.question_count for m in modes] == [m["question_count"] for m in expected]
    assert [m.seconds_per_q for m in modes] == [m["seconds_per_q"] for m in expected]


async def test_load_deck_respects_the_mode_spec(store: DbStore) -> None:
    spec = _seed_file("modes.json")["modes"][0]
    mode = next(m for m in await store.list_modes() if m.slug == spec["slug"])

    deck, config = await store.load_deck(mode.id)
    assert len(deck) == spec["question_count"]
    assert len({q.id for q in deck}) == len(deck)  # without replacement (R-15)
    assert config.question_count == spec["question_count"]
    assert config.seconds_per_question == float(spec["seconds_per_q"])
    assert config.grace_seconds == BASE.grace_seconds  # pacing comes from base
    for q in deck:
        option_ids = {o.id for o in q.options}
        assert q.correct_option_id in option_ids
        assert len(option_ids) == 4
        assert min(option_ids) > 0  # global answer_option ids, not ordinals
        assert spec["difficulty_min"] <= q.difficulty <= spec["difficulty_max"]


async def test_every_seeded_mode_can_deal_a_deck(store: DbStore) -> None:
    """check_mode_margins proves enough questions match; this proves they
    actually come back. A tag typo satisfies the first and fails the second."""
    for mode in await store.list_modes():
        deck, _ = await store.load_deck(mode.id)
        assert len(deck) == mode.question_count, mode.slug


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


def _record(
    deck_question_id: int,
    correct_option_id: int,
    winner: str = "aoi",
    loser: str = "ren",
) -> GameRecord:
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
            StandingRow(pid_a, winner, 145, 1, 2000),
            StandingRow(pid_b, loser, 0, 2, 20000),
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
        mode = modes[0]

        with (
            client.websocket_connect(f"/ws/rooms/{code}") as ws1,
            client.websocket_connect(f"/ws/rooms/{code}") as ws2,
        ):

            def pump(ws, type_):
                for _ in range(80):
                    frame = json.loads(ws.receive_text())
                    if frame["type"] == type_:
                        return frame
                raise AssertionError(f"no {type_} frame")

            # Wait for each snapshot before starting. Connecting only enqueues
            # a join on the room's inbox, and the handler now takes a database
            # round trip on the way (the name-ownership check), so firing
            # start_game straight after the joins can beat the second player
            # into the room and leave them spectating round one.
            for ws, name in ((ws1, "aoi"), (ws2, "ren")):
                ws.send_text(json.dumps({"type": "join", "name": name}))
                pump(ws, "snapshot")
            ws1.send_text(json.dumps({"type": "start_game", "mode_id": mode["id"]}))

            # Real questions do not name their own answer — that is R-12
            # working. So both players pick blind and the reveal frame, which
            # is the only place the key is ever sent, says what it was worth.
            awarded: dict[str, int] = {}
            for _ in range(mode["question_count"]):
                q1 = pump(ws1, "question")
                pump(ws2, "question")
                assert "correct" not in json.dumps(q1["data"])
                for ws, option in (
                    (ws1, q1["data"]["options"][0]["id"]),
                    (ws2, q1["data"]["options"][1]["id"]),
                ):
                    ws.send_text(
                        json.dumps(
                            {
                                "type": "submit_answer",
                                "round_seq": q1["data"]["round_seq"],
                                "option_id": option,
                            }
                        )
                    )
                reveal = pump(ws1, "reveal")
                pump(ws2, "reveal")
                for result in reveal["data"]["results"]:
                    awarded[result["player_id"]] = (
                        awarded.get(result["player_id"], 0) + result["delta"]
                    )
            over = pump(ws1, "game_over")
            assert {s["player_id"]: s["score"] for s in over["data"]["standings"]} == awarded

    # The lifespan exit disposed the app's engine; inspect with a fresh one.
    import asyncio

    async def fetch_counts() -> tuple[int, int, int, int]:
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
                points = await conn.scalar(
                    select(func.coalesce(func.sum(GameAnswer.points), 0))
                    .select_from(GameAnswer)
                    .join(Game, Game.id == GameAnswer.game_id)
                    .where(Game.room_code == code)
                )
        finally:
            await engine.dispose()
        return games or 0, answers or 0, players or 0, points or 0

    games, answers, players, points = asyncio.run(fetch_counts())
    assert games == 1
    assert players == 2
    assert answers == mode["question_count"] * 2  # every round, both players
    # The persisted points are the same numbers the live game broadcast — the
    # crossing that the whole data-model boundary exists to make trustworthy.
    assert points == sum(awarded.values())


# --- currency ------------------------------------------------------------
#
# Wallets accumulate across games by design, so each test below uses names
# nothing else touches: a shared database plus a running total makes any
# assertion on an absolute balance a hidden dependency on test order.


async def test_finishing_a_game_credits_both_wallets(store: DbStore) -> None:
    deck, _ = await store.load_deck(1)
    await store.record_game(_record(deck[0].id, deck[0].correct_option_id, "wal-win", "wal-lose"))

    winner = await store.get_wallet("wal-win")
    loser = await store.get_wallet("wal-lose")
    assert winner is not None and loser is not None
    # 10 participation + 145//20 = 7 performance + 50 for first of two.
    assert winner.coins == 67
    assert winner.wins == 1 and winner.games_played == 1 and winner.best_score == 145
    # Second of two is last: participation only, no podium bonus.
    assert loser.coins == 10
    assert loser.wins == 0 and loser.games_played == 1


async def test_a_second_game_accumulates_rather_than_replaces(store: DbStore) -> None:
    deck, _ = await store.load_deck(1)
    for _ in range(2):
        await store.record_game(
            _record(deck[0].id, deck[0].correct_option_id, "wal-twice", "wal-other")
        )
    wallet = await store.get_wallet("wal-twice")
    assert wallet is not None
    assert wallet.coins == 134 and wallet.games_played == 2 and wallet.wins == 2
    # best_score is a high-water mark, not a running sum.
    assert wallet.best_score == 145 and wallet.lifetime_score == 290


async def test_the_wallet_is_reached_by_normalised_name(store: DbStore) -> None:
    deck, _ = await store.load_deck(1)
    await store.record_game(
        _record(deck[0].id, deck[0].correct_option_id, "Wal-Case", "wal-case-loser")
    )
    # Same wallet however it is typed — v1 has no accounts, so the normalised
    # name IS the identity.
    for spelling in ("wal-case", "WAL-CASE", "  Wal-Case  "):
        wallet = await store.get_wallet(spelling)
        assert wallet is not None and wallet.coins == 67
    # ...and it renders with the spelling its owner actually used.
    assert (await store.get_wallet("wal-case")).name == "Wal-Case"  # type: ignore[union-attr]


async def test_an_unplayed_name_has_no_wallet(store: DbStore) -> None:
    assert await store.get_wallet("nobody-has-ever-been-called-this") is None


async def test_rankings_order_by_coins_and_assign_positions(store: DbStore) -> None:
    deck, _ = await store.load_deck(1)
    await store.record_game(_record(deck[0].id, deck[0].correct_option_id, "wal-rank", "wal-tail"))

    rows = await store.list_rankings(limit=100)
    assert [r.rank for r in rows] == list(range(1, len(rows) + 1))
    coins = [r.wallet.coins for r in rows]
    assert coins == sorted(coins, reverse=True)
    assert any(r.wallet.name == "wal-rank" for r in rows)


async def test_rankings_respect_the_limit(store: DbStore) -> None:
    deck, _ = await store.load_deck(1)
    await store.record_game(
        _record(deck[0].id, deck[0].correct_option_id, "wal-lim-a", "wal-lim-b")
    )
    assert len(await store.list_rankings(limit=1)) == 1


# --- name ownership ------------------------------------------------------


async def test_claiming_a_free_name_succeeds_once(store: DbStore) -> None:
    assert await store.claim_account("acct-fresh", "hash-a") is True
    # The second attempt is somebody else arriving at a name already held.
    assert await store.claim_account("acct-fresh", "hash-b") is False
    account = await store.get_account("acct-fresh")
    assert account is not None and account.password_hash == "hash-a"


async def test_the_account_is_reached_by_normalised_name(store: DbStore) -> None:
    await store.claim_account("Acct-Case", "hash")
    for spelling in ("acct-case", "ACCT-CASE", "  Acct-Case  "):
        assert await store.get_account(spelling) is not None
    # ...and claiming a differently-spelled version of a held name fails.
    assert await store.claim_account("ACCT-CASE", "other") is False


async def test_an_unclaimed_name_has_no_account(store: DbStore) -> None:
    assert await store.get_account("acct-nobody-claimed-this") is None


async def test_a_wallet_can_exist_without_an_account(store: DbStore) -> None:
    """The two tables have different lifecycles on purpose: a name that has
    played games but was never claimed still has coins, and is still free for
    someone to claim."""
    deck, _ = await store.load_deck(1)
    await store.record_game(
        _record(deck[0].id, deck[0].correct_option_id, "acct-walletonly", "acct-other")
    )
    assert await store.get_wallet("acct-walletonly") is not None
    assert await store.get_account("acct-walletonly") is None
