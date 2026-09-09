import type { RoomSocket } from "../net/socket";
import type { RoomView } from "../store/room";
import { Leaderboard } from "./Leaderboard";

interface Props {
  view: RoomView;
  socket: RoomSocket;
}

export function GameOverScreen({ view, socket }: Props) {
  const standings = view.standings ?? [];
  const winner = standings[0];
  const isHost = view.players.find((p) => p.id === view.you)?.is_host ?? false;
  const youWon = winner !== undefined && winner.player_id === view.you;

  return (
    <>
      <div className="podium">
        <div className="crown" aria-hidden="true">
          🏆
        </div>
        <div className="label">{youWon ? "you win" : "winner"}</div>
        {winner !== undefined && (
          <>
            <div className="winner">{winner.name}</div>
            <div className="final">{winner.score} points</div>
          </>
        )}
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="label">final standings</span>
          <span className="spacer" />
          <span className="live">{view.players.length} played</span>
        </div>
        <Leaderboard view={view} />
      </div>

      <div className="actions mid">
        {isHost ? (
          <button type="button" onClick={() => socket.send({ type: "rematch" })}>
            play again
          </button>
        ) : (
          <span className="muted">waiting for the host to start another…</span>
        )}
      </div>
    </>
  );
}
