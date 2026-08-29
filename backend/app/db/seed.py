"""Seed importer: seed/questions.json + seed/modes.json -> Postgres.

Run from backend/:  python -m uv run python -m app.db.seed [--replace]

Validation is deck.load_questions — the same code the file-backed store
runs — so a question that would break the game cannot be imported. Question
ids are inserted explicitly (the seed file owns them) and the sequence is
bumped past them.

Refuses to touch a database that already has questions unless --replace is
given; --replace truncates CONTENT tables only and fails if game history
references them (game_answer's FK is deliberately not cascade).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db.models import AnswerOption, GameMode, Question, QuestionTag, Series, Tag
from app.db.store import make_engine
from app.game.deck import load_questions

SEED_DIR = Path(__file__).parents[2] / "seed"
MIN_MARGIN = 2  # each mode needs question_count x2 matching questions (spec §06)


class SeedImportError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"seed import failed: {message}")


async def import_seed(engine: AsyncEngine, replace: bool = False) -> dict[str, int]:
    raw_questions = json.loads((SEED_DIR / "questions.json").read_text(encoding="utf-8"))
    raw_modes = json.loads((SEED_DIR / "modes.json").read_text(encoding="utf-8"))
    # Same validation as the file-backed game path; raises SeedError on junk.
    load_questions(SEED_DIR / "questions.json")

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session, session.begin():
        existing = await session.scalar(select(func.count(Question.id)))
        if existing and not replace:
            raise SeedImportError(
                f"database already holds {existing} questions; rerun with --replace "
                "to truncate content tables (game history blocks it by FK, on purpose)"
            )
        if existing:
            await session.execute(
                text(
                    "TRUNCATE question_tag, answer_option, question, tag, series, game_mode "
                    "RESTART IDENTITY"
                )
            )

        tags_by_slug: dict[str, Tag] = {}
        series_by_slug: dict[str, Series] = {}
        counts = {"questions": 0, "options": 0, "tags": 0, "series": 0, "modes": 0}

        for raw in raw_questions["questions"]:
            series_id: int | None = None
            if raw.get("series"):
                series = series_by_slug.get(raw["series"])
                if series is None:
                    series = Series(slug=raw["series"], title=raw["series"])
                    session.add(series)
                    await session.flush()
                    series_by_slug[raw["series"]] = series
                    counts["series"] += 1
                series_id = series.id

            question = Question(
                id=raw["id"],
                series_id=series_id,
                kind=raw["kind"],
                prompt=raw["prompt"],
                media_ref=raw.get("media"),
                difficulty=raw["difficulty"],
            )
            session.add(question)
            counts["questions"] += 1
            for ordinal, label in enumerate(raw["options"]):
                session.add(
                    AnswerOption(
                        question_id=raw["id"],
                        ordinal=ordinal,
                        label=label,
                        is_correct=ordinal == raw["correct"],
                    )
                )
                counts["options"] += 1
            for slug in raw.get("tags", []):
                tag = tags_by_slug.get(slug)
                if tag is None:
                    tag = Tag(slug=slug, kind="genre")
                    session.add(tag)
                    await session.flush()
                    tags_by_slug[slug] = tag
                    counts["tags"] += 1
                session.add(QuestionTag(question_id=raw["id"], tag_id=tag.id))

        # The seed owns question ids; move the sequence past them.
        await session.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('question', 'id'), "
                "(SELECT max(id) FROM question))"
            )
        )

        for raw in raw_modes["modes"]:
            required: list[int] = []
            for slug in raw.get("required_tags", []):
                if slug not in tags_by_slug:
                    raise SeedImportError(f"mode {raw['slug']!r} requires unknown tag {slug!r}")
                required.append(tags_by_slug[slug].id)
            series_id = None
            if raw.get("series"):
                if raw["series"] not in series_by_slug:
                    raise SeedImportError(
                        f"mode {raw['slug']!r} names unknown series {raw['series']!r}"
                    )
                series_id = series_by_slug[raw["series"]].id
            session.add(
                GameMode(
                    slug=raw["slug"],
                    name=raw["name"],
                    blurb=raw.get("blurb"),
                    series_id=series_id,
                    required_tags=required,
                    difficulty_min=raw.get("difficulty_min", 1),
                    difficulty_max=raw.get("difficulty_max", 3),
                    question_count=raw["question_count"],
                    seconds_per_q=raw.get("seconds_per_q", 20),
                )
            )
            counts["modes"] += 1

    await check_mode_margins(engine)
    return counts


async def check_mode_margins(engine: AsyncEngine) -> None:
    """The seed-time check (spec §06): a mode that quietly runs short of
    questions is the most likely content bug, so it fails the import."""
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        modes = (await session.scalars(select(GameMode))).all()
        for mode in modes:
            stmt = select(func.count(Question.id)).where(
                Question.is_active,
                Question.difficulty.between(mode.difficulty_min, mode.difficulty_max),
            )
            if mode.series_id is not None:
                stmt = stmt.where(Question.series_id == mode.series_id)
            if mode.required_tags:
                sub = (
                    select(QuestionTag.question_id)
                    .where(QuestionTag.tag_id.in_(mode.required_tags))
                    .group_by(QuestionTag.question_id)
                    .having(func.count(QuestionTag.tag_id) == len(mode.required_tags))
                )
                stmt = stmt.where(Question.id.in_(sub))
            matching = await session.scalar(stmt) or 0
            if matching < mode.question_count * MIN_MARGIN:
                raise SeedImportError(
                    f"mode {mode.slug!r} wants {mode.question_count} questions but only "
                    f"{matching} match — needs at least {mode.question_count * MIN_MARGIN} "
                    "(seed more questions or shrink the mode)"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://trivia:trivia@localhost:5433/trivia")

    async def run() -> None:
        engine = make_engine(url)
        try:
            counts = await import_seed(engine, replace=args.replace)
        finally:
            await engine.dispose()
        print(f"seeded: {counts}", file=sys.stderr)

    asyncio.run(run())


if __name__ == "__main__":
    main()
