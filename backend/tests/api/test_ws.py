"""Integration over real sockets (spec §11, layer 4). Real time too — the
game config is sped up so a full game fits inside a second."""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from app.game.state import GameConfig
from app.main import create_app

FAST = GameConfig(
    question_count=2,
    seconds_per_question=0.4,
    intro_seconds=0.05,
    reveal_seconds=0.05,
    grace_seconds=0.1,
)


@pytest.fixture
def client() -> Any:
    app = create_app(game_config=FAST)
    with TestClient(app) as c:
        yield c


def make_room(client: TestClient) -> str:
    response = client.post("/api/rooms")
    assert response.status_code == 201
    return str(response.json()["code"])


def recv_until(ws: WebSocketTestSession, type_: str, limit: int = 50) -> dict[str, Any]:
    """Read frames until one of `type_` arrives; TestClient recv blocks with
    a timeout under the hood, so a missing frame fails the test, not hangs it."""
    for _ in range(limit):
        frame = json.loads(ws.receive_text())
        if frame["type"] == type_:
            return frame
    raise AssertionError(f"no {type_!r} frame within {limit} frames")


def send(ws: WebSocketTestSession, **payload: Any) -> None:
    ws.send_text(json.dumps(payload))


def test_health(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True


def test_modes_lists_the_seed_stores_synthetic_mode(client: TestClient) -> None:
    modes = client.get("/api/modes").json()
    assert len(modes) == 1
    assert modes[0]["id"] == 1
    assert modes[0]["question_count"] == 2  # mirrors the FAST base config


def test_start_with_unknown_mode_bounces(client: TestClient) -> None:
    code = make_room(client)
    with client.websocket_connect(f"/ws/rooms/{code}") as ws:
        send(ws, type="join", name="aoi")
        recv_until(ws, "snapshot")
        send(ws, type="start_game", mode_id=99)
        error = recv_until(ws, "error")
        assert error["data"]["code"] == "mode_not_found"


def test_join_yields_snapshot_and_peers_see_it(client: TestClient) -> None:
    code = make_room(client)
    with client.websocket_connect(f"/ws/rooms/{code}") as ws1:
        send(ws1, type="join", name="aoi")
        snap = recv_until(ws1, "snapshot")
        assert snap["data"]["phase"] == "LOBBY"
        assert snap["data"]["players"][0]["name"] == "aoi"

        with client.websocket_connect(f"/ws/rooms/{code}") as ws2:
            send(ws2, type="join", name="ren")
            recv_until(ws2, "snapshot")
            joined = recv_until(ws1, "player_joined")
            assert joined["data"]["name"] == "ren"


def test_unknown_room_is_refused(client: TestClient) -> None:
    with client.websocket_connect("/ws/rooms/XXXX") as ws:
        frame = json.loads(ws.receive_text())
        assert frame["type"] == "error"
        assert frame["data"]["code"] == "room_not_found"


def test_malformed_frame_bounces_but_connection_survives(client: TestClient) -> None:
    code = make_room(client)
    with client.websocket_connect(f"/ws/rooms/{code}") as ws:
        send(ws, type="join", name="aoi")
        recv_until(ws, "snapshot")
        ws.send_text("{not json")
        error = recv_until(ws, "error")
        assert error["data"]["code"] == "malformed_json"
        send(ws, type="set_ready", ready=True)  # still alive and processing
        ready = recv_until(ws, "player_ready")
        assert ready["data"]["ready"] is True


def test_ping_pong_offset_handshake(client: TestClient) -> None:
    code = make_room(client)
    with client.websocket_connect(f"/ws/rooms/{code}") as ws:
        send(ws, type="join", name="aoi")
        recv_until(ws, "snapshot")
        send(ws, type="ping", t0=123456789)
        pong = recv_until(ws, "pong")
        assert pong["data"]["t0"] == 123456789
        assert pong["data"]["ts"] > 0


def test_full_game_over_real_sockets(client: TestClient) -> None:
    """Two clients play both rounds to game over. Placeholder prompts name
    their own correct option, so we answer deliberately from the wire data."""
    code = make_room(client)
    with (
        client.websocket_connect(f"/ws/rooms/{code}") as ws1,
        client.websocket_connect(f"/ws/rooms/{code}") as ws2,
    ):
        send(ws1, type="join", name="aoi")
        recv_until(ws1, "snapshot")
        send(ws2, type="join", name="ren")
        recv_until(ws2, "snapshot")

        send(ws1, type="start_game", mode_id=1)
        for round_no in range(2):
            q1 = recv_until(ws1, "question")
            q2 = recv_until(ws2, "question")
            assert q1["data"]["question_id"] == q2["data"]["question_id"]
            assert "correct" not in json.dumps(q1["data"])  # R-12 on the wire

            # "pick option B." -> the option whose label ends with that letter.
            prompt: str = q1["data"]["prompt"]
            letter = prompt.rstrip(".").split()[-1]
            correct_id = next(o["id"] for o in q1["data"]["options"] if o["label"].endswith(letter))
            wrong_id = next(o["id"] for o in q1["data"]["options"] if o["id"] != correct_id)

            seq = q1["data"]["round_seq"]
            send(ws1, type="submit_answer", round_seq=seq, option_id=correct_id, cid=f"c{round_no}")
            ack = recv_until(ws1, "answer_ack")
            assert ack["data"]["cid"] == f"c{round_no}"
            send(ws2, type="submit_answer", round_seq=seq, option_id=wrong_id)

            reveal = recv_until(ws2, "reveal")
            assert reveal["data"]["correct_option_id"] == correct_id
            results = {r["player_id"]: r for r in reveal["data"]["results"]}
            assert sum(1 for r in results.values() if r["correct"]) == 1

        over = recv_until(ws1, "game_over")
        standings = over["data"]["standings"]
        assert standings[0]["name"] == "aoi"  # two correct answers beat zero
        assert standings[0]["score"] > standings[1]["score"] == 0


def test_disconnect_mid_round_greys_player_and_game_continues(client: TestClient) -> None:
    code = make_room(client)
    with client.websocket_connect(f"/ws/rooms/{code}") as ws1:
        send(ws1, type="join", name="aoi")
        recv_until(ws1, "snapshot")
        ws2 = client.websocket_connect(f"/ws/rooms/{code}").__enter__()
        send(ws2, type="join", name="ren")
        recv_until(ws2, "snapshot")

        send(ws1, type="start_game", mode_id=1)
        recv_until(ws1, "question")
        ws2.__exit__(None, None, None)  # ren's laptop lid closes mid-round

        left = recv_until(ws1, "player_left")
        assert left["data"]["reason"] == "dropped"
        # aoi alone is now the whole eligible set; answering ends the round.
        snap_q = recv_until(ws1, "answer_progress")
        assert snap_q["data"]["total"] == 1
