"""The synchronous state machine (invariant 2).

transition() and everything it calls contain no `await`, no I/O, and no clock
reads — `now` is passed in. Because a block with no await cannot be
interrupted in asyncio, every check-then-write in here is atomic by
construction (R-07). tests/game/test_purity.py fails the build if an await
sneaks in.

Rejected commands raise GameError; the room layer turns those into targeted
`error` events. Stale clock-driven commands (fenced by round_seq) return []
silently — staleness is expected, not exceptional (R-03).
"""

from __future__ import annotations

from app.game import scoring
from app.game.errors import GameError
from app.game.events import (
    AnswerAck,
    AnswerProgress,
    Command,
    DeadlineReached,
    Disconnect,
    Event,
    GameOver,
    HostChanged,
    IntroElapsed,
    Join,
    Kick,
    Leave,
    PhaseChanged,
    PlayerJoined,
    PlayerLeft,
    PlayerReadyChanged,
    PlayerReconnected,
    PlayerResult,
    QuestionOpened,
    ReapPlayer,
    ReconnectPlayer,
    Rematch,
    Reveal,
    RevealElapsed,
    Scoreboard,
    ScoreboardRow,
    SetReady,
    Snapshot,
    StartGame,
    SubmitAnswer,
)
from app.game.state import (
    MAX_PLAYERS,
    Answer,
    ConnState,
    Phase,
    Player,
    PlayerId,
    RoomState,
    Round,
    RoundRecord,
)
from app.game.view import public_view


def transition(state: RoomState, cmd: Command, now: float) -> list[Event]:
    """The only mutation path for RoomState. Synchronous, no await."""
    match cmd:
        case Join():
            return _on_join(state, cmd, now)
        case ReconnectPlayer():
            return _on_reconnect(state, cmd)
        case Leave():
            return _on_leave(state, cmd, now)
        case Disconnect():
            return _on_disconnect(state, cmd, now)
        case SetReady():
            return _on_set_ready(state, cmd)
        case StartGame():
            return _on_start_game(state, cmd, now)
        case SubmitAnswer():
            return _on_submit(state, cmd, now)
        case Kick():
            return _on_kick(state, cmd, now)
        case Rematch():
            return _on_rematch(state, cmd)
        case IntroElapsed():
            return _on_intro_elapsed(state, cmd, now)
        case DeadlineReached():
            return _on_deadline(state, cmd, now)
        case RevealElapsed():
            return _on_reveal_elapsed(state, cmd, now)
        case ReapPlayer():
            return _on_reap(state, cmd, now)


# --------------------------------------------------------------------------
# Membership
# --------------------------------------------------------------------------


def _on_join(state: RoomState, cmd: Join, now: float) -> list[Event]:
    if cmd.player_id in state.players:
        raise GameError("already_joined")
    seated = [p for p in state.players.values() if p.conn is not ConnState.GONE]
    if len(seated) >= MAX_PLAYERS:
        raise GameError("room_full")

    in_game = state.phase not in (Phase.LOBBY, Phase.GAME_OVER)
    player = Player(
        id=cmd.player_id,
        name=cmd.name,
        seat=state.next_seat,
        # Mid-game joiners watch until the next round starts, rather than
        # getting an unfair slice of the current timer (R-10).
        spectating_until=state.round_seq + 1 if in_game else 0,
        is_host=state.host() is None,
    )
    state.next_seat += 1
    state.players[player.id] = player

    events: list[Event] = [
        PlayerJoined(
            player_id=player.id,
            name=player.name,
            seat=player.seat,
            spectating=player.spectating_until > state.round_seq,
        ),
        Snapshot(to=player.id, view=public_view(state, player.id)),
    ]
    if player.is_host:
        events.append(HostChanged(player_id=player.id))
    return events


def _on_reconnect(state: RoomState, cmd: ReconnectPlayer) -> list[Event]:
    player = state.players.get(cmd.player_id)
    if player is None:
        raise GameError("unknown_player")
    if player.conn is ConnState.GONE:
        raise GameError("seat_expired")  # rejoin as a new player instead
    if player.conn is ConnState.LIVE:
        # Socket takeover: a phone that switched networks reconnects before
        # the server notices the old socket is dead. The connection layer has
        # already replaced the socket; the seat never looked dropped, so no
        # PlayerReconnected broadcast — just the resync snapshot (R-11).
        return [Snapshot(to=player.id, view=public_view(state, player.id))]

    player.conn = ConnState.LIVE
    player.dropped_at = None
    # Distinct from PlayerJoined so the UI restores a greyed row rather than
    # animating in a new one (wire table, spec). Snapshot, never deltas (R-11).
    return [
        PlayerReconnected(player_id=player.id),
        Snapshot(to=player.id, view=public_view(state, player.id)),
    ]


