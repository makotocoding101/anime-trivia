"""Resume-token crypto (R-14)."""

from __future__ import annotations

from app.api.auth import mint_token, verify_token

SECRET = "test-secret"


def test_roundtrip() -> None:
    token = mint_token("player-abc", SECRET)
    assert verify_token(token, SECRET) == "player-abc"


def test_player_id_alone_is_not_a_credential() -> None:  # R-14
    # The id is public (every scoreboard payload); the signature is not.
    assert verify_token("player-abc", SECRET) is None
    assert verify_token("player-abc.", SECRET) is None
    assert verify_token("player-abc.deadbeef", SECRET) is None


def test_tampered_id_fails() -> None:
    token = mint_token("player-abc", SECRET)
    _, _, signature = token.rpartition(".")
    assert verify_token(f"player-xyz.{signature}", SECRET) is None


def test_wrong_secret_fails() -> None:
    token = mint_token("player-abc", SECRET)
    assert verify_token(token, "other-secret") is None


def test_garbage_never_raises() -> None:
    for junk in ("", ".", "a.b.c.d", "\x00\x00", "." * 200):
        assert verify_token(junk, SECRET) is None


def test_ids_with_dots_survive_the_encoding() -> None:
    # rpartition splits on the LAST dot, so dotted ids round-trip.
    token = mint_token("weird.id.with.dots", SECRET)
    assert verify_token(token, SECRET) == "weird.id.with.dots"
