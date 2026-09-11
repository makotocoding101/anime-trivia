"""The wire protocol (spec §05): typed client frames in, enveloped events out.

Envelope: {"v": 1, "seq": N, "type": "...", "ts": wall_ms, "data": {...}}

seq semantics: the counter increments only for BROADCAST events, so the
broadcast stream every client sees is dense — a gap means missed events and
the client asks for a snapshot (R-11). Targeted frames (snapshot, answer_ack,
error) carry the current counter without incrementing it: they mark "as of
seq N", and clients exempt them from gap detection.

Monotonic-to-wall conversion happens here, at encode time: the server's
authority stays loop.time() (§03); the wall-clock `ends_at` in payloads is a
rendering hint only.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.game.clock import Clock
from app.game.events import (
    AnswerAck,
    AnswerProgress,
    Error,
    Event,
    GameOver,
    HostChanged,
    PhaseChanged,
    PlayerJoined,
    PlayerLeft,
    PlayerReadyChanged,
    PlayerReconnected,
    QuestionOpened,
    Reveal,
    Scoreboard,
    ScoreboardRow,
    Snapshot,
)

# --------------------------------------------------------------------------
# Client -> server frames. Validated at the socket boundary before anything
# reaches the room inbox — a malformed frame becomes an error event, never an
# exception inside the room task (schema discipline, spec §05).
# --------------------------------------------------------------------------


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JoinFrame(_Frame):
    type: Literal["join"]
    name: str = Field(min_length=1, max_length=24)
    token: str | None = Field(default=None, max_length=128)  # resume credential (§08)
    #: Proof of name ownership, from /api/account/session. Required only for
    #: names somebody has claimed; unclaimed names stay open to anyone.
    name_token: str | None = Field(default=None, max_length=256)


class StartGameFrame(_Frame):
    type: Literal["start_game"]
    mode_id: int = Field(ge=0)


class SubmitAnswerFrame(_Frame):
    type: Literal["submit_answer"]
    round_seq: int = Field(ge=0)
    option_id: int = Field(ge=0)
    cid: str | None = Field(default=None, max_length=64)


class SetReadyFrame(_Frame):
    type: Literal["set_ready"]
    ready: bool


class KickFrame(_Frame):
    type: Literal["kick"]
    target_id: str = Field(min_length=1, max_length=64)


class LeaveFrame(_Frame):
    type: Literal["leave"]


class RematchFrame(_Frame):
    type: Literal["rematch"]


class PingFrame(_Frame):
    type: Literal["ping"]
    t0: int  # client wall-clock ms; echoed verbatim for the offset handshake


type ClientFrame = (
    JoinFrame
    | StartGameFrame
    | SubmitAnswerFrame
    | SetReadyFrame
    | KickFrame
    | LeaveFrame
    | RematchFrame
    | PingFrame
)


class _FrameEnvelope(BaseModel):
    frame: Annotated[
        JoinFrame
        | StartGameFrame
        | SubmitAnswerFrame
        | SetReadyFrame
        | KickFrame
        | LeaveFrame
        | RematchFrame
        | PingFrame,
        Field(discriminator="type"),
    ]


class FrameError(ValueError):
    """A frame the boundary refuses. `code` goes back on the wire."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_client_frame(raw: str) -> ClientFrame:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FrameError("malformed_json") from exc
    if not isinstance(payload, dict):
        raise FrameError("malformed_frame")
    try:
        return _FrameEnvelope.model_validate({"frame": payload}).frame
    except ValidationError as exc:
        raise FrameError("invalid_frame") from exc


# --------------------------------------------------------------------------
# Server -> client encoding
# --------------------------------------------------------------------------

TARGETED_TYPES = frozenset({"snapshot", "answer_ack", "error", "session"})
"""Frame types exempt from the client's dense-seq gap detection."""


def error_frame(code: str, seq: int = 0, cid: str | None = None) -> str:
    """A bare error frame for boundary rejections that never reach the room."""
    return json.dumps(
        {
            "v": 1,
            "seq": seq,
            "type": "error",
            "ts": int(time.time() * 1000),
            "data": {"code": code, "cid": cid},
        }
    )


def session_frame(player_id: str, token: str, seq: int) -> str:
    """Sent once, right after a fresh join: the identity the client stores
    and the credential it resumes with (R-14). Boundary-generated, like pong —
    the domain never sees tokens."""
    return json.dumps(
        {
            "v": 1,
            "seq": seq,
            "type": "session",
            "ts": int(time.time() * 1000),
            "data": {"player_id": player_id, "resume_token": token},
        }
    )