def _on_disconnect(state: RoomState, cmd: Disconnect, now: float) -> list[Event]:
    player = state.players.get(cmd.player_id)
    if player is None or player.conn is not ConnState.LIVE:
        return []  # socket teardown races are normal; nothing to do

    # Seat and score are held: the player is DROPPED, not removed. Host is
    # NOT migrated here — a two-second blip should not shuffle control;
    # migration happens at grace expiry in _on_reap (R-16).
    player.conn = ConnState.DROPPED
    player.dropped_at = now

    events: list[Event] = [PlayerLeft(player_id=player.id, reason="dropped")]
    events += _after_eligibility_change(state, now)
    return events


def _on_leave(state: RoomState, cmd: Leave, now: float) -> list[Event]:
    player = state.players.get(cmd.player_id)
    if player is None or player.conn is ConnState.GONE:
        return []
    return _remove_player(state, player, reason="left", now=now)


def _on_reap(state: RoomState, cmd: ReapPlayer, now: float) -> list[Event]:
    player = state.players.get(cmd.player_id)
    # Fencing (R-03 pattern): only reap the exact drop this was scheduled for.
    # A reconnect — or a reconnect followed by a fresh drop — makes it stale.
    if player is None or player.conn is not ConnState.DROPPED:
        return []
    if player.dropped_at != cmd.dropped_at:
        return []
    return _remove_player(state, player, reason="timeout", now=now)


def _on_kick(state: RoomState, cmd: Kick, now: float) -> list[Event]:
    _require_host(state, cmd.player_id)
    if cmd.target_id == cmd.player_id:
        raise GameError("cannot_kick_self")
    target = state.players.get(cmd.target_id)
    if target is None or target.conn is ConnState.GONE:
        raise GameError("unknown_player")
    return _remove_player(state, target, reason="kicked", now=now)


def _remove_player(state: RoomState, player: Player, reason: str, now: float) -> list[Event]:
    was_host = player.is_host
    player.conn = ConnState.GONE
    player.dropped_at = None
    player.is_host = False

    events: list[Event] = [PlayerLeft(player_id=player.id, reason=reason)]
    if was_host:
        # Longest-connected live player takes over (R-16); nobody live means
        # the room idles until someone reconnects or the sweeper (M2) acts.
        live = state.live_players()
        if live:
            successor = min(live, key=lambda p: p.seat)
            successor.is_host = True
            events.append(HostChanged(player_id=successor.id))
    events += _after_eligibility_change(state, now)
    return events


def _after_eligibility_change(state: RoomState, now: float) -> list[Event]:
    """A departure can complete a round: the missing answer may have been the
    one everyone was waiting on. Re-check, and refresh the progress counter."""
    if state.phase is not Phase.QUESTION_OPEN:
        return []
    if _question_complete(state):
        return _lock_and_reveal(state, now)
    assert state.round is not None
    eligible = state.eligible()
    return [
        AnswerProgress(
            answered=sum(1 for p in eligible if p.id in state.round.answers),
            total=len(eligible),
        )
    ]


# --------------------------------------------------------------------------
# Lobby
# --------------------------------------------------------------------------


def _on_set_ready(state: RoomState, cmd: SetReady) -> list[Event]:
    if state.phase is not Phase.LOBBY:
        raise GameError("not_in_lobby")
    player = state.players.get(cmd.player_id)
    if player is None or player.conn is not ConnState.LIVE:
        raise GameError("not_in_room")
    player.ready = cmd.ready
    return [PlayerReadyChanged(player_id=player.id, ready=player.ready)]


def _on_start_game(state: RoomState, cmd: StartGame, now: float) -> list[Event]:
    if state.phase is not Phase.LOBBY:
        return []  # idempotent: a second start while in-game is ignored (wire table)
    _require_host(state, cmd.player_id)
    if not cmd.deck:
        raise GameError("empty_deck")

    state.config = cmd.config
    state.deck = list(cmd.deck)
    state.history = []
    for p in state.players.values():
        p.ready = False
    return _begin_round(state, index=0, now=now)


