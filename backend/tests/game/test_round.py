"""The round loop and the hot-path guards (spec §04)."""

from __future__ import annotations

import pytest

from app.game.errors import GameError
from app.game.events import (
    AnswerAck,
    AnswerProgress,
    DeadlineReached,
    GameOver,
    IntroElapsed,
    Join,
    PhaseChanged,
    QuestionOpened,
    Rematch,
    Reveal,
    RevealElapsed,
    Scoreboard,
    StartGame,
    SubmitAnswer,
)
from app.game.state import Phase
from tests.game.conftest import CONFIG, Driver, events_of, make_deck, one_event


def submit(d: Driver, pid: str, option: int = 0) -> list:
    return d.do(SubmitAnswer(player_id=pid, round_seq=d.state.round_seq, option_id=option))


# --------------------------------------------------------------------------
# Guards (R-01, R-02, R-04, R-10)
# --------------------------------------------------------------------------


def test_double_submission_rejected(open_room: Driver) -> None:  # R-01
    submit(open_room, "p0")
    with pytest.raises(GameError) as exc:
        submit(open_room, "p0", option=1)
    assert exc.value.code == "already_answered"
    # First write won: the stored answer is still option 0.
    assert open_room.state.round is not None
    assert open_room.state.round.answers["p0"].option_id == 0


def test_stale_round_seq_rejected(open_room: Driver) -> None:  # R-02
    cmd = SubmitAnswer(player_id="p0", round_seq=open_room.state.round_seq - 1, option_id=0)
    with pytest.raises(GameError) as exc:
        open_room.do(cmd)
    assert exc.value.code == "stale_round"


def test_answer_in_grace_window_accepted_and_clamped(open_room: Driver) -> None:  # §03
    rnd = open_room.state.round
    assert rnd is not None
    open_room.now = rnd.deadline + 0.2  # inside the 0.35s grace
    events = submit(open_room, "p0")
    one_event(events, AnswerAck)
    # Clamped to the deadline: full 20s elapsed, so the speed bonus is zero.
    assert rnd.answers["p0"].elapsed_ms == 20000


def test_answer_after_grace_rejected(open_room: Driver) -> None:  # R-04
    rnd = open_room.state.round
    assert rnd is not None
    open_room.now = rnd.deadline + 0.36
    with pytest.raises(GameError) as exc:
        submit(open_room, "p0")
    assert exc.value.code == "too_late"


def test_submit_outside_question_phase_rejected(driver: Driver) -> None:
    driver.join("p0")
    driver.start(deck_n=2)
    assert driver.state.phase is Phase.INTRO
    with pytest.raises(GameError) as exc:
        submit(driver, "p0")
    assert exc.value.code == "round_closed"


def test_invalid_option_rejected(open_room: Driver) -> None:
    with pytest.raises(GameError) as exc:
        submit(open_room, "p0", option=99)
    assert exc.value.code == "invalid_option"


def test_midgame_joiner_spectates_until_next_round(open_room: Driver) -> None:  # R-10
    open_room.do(Join(player_id="late", name="late"))
    with pytest.raises(GameError) as exc:
        submit(open_room, "late")
    assert exc.value.code == "spectating"

    # Finish this round; the next one they play.
    for pid in ("p0", "p1", "p2"):
        submit(open_room, pid)
    open_room.do(RevealElapsed(round_seq=open_room.state.round_seq))
    open_room.open_question()
    events = submit(open_room, "late")
    one_event(events, AnswerAck)


# --------------------------------------------------------------------------
# Timer semantics (R-03)
# --------------------------------------------------------------------------


def test_early_advance_when_all_answered(open_room: Driver) -> None:
    submit(open_room, "p0")
    submit(open_room, "p1")
    events = submit(open_room, "p2")
    phases = [e.phase for e in events_of(events, PhaseChanged)]
    assert phases == [Phase.LOCKED, Phase.REVEAL]
    assert open_room.state.phase is Phase.REVEAL


def test_stale_deadline_after_early_advance_is_noop(open_room: Driver) -> None:  # R-03
    for pid in ("p0", "p1", "p2"):
        submit(open_room, pid)
    seq_before = open_room.state.round_seq
    history_before = len(open_room.state.history)
    # The timer task's DeadlineReached lands after the round already closed.
    assert open_room.do(DeadlineReached(round_seq=seq_before)) == []
    assert len(open_room.state.history) == history_before  # not scored twice


def test_stale_intro_and_reveal_timers_are_noops(open_room: Driver) -> None:
    assert open_room.do(IntroElapsed(round_seq=open_room.state.round_seq)) == []
    assert open_room.do(RevealElapsed(round_seq=open_room.state.round_seq)) == []
    assert open_room.do(DeadlineReached(round_seq=open_room.state.round_seq - 1)) == []