def pong_frame(t0: int) -> str:
    """Clock-offset handshake reply (§03). Answered at the socket boundary —
    it must not queue behind game traffic in the room inbox."""
    return json.dumps(
        {
            "v": 1,
            "seq": 0,
            "type": "pong",
            "ts": int(time.time() * 1000),
            "data": {"t0": t0, "ts": int(time.time() * 1000)},
        }
    )


class WireEncoder:
    """Encodes domain events into wire frames, converting monotonic instants
    to wall-clock ms relative to `clock.now()` at encode time."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def _wall_ms(self, mono: float | None) -> int | None:
        if mono is None:
            return None
        return int(time.time() * 1000 + (mono - self._clock.now()) * 1000)

    def encode(self, event: Event, seq: int) -> str:
        name, data = self._payload(event)
        return json.dumps(
            {"v": 1, "seq": seq, "type": name, "ts": int(time.time() * 1000), "data": data}
        )

    def _payload(self, event: Event) -> tuple[str, dict[str, object]]:
        match event:
            case Snapshot():
                return "snapshot", self._snapshot_data(event)
            case PhaseChanged():
                return "phase_changed", {
                    "phase": event.phase.name,
                    "round_seq": event.round_seq,
                    "round_index": event.round_index,
                }
            case QuestionOpened():
                return "question", {
                    "round_seq": event.round_seq,
                    "round_index": event.round_index,
                    "question_count": event.question_count,
                    "question_id": event.question_id,
                    "kind": event.kind,
                    "prompt": event.prompt,
                    "options": [{"id": oid, "label": label} for oid, label in event.options],
                    "media_ref": event.media_ref,
                    "ends_at": self._wall_ms(event.deadline),
                    "seconds": event.deadline - event.opened_at,
                }
            case AnswerAck():
                return "answer_ack", {"round_seq": event.round_seq, "cid": event.cid}
            case AnswerProgress():
                return "answer_progress", {"answered": event.answered, "total": event.total}
            case Reveal():
                return "reveal", {
                    "round_seq": event.round_seq,
                    "round_index": event.round_index,
                    "correct_option_id": event.correct_option_id,
                    "results": [
                        {
                            "player_id": r.player_id,
                            "option_id": r.option_id,
                            "correct": r.correct,
                            "delta": r.delta,
                            "score": r.score,
                            "streak": r.streak,
                        }
                        for r in event.results
                    ],
                }
            case Scoreboard():
                return "scoreboard", {"rows": _rows(event.rows)}
            case GameOver():
                return "game_over", {"standings": _rows(event.standings)}
            case PlayerJoined():
                return "player_joined", {
                    "player_id": event.player_id,
                    "name": event.name,
                    "seat": event.seat,
                    "spectating": event.spectating,
                }
            case PlayerLeft():
                return "player_left", {"player_id": event.player_id, "reason": event.reason}
            case PlayerReconnected():
                return "player_reconnected", {"player_id": event.player_id}
            case PlayerReadyChanged():
                return "player_ready", {"player_id": event.player_id, "ready": event.ready}
            case HostChanged():
                return "host_changed", {"player_id": event.player_id}
            case Error():
                return "error", {"code": event.code, "cid": event.cid}

    def _snapshot_data(self, event: Snapshot) -> dict[str, object]:
        # The view is JSON-ready except for its monotonic instants; convert
        # them here so clients only ever see wall-clock hints.
        data = dict(event.view)
        deadline = data.get("phase_deadline")
        data["phase_deadline"] = self._wall_ms(deadline) if isinstance(deadline, float) else None
        question = data.get("question")
        if isinstance(question, dict):
            question = dict(question)
            for key in ("opened_at", "deadline"):
                value = question.get(key)
                question[key] = self._wall_ms(value) if isinstance(value, float) else None
            data["question"] = question
        return data


def _rows(rows: list[ScoreboardRow]) -> list[dict[str, object]]:
    return [
        {
            "rank": r.rank,
            "player_id": r.player_id,
            "name": r.name,
            "score": r.score,
            "streak": r.streak,
            # Omitted entirely mid-game rather than sent as 0: a client
            # rendering "+0 coins" after every round would be wrong, and a
            # missing key is harder to misread than a zero.
            **({} if r.coins_earned is None else {"coins_earned": r.coins_earned}),
        }
        for r in rows
    ]
