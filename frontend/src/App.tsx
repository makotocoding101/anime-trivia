import { useCallback, useEffect, useRef, useState } from "react";

import { ConnectionDot } from "./components/ConnectionDot";
import { GameOverScreen } from "./components/GameOverScreen";
import { GameScreen } from "./components/GameScreen";
import { JoinScreen } from "./components/JoinScreen";
import { LobbyScreen } from "./components/LobbyScreen";
import { createRoom, RoomSocket, type SocketStatus } from "./net/socket";
import { useRoomStore } from "./store/room";

/** Wire error codes are machine-readable by design (spec §05); the UI is
 * where they become sentences. Anything unlisted falls back to its code. */
const ERROR_TEXT: Record<string, string> = {
  already_answered: "you already answered this one",
  too_late: "too late — the clock ran out",
  stale_round: "that answer was for the previous question",
  round_closed: "the round is closed",
  spectating: "you're watching this round — you're in from the next one",
  not_host: "only the host can do that",
  room_full: "that room is full",
  deck_too_small: "that mode doesn't have enough questions yet",
  mode_not_found: "that game mode is gone",
};

export function App() {
  const view = useRoomStore((s) => s.view);
  const dispatchFrame = useRoomStore((s) => s.dispatchFrame);
  const reset = useRoomStore((s) => s.reset);
  const clearError = useRoomStore((s) => s.clearError);

  const socketRef = useRef<RoomSocket | null>(null);
  const [status, setStatus] = useState<SocketStatus | "idle">("idle");
  const [joinError, setJoinError] = useState<string | null>(null);
  const needsResync = view.needsResync;

  // R-11's gap rule: a hole in the broadcast stream means this connection is
  // suspect; bounce it and resume — the snapshot re-baselines everything.
  useEffect(() => {
    if (needsResync) socketRef.current?.resync();
  }, [needsResync]);

  const enterRoom = useCallback(
    (code: string, name: string) => {
      reset();
      setJoinError(null);
      const socket = new RoomSocket(code.toUpperCase(), name, {
        onFrame: dispatchFrame,
        onStatus: setStatus,
      });
      socketRef.current = socket;
      socket.connect();
    },
    [dispatchFrame, reset],
  );

  const handleCreate = useCallback(
    async (name: string) => {
      try {
        enterRoom(await createRoom(), name);
      } catch {
        setJoinError("couldn't reach the server — is it running?");
      }
    },
    [enterRoom],
  );

  const leave = useCallback(() => {
    socketRef.current?.send({ type: "leave" });
    socketRef.current?.close();
    socketRef.current = null;
    setStatus("idle");
    reset();
  }, [reset]);

  const socket = socketRef.current;
  const inRoom = status !== "idle" && socket !== null && view.you !== null;

  if (!inRoom || socket === null) {
    return (
      <div className="app">
        <JoinScreen
          onJoin={enterRoom}
          onCreate={handleCreate}
          connecting={status === "connecting"}
          error={
            joinError ?? (status === "closed" ? "you left the room — rejoin below" : null)
          }
        />
      </div>
    );
  }

  const body =
    view.phase === "LOBBY" ? (
      <LobbyScreen view={view} socket={socket} />
    ) : view.phase === "GAME_OVER" ? (
      <GameOverScreen view={view} socket={socket} />
    ) : (
      <GameScreen view={view} socket={socket} />
    );

  return (
    <div className="app">
      <div className="frame">
        <header className="topbar">
          <span className="brand">
            kagen<span>.</span>
          </span>
          <span className="roomcode">{view.roomCode}</span>
          <span className="spacer" />
          <ConnectionDot status={status} />
          <button type="button" className="ghost" onClick={leave}>
            leave
          </button>
        </header>

        {view.lastError !== null && (
          <div className="notice" role="alert">
            {ERROR_TEXT[view.lastError] ?? view.lastError.replaceAll("_", " ")}
            <button type="button" onClick={clearError} aria-label="dismiss">
              ✕
            </button>
          </div>
        )}

        {body}
      </div>
    </div>
  );
}
