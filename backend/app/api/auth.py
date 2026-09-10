"""Credentials: resume tokens and name ownership (spec §08, R-14).

Two unrelated things are signed here, so both are domain-separated before
hashing. Without a prefix, a name token and a resume token are both "an
identifier plus an HMAC over it with the same key" — and since a player can
choose their own name, someone could register a name equal to a player id
and present the resulting name token as a resume token for that player.
Prefixing the signed message makes the two namespaces disjoint by
construction rather than by luck.

Passwords use scrypt from the standard library: memory-hard, no dependency
to add, and the parameters travel in the encoded string so they can be
raised later without invalidating what is already stored.

The secret comes from SESSION_SECRET, or is random per process. A random
secret means restarts invalidate tokens — acceptable, because a restart ends
in-flight games anyway (room state is deliberately not durable, §01).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.game.state import PlayerId

_PLAYER_DOMAIN = "player:"
_NAME_DOMAIN = "name:"

# N=16384, r=8, p=1 — the standard interactive parameters: 16MB and roughly
# 50ms per hash. Stored per hash, so raising them later re-hashes gradually on
# next sign-in rather than locking anybody out.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16


def _maxmem(n: int, r: int) -> int:
    """scrypt needs 128*N*r bytes; OpenSSL refuses above a 32MB default, which
    these parameters sit exactly on. Passing the requirement explicitly means
    raising N later is a one-line change here, not a puzzling ValueError."""
    return 128 * n * r * 2


def _sign(domain: str, value: str, secret: str) -> str:
    message = f"{domain}{value}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _verify(domain: str, token: str, secret: str) -> str | None:
    value, dot, signature = token.rpartition(".")
    if not dot or not value or not signature:
        return None
    expected = _sign(domain, value, secret)
    return value if hmac.compare_digest(signature, expected) else None  # R-14


def mint_token(player_id: PlayerId, secret: str) -> str:
    """A resume credential. player_id appears in every scoreboard payload, so
    possession of an id must not be enough to reconnect as that player."""
    return f"{player_id}.{_sign(_PLAYER_DOMAIN, player_id, secret)}"


def verify_token(token: str, secret: str) -> PlayerId | None:
    """The player_id inside a valid token, else None. Never raises."""
    return _verify(_PLAYER_DOMAIN, token, secret)


def mint_name_token(name_key: str, secret: str) -> str:
    """Proof that the bearer authenticated as this name. Held by the browser
    and presented on join, so a claimed name cannot be worn by anyone else."""
    return f"{name_key}.{_sign(_NAME_DOMAIN, name_key, secret)}"


def verify_name_token(token: str, secret: str) -> str | None:
    """The name_key inside a valid name token, else None. Never raises."""
    return _verify(_NAME_DOMAIN, token, secret)


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$hash`, everything needed to verify it later."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_maxmem(_SCRYPT_N, _SCRYPT_R),
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time on the comparison, and False on anything malformed —
    a corrupt row must fail closed, never raise into the request handler."""
    try:
        scheme, n, r, p, salt_hex, hash_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(hash_hex) // 2,
            maxmem=_maxmem(int(n), int(r)),
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)
