import type { RoomView } from "../store/room";

export function PlayerList({ view }: { view: RoomView }) {
  return (
    <ul className="players">
      {view.players.map((p) => (
        <li key={p.id} className={p.conn === "DROPPED" ? "dropped" : undefined}>
          <span className="name">
            {p.name}
            {p.id === view.you && <span className="dim"> (you)</span>}
          </span>
          {p.is_host && <span className="tag">host</span>}
          {p.spectating && <span className="tag">spectating</span>}
          {p.ready && view.phase === "LOBBY" && <span className="tag ok">ready</span>}
          {p.conn === "DROPPED" && <span className="tag warn">reconnecting…</span>}
          <span className="score">{p.score}</span>
        </li>
      ))}
    </ul>
  );
}
