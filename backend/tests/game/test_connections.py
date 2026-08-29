"""Drops, returns, reaps and host migration (spec §08, R-14 aside — the HMAC
token itself is the connection layer's job in M5)."""

from __future__ import annotations

import pytest

from app.game.errors import GameError
from app.game.events import (
    Disconnect,
    HostChanged,
    Join,
    Kick,
    Leave,
    PhaseChanged,
    PlayerLeft,
    PlayerReconnected,
    ReapPlayer,
    ReconnectPlayer,
    Reveal,
    Snapshot,
    SubmitAnswer,
)
from app.game.state import MAX_PLAYERS, ConnState, Phase
from tests.game.conftest import Driver, events_of, one_event


def submit(d: Driver, pid: str, option: int = 0) -> list:
    return d.do(SubmitAnswer(player_id=pid, round_seq=d.state.round_seq, option_id=option))


# --------------------------------------------------------------------------
# Drop / reconnect
# --------------------------------------------------------------------------


def test_disconnect_holds_seat_and_score(open_room: Driver) -> None:
    submit(open_room, "p1")
    score_snapshot = open_room.state.players["p1"].score
    events = open_room.do(Disconnect(player_id="p1"))
    left = one_event(events, PlayerLeft)
    assert left.reason == "dropped"
    p1 = open_room.state.players["p1"]
    assert p1.conn is ConnState.DROPPED
    assert p1.score == score_snapshot
    assert p1.dropped_at == open_room.now


def test_reconnect_restores_player_with_snapshot(open_room: Driver) -> None:  # R-11
    submit(open_room, "p1", option=2)
    open_room.do(Disconnect(player_id="p1"))
    events = open_room.do(ReconnectPlayer(player_id="p1"))
    one_event(events, PlayerReconnected)
    snap = one_event(events, Snapshot)
    assert snap.to == "p1"
    question = snap.view["question"]
    assert isinstance(question, dict)
    # The snapshot tells the returning client it already answered this round.
    assert question["your_answer"] == 2
    assert open_room.state.players["p1"].conn is ConnState.LIVE
    assert open_room.state.players["p1"].dropped_at is None


def test_reconnect_error_codes(open_room: Driver) -> None:
    with pytest.raises(GameError) as exc:
        open_room.do(ReconnectPlayer(player_id="ghost"))
    assert exc.value.code == "unknown_player"
    open_room.do(Leave(player_id="p1"))
    with pytest.raises(GameError) as exc:
        open_room.do(ReconnectPlayer(player_id="p1"))  # GONE
    assert exc.value.code == "seat_expired"


def test_reconnect_while_live_is_a_silent_takeover(open_room: Driver) -> None:
    # The zombie-socket case: resume lands while the old socket still looks
    # LIVE. No dropped state, no broadcast — just a fresh snapshot.
    events = open_room.do(ReconnectPlayer(player_id="p1"))
    snap = one_event(events, Snapshot)
    assert snap.to == "p1"
    assert events_of(events, PlayerReconnected) == []
    assert open_room.state.players["p1"].conn is ConnState.LIVE


def test_dropped_player_excluded_from_all_answered(open_room: Driver) -> None:  # §08
    open_room.do(Disconnect(player_id="p2"))
    submit(open_room, "p0")
    events = submit(open_room, "p1")  # last eligible answer → round completes
    one_event(events, Reveal)
    assert open_room.state.phase is Phase.REVEAL


def test_disconnect_of_last_holdout_completes_round(open_room: Driver) -> None:
    submit(open_room, "p0")
    submit(open_room, "p1")
    events = open_room.do(Disconnect(player_id="p2"))
    reveal = one_event(events, Reveal)
    # p2 is seated (DROPPED, not GONE) so they appear in results with no answer.
    by_pid = {r.player_id: r for r in reveal.results}
    assert by_pid["p2"].option_id is None


def test_answer_survives_dropping_after_submitting(open_room: Driver) -> None:
    submit(open_room, "p0", option=0)  # correct
    open_room.do(Disconnect(player_id="p0"))
    submit(open_room, "p1")
    events = submit(open_room, "p2")
    reveal = one_event(events, Reveal)
    by_pid = {r.player_id: r for r in reveal.results}
    # Answered while live: the answer stands and scores even though dropped.
    assert by_pid["p0"].correct and by_pid["p0"].delta > 0


# --------------------------------------------------------------------------
# The reaper and its fence
# --------------------------------------------------------------------------


def test_reap_moves_dropped_player_to_gone(open_room: Driver) -> None:
    open_room.do(Disconnect(player_id="p1"))
    dropped_at = open_room.now
    open_room.advance(60.0)
    events = open_room.do(ReapPlayer(player_id="p1", dropped_at=dropped_at))
    assert one_event(events, PlayerLeft).reason == "timeout"
    assert open_room.state.players["p1"].conn is ConnState.GONE


