import { useEffect, useState } from "react";

import type { RoomSocket } from "../net/socket";
import type { RoomView } from "../store/room";
import { Leaderboard } from "./Leaderboard";

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
  const [modesFailed, setModesFailed] = useState(false);

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
        if (!cancelled) setModesFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <div className="center">
        <div className="label">waiting room</div>
        <p className="muted">
          {view.players.length === 1
            ? "Share the room code — the game needs at least one more player."
            : isHost
              ? "Pick a mode and start whenever everyone's in."
              : "The host will start the game."}
        </p>
      </div>

      <Leaderboard view={view} />

      <div>
        <div className="label" style={{ marginBottom: "8px" }}>
          game mode
        </div>
        {modesFailed ? (
          <p className="muted">Couldn&apos;t load modes — is the server running?</p>
        ) : (
          <div className="modes" role="radiogroup" aria-label="game mode">
            {modes.map((mode) => (
              <label
                key={mode.id}
                className={`mode${modeId === mode.id ? " picked" : ""}`}
              >
                <input
                  type="radio"
                  name="mode"
                  checked={modeId === mode.id}
                  disabled={!isHost}
                  onChange={() => setModeId(mode.id)}
                />
                <span className="name">{mode.name}</span>
                <span className="meta">
                  {mode.question_count} × {mode.seconds_per_q}s
                </span>
                {mode.blurb !== null && <span className="blurb">{mode.blurb}</span>}
              </label>
            ))}
          </div>
        )}
      </div>

      <div className="actions">
        <button
          type="button"
          className={`ghost${me?.ready === true ? " on" : ""}`}
          onClick={() => socket.send({ type: "set_ready", ready: !(me?.ready ?? false) })}
        >
          {me?.ready === true ? "ready ✓" : "i'm ready"}
        </button>
        {isHost && (
          <button
            type="button"
            disabled={modeId === null}
            onClick={() => modeId !== null && socket.send({ type: "start_game", mode_id: modeId })}
          >
            start game
          </button>
        )}
      </div>
    </>
  );
}
