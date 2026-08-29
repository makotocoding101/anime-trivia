import type { RoomSocket } from "../net/socket";
import type { RoomView } from "../store/room";
import { PlayerList } from "./PlayerList";

interface Props {
  view: RoomView;
  socket: RoomSocket;
}

export function LobbyScreen({ view, socket }: Props) {
  const me = view.players.find((p) => p.id === view.you);
  const isHost = me?.is_host ?? false;

  return (
    <main>
      <h2>lobby</h2>
      <p className="dim">share the room code; the host starts the game.</p>
      <PlayerList view={view} />
      <div className="actions">
        <button
          className="ghost"
          onClick={() => socket.send({ type: "set_ready", ready: !(me?.ready ?? false) })}
        >
          {me?.ready ? "unready" : "ready"}
        </button>
        {isHost && (
          <button onClick={() => socket.send({ type: "start_game" })}>start game</button>
        )}
        {!isHost && <span className="dim">waiting for the host…</span>}
      </div>
    </main>
  );
}
