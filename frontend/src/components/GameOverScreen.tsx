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
  const mine = standings.find((row) => row.player_id === view.you);

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

      {/* Where points become coins. Without this the conversion is invisible
          and it looks like the game took your score away. The total comes
          from the server (economy.coins_awarded) rather than being recomputed
          here, so the figure shown and the balance credited cannot drift. */}
      {mine?.coins_earned !== undefined && (
        <div className="payout">
          <span className="label">coins earned</span>
          <span className="amount">
            <span aria-hidden="true">🪙</span> +{mine.coins_earned}
          </span>
          <p className="muted">
            Your <b>{mine.score} points</b> this game convert into coins: 10 for
            finishing, 1 per 20 points, plus a bonus for the podium. Points reset
            every game — coins are your permanent balance.
          </p>
        </div>
      )}

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