def _on_rematch(state: RoomState, cmd: Rematch) -> list[Event]:
    if state.phase is not Phase.GAME_OVER:
        raise GameError("not_game_over")
    _require_host(state, cmd.player_id)

    # Same room, scores cleared. GONE players fall away; round_seq is NOT
    # reset — it fences for the room's whole life, so a stale timer from the
    # previous game can never pass the fence into this one.
    state.players = {pid: p for pid, p in state.players.items() if p.conn is not ConnState.GONE}
    for p in state.players.values():
        p.score = 0
        p.streak = 0
        p.total_elapsed_ms = 0
        p.spectating_until = 0
        p.ready = False
    state.phase = Phase.LOBBY
    state.deck = []
    state.config = None
    state.round = None
    state.round_index = -1
    state.history = []
    state.phase_deadline = None
    # Everyone gets a fresh snapshot: scores, ready flags and spectator state
    # all just reset, and a PhaseChanged alone would leave clients rendering
    # stale rows. Same machinery as reconnect (R-11) — full state, no deltas.
    events: list[Event] = [
        PhaseChanged(phase=state.phase, round_seq=state.round_seq, round_index=-1)
    ]
    events += [
        Snapshot(to=p.id, view=public_view(state, p.id))
        for p in state.players.values()
        if p.conn is ConnState.LIVE
    ]
    return events


# --------------------------------------------------------------------------
# The round loop
# --------------------------------------------------------------------------


def _begin_round(state: RoomState, index: int, now: float) -> list[Event]:
    assert state.config is not None
    state.round_seq += 1
    state.round_index = index
    state.round = None  # the question stays server-side until QUESTION_OPEN
    state.phase = Phase.INTRO
    state.phase_deadline = now + state.config.intro_seconds
    return [PhaseChanged(phase=state.phase, round_seq=state.round_seq, round_index=index)]


def _on_intro_elapsed(state: RoomState, cmd: IntroElapsed, now: float) -> list[Event]:
    if state.phase is not Phase.INTRO or cmd.round_seq != state.round_seq:
        return []  # stale timer generation (R-03)
    assert state.config is not None

    question = state.deck[state.round_index]
    state.round = Round(
        index=state.round_index,
        question=question,
        opened_at=now,
        deadline=now + state.config.seconds_per_question,
    )
    state.phase = Phase.QUESTION_OPEN
    # The room task schedules DeadlineReached at deadline + grace: answers in
    # flight at the deadline still land (§03), and individual accept/reject
    # is decided against the stored deadline either way (R-04).
    state.phase_deadline = state.round.deadline + state.config.grace_seconds
    return [
        PhaseChanged(phase=state.phase, round_seq=state.round_seq, round_index=state.round_index),
        QuestionOpened(
            round_seq=state.round_seq,
            round_index=state.round_index,
            question_count=len(state.deck),
            question_id=question.id,
            kind=question.kind,
            prompt=question.prompt,
            options=tuple((o.id, o.label) for o in question.options),
            media_ref=question.media_ref,
            opened_at=state.round.opened_at,
            deadline=state.round.deadline,
        ),
    ]


def _on_submit(state: RoomState, cmd: SubmitAnswer, now: float) -> list[Event]:
    """The busiest intersection in the codebase: guards, one mutation, no
    await — so the already-answered check and the write are one atomic step.
    """
    if state.phase is not Phase.QUESTION_OPEN:
        raise GameError("round_closed")
    if cmd.round_seq != state.round_seq:
        raise GameError("stale_round")  # R-02
    assert state.round is not None and state.config is not None
    if now > state.round.deadline + state.config.grace_seconds:
        raise GameError("too_late")  # R-04: receive time vs deadline, always
    if cmd.player_id in state.round.answers:
        raise GameError("already_answered")  # R-01

    player = state.players.get(cmd.player_id)
    if player is None or player.conn is not ConnState.LIVE:
        raise GameError("not_in_room")
    if player.spectating_until > state.round_seq:
        raise GameError("spectating")  # R-10
    if cmd.option_id not in {o.id for o in state.round.question.options}:
        raise GameError("invalid_option")

    # Clamped to the deadline: latency inside the grace window cannot earn a
    # speed bonus it did not deserve (§03). Server receive time only (R-13).
    elapsed_ms = int((min(now, state.round.deadline) - state.round.opened_at) * 1000)
    state.round.answers[cmd.player_id] = Answer(option_id=cmd.option_id, elapsed_ms=elapsed_ms)

    eligible = state.eligible()
    events: list[Event] = [
        AnswerAck(to=cmd.player_id, round_seq=state.round_seq, cid=cmd.cid),
        AnswerProgress(
            answered=sum(1 for p in eligible if p.id in state.round.answers),
            total=len(eligible),
        ),
    ]
    if _question_complete(state):
        events += _lock_and_reveal(state, now)  # early advance; R-03 fences the timer
    return events


