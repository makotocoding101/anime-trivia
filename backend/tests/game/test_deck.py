"""Seed validation and deck sampling (R-15). Runs against the real seed file,
so a bad question fails the build instead of a game."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from app.game.deck import SeedError, load_questions, sample_deck

SEED_PATH = Path(__file__).parents[2] / "seed" / "questions.json"


def valid_seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def mutate(mutation) -> str:
    payload = valid_seed()
    mutation(payload)
    return json.dumps(payload)


def test_real_seed_file_is_valid() -> None:
    questions = load_questions(SEED_PATH)
    assert len(questions) == 10
    for q in questions:
        assert q.kind == "text"
        assert 1 <= q.difficulty <= 3
        assert any(o.id == q.correct_option_id for o in q.options)


def test_real_seed_supports_the_default_mode_size() -> None:
    # The seed-time check (spec §06): a mode that quietly runs short of
    # questions is the most likely v1 content bug. The placeholder bank must
    # at least fill one default game.
    questions = load_questions(SEED_PATH)
    sample_deck(questions, 10, random.Random(0))


def test_sampling_is_without_replacement_and_deterministic() -> None:  # R-15
    questions = load_questions(SEED_PATH)
    deck_a = sample_deck(questions, 6, random.Random(42))
    deck_b = sample_deck(questions, 6, random.Random(42))
    assert deck_a == deck_b  # injected rng → reproducible
    assert len({q.id for q in deck_a}) == 6  # no duplicates in one game


def test_sampling_more_than_available_raises() -> None:
    questions = load_questions(SEED_PATH)
    with pytest.raises(SeedError, match="only 10 match"):
        sample_deck(questions, 11, random.Random(0))


def test_audio_kind_rejected_as_out_of_scope() -> None:
    text = mutate(lambda p: p["questions"][0].update(kind="audio"))
    with pytest.raises(SeedError, match="out of scope"):
        load_questions(text)


def test_correct_must_index_into_options() -> None:
    with pytest.raises(SeedError, match="must index into options"):
        load_questions(mutate(lambda p: p["questions"][2].update(correct=4)))
    with pytest.raises(SeedError, match="must index into options"):
        load_questions(mutate(lambda p: p["questions"][2].update(correct=True)))


def test_duplicate_ids_rejected() -> None:
    text = mutate(lambda p: p["questions"][1].update(id=p["questions"][0]["id"]))
    with pytest.raises(SeedError, match="duplicate question id"):
        load_questions(text)


def test_option_count_bounds() -> None:
    with pytest.raises(SeedError, match="2-6 labels"):
        load_questions(mutate(lambda p: p["questions"][0].update(options=["only one"])))


def test_difficulty_bounds() -> None:
    with pytest.raises(SeedError, match="1, 2 or 3"):
        load_questions(mutate(lambda p: p["questions"][0].update(difficulty=4)))


def test_blank_prompt_rejected() -> None:
    with pytest.raises(SeedError, match="non-empty string"):
        load_questions(mutate(lambda p: p["questions"][0].update(prompt="   ")))


def test_malformed_json_gets_a_pointed_error() -> None:
    with pytest.raises(SeedError, match="not valid JSON"):
        load_questions("{nope")
