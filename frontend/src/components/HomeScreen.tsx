import { useEffect, useState, type CSSProperties } from "react";

import {
  fetchModes,
  fetchProfile,
  fetchStats,
  saveName,
  type Mode,
  type Stats,
  type Wallet,
} from "../net/menu";
import { avatarFor, hueForIndex, iconForMode } from "../ui/identity";

interface Props {
  name: string;
  onNameChange: (name: string) => void;
  wallet: Wallet | null;
  /** Start a room already set to this mode; the host can still change it. */
  onPlay: (modeId: number) => void;
  onJoin: (code: string) => void;
  onOpenRankings: () => void;
  onOpenProfile: () => void;
  connecting: boolean;
  error: string | null;
}

export function HomeScreen({
  name,
  onNameChange,
  wallet,
  onPlay,
  onJoin,
  onOpenRankings,
  onOpenProfile,
  connecting,
  error,
}: Props) {
  const [modes, setModes] = useState<Mode[]>([]);
  const [modesFailed, setModesFailed] = useState(false);
  const [stats, setStats] = useState<Stats | null>(null);
  const [code, setCode] = useState("");
  const [draft, setDraft] = useState(name);
  const [editing, setEditing] = useState(name === "");

  useEffect(() => {
    let cancelled = false;
    fetchModes()
      .then((rows) => !cancelled && setModes(rows))
      .catch(() => !cancelled && setModesFailed(true));
    return () => {
      cancelled = true;
    };
  }, []);

  // The live counter, polled rather than pushed: it is menu furniture, and
  // opening a websocket just to watch a number would cost a room slot.
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchStats()
        .then((s) => !cancelled && setStats(s))
        .catch(() => undefined); // a stale count is better than an error box
    };
    tick();
    const timer = window.setInterval(tick, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const named = name.trim().length > 0;

  const commitName = () => {
    const trimmed = draft.trim();
    if (trimmed === "") return;
    saveName(trimmed);
    onNameChange(trimmed);
    setEditing(false);
  };

  return (
    <div className="frame">
      <div className="homebar">
        <span className="spacer" />
        <span className="coin">
          <span aria-hidden="true">🪙</span>
          <span className="sr-only">your coins: </span>
          {wallet?.coins ?? 0}
        </span>
      </div>

      <div className="hero">
        <h1>OtaKizu</h1>
        <div className="tagline">The real-time anime arena</div>
        <p>The Strongest Weeb in History vs The Strongest Weeb of Today</p>
      </div>

      {error !== null && (
        <div className="notice bad" role="alert">
          {error}
        </div>
      )}

      {editing ? (
        <form
          className="namebar"
          onSubmit={(e) => {
            e.preventDefault();
            commitName();
          }}
        >
          <label className="label" htmlFor="name">
            what should we call you?
          </label>
          <div className="code-row">
            <input
              id="name"
              value={draft}
              maxLength={24}
              autoFocus
              autoComplete="off"
              placeholder="your name"
              onChange={(e) => setDraft(e.target.value)}
            />
            <button type="submit" disabled={draft.trim() === ""}>
              continue
            </button>
          </div>
        </form>
      ) : (
        <div className="whoami wide">
          <span className="face" aria-hidden="true">
            {avatarFor(name)}
          </span>
          <span className="who">
            <span className="label">welcome back,</span>
            <b>{name}</b>
          </span>
          <span className="rankchip">
            <span className="label">rank</span>
            <b>{wallet?.tier ?? "ROOKIE"}</b>
          </span>
          <span className="spacer" />
          <button
            type="button"
            className="ghost"
            onClick={() => {
              setDraft(name);
              setEditing(true);
            }}
          >
            not you?
          </button>
        </div>
      )}

      <div className="stage">
        <div className="panel">
          <div className="panel-head">
            <span className="label">⚡ pick a genre</span>
            <span className="spacer" />
            {!named && <span className="label">name first</span>}
          </div>

          {modesFailed ? (
            <p className="muted">Couldn&apos;t load genres — is the server running?</p>
          ) : (
            <div className="genres">
              {modes.map((mode, index) => (
                <button
                  key={mode.id}
                  type="button"
                  className="genre"
                  style={{ "--h": hueForIndex(index) } as CSSProperties}
                  disabled={!named || connecting}
                  title={mode.blurb ?? undefined}
                  onClick={() => onPlay(mode.id)}
                >
                  <span className="icon" aria-hidden="true">
                    {iconForMode(mode.slug, mode.name)}
                  </span>
                  <span className="name">{mode.name}</span>
                  <span className="meta">
                    {mode.question_count} × {mode.seconds_per_q}s
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="stack">
          <div className="bigcard">
            <span className="glyph" aria-hidden="true">
              👥
            </span>
            <span className="title">MULTIPLAYER</span>
            <span className="tally">
              <b>{stats?.live_games ?? 0}</b> Live games
            </span>

            <form
              className="code-row"
              onSubmit={(e) => {
                e.preventDefault();
                if (named && code.length === 4 && !connecting) onJoin(code);
              }}
            >
              <input
                value={code}
                maxLength={4}
                placeholder="CODE"
                aria-label="room code"
                autoComplete="off"
                onChange={(e) =>
                  setCode(e.target.value.toUpperCase().replace(/[^A-Z]/g, ""))
                }
              />
              <button
                type="submit"
                className="ghost"
                disabled={!named || code.length !== 4 || connecting}
              >
                join
              </button>
            </form>
          </div>

          <div className="navpair">
            <button type="button" className="navcard" onClick={onOpenRankings}>
              <span className="glyph" aria-hidden="true">
                🏆
              </span>
              RANKINGS
            </button>
            <button type="button" className="navcard" onClick={onOpenProfile}>
              <span className="glyph" aria-hidden="true">
                🧿
              </span>
              PROFILE
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Load the wallet for a name, falling back to null on any failure — the
 * menu renders zeroes rather than an error, because a missing wallet and a
 * brand-new one look the same to a player. */
export async function loadWallet(name: string): Promise<Wallet | null> {
  if (name.trim() === "") return null;
  try {
    return await fetchProfile(name);
  } catch {
    return null;
  }
}
