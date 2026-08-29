import type { RoomSocket } from "../net/socket";
import type { RoomView } from "../store/room";

interface Props {
  view: RoomView;
  socket: RoomSocket;
}

export function GameOverScreen({ view, socket }: Props) {
  const standings = view.standings ?? [];
  const isHost = view.players.find((p) => p.id === view.you)?.is_host ?? false;
  const winner = standings[0];

  return (
    <main className="center">
      <h2>game over</h2>
      {winner !== undefined && (
        <p className="winner">
          🏆 {winner.name} — {winner.score}
        </p>
      )}
      <ol className="standings">
        {standings.map((row) => (
          <li key={row.player_id}>
            <span className="name">{row.name}</span>
            <span className="score">{row.score}</span>
          </li>
        ))}
      </ol>
      <div className="actions">
        {isHost ? (
          <button onClick={() => socket.send({ type: "rematch" })}>rematch</button>
        ) : (
          <span className="dim">waiting for the host to rematch…</span>
        )}
      </div>
    </main>
  );
}
