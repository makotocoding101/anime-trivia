import { useCallback, useEffect, useRef, useState } from "react";

import { ConnectionDot } from "./components/ConnectionDot";
import { GameOverScreen } from "./components/GameOverScreen";
import { GameScreen } from "./components/GameScreen";
import { HomeScreen, loadWallet } from "./components/HomeScreen";
import { LobbyScreen } from "./components/LobbyScreen";
import { ProfileScreen } from "./components/ProfileScreen";
import { RankingsScreen } from "./components/RankingsScreen";
import { clearNameToken, loadName, loadNameToken, type Wallet } from "./net/menu";
import { createRoom, RoomSocket, type SocketStatus } from "./net/socket";
import { useRoomStore } from "./store/room";
import { avatarFor } from "./ui/identity";

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

type MenuView = "home" | "rankings" | "profile";

export function App() {
  const view = useRoomStore((s) => s.view);
  const dispatchFrame = useRoomStore((s) => s.dispatchFrame);
  const reset = useRoomStore((s) => s.reset);
  const clearError = useRoomStore((s) => s.clearError);

  const socketRef = useRef<RoomSocket | null>(null);
  const [status, setStatus] = useState<SocketStatus | "idle">("idle");
  const [joinError, setJoinError] = useState<string | null>(null);
  const [name, setName] = useState(loadName);
  const [nameToken, setNameToken] = useState(loadNameToken);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [menu, setMenu] = useState<MenuView>("home");
  const [pendingModeId, setPendingModeId] = useState<number | null>(null);
  const needsResync = view.needsResync;

  // R-11's gap rule: a hole in the broadcast stream means this connection is
  // suspect; bounce it and resume — the snapshot re-baselines everything.
  useEffect(() => {
    if (needsResync) socketRef.current?.resync();
  }, [needsResync]);

  const refreshWallet = useCallback(() => {
    loadWallet(name).then(setWallet).catch(() => undefined);
  }, [name]);

  useEffect(refreshWallet, [refreshWallet]);

  // Coins are credited inside the transaction that records the game, which
  // runs after the game_over broadcast — so the balance may not be written
  // yet when this fires. Refetching again on the way back to the menu is the
  // safety net; two cheap reads beat guessing at a delay.
  const phase = view.phase;
  useEffect(() => {
    if (phase === "GAME_OVER") refreshWallet();
  }, [phase, refreshWallet]);

  const enterRoom = useCallback(
    (code: string, playerName: string) => {
      reset();
      setJoinError(null);
      const socket = new RoomSocket(
        code.toUpperCase(),
        playerName,
        {
          onFrame: dispatchFrame,
          onStatus: setStatus,
          onFatal: (reason) => {
            if (reason === "name_taken") {
              // The stored token is for a different name, or there is none.
              // Either way it will not open this door; drop it so the next
              // attempt shows the sign-in form rather than failing again.
              clearNameToken();
              setNameToken(null);
              setJoinError(
                "that name is claimed by someone else — sign in with its passphrase, or pick another",
              );
            }
          },
        },
        nameToken,
      );
      socketRef.current = socket;
      socket.connect();
    },
    [dispatchFrame, reset, nameToken],
  );

  const handlePlay = useCallback(
    async (modeId: number) => {
      setPendingModeId(modeId);
      try {
        enterRoom(await createRoom(), name);
      } catch {
        setJoinError("couldn't reach the server — is it running?");
      }
    },
    [enterRoom, name],
  );

  const handleJoin = useCallback(
    (code: string) => {
      setPendingModeId(null);
      enterRoom(code, name);
    },
    [enterRoom, name],
  );

  const leave = useCallback(() => {
    socketRef.current?.send({ type: "leave" });
    socketRef.current?.close();
    socketRef.current = null;
    setStatus("idle");
    setPendingModeId(null);
    setMenu("home");
    reset();
    refreshWallet();
  }, [reset, refreshWallet]);

  const socket = socketRef.current;
  const inRoom = status !== "idle" && socket !== null && view.you !== null;
  const me = view.players.find((p) => p.id === view.you) ?? null;

  if (!inRoom || socket === null) {
    const error =
      joinError ?? (status === "closed" ? "you left the room — rejoin below" : null);

    if (menu === "rankings") {
      return (
        <div className="app">
          <RankingsScreen name={name} onBack={() => setMenu("home")} />
        </div>
      );
    }
    if (menu === "profile") {
      return (
        <div className="app">
          <ProfileScreen name={name} wallet={wallet} onBack={() => setMenu("home")} />
        </div>
      );
    }
    return (
      <div className="app">
        <HomeScreen
          name={name}
          onNameChange={(next, token) => {
            setName(next);
            setNameToken(token);
          }}
          wallet={wallet}
          onPlay={handlePlay}
          onJoin={handleJoin}
          onOpenRankings={() => setMenu("rankings")}
          onOpenProfile={() => setMenu("profile")}
          connecting={status === "connecting"}
          error={error}
        />
      </div>
    );
  }

  const body =
    view.phase === "LOBBY" ? (
      <LobbyScreen view={view} socket={socket} initialModeId={pendingModeId} />
    ) : view.phase === "GAME_OVER" ? (
      <GameOverScreen view={view} socket={socket} />
    ) : (
      <GameScreen view={view} socket={socket} />
    );

  return (
    <div className="app">
      <div className="frame">
        <header className="topbar">
          <span className="brand grad">OtaKizu</span>
          <span className="roomcode">{view.roomCode}</span>
          <span className="spacer" />

          {me !== null && (
            <span className="whoami">
              <span className="face" aria-hidden="true">
                {avatarFor(me.id)}
              </span>
              <span className="who">
                <span className="label">playing as</span>
                <b>{me.name}</b>
              </span>
            </span>
          )}
          {me !== null && view.phase !== "LOBBY" && (
            <span className="coin">
              <span aria-hidden="true">🪙</span>
              <span className="sr-only">your score: </span>
              {me.score}
            </span>
          )}

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
