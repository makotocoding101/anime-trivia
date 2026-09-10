import { useEffect, useState, type CSSProperties } from "react";

import { apiUrl } from "../net/base";
import type { RoomSocket } from "../net/socket";
import type { RoomView } from "../store/room";
import { hueForIndex, iconForMode } from "../ui/identity";
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
  /** Chosen on the home screen before the room existed. Only a starting
   * point — the host can still change it here. */
  initialModeId?: number | null;
}

export function LobbyScreen({ view, socket, initialModeId = null }: Props) {
  const me = view.players.find((p) => p.id === view.you);
  const isHost = me?.is_host ?? false;
  const [modes, setModes] = useState<Mode[]>([]);
  const [modeId, setModeId] = useState<number | null>(initialModeId);
  const [modesFailed, setModesFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(apiUrl("/api/modes"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((rows: Mode[]) => {
        if (cancelled) return;
        setModes(rows);
        // Keep the genre picked on the home screen if it is still a real
        // mode; otherwise fall back to the first one rather than leaving the
        // host with nothing selected.
        setModeId((current) =>
          current !== null && rows.some((r) => r.id === current)
            ? current
            : (rows[0]?.id ?? null),
        );
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

      <div className="stage">
        <div className="panel">
          <div className="panel-head">
            <span className="label">⚡ game mode</span>
            <span className="spacer" />
            {!isHost && modes.length > 0 && (
              <span className="label">host&apos;s pick</span>
            )}
          </div>

          {modesFailed ? (
            <p className="muted">Couldn&apos;t load modes — is the server running?</p>
          ) : (
            <div className="modes" role="radiogroup" aria-label="game mode">
              {modes.map((mode, index) => (
                <label
                  key={mode.id}
                  className={`mode${modeId === mode.id ? " picked" : ""}`}
                  /* Hue by position, not by id: modes are rows, so the palette
                     cannot be hard-coded per mode without a deploy. */
                  style={{ "--h": hueForIndex(index) } as CSSProperties}
                >
                  <input
                    type="radio"
                    name="mode"
                    checked={modeId === mode.id}
                    disabled={!isHost}
                    onChange={() => setModeId(mode.id)}
                  />
                  <span className="icon" aria-hidden="true">
                    {iconForMode(mode.slug, mode.name)}
                  </span>
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

        <div className="stack">
          <div className="panel">
            <div className="panel-head">
              <span className="label">players</span>
              <span className="spacer" />
              <span className="live">
                {view.players.length} in room
              </span>
            </div>
            <Leaderboard view={view} />
          </div>

          <div className="actions">
            <button
              type="button"
              className={`ghost${me?.ready === true ? " on" : ""}`}
              onClick={() =>
                socket.send({ type: "set_ready", ready: !(me?.ready ?? false) })
              }
            >
              {me?.ready === true ? "ready ✓" : "i'm ready"}
            </button>
            {isHost && (
              <button
                type="button"
                disabled={modeId === null}
                onClick={() =>
                  modeId !== null && socket.send({ type: "start_game", mode_id: modeId })
                }
              >
                start game
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
