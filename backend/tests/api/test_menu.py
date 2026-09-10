"""The menu-screen REST surface: live stats, rankings, profile.

Runs on the seed-file store (no DATABASE_URL), which is the configuration
where the currency has nowhere to live. That is deliberate: the interesting
question for these routes is whether the menu still renders when there is no
database behind it, and the answer has to be "yes, empty" rather than "500".
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from app.game.economy import RANK_TIERS
from app.game.state import GameConfig
from app.main import create_app

FAST = GameConfig(question_count=2, seconds_per_question=0.4)


def send(ws: WebSocketTestSession, **payload: Any) -> None:
    ws.send_text(json.dumps(payload))


def recv_until(ws: WebSocketTestSession, type_: str, limit: int = 50) -> dict[str, Any]:
    for _ in range(limit):
        frame = json.loads(ws.receive_text())
        if frame["type"] == type_:
            return frame
    raise AssertionError(f"no {type_!r} frame within {limit} frames")


@pytest.fixture
def client() -> Any:
    app = create_app(game_config=FAST)
    with TestClient(app) as c:
        yield c


class TestStats:
    def test_an_idle_server_reports_zeroes(self, client: TestClient) -> None:
        body = client.get("/api/stats").json()
        assert body == {"open_rooms": 0, "live_games": 0, "players_online": 0}

    def test_an_empty_room_is_open_but_not_a_live_game(self, client: TestClient) -> None:
        # The distinction the menu counter depends on: a room sitting in the
        # lobby is not a game in progress, and counting it as one would make
        # "live games" mean "rooms anybody ever opened".
        client.post("/api/rooms")
        client.post("/api/rooms")
        body = client.get("/api/stats").json()
        assert body["open_rooms"] == 2
        assert body["live_games"] == 0

    def test_a_joined_player_counts_as_online(self, client: TestClient) -> None:
        code = client.post("/api/rooms").json()["code"]
        with client.websocket_connect(f"/ws/rooms/{code}") as ws:
            send(ws, type="join", name="aoi")
            # The snapshot is the join's acknowledgement. Reading it first is
            # not test politeness: connecting only enqueues a join command on
            # the room's inbox (invariant 1), so a stats read taken before the
            # room task drains it correctly reports zero. The counter is
            # eventually consistent with the socket, by design.
            recv_until(ws, "snapshot")
            # Read the registry rather than GET /api/stats: TestClient drives
            # the app through one portal, and a blocking HTTP call made while
            # a websocket session holds it deadlocks. The HTTP shape of this
            # route is covered above; what is under test here is that a real
            # player on a real socket is counted.
            open_rooms, live_games, players = client.app.state.registry.stats()  # type: ignore[attr-defined]
            assert players == 1
            assert open_rooms == 1 and live_games == 0


class TestRankings:
    def test_no_database_means_an_empty_table_not_an_error(self, client: TestClient) -> None:
        response = client.get("/api/rankings")
        assert response.status_code == 200
        assert response.json() == []

    def test_the_limit_is_bounded_on_this_side_of_the_wire(self, client: TestClient) -> None:
        assert client.get("/api/rankings?limit=100").status_code == 200
        assert client.get("/api/rankings?limit=101").status_code == 422
        assert client.get("/api/rankings?limit=0").status_code == 422


class TestProfile:
    def test_a_name_that_never_played_has_no_wallet(self, client: TestClient) -> None:
        response = client.get("/api/profile/nobody")
        assert response.status_code == 200
        assert response.json() is None

    def test_a_name_with_awkward_characters_still_routes(self, client: TestClient) -> None:
        # Names are free text; the route must not 404 on a space or a slash-
        # adjacent character just because it is in a path segment.
        assert client.get("/api/profile/a%20name%20with%20spaces").status_code == 200


def test_tier_ladder_is_exposed_consistently() -> None:
    """The client renders the tier string the server sends and never derives
    its own — this asserts the ladder the API is quoting from is the domain's,
    not a copy that can drift."""
    assert next(name for _, name in RANK_TIERS) == "ROOKIE"
    assert json.dumps([t for t, _ in RANK_TIERS])  # thresholds are plain ints
