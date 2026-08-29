"""Scoring math (§07). Integers in, integers out."""

from __future__ import annotations

from app.game.scoring import rank, score_answer
from app.game.state import Player

LIMIT = 20000


def test_wrong_answer_scores_zero_regardless() -> None:
    assert (
        score_answer(correct=False, difficulty=3, elapsed_ms=0, limit_ms=LIMIT, streak_before=10)
        == 0
    )


def test_base_scales_with_difficulty() -> None:
    for difficulty, expected in ((1, 100), (2, 200), (3, 300)):
        assert (
            score_answer(
                correct=True,
                difficulty=difficulty,
                elapsed_ms=LIMIT,  # zero speed bonus isolates the base
                limit_ms=LIMIT,
                streak_before=0,
            )
            == expected
        )


def test_speed_bonus_boundaries() -> None:
    instant = score_answer(
        correct=True, difficulty=1, elapsed_ms=0, limit_ms=LIMIT, streak_before=0
    )
    at_limit = score_answer(
        correct=True, difficulty=1, elapsed_ms=LIMIT, limit_ms=LIMIT, streak_before=0
    )
    halfway = score_answer(
        correct=True, difficulty=1, elapsed_ms=LIMIT // 2, limit_ms=LIMIT, streak_before=0
    )
    assert instant == 150  # 100 + 50
    assert at_limit == 100  # clamped answers get no speed bonus
    assert halfway == 125


def test_speed_never_beats_knowledge() -> None:
    # A perfect-speed easy answer must lose to a zero-speed answer one tier up.
    fastest_easy = score_answer(
        correct=True, difficulty=1, elapsed_ms=0, limit_ms=LIMIT, streak_before=0
    )
    slowest_medium = score_answer(
        correct=True, difficulty=2, elapsed_ms=LIMIT, limit_ms=LIMIT, streak_before=0
    )
    assert fastest_easy < slowest_medium


def test_streak_bonus_reads_streak_before_the_answer() -> None:
    # Spec §07 literal: the bonus starts paying on the answer taken while
    # already holding a streak of 3 — the fourth consecutive correct.
    assert (
        score_answer(correct=True, difficulty=1, elapsed_ms=LIMIT, limit_ms=LIMIT, streak_before=2)
        == 100
    )
    assert (
        score_answer(correct=True, difficulty=1, elapsed_ms=LIMIT, limit_ms=LIMIT, streak_before=3)
        == 125
    )


def test_elapsed_out_of_range_is_clamped_not_amplified() -> None:
    over = score_answer(
        correct=True, difficulty=1, elapsed_ms=LIMIT * 2, limit_ms=LIMIT, streak_before=0
    )
    negative = score_answer(
        correct=True, difficulty=1, elapsed_ms=-500, limit_ms=LIMIT, streak_before=0
    )
    assert over == 100
    assert negative == 150


def _player(pid: str, score: int, elapsed: int) -> Player:
    return Player(id=pid, name=pid, seat=0, score=score, total_elapsed_ms=elapsed)


def test_rank_tiebreaks_deterministically() -> None:
    players = [
        _player("c", 100, 9000),
        _player("a", 100, 9000),  # full tie with c → player_id decides
        _player("b", 100, 4000),  # same score, faster → above both
        _player("d", 250, 30000),  # higher score wins regardless of elapsed
    ]
    assert [p.id for p in rank(players)] == ["d", "b", "a", "c"]


def test_rank_is_stable_across_recomputation() -> None:
    players = [_player(f"p{i}", 100, 5000) for i in range(6)]
    first = [p.id for p in rank(players)]
    second = [p.id for p in rank(list(reversed(players)))]
    assert first == second
