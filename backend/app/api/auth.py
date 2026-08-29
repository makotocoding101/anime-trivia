"""Resume tokens: identity without accounts (spec §08, R-14).

player_id appears in every scoreboard payload, so possession of an id must
not be enough to reconnect as that player — otherwise anyone could resume as
whoever is in first place. The token is the id plus an HMAC signature; only
the server can mint one, and verification is constant-time.

The secret comes from SESSION_SECRET, or is random per process. A random
secret means restarts invalidate tokens — acceptable, because a restart ends
in-flight games anyway (room state is deliberately not durable, §01).
"""

from __future__ import annotations

import hashlib
import hmac

from app.game.state import PlayerId


def mint_token(player_id: PlayerId, secret: str) -> str:
    signature = hmac.new(secret.encode(), player_id.encode(), hashlib.sha256).hexdigest()
    return f"{player_id}.{signature}"


def verify_token(token: str, secret: str) -> PlayerId | None:
    """The player_id inside a valid token, else None. Never raises."""
    player_id, dot, signature = token.rpartition(".")
    if not dot or not player_id or not signature:
        return None
    expected = hmac.new(secret.encode(), player_id.encode(), hashlib.sha256).hexdigest()
    return player_id if hmac.compare_digest(signature, expected) else None  # R-14
