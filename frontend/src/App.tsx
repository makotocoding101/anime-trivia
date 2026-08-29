import { useCallback, useEffect, useRef, useState } from "react";

import { GameOverScreen } from "./components/GameOverScreen";
import { GameScreen } from "./components/GameScreen";
import { JoinScreen } from "./components/JoinScreen";
import { LobbyScreen } from "./components/LobbyScreen";
import { createRoom, RoomSocket, type SocketStatus } from "./net/socket";
import { useRoomStore } from "./store/room";

export function App() {
  const view = useRoomStore((s) => s.view);
  const dispatchFrame = useRoomStore((s) => s.dispatchFrame);
  const reset = useRoomStore((s) => s.reset);
  const clearError = useRoomStore((s) => s.clearError);

  const socketRef = useRef<RoomSocket | null>(null);
  const needsResync = view.needsResync;

  // R-11's gap rule: a hole in the broadcast stream means this connection is
  // suspect; bounce it and resume — the snapshot re-baselines everything.
  useEffect(() => {
    if (needsResync) socketRef.current?.resync();
  }, [needsResync]);
  const [status, setStatus] = useState<SocketStatus | "idle">("idle");
  const [joinError, setJoinError] = useState<string | null>(null);

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
        setJoinError("could not create a room — is the server up?");
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
      <JoinScreen
        onJoin={enterRoom}
        onCreate={handleCreate}
        connecting={status === "connecting"}
        error={joinError ?? (status === "closed" ? "disconnected from the room — rejoin below" : null)}
      />
    );
  }

  const shell = (body: React.ReactNode) => (
    <div className="shell">
      <header className="bar">
        <span className="code">room {view.roomCode}</span>
        <span className={`conn conn-${status}`}>
          {status === "reconnecting" ? "reconnecting…" : status}
        </span>
        <button className="ghost" onClick={leave}>
          leave
        </button>
      </header>
      {view.lastError !== null && (
        <div className="toast" onClick={clearError}>
          {view.lastError.replaceAll("_", " ")}
        </div>
      )}
      {body}
    </div>
  );

  if (view.phase === "LOBBY") return shell(<LobbyScreen view={view} socket={socket} />);
  if (view.phase === "GAME_OVER") return shell(<GameOverScreen view={view} socket={socket} />);
  return shell(<GameScreen view={view} socket={socket} />);
}
