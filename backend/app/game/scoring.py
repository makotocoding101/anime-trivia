"""Scoring (§07). Integers throughout — floats in a leaderboard eventually
produce two players tied at 1049.9999999 and an unstable sort."""

from __future__ import annotations

from app.game.state import Player

BASE_PER_DIFFICULTY = 100
SPEED_MAX = 50
STREAK_BONUS = 25
STREAK_THRESHOLD = 3


def score_answer(
    *, correct: bool, difficulty: int, elapsed_ms: int, limit_ms: int, streak_before: int
) -> int:
    """Points for one answer. `elapsed_ms` comes from server receive time,
    already clamped to the deadline by the caller (R-13, §03).

    The streak bonus reads the streak BEFORE this answer (spec §07 literal):
    it starts paying on the answer after the third consecutive correct one.
    The speed component is capped at half the lowest base so a fast guess on
    an easy question never beats a considered correct answer on a hard one.
    """
    if not correct:
        return 0
    base = BASE_PER_DIFFICULTY * difficulty
    ratio = min(max(elapsed_ms, 0), limit_ms) / limit_ms
    speed = round(SPEED_MAX * (1 - ratio))
    streak = STREAK_BONUS if streak_before >= STREAK_THRESHOLD else 0
    return base + speed + streak


def rank(players: list[Player]) -> list[Player]:
    """Deterministic ordering: score desc, total elapsed asc, player_id.

    The last key is arbitrary but must exist — without it the leaderboard
    reorders itself between renders and looks broken (§07).
    """
    return sorted(players, key=lambda p: (-p.score, p.total_elapsed_ms, p.id))
