"""Reconnection over real sockets (spec §08): the session frame, the resume
snapshot, socket takeover, and token failure modes. The reconnect grace is
shrunk so the reaped-seat path runs in real time.

Every websocket session is owned by an ExitStack: TestClient's portal thread
joins each session's handler task at teardown, so a dangling session hangs
the whole test on exit.
"""

from __future__ import annotations

import json
import time
from contextlib import ExitStack
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.game.state import GameConfig
from app.main import create_app

FAST = GameConfig(
    question_count=2,
    seconds_per_question=0.4,
    intro_seconds=0.05,
    reveal_seconds=0.05,
    grace_seconds=0.1,
)
GRACE_S = 0.3


@pytest.fixture
def client() -> Any:
    app = create_app(game_config=FAST, reconnect_grace_s=GRACE_S)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def stack() -> Any:
    with ExitStack() as s:
        yield s


def make_room(client: TestClient) -> str:
    return str(client.post("/api/rooms").json()["code"])


def recv_until(ws: WebSocketTestSession, type_: str, limit: int = 60) -> dict[str, Any]:
    for _ in range(limit):
        frame = json.loads(ws.receive_text())
        if frame["type"] == type_:
            return frame
    raise AssertionError(f"no {type_!r} frame within {limit} frames")


def send(ws: WebSocketTestSession, **payload: Any) -> None:
    ws.send_text(json.dumps(payload))


def connect(stack: ExitStack, client: TestClient, code: str) -> WebSocketTestSession:
    return stack.enter_context(client.websocket_connect(f"/ws/rooms/{code}"))


def join(
    stack: ExitStack, client: TestClient, code: str, name: str
) -> tuple[WebSocketTestSession, str, str]:
    """Connect, join fresh, return (ws, player_id, resume_token)."""
    ws = connect(stack, client, code)
    send(ws, type="join", name=name)
    session = recv_until(ws, "session")
    recv_until(ws, "snapshot")
    return ws, session["data"]["player_id"], session["data"]["resume_token"]


def test_fresh_join_issues_a_session(client: TestClient, stack: ExitStack) -> None:
    code = make_room(client)
    _, player_id, token = join(stack, client, code, "aoi")
    assert token.startswith(f"{player_id}.")
    assert len(token.split(".")[-1]) == 64  # a real signature, not decoration


def test_resume_restores_seat_score_and_view(client: TestClient, stack: ExitStack) -> None:
    code = make_room(client)
    ws1, pid1, token1 = join(stack, client, code, "aoi")
    ws2, _, _ = join(stack, client, code, "ren")

    # Play the first round so there is a score to keep.
    send(ws1, type="start_game", mode_id=1)
    q = recv_until(ws1, "question")
    letter = q["data"]["prompt"].rstrip(".").split()[-1]
    correct = next(o["id"] for o in q["data"]["options"] if o["label"].endswith(letter))
    send(ws1, type="submit_answer", round_seq=q["data"]["round_seq"], option_id=correct)
    send(ws2, type="submit_answer", round_seq=q["data"]["round_seq"], option_id=correct)
    recv_until(ws1, "reveal")

    ws1.close()  # the laptop lid, mid-game
    left = recv_until(ws2, "player_left")
    assert left["data"]["player_id"] == pid1 and left["data"]["reason"] == "dropped"

    ws1b = connect(stack, client, code)
    send(ws1b, type="join", name="aoi", token=token1)
    snap = recv_until(ws1b, "snapshot")
    assert snap["data"]["you"] == pid1  # same seat, not a new player
    me = next(p for p in snap["data"]["players"] if p["id"] == pid1)
    assert me["score"] > 0  # the round played before the drop still counts
    assert me["conn"] == "LIVE"
    back = recv_until(ws2, "player_reconnected")
    assert back["data"]["player_id"] == pid1


def test_bad_token_is_refused_before_touching_the_room(
    client: TestClient, stack: ExitStack
) -> None:
    code = make_room(client)
    ws = connect(stack, client, code)
    send(ws, type="join", name="mallory", token="somebody-else." + "0" * 64)
    frame = json.loads(ws.receive_text())
    assert frame["type"] == "error" and frame["data"]["code"] == "bad_token"
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_text()
    assert exc.value.code == 4401


def test_resume_after_reap_fails_closed_and_seat_is_free(
    client: TestClient, stack: ExitStack
) -> None:
    code = make_room(client)
    ws1, pid1, token1 = join(stack, client, code, "aoi")
    ws2, _, _ = join(stack, client, code, "ren")
    ws1.close()
    recv_until(ws2, "player_left")  # dropped

    time.sleep(GRACE_S + 0.3)  # the real reaper fires on the real clock
    reaped = recv_until(ws2, "player_left")
    assert reaped["data"]["reason"] == "timeout"

    ws1b = connect(stack, client, code)
    send(ws1b, type="join", name="aoi", token=token1)
    with pytest.raises(WebSocketDisconnect) as exc:
        while True:
            ws1b.receive_text()
    assert exc.value.reason == "seat_expired"

    # The token is dead but the person is not: a fresh join gets a new seat.
    _, pid_new, _ = join(stack, client, code, "aoi")
    assert pid_new != pid1


def test_takeover_replaces_the_zombie_socket(client: TestClient, stack: ExitStack) -> None:
    code = make_room(client)
    ws1, pid1, token1 = join(stack, client, code, "aoi")

    ws1b = connect(stack, client, code)
    send(ws1b, type="join", name="aoi", token=token1)
    snap = recv_until(ws1b, "snapshot")
    assert snap["data"]["you"] == pid1
    roster = snap["data"]["players"]
    assert len(roster) == 1 and roster[0]["conn"] == "LIVE"  # never dropped

    # The old socket was closed by the server, and its teardown must NOT
    # have marked the (resumed) player as dropped.
    with pytest.raises(WebSocketDisconnect):
        while True:
            ws1.receive_text()
    send(ws1b, type="set_ready", ready=True)
    ready = recv_until(ws1b, "player_ready")  # room still answers the new socket
    assert ready["data"]["ready"] is True
