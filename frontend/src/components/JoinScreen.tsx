import { useState } from "react";

interface Props {
  onJoin: (code: string, name: string) => void;
  onCreate: (name: string) => void;
  connecting: boolean;
  error: string | null;
}

export function JoinScreen({ onJoin, onCreate, connecting, error }: Props) {
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const named = name.trim().length > 0;

  return (
    <div className="frame">
      <div className="hero">
        <h1>
          kagen<span>.</span>
        </h1>
        <p>
          Real-time anime trivia. Same question, same clock, everyone at once.
        </p>
      </div>

      {error !== null && (
        <div className="notice bad" role="alert">
          {error}
        </div>
      )}

      <form
        className="card join-card"
        onSubmit={(e) => {
          e.preventDefault();
          if (named && !connecting) onCreate(name.trim());
        }}
      >
        <div className="field">
          <label className="label" htmlFor="name">
            your name
          </label>
          <input
            id="name"
            value={name}
            maxLength={24}
            autoFocus
            autoComplete="off"
            placeholder="what should we call you?"
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <button type="submit" disabled={!named || connecting}>
          {connecting ? "connecting…" : "create a room"}
        </button>

        <div className="divider">or join one</div>

        <div className="code-row">
          <input
            value={code}
            maxLength={4}
            placeholder="CODE"
            aria-label="room code"
            autoComplete="off"
            onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z]/g, ""))}
          />
          <button
            type="button"
            className="ghost"
            disabled={!named || code.length !== 4 || connecting}
            onClick={() => onJoin(code, name.trim())}
          >
            join
          </button>
        </div>
      </form>
    </div>
  );
}
