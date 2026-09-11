"""Commands in, events out.

Commands are everything that can happen to a room — client actions AND
clock-driven wakeups. The timer never mutates state directly; it enqueues a
command like everyone else, so ordering is decided in one place (invariant 1).

Events are what transition() returns. `to` is None for broadcast, a PlayerId
for a targeted send. Wire encoding (names, seq stamping) is the room layer's
job in M2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.game.state import GameConfig, LoadedQuestion, Phase, PlayerId

# --------------------------------------------------------------------------
# Commands — client-originated
# --------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Join:
    player_id: PlayerId
    name: str


@dataclass(slots=True, frozen=True)
class ReconnectPlayer:
    """A returning socket presenting a valid resume token (§08)."""

    player_id: PlayerId


@dataclass(slots=True, frozen=True)
class Leave:
    """Explicit exit — skips the reconnect grace entirely."""

    player_id: PlayerId


@dataclass(slots=True, frozen=True)
class Disconnect:
    """Socket vanished without a leave; enqueued by the connection layer."""

    player_id: PlayerId


@dataclass(slots=True, frozen=True)
class SetReady:
    player_id: PlayerId
    ready: bool


@dataclass(slots=True, frozen=True)
class StartGame:
    """Deck is already sampled (without replacement — R-15) by the room layer,
    which is where the I/O of loading questions lives. The domain only ever
    sees a finished deck, which keeps transition() synchronous."""

    player_id: PlayerId
    deck: tuple[LoadedQuestion, ...]
    config: GameConfig
    # Persistence context, minted by the socket layer and read back by the
    # room layer when the game actually starts. Riding the command keeps meta
    # capture inside the single-writer path, so a duplicate or rejected start
    # can never attach meta to a game that did not begin. The domain itself
    # never reads these.
    mode_id: int | None = None
    game_id: str | None = None


@dataclass(slots=True, frozen=True)
class SubmitAnswer:
    player_id: PlayerId
    round_seq: int  # the round this answers — never inferred from phase (R-02)
    option_id: int
    cid: str | None = None  # client message id, echoed in the ack (R-01)


@dataclass(slots=True, frozen=True)
class Kick:
    player_id: PlayerId
    target_id: PlayerId


@dataclass(slots=True, frozen=True)
class Rematch:
    player_id: PlayerId


# --------------------------------------------------------------------------
# Commands — clock-driven (enqueued by the room task's fenced timers)
# --------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class IntroElapsed:
    round_seq: int


@dataclass(slots=True, frozen=True)
class DeadlineReached:
    """Scheduled at deadline + grace. The stored deadline, not this command's
    arrival time, decides individual answers (R-04)."""

    round_seq: int


@dataclass(slots=True, frozen=True)
class RevealElapsed:
    round_seq: int


@dataclass(slots=True, frozen=True)
class ReapPlayer:
    """Grace expiry for a DROPPED player. Fenced by dropped_at: if the player
    reconnected (and possibly re-dropped) since scheduling, this is stale."""

    player_id: PlayerId
    dropped_at: float


type Command = (
    Join
    | ReconnectPlayer
    | Leave
    | Disconnect
    | SetReady
    | StartGame
    | SubmitAnswer
    | Kick
    | Rematch
    | IntroElapsed
    | DeadlineReached
    | RevealElapsed
    | ReapPlayer
)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Snapshot:
    """Complete room state for one client — sent on join and reconnect,
    never deltas (R-11). Payload built by view.public_view (R-12 redaction)."""

    to: PlayerId
    view: dict[str, object]


@dataclass(slots=True)
class PhaseChanged:
    phase: Phase
    round_seq: int
    round_index: int


@dataclass(slots=True)
class QuestionOpened:
    """The question payload. Carries NO correctness information (R-12)."""

    round_seq: int
    round_index: int
    question_count: int
    question_id: int
    kind: str
    prompt: str
    options: tuple[tuple[int, str], ...]  # (option_id, label)
    media_ref: str | None
    opened_at: float  # monotonic; wall-clock mapping happens at encode time
    deadline: float


@dataclass(slots=True)
class AnswerAck:
    """Receipt, not verdict — says nothing about correctness."""

    to: PlayerId
    round_seq: int
    cid: str | None


@dataclass(slots=True)
class AnswerProgress:
    answered: int
    total: int


@dataclass(slots=True)
class PlayerResult:
    player_id: PlayerId
    option_id: int | None
    correct: bool
    delta: int
    score: int
    streak: int


@dataclass(slots=True)
class Reveal:
    """The only event that carries the answer key (R-12)."""

    round_seq: int
    round_index: int
    correct_option_id: int
    results: list[PlayerResult] = field(default_factory=list)


@dataclass(slots=True)
class ScoreboardRow:
    rank: int
    player_id: PlayerId
    name: str
    score: int
    streak: int
    #: Coins this game paid out, set only on the final standings. None on a
    #: mid-game scoreboard, because nothing has been earned yet — distinct
    #: from 0, which means "the game ended and this player earned nothing".
    coins_earned: int | None = None


@dataclass(slots=True)
class Scoreboard:
    rows: list[ScoreboardRow] = field(default_factory=list)


@dataclass(slots=True)
class PlayerJoined:
    player_id: PlayerId
    name: str
    seat: int
    spectating: bool


@dataclass(slots=True)
class PlayerLeft:
    player_id: PlayerId
    reason: str  # 'dropped' | 'left' | 'kicked' | 'timeout'


@dataclass(slots=True)
class PlayerReconnected:
    player_id: PlayerId


@dataclass(slots=True)
class PlayerReadyChanged:
    player_id: PlayerId
    ready: bool


@dataclass(slots=True)
class HostChanged:
    player_id: PlayerId


@dataclass(slots=True)
class Error:
    """A rejected command, bounced back to its sender only. Emitted by the
    room layer when transition() raises GameError — never broadcast."""

    to: PlayerId
    code: str
    cid: str | None = None


@dataclass(slots=True)
class GameOver:
    standings: list[ScoreboardRow] = field(default_factory=list)


type Event = (
    Snapshot
    | PhaseChanged
    | QuestionOpened
    | AnswerAck
    | AnswerProgress
    | Reveal
    | Scoreboard
    | PlayerJoined
    | PlayerLeft
    | PlayerReconnected
    | PlayerReadyChanged
    | HostChanged
    | GameOver
    | Error
)
