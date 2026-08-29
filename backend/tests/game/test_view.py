"""Snapshot redaction (R-12): the answer key exists nowhere a client can see
until the phase makes it public."""

from __future__ import annotations

import json

from app.game.events import Join, RevealElapsed, SubmitAnswer
from app.game.state import Phase
from app.game.view import public_view
from tests.game.conftest import Driver


def submit(d: Driver, pid: str, option: int = 0) -> None:
    d.do(SubmitAnswer(player_id=pid, round_seq=d.state.round_seq, option_id=option))


def test_open_question_view_never_contains_the_key(open_room: Driver) -> None:  # R-12
    for viewer in ("p0", "p1", "p2"):
        view = public_view(open_room.state, viewer)
        serialized = json.dumps(view)
        assert "correct" not in serialized
        assert view["reveal"] is None


def test_your_answer_is_private_to_the_viewer(open_room: Driver) -> None:
    submit(open_room, "p0", option=2)
    q_for_p0 = public_view(open_room.state, "p0")["question"]
    q_for_p1 = public_view(open_room.state, "p1")["question"]
    assert isinstance(q_for_p0, dict) and isinstance(q_for_p1, dict)
    assert q_for_p0["your_answer"] == 2
    assert q_for_p1["your_answer"] is None
    # Nothing in p1's view says who has answered what.
    assert "p0" not in json.dumps(q_for_p1)


def test_reveal_view_carries_the_key_once_public(open_room: Driver) -> None:
    for pid in ("p0", "p1", "p2"):
        submit(open_room, pid)
    assert open_room.state.phase is Phase.REVEAL
    view = public_view(open_room.state, "p0")
    reveal = view["reveal"]
    assert isinstance(reveal, dict)
    assert reveal["correct_option_id"] == 0
    assert reveal["entries"]["p1"]["points"] > 0


def test_lobby_view_before_any_game(driver: Driver) -> None:
    driver.join("p0", "p1")
    view = public_view(driver.state, "p1")
    assert view["phase"] == "LOBBY"
    assert view["question"] is None and view["reveal"] is None
    players = view["players"]
    assert isinstance(players, list) and len(players) == 2
    assert players[0]["is_host"] and not players[1]["is_host"]


def test_gone_players_are_absent_spectators_are_flagged(open_room: Driver) -> None:
    from app.game.events import Leave

    open_room.do(Leave(player_id="p2"))
    open_room.do(Join(player_id="late", name="late"))
    view = public_view(open_room.state, "p0")
    players = view["players"]
    assert isinstance(players, list)
    ids = {p["id"] for p in players}
    assert "p2" not in ids
    flags = {p["id"]: p["spectating"] for p in players}
    assert flags["late"] is True and flags["p0"] is False


def test_snapshot_view_is_json_serializable_in_every_phase(open_room: Driver) -> None:
    json.dumps(public_view(open_room.state, "p0"))  # QUESTION_OPEN
    for pid in ("p0", "p1", "p2"):
        submit(open_room, pid)
    json.dumps(public_view(open_room.state, "p0"))  # REVEAL
    open_room.do(RevealElapsed(round_seq=open_room.state.round_seq))
    json.dumps(public_view(open_room.state, "p0"))  # next INTRO
