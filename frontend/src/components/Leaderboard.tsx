import { useFlip } from "../hooks/useFlip";
import type { RoomView } from "../store/room";
import type { PlayerRow } from "../types/wire";
import { avatarFor } from "../ui/identity";

interface Props {
  view: RoomView;
  /** Per-player point change from the round just revealed, if any. */
  deltas?: Map<string, number>;
}

/**
 * The roster, in both of its moods. Before a game it is a waiting list in
 * join order with ready flags; once play starts it is a live scoreboard, and
 * rank changes animate (spec §09) because an overtake that happens instantly
 * is an overtake nobody saw.
 *
 * The ordering mirrors the server's rule (§07: score desc, elapsed asc, id)
 * as closely as the client's data allows — it is presentation only. Scores
 * themselves are never computed here.
 */
function order(players: PlayerRow[], lobby: boolean): PlayerRow[] {
  const rows = [...players];
  if (lobby) return rows.sort((a, b) => a.seat - b.seat);
  return rows.sort((a, b) => b.score - a.score || a.seat - b.seat);
}

export function Leaderboard({ view, deltas }: Props) {
  const rowRef = useFlip();
  const lobby = view.phase === "LOBBY";
  const rows = order(view.players, lobby);

  return (
    <ol className="board" aria-label={lobby ? "players" : "scores"}>
      {rows.map((player, index) => {
        const delta = deltas?.get(player.id);
        return (
          <li
            key={player.id}
            ref={rowRef(player.id)}
            className={`row${player.id === view.you ? " you" : ""}${
              player.conn === "DROPPED" ? " gone" : ""
            }`}
          >
            {!lobby && <span className="rank">{index + 1}</span>}
            <span className="face" aria-hidden="true">
              {avatarFor(player.id)}
            </span>
            <span className="who">{player.name}</span>
            {player.is_host && <span className="chip host">host</span>}
            {lobby && player.ready && <span className="chip ready">ready</span>}
            {!lobby && player.spectating && <span className="chip">watching</span>}
            {player.conn === "DROPPED" && <span className="chip away">away</span>}
            {delta !== undefined && (
              <span className={`delta${delta === 0 ? " zero" : ""}`}>
                {delta > 0 ? `+${delta}` : "—"}
              </span>
            )}
            {!lobby && <span className="score">{player.score}</span>}
          </li>
        );
      })}
    </ol>
  );
}