def test_stale_reap_after_reconnect_is_noop(open_room: Driver) -> None:  # fencing
    open_room.do(Disconnect(player_id="p1"))
    dropped_at = open_room.now
    open_room.advance(30.0)
    open_room.do(ReconnectPlayer(player_id="p1"))
    open_room.advance(30.0)
    assert open_room.do(ReapPlayer(player_id="p1", dropped_at=dropped_at)) == []
    assert open_room.state.players["p1"].conn is ConnState.LIVE


def test_stale_reap_after_reconnect_and_redrop_is_noop(open_room: Driver) -> None:
    open_room.do(Disconnect(player_id="p1"))
    first_drop = open_room.now
    open_room.advance(10.0)
    open_room.do(ReconnectPlayer(player_id="p1"))
    open_room.advance(10.0)
    open_room.do(Disconnect(player_id="p1"))  # a fresh drop, fresh dropped_at
    assert open_room.do(ReapPlayer(player_id="p1", dropped_at=first_drop)) == []
    assert open_room.state.players["p1"].conn is ConnState.DROPPED


# --------------------------------------------------------------------------
# Host migration (R-16)
# --------------------------------------------------------------------------


def test_host_survives_a_blip(open_room: Driver) -> None:
    events = open_room.do(Disconnect(player_id="p0"))
    assert events_of(events, HostChanged) == []  # migration waits for the grace
    assert open_room.state.players["p0"].is_host


def test_host_migrates_on_reap_to_lowest_live_seat(open_room: Driver) -> None:
    open_room.do(Disconnect(player_id="p0"))
    dropped_at = open_room.now
    open_room.advance(60.0)
    events = open_room.do(ReapPlayer(player_id="p0", dropped_at=dropped_at))
    assert one_event(events, HostChanged).player_id == "p1"
    assert open_room.state.players["p1"].is_host
    assert not open_room.state.players["p0"].is_host


def test_explicit_leave_migrates_host_immediately(open_room: Driver) -> None:
    events = open_room.do(Leave(player_id="p0"))
    assert one_event(events, HostChanged).player_id == "p1"
    assert open_room.state.players["p0"].conn is ConnState.GONE


def test_host_migration_skips_dropped_players(open_room: Driver) -> None:
    open_room.do(Disconnect(player_id="p1"))  # p1 (next seat) is dropped
    events = open_room.do(Leave(player_id="p0"))
    assert one_event(events, HostChanged).player_id == "p2"


# --------------------------------------------------------------------------
# Kick, capacity, join edges
# --------------------------------------------------------------------------


def test_kick(open_room: Driver) -> None:
    events = open_room.do(Kick(player_id="p0", target_id="p2"))
    assert one_event(events, PlayerLeft).reason == "kicked"
    assert open_room.state.players["p2"].conn is ConnState.GONE

    with pytest.raises(GameError) as exc:
        open_room.do(Kick(player_id="p1", target_id="p0"))
    assert exc.value.code == "not_host"
    with pytest.raises(GameError) as exc:
        open_room.do(Kick(player_id="p0", target_id="p0"))
    assert exc.value.code == "cannot_kick_self"


def test_room_capacity_counts_only_seated_players(driver: Driver) -> None:
    for i in range(MAX_PLAYERS):
        driver.do(Join(player_id=f"p{i}", name=f"p{i}"))
    with pytest.raises(GameError) as exc:
        driver.do(Join(player_id="extra", name="extra"))
    assert exc.value.code == "room_full"
    driver.do(Leave(player_id="p3"))  # GONE seats free capacity
    driver.do(Join(player_id="extra", name="extra"))


def test_duplicate_join_rejected(driver: Driver) -> None:
    driver.join("p0")
    with pytest.raises(GameError) as exc:
        driver.do(Join(player_id="p0", name="again"))
    assert exc.value.code == "already_joined"


def test_first_joiner_becomes_host_with_event(driver: Driver) -> None:
    events = driver.do(Join(player_id="p0", name="p0"))
    assert one_event(events, HostChanged).player_id == "p0"
    snap = one_event(events, Snapshot)
    assert snap.to == "p0"
    events = driver.do(Join(player_id="p1", name="p1"))
    assert events_of(events, HostChanged) == []


def test_disconnect_races_are_noops(open_room: Driver) -> None:
    open_room.do(Disconnect(player_id="p1"))
    assert open_room.do(Disconnect(player_id="p1")) == []  # double teardown
    assert open_room.do(Disconnect(player_id="ghost")) == []
    open_room.do(Leave(player_id="p1"))
    assert open_room.do(Leave(player_id="p1")) == []  # already gone


def test_phase_changed_events_on_rematch_only_from_game_over(open_room: Driver) -> None:
    from app.game.events import Rematch

    with pytest.raises(GameError) as exc:
        open_room.do(Rematch(player_id="p0"))
    assert exc.value.code == "not_game_over"
    assert events_of([], PhaseChanged) == []
