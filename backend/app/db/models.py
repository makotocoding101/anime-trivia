"""Durable side of the data model boundary (spec §06).

Postgres holds question content and post-game history; memory holds the live
game; nothing crosses during a round. Every table here is read once at game
start or written once at game end.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Series(Base):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)  # 'jujutsu-kaisen'
    title: Mapped[str] = mapped_column(Text)
    title_en: Mapped[str | None] = mapped_column(Text)


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)  # 'shonen' | 'openings'
    kind: Mapped[str] = mapped_column(Text)  # 'genre' | 'format' | 'season' | 'era'


class Question(Base):
    __tablename__ = "question"
    __table_args__ = (CheckConstraint("difficulty between 1 and 3", name="difficulty_range"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id"))  # null = cross-series
    kind: Mapped[str] = mapped_column(Text)  # 'text' | 'image' — audio is out of scope
    prompt: Mapped[str] = mapped_column(Text)
    media_ref: Mapped[str | None] = mapped_column(Text)  # opaque key, never descriptive (R-12)
    difficulty: Mapped[int] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    options: Mapped[list[AnswerOption]] = relationship(
        back_populates="question", order_by="AnswerOption.ordinal", cascade="all, delete-orphan"
    )


class AnswerOption(Base):
    __tablename__ = "answer_option"
    __table_args__ = (
        Index("uq_option_ordinal", "question_id", "ordinal", unique=True),
        # A question with two right answers is a data bug, not a runtime bug:
        # the partial unique index makes it unrepresentable (spec §06).
        Index(
            "one_correct_per_question",
            "question_id",
            unique=True,
            postgresql_where=text("is_correct"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("question.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(SmallInteger)
    label: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)

    question: Mapped[Question] = relationship(back_populates="options")


class QuestionTag(Base):
    __tablename__ = "question_tag"

    question_id: Mapped[int] = mapped_column(
        ForeignKey("question.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True)


class GameMode(Base):
    """Modes are rows, not code (spec §06): a stored query specification.
    Adding 'Jujutsu Kaisen only' or 'Shonen — hard' is an INSERT, not a
    deploy."""

    __tablename__ = "game_mode"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    blurb: Mapped[str | None] = mapped_column(Text)
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id"))
    required_tags: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    difficulty_min: Mapped[int] = mapped_column(SmallInteger, default=1)
    difficulty_max: Mapped[int] = mapped_column(SmallInteger, default=3)
    question_count: Mapped[int] = mapped_column(SmallInteger, default=10)
    seconds_per_q: Mapped[int] = mapped_column(SmallInteger, default=20)


class Game(Base):
    __tablename__ = "game"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    room_code: Mapped[str] = mapped_column(Text)
    mode_id: Mapped[int] = mapped_column(ForeignKey("game_mode.id"))
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    question_count: Mapped[int] = mapped_column(SmallInteger)


class GamePlayer(Base):
    """One row per participant per game — names live here because v1 has no
    accounts, so a name exists only for the duration of a game."""

    __tablename__ = "game_player"

    game_id: Mapped[str] = mapped_column(
        ForeignKey("game.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    final_score: Mapped[int] = mapped_column(Integer)
    final_rank: Mapped[int] = mapped_column(SmallInteger)
    total_elapsed_ms: Mapped[int] = mapped_column(Integer)


class PlayerAccount(Base):
    """Name ownership.

    Separate from PlayerWallet even though both key on the same normalised
    name, because they have different lifecycles: a wallet appears the first
    time a game is recorded, an account the moment someone claims the name.
    A name can have either without the other — an unclaimed name that has
    played games, or a claimed name that has not played one yet.

    Claiming is what stops a player wearing somebody else's identity. It is
    not full accounts (no email, so no recovery): lose the passphrase and the
    name is gone. That is the honest cost of adding ownership without adding
    an identity provider.
    """

    __tablename__ = "player_account"

    name_key: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


class PlayerWallet(Base):
    """The currency ledger, and the only table here that is not write-once.

    v1 has no accounts, so this hangs on a normalised display name
    (app.game.economy.wallet_key) rather than a user id. That is the whole
    identity model: anyone who types an existing name plays as that wallet.
    It is stated rather than hidden because the alternative — accounts — is
    explicitly out of scope for v1.

    Totals are stored rather than aggregated from game_player on every read.
    Rankings are the hottest read in the app and would otherwise be a
    GROUP BY over all history; the write side is already inside the single
    end-of-game transaction, so keeping them current costs nothing extra.
    """

    __tablename__ = "player_wallet"
    __table_args__ = (
        # The rankings query, and the only reason this index exists.
        Index("ix_wallet_coins", text("coins DESC")),
    )

    name_key: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    coins: Mapped[int] = mapped_column(BigInteger, default=0)
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    best_score: Mapped[int] = mapped_column(Integer, default=0)
    lifetime_score: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


class GameAnswer(Base):
    __tablename__ = "game_answer"

    # The composite primary key is R-01 expressed as a database constraint:
    # even a bypassed in-memory guard cannot record a player answering the
    # same round twice.
    game_id: Mapped[str] = mapped_column(
        ForeignKey("game.id", ondelete="CASCADE"), primary_key=True
    )
    round_index: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    player_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("question.id"))
    option_id: Mapped[int | None] = mapped_column(ForeignKey("answer_option.id"))  # null = none
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    is_correct: Mapped[bool] = mapped_column(Boolean)
    points: Mapped[int] = mapped_column(Integer)
