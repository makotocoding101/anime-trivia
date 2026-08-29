"""WebSocket lifecycle: accept, join, pump commands, report the disconnect.

This layer holds all the socket-shaped mess so the room actor never sees it:
frames are validated here (a bad frame becomes an error frame, never an
exception in the room task), pings are answered here (the offset handshake
must not queue behind game traffic), and a vanished socket becomes a
Disconnect command like any other.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.game.errors import GameError
from app.game.events import (
    Command,
    Disconnect,
    Join,
    Kick,
    Leave,
    Rematch,
    SetReady,
    StartGame,
    SubmitAnswer,
)
from app.game.state import PlayerId
from app.rooms.connection import Connection
from app.rooms.registry import InProcessRegistry
from app.rooms.room import Room, RoomClosed
from app.schemas.wire import (
    ClientFrame,
    FrameError,
    JoinFrame,
    KickFrame,
    LeaveFrame,
    PingFrame,
    RematchFrame,
    SetReadyFrame,
    StartGameFrame,
    SubmitAnswerFrame,
    error_frame,
    parse_client_frame,
    pong_frame,
)
from app.store import GameStore

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/rooms/{code}")
async def ws_room(websocket: WebSocket) -> None:
    await websocket.accept()
    code: str = websocket.path_params["code"].upper()
    registry: InProcessRegistry = websocket.app.state.registry

    room = registry.get(code)
    if room is None:
        await websocket.send_text(error_frame("room_not_found"))
        await websocket.close(code=4404, reason="room_not_found")
        return

    # First frame must be a join; everything else is meaningless without a seat.
    try:
        first = parse_client_frame(await websocket.receive_text())
    except WebSocketDisconnect:
        return
    except FrameError as exc:
        await websocket.send_text(error_frame(exc.code))
        await websocket.close(code=4400, reason=exc.code)
        return
    if not isinstance(first, JoinFrame):
        await websocket.send_text(error_frame("join_first"))
        await websocket.close(code=4400, reason="join_first")
        return

    player_id: PlayerId = str(uuid.uuid4())
    conn = Connection(player_id, websocket)
    try:
        room.attach(conn)
    except RoomClosed:
        await websocket.send_text(error_frame("room_not_found"))
        await websocket.close(code=4404, reason="room_not_found")
        return
    room.inbox.put_nowait(Join(player_id=player_id, name=first.name.strip()))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = parse_client_frame(raw)
            except FrameError as exc:
                # Rejected at the boundary; the connection lives on (§05).
                conn.try_send(error_frame(exc.code, seq=room.state.seq))
                continue
            if isinstance(frame, PingFrame):
                conn.try_send(pong_frame(frame.t0))
                continue
            if isinstance(frame, StartGameFrame):
                await _start_game(room, conn, frame, player_id, websocket)
                continue
            room.inbox.put_nowait(_to_command(frame, player_id))
    except WebSocketDisconnect:
        pass
    finally:
        await room.detach(conn)
        if not room.stopped:
            # The domain decides what a vanished socket means (DROPPED, held
            # seat, reconnect grace) — this layer only reports it.
            room.inbox.put_nowait(Disconnect(player_id=player_id))


async def _start_game(
    room: Room,
    conn: Connection,
    frame: StartGameFrame,
    player_id: PlayerId,
    websocket: WebSocket,
) -> None:
    """Loading the deck is I/O, so it happens here — in the socket handler,
    never the room task (§06: one SELECT at game start). Two racing starts
    both load; the domain's idempotent-start guard discards the loser."""
    store: GameStore = websocket.app.state.store
    try:
        deck, config = await store.load_deck(frame.mode_id)
    except GameError as exc:
        conn.try_send(error_frame(exc.code, seq=room.state.seq))
        return
    room.inbox.put_nowait(
        StartGame(
            player_id=player_id,
            deck=deck,
            config=config,
            mode_id=frame.mode_id,
            game_id=str(uuid.uuid4()),
        )
    )


def _to_command(frame: ClientFrame, player_id: PlayerId) -> Command:
    match frame:
        case SubmitAnswerFrame():
            return SubmitAnswer(
                player_id=player_id,
                round_seq=frame.round_seq,
                option_id=frame.option_id,
                cid=frame.cid,
            )
        case SetReadyFrame():
            return SetReady(player_id=player_id, ready=frame.ready)
        case KickFrame():
            return Kick(player_id=player_id, target_id=frame.target_id)
        case JoinFrame():
            # A second join on a live socket; the domain rejects it politely.
            return Join(player_id=player_id, name=frame.name.strip())
        case LeaveFrame():
            return Leave(player_id=player_id)
        case RematchFrame():
            return Rematch(player_id=player_id)
        case PingFrame() | StartGameFrame():  # both handled by the recv loop
            raise AssertionError("frame is handled at the boundary, not here")
