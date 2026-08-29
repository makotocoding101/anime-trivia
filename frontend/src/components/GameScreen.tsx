import { useCountdown } from "../hooks/useCountdown";
import type { RoomSocket } from "../net/socket";
import type { RoomView } from "../store/room";
import { useRoomStore } from "../store/room";
import { PlayerList } from "./PlayerList";

interface Props {
  view: RoomView;
  socket: RoomSocket;
}

export function GameScreen({ view, socket }: Props) {
  if (view.phase === "INTRO" || view.question === null) {
    return (
      <main className="center">
        <h2 className="round-title">
          round {view.roundIndex + 1}
          {view.questionCount > 0 && <span className="dim"> of {view.questionCount}</span>}
        </h2>
        <p className="dim">get ready…</p>
        <PlayerList view={view} />
      </main>
    );
  }
  if (view.phase === "REVEAL" && view.reveal !== null) {
    return <RevealBody view={view} />;
  }
  return <QuestionBody view={view} socket={socket} />;
}

function QuestionBody({ view, socket }: Props) {
  const setPendingAnswer = useRoomStore((s) => s.setPendingAnswer);
  const question = view.question!;
  const seconds = useCountdown(question.endsAt, socket.clock);
  const me = view.players.find((p) => p.id === view.you);
  const spectating = me?.spectating ?? false;
  const locked = question.yourAnswer !== null || view.pendingAnswer !== null;
  const expired = seconds <= 0;

  const pick = (optionId: number) => {
    if (locked || expired || spectating) return;
    setPendingAnswer(optionId);
    socket.submitAnswer(question.roundSeq, optionId);
  };

  const chosen = question.yourAnswer ?? view.pendingAnswer;

  return (
    <main>
      <div className="question-head">
        <span className="dim">
          Q{question.roundIndex + 1}/{view.questionCount}
        </span>
        <span className={`timer${seconds <= 5 ? " low" : ""}`}>{Math.ceil(seconds)}</span>
      </div>
      <h2 className="prompt">{question.prompt}</h2>
      <div className="options">
        {question.options.map((o) => (
          <button
            key={o.id}
            className={`option${chosen === o.id ? " picked" : ""}`}
            disabled={locked || expired || spectating}
            onClick={() => pick(o.id)}
          >
            {o.label}
          </button>
        ))}
      </div>
      <p className="dim">
        {spectating
          ? "you join next round — spectating this one"
          : locked
            ? "locked in."
            : expired
              ? "time!"
              : "pick an answer"}
        {view.progress !== null &&
          ` · ${view.progress.answered} of ${view.progress.total} answered`}
      </p>
    </main>
  );
}

function RevealBody({ view }: { view: RoomView }) {
  const question = view.question!;
  const reveal = view.reveal!;
  const mine = reveal.results.find((r) => r.player_id === view.you);

  return (
    <main>
      <h2 className="prompt">{question.prompt}</h2>
      <div className="options">
        {question.options.map((o) => {
          const isCorrect = o.id === reveal.correctOptionId;
          const wasMine = mine?.option_id === o.id;
          return (
            <div
              key={o.id}
              className={`option static${isCorrect ? " correct" : wasMine ? " wrong" : ""}`}
            >
              {o.label}
              {isCorrect && " ✓"}
            </div>
          );
        })}
      </div>
      <ul className="deltas">
        {reveal.results.map((r) => {
          const name = view.players.find((p) => p.id === r.player_id)?.name ?? "?";
          return (
            <li key={r.player_id} className={r.correct ? "ok" : "dim"}>
              {name}: {r.correct ? `+${r.delta}` : r.option_id === null ? "no answer" : "+0"}
              {r.streak >= 3 && ` · streak ${r.streak}`}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
