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
  const ready = name.trim().length > 0;

  return (
    <div className="shell center">
      <h1>anime trivia</h1>
      {error !== null && <div className="toast">{error}</div>}
      <label>
        your name
        <input
          value={name}
          maxLength={24}
          autoFocus
          onChange={(e) => setName(e.target.value)}
          placeholder="gojo_stan_99"
        />
      </label>
      <div className="join-row">
        <button disabled={!ready || connecting} onClick={() => onCreate(name.trim())}>
          create room
        </button>
        <span className="dim">or</span>
        <input
          value={code}
          maxLength={4}
          placeholder="CODE"
          className="code-input"
          onChange={(e) => setCode(e.target.value.toUpperCase())}
        />
        <button
          disabled={!ready || code.length !== 4 || connecting}
          onClick={() => onJoin(code, name.trim())}
        >
          join
        </button>
      </div>
    </div>
  );
}