def _on_deadline(state: RoomState, cmd: DeadlineReached, now: float) -> list[Event]:
    if state.phase is not Phase.QUESTION_OPEN or cmd.round_seq != state.round_seq:
        return []  # stale generation — everyone answered early (R-03)
    return _lock_and_reveal(state, now)


def _question_complete(state: RoomState) -> bool:
    """True when every eligible player has answered. Answers from players who
    dropped after answering still count, so this is a subset — not a size —
    comparison. An empty eligible set (everyone dropped) also completes."""
    assert state.round is not None
    return all(p.id in state.round.answers for p in state.eligible())


def _lock_and_reveal(state: RoomState, now: float) -> list[Event]:
    """LOCKED and REVEAL in one synchronous pass. LOCKED is deliberately never
    dwelled: it exists to keep score-commit and key-release as separate code
    moments (§02) — collapsing them is where an answer leak gets introduced.
    """
    assert state.round is not None and state.config is not None
    rnd = state.round
    question = rnd.question
    limit_ms = int(state.config.seconds_per_question * 1000)

    # -- LOCKED: answers stop; scores commit. Nothing here reveals the key.
    state.phase = Phase.LOCKED
    events: list[Event] = [
        PhaseChanged(phase=state.phase, round_seq=state.round_seq, round_index=rnd.index)
    ]

    results: list[PlayerResult] = []
    record_entries: dict[PlayerId, tuple[int | None, int, int]] = {}
    for player in sorted(state.seated(), key=lambda p: p.seat):
        answer = rnd.answers.get(player.id)
        if answer is not None:
            correct = answer.option_id == question.correct_option_id
            delta = scoring.score_answer(
                correct=correct,
                difficulty=question.difficulty,
                elapsed_ms=answer.elapsed_ms,
                limit_ms=limit_ms,
                streak_before=player.streak,
            )
            player.score += delta
            player.streak = player.streak + 1 if correct else 0
            player.total_elapsed_ms += answer.elapsed_ms
            results.append(
                PlayerResult(
                    player_id=player.id,
                    option_id=answer.option_id,
                    correct=correct,
                    delta=delta,
                    score=player.score,
                    streak=player.streak,
                )
            )
            record_entries[player.id] = (answer.option_id, answer.elapsed_ms, delta)
        else:
            # Timeout or dropped mid-round: zero points, streak resets, and
            # the full limit lands on the elapsed tiebreak (§07).
            player.streak = 0
            player.total_elapsed_ms += limit_ms
            results.append(
                PlayerResult(
                    player_id=player.id,
                    option_id=None,
                    correct=False,
                    delta=0,
                    score=player.score,
                    streak=0,
                )
            )
            record_entries[player.id] = (None, limit_ms, 0)

    state.history.append(
        RoundRecord(
            index=rnd.index,
            question_id=question.id,
            correct_option_id=question.correct_option_id,
            entries=record_entries,
        )
    )

    # -- REVEAL: the key becomes public. The only event that carries it (R-12).
    state.phase = Phase.REVEAL
    state.phase_deadline = now + state.config.reveal_seconds
    events.append(PhaseChanged(phase=state.phase, round_seq=state.round_seq, round_index=rnd.index))
    events.append(
        Reveal(
            round_seq=state.round_seq,
            round_index=rnd.index,
            correct_option_id=question.correct_option_id,
            results=results,
        )
    )
    events.append(_scoreboard(state))
    return events


def _on_reveal_elapsed(state: RoomState, cmd: RevealElapsed, now: float) -> list[Event]:
    if state.phase is not Phase.REVEAL or cmd.round_seq != state.round_seq:
        return []  # stale generation (R-03)
    next_index = state.round_index + 1
    if next_index < len(state.deck):
        return _begin_round(state, index=next_index, now=now)

    state.phase = Phase.GAME_OVER
    state.round = None
    state.phase_deadline = None
    return [
        PhaseChanged(phase=state.phase, round_seq=state.round_seq, round_index=state.round_index),
        GameOver(standings=_scoreboard(state).rows),
    ]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _require_host(state: RoomState, player_id: PlayerId) -> None:
    player = state.players.get(player_id)
    if player is None or player.conn is not ConnState.LIVE:
        raise GameError("not_in_room")
    if not player.is_host:
        raise GameError("not_host")


def _scoreboard(state: RoomState) -> Scoreboard:
    in_game = [p for p in state.players.values() if p.conn is not ConnState.GONE]
    return Scoreboard(
        rows=[
            ScoreboardRow(rank=i + 1, player_id=p.id, name=p.name, score=p.score, streak=p.streak)
            for i, p in enumerate(scoring.rank(in_game))
        ]
    )
