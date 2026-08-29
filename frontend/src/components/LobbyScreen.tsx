import { useEffect, useState } from "react";

import type { RoomSocket } from "../net/socket";
import type { RoomView } from "../store/room";
import { PlayerList } from "./PlayerList";

interface Mode {
  id: number;
  slug: string;
  name: string;
  blurb: string | null;
  question_count: number;
  seconds_per_q: number;
}

interface Props {
  view: RoomView;
  socket: RoomSocket;
}

export function LobbyScreen({ view, socket }: Props) {
  const me = view.players.find((p) => p.id === view.you);
  const isHost = me?.is_host ?? false;
  const [modes, setModes] = useState<Mode[]>([]);
  const [modeId, setModeId] = useState<number | null>(null);
  const [modesError, setModesError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/modes")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((rows: Mode[]) => {
        if (cancelled) return;
        setModes(rows);
        setModeId((current) => current ?? rows[0]?.id ?? null);
      })
      .catch(() => {
        if (!cancelled) setModesError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <h2>lobby</h2>
      <p className="dim">share the room code; the host picks a mode and starts.</p>
      <PlayerList view={view} />

      <h3 className="dim">game mode</h3>
      {modesError && <p className="dim">could not load modes — is the server up?</p>}
      <ul className="modes">
        {modes.map((mode) => (
          <li key={mode.id}>
            <label className={`mode${modeId === mode.id ? " picked" : ""}`}>
              <input
                type="radio"
                name="mode"
                checked={modeId === mode.id}
                disabled={!isHost}
                onChange={() => setModeId(mode.id)}
              />
              <span className="mode-name">{mode.name}</span>
              <span className="dim">
                {mode.question_count} questions · {mode.seconds_per_q}s each
              </span>
              {mode.blurb !== null && <span className="mode-blurb dim">{mode.blurb}</span>}
            </label>
          </li>
        ))}
      </ul>

      <div className="actions">
        <button
          className="ghost"
          onClick={() => socket.send({ type: "set_ready", ready: !(me?.ready ?? false) })}
        >
          {me?.ready ? "unready" : "ready"}
        </button>
        {isHost ? (
          <button
            disabled={modeId === null}
            onClick={() => modeId !== null && socket.send({ type: "start_game", mode_id: modeId })}
          >
            start game
          </button>
        ) : (
          <span className="dim">waiting for the host…</span>
        )}
      </div>
    </main>
  );
}
