"""Seed parsing and deck sampling.

Questions come from a seed file in this repo, not an external API. The seed
format is the contract the M4 database import will consume too, so validation
lives here and runs in tests against the real seed file.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from app.game.state import LoadedQuestion, Option

ALLOWED_KINDS = frozenset({"text", "image"})  # audio is a v1 non-goal


class SeedError(ValueError):
    pass


def load_questions(source: str | Path) -> tuple[LoadedQuestion, ...]:
    """Parse and validate a seed file. Raises SeedError with a pointed message
    rather than letting a bad question surface as a confusing runtime error."""
    text = Path(source).read_text(encoding="utf-8") if isinstance(source, Path) else source
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SeedError(f"seed is not valid JSON: {exc}") from exc

    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise SeedError("seed must contain a non-empty 'questions' list")

    questions: list[LoadedQuestion] = []
    seen_ids: set[int] = set()
    for i, raw in enumerate(raw_questions):
        where = f"questions[{i}]"
        qid = raw.get("id")
        if not isinstance(qid, int):
            raise SeedError(f"{where}: 'id' must be an integer")
        if qid in seen_ids:
            raise SeedError(f"{where}: duplicate question id {qid}")
        seen_ids.add(qid)

        kind = raw.get("kind")
        if kind not in ALLOWED_KINDS:
            raise SeedError(
                f"{where} (id={qid}): kind {kind!r} not allowed; "
                f"expected one of {sorted(ALLOWED_KINDS)} (audio is out of scope for v1)"
            )

        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SeedError(f"{where} (id={qid}): 'prompt' must be a non-empty string")

        difficulty = raw.get("difficulty")
        if difficulty not in (1, 2, 3):
            raise SeedError(f"{where} (id={qid}): 'difficulty' must be 1, 2 or 3")

        labels = raw.get("options")
        if not isinstance(labels, list) or not (2 <= len(labels) <= 6):
            raise SeedError(f"{where} (id={qid}): 'options' must list 2-6 labels")
        if any(not isinstance(lbl, str) or not lbl.strip() for lbl in labels):
            raise SeedError(f"{where} (id={qid}): every option label must be a non-empty string")

        correct = raw.get("correct")
        # Exactly one correct answer, by construction: 'correct' is a single
        # index. The DB carries the same guarantee as a partial unique index.
        if (
            not isinstance(correct, int)
            or isinstance(correct, bool)
            or not (0 <= correct < len(labels))
        ):
            raise SeedError(f"{where} (id={qid}): 'correct' must index into options")

        media_ref = raw.get("media")
        if media_ref is not None and not isinstance(media_ref, str):
            raise SeedError(f"{where} (id={qid}): 'media' must be a string key or null")

        questions.append(
            LoadedQuestion(
                id=qid,
                kind=kind,
                prompt=prompt,
                options=tuple(Option(id=j, label=lbl) for j, lbl in enumerate(labels)),
                correct_option_id=correct,
                difficulty=difficulty,
                media_ref=media_ref,
            )
        )
    return tuple(questions)


def sample_deck(
    questions: tuple[LoadedQuestion, ...], count: int, rng: random.Random
) -> tuple[LoadedQuestion, ...]:
    """One draw for the whole game, without replacement (R-15). The rng is
    injected so tests are deterministic."""
    if count < 1:
        raise SeedError("deck size must be at least 1")
    if len(questions) < count:
        raise SeedError(
            f"mode needs {count} questions but only {len(questions)} match; "
            "seed more questions or shrink the mode"
        )
    return tuple(rng.sample(questions, count))