def test_deadline_scores_missing_answers_as_zero(open_room: Driver) -> None:
    submit(open_room, "p0")
    rnd = open_room.state.round
    assert rnd is not None
    open_room.now = rnd.deadline + CONFIG.grace_seconds
    events = open_room.do(DeadlineReached(round_seq=open_room.state.round_seq))
    reveal = one_event(events, Reveal)
    by_pid = {r.player_id: r for r in reveal.results}
    assert by_pid["p1"].option_id is None and by_pid["p1"].delta == 0
    assert by_pid["p2"].option_id is None and by_pid["p2"].streak == 0
    # Timeouts land the full limit on the elapsed tiebreak (§07).
    assert open_room.state.players["p1"].total_elapsed_ms == 20000


# --------------------------------------------------------------------------
# Full game flow
# --------------------------------------------------------------------------


def test_full_game_to_game_over_and_rematch(driver: Driver) -> None:
    driver.join("p0", "p1")
    driver.start(deck_n=2)

    for _round in range(2):
        driver.open_question()
        driver.advance(2.0)  # answer 2s in: speed = round(50 * 0.9) = 45
        submit(driver, "p0", option=0)  # correct
        events = submit(driver, "p1", option=1)  # wrong — completes the round
        reveal = one_event(events, Reveal)
        by_pid = {r.player_id: r for r in reveal.results}
        assert by_pid["p0"].correct and by_pid["p0"].delta == 145
        assert not by_pid["p1"].correct and by_pid["p1"].delta == 0
        one_event(events, Scoreboard)
        driver.do(RevealElapsed(round_seq=driver.state.round_seq))

    assert driver.state.phase is Phase.GAME_OVER
    standings = driver.state.players
    assert standings["p0"].score == 290 and standings["p0"].streak == 2
    assert standings["p1"].score == 0

    events = driver.do(Rematch(player_id="p0"))
    one_event(events, PhaseChanged)
    assert driver.state.phase is Phase.LOBBY
    assert driver.state.players["p0"].score == 0
    assert driver.state.history == []


def test_game_over_emits_ranked_standings(driver: Driver) -> None:
    driver.join("p0", "p1")
    driver.start(deck_n=1)
    driver.open_question()
    driver.advance(1.0)
    submit(driver, "p1", option=0)  # correct, fast
    driver.advance(5.0)
    submit(driver, "p0", option=1)  # wrong
    over_events = driver.do(RevealElapsed(round_seq=driver.state.round_seq))
    over = one_event(over_events, GameOver)
    assert [row.player_id for row in over.standings] == ["p1", "p0"]
    assert over.standings[0].rank == 1


def test_round_seq_increments_per_round_and_survives_rematch(driver: Driver) -> None:
    driver.join("p0")
    driver.start(deck_n=2)
    assert driver.state.round_seq == 1
    driver.open_question()
    submit(driver, "p0")
    driver.do(RevealElapsed(round_seq=1))
    assert driver.state.round_seq == 2
    driver.open_question()
    submit(driver, "p0")
    driver.do(RevealElapsed(round_seq=2))
    driver.do(Rematch(player_id="p0"))
    # NOT reset: a stale timer from game one can never fence-pass into game two.
    assert driver.state.round_seq == 2
    driver.start(deck_n=1)
    assert driver.state.round_seq == 3


# --------------------------------------------------------------------------
# Lobby edges
# --------------------------------------------------------------------------


def test_start_requires_host(driver: Driver) -> None:
    driver.join("p0", "p1")
    with pytest.raises(GameError) as exc:
        driver.do(StartGame(player_id="p1", deck=make_deck(2), config=CONFIG))
    assert exc.value.code == "not_host"


def test_start_is_idempotent_outside_lobby(driver: Driver) -> None:
    driver.join("p0")
    driver.start(deck_n=2)
    round_seq = driver.state.round_seq
    # A retried/duplicated start while in-game is silently ignored (§05).
    assert driver.do(StartGame(player_id="p0", deck=make_deck(2), config=CONFIG)) == []
    assert driver.state.round_seq == round_seq


def test_start_with_empty_deck_rejected(driver: Driver) -> None:
    driver.join("p0")
    with pytest.raises(GameError) as exc:
        driver.do(StartGame(player_id="p0", deck=(), config=CONFIG))
    assert exc.value.code == "empty_deck"


def test_question_event_carries_progress_totals(open_room: Driver) -> None:
    events = submit(open_room, "p0")
    progress = one_event(events, AnswerProgress)
    assert (progress.answered, progress.total) == (1, 3)


def test_question_opened_event_shape(driver: Driver) -> None:
    driver.join("p0")
    driver.start(deck_n=3)
    events = driver.open_question()
    q = one_event(events, QuestionOpened)
    assert q.question_count == 3
    assert q.deadline == pytest.approx(q.opened_at + CONFIG.seconds_per_question)
    assert len(q.options) == 4
