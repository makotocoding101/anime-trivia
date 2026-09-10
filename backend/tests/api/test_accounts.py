"""Name ownership: the credentials, and the rule that a claimed name cannot
be worn by anyone else.

The token/password unit tests need no server. The join-refusal tests do, and
they run against a stub store rather than Postgres — what is under test is
the socket handler's decision, not the query behind it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from app.api.auth import (
    hash_password,
    mint_name_token,
    mint_token,
    verify_name_token,
    verify_password,
    verify_token,
)
from app.game.economy import wallet_key
from app.game.state import GameConfig
from app.main import create_app
from app.store import AccountRow

SECRET = "test-secret"
FAST = GameConfig(question_count=2, seconds_per_question=0.4)


class TestPasswords:
    def test_round_trips(self) -> None:
        encoded = hash_password("correct horse battery")
        assert verify_password("correct horse battery", encoded)
        assert not verify_password("wrong horse battery", encoded)

    def test_the_hash_is_not_the_password(self) -> None:
        encoded = hash_password("hunter2hunter2")
        assert "hunter2hunter2" not in encoded
        assert encoded.startswith("scrypt$")

    def test_two_hashes_of_one_password_differ(self) -> None:
        # Distinct salts, so a stolen table cannot be scanned for repeats.
        assert hash_password("same passphrase") != hash_password("same passphrase")

    @pytest.mark.parametrize(
        "corrupt",
        ["", "nonsense", "scrypt$bad", "bcrypt$1$2$3$4$5", "scrypt$x$8$1$aa$bb"],
    )
    def test_a_corrupt_row_fails_closed(self, corrupt: str) -> None:
        # It must never raise into the request handler, and must never say yes.
        assert verify_password("anything", corrupt) is False


class TestTokens:
    def test_name_tokens_round_trip(self) -> None:
        token = mint_name_token("aoi", SECRET)
        assert verify_name_token(token, SECRET) == "aoi"

    def test_a_forged_signature_is_refused(self) -> None:
        assert verify_name_token("aoi." + "0" * 64, SECRET) is None
        assert verify_name_token(mint_name_token("aoi", SECRET), "other-secret") is None

    @pytest.mark.parametrize("junk", ["", ".", "aoi", "aoi.", ".sig"])
    def test_malformed_tokens_never_raise(self, junk: str) -> None:
        assert verify_name_token(junk, SECRET) is None
        assert verify_token(junk, SECRET) is None

    def test_the_two_token_kinds_are_not_interchangeable(self) -> None:
        """The reason both are domain-separated before signing.

        A player picks their own name, so without a prefix somebody could
        register a name equal to a player id and present the resulting name
        token as a resume credential for that player.
        """
        victim = "6f1d3c9e-0000-4000-8000-000000000001"
        name_token = mint_name_token(victim, SECRET)
        assert verify_token(name_token, SECRET) is None

        resume = mint_token(victim, SECRET)
        assert verify_name_token(resume, SECRET) is None


class StubStore:
    """Just enough store to answer the join handler's one question."""

    def __init__(self, claimed: dict[str, AccountRow]) -> None:
        self._claimed = claimed

    async def get_account(self, name: str) -> AccountRow | None:
        return self._claimed.get(wallet_key(name))

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - unused paths
        raise AttributeError(item)


def send(ws: WebSocketTestSession, **payload: Any) -> None:
    ws.send_text(json.dumps(payload))


def first_frame(ws: WebSocketTestSession) -> dict[str, Any]:
    return json.loads(ws.receive_text())


@pytest.fixture
def claimed_client() -> Any:
    """A server where "aoi" is spoken for and nothing else is."""
    app = create_app(game_config=FAST)
    app.state.store = StubStore({"aoi": AccountRow("aoi", hash_password("passphrase"))})
    app.state.session_secret = SECRET
    with TestClient(app) as client:
        # create_app's lifespan installs the real store; put the stub back.
        client.app.state.store = StubStore(  # type: ignore[attr-defined]
            {"aoi": AccountRow("aoi", hash_password("passphrase"))}
        )
        client.app.state.session_secret = SECRET  # type: ignore[attr-defined]
        yield client


class TestJoinEnforcement:
    def test_a_claimed_name_is_refused_without_a_token(self, claimed_client: TestClient) -> None:
        code = claimed_client.post("/api/rooms").json()["code"]
        with claimed_client.websocket_connect(f"/ws/rooms/{code}") as ws:
            send(ws, type="join", name="aoi")
            assert first_frame(ws)["data"]["code"] == "name_taken"

    def test_case_and_spacing_do_not_slip_past_it(self, claimed_client: TestClient) -> None:
        # The wallet key is the identity, so "  AOI " is the same name.
        code = claimed_client.post("/api/rooms").json()["code"]
        for spelling in ("AOI", " aoi ", "Aoi"):
            with claimed_client.websocket_connect(f"/ws/rooms/{code}") as ws:
                send(ws, type="join", name=spelling)
                assert first_frame(ws)["data"]["code"] == "name_taken", spelling

    def test_a_token_for_a_different_name_does_not_help(self, claimed_client: TestClient) -> None:
        code = claimed_client.post("/api/rooms").json()["code"]
        with claimed_client.websocket_connect(f"/ws/rooms/{code}") as ws:
            send(ws, type="join", name="aoi", name_token=mint_name_token("ren", SECRET))
            assert first_frame(ws)["data"]["code"] == "name_taken"

    def test_the_owner_gets_in(self, claimed_client: TestClient) -> None:
        code = claimed_client.post("/api/rooms").json()["code"]
        with claimed_client.websocket_connect(f"/ws/rooms/{code}") as ws:
            send(ws, type="join", name="aoi", name_token=mint_name_token("aoi", SECRET))
            # session first, then the snapshot — no refusal in between.
            assert first_frame(ws)["type"] == "session"

    def test_an_unclaimed_name_stays_open_to_anyone(self, claimed_client: TestClient) -> None:
        code = claimed_client.post("/api/rooms").json()["code"]
        with claimed_client.websocket_connect(f"/ws/rooms/{code}") as ws:
            send(ws, type="join", name="ren")
            assert first_frame(ws)["type"] == "session"


class TestSessionEndpoint:
    def test_accounts_need_a_database(self) -> None:
        """On the seed-file store nothing can be claimed, so the endpoint says
        so rather than pretending the name was secured."""
        app = create_app(game_config=FAST)
        with TestClient(app) as client:
            response = client.post(
                "/api/account/session", json={"name": "aoi", "password": "passphrase"}
            )
            assert response.status_code == 503

    def test_short_passphrases_are_rejected_before_anything_is_written(self) -> None:
        app = create_app(game_config=FAST)
        with TestClient(app) as client:
            assert (
                client.post(
                    "/api/account/session", json={"name": "aoi", "password": "short"}
                ).status_code
                == 422
            )

    def test_status_reports_an_unclaimed_name(self) -> None:
        app = create_app(game_config=FAST)
        with TestClient(app) as client:
            assert client.get("/api/account/aoi").json() == {"name": "aoi", "claimed": False}
