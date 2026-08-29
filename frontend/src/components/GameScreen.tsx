import { useCountdown } from "../hooks/useCountdown";
import type { RoomSocket } from "../net/socket";
import type { RoomView } from "../store/room";
import { useRoomStore } from "../store/room";
import { AnswerTile, type TileState } from "./AnswerTile";
import { Leaderboard } from "./Leaderboard";
import { TimerRing } from "./TimerRing";

interface Props {
  view: RoomView;
  socket: RoomSocket;
}

export function GameScreen({ view, socket }: Props) {
  if (view.question === null) return <IntroBody view={view} />;
  if (view.phase === "REVEAL" && view.reveal !== null) {
    return <RevealBody view={view} />;
  }
  return <QuestionBody view={view} socket={socket} />;
}

/** Round pips: how far through the deck, at a glance. */
function Pips({ index, total }: { index: number; total: number }) {
  if (total <= 0 || total > 20) return null;
  return (
    <span className="pips" aria-hidden="true">
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={`pip${i < index ? " done" : i === index ? " now" : ""}`}
        />
      ))}
    </span>
  );
}

function RoundBar({ view, remaining }: { view: RoomView; remaining: number | null }) {
  return (
    <div className="roundbar">
      <div>
        <div className="label">
          round {view.roundIndex + 1}
          {view.questionCount > 0 && ` of ${view.questionCount}`}
        </div>
        <Pips index={view.roundIndex} total={view.questionCount} />
      </div>
      {remaining !== null && (
        <TimerRing remaining={remaining} total={view.question?.seconds ?? null} />
      )}
    </div>
  );
}

function IntroBody({ view }: { view: RoomView }) {
  return (
    <>
      <div className="center intro">
        <div className="label">get ready</div>
        <div className="round">
          Round {view.roundIndex + 1}
          {view.questionCount > 0 && (
            <span className="muted"> / {view.questionCount}</span>
          )}
        </div>
      </div>
      <Leaderboard view={view} />
    </>
  );
}

function QuestionBody({ view, socket }: Props) {
  const setPendingAnswer = useRoomStore((s) => s.setPendingAnswer);
  const question = view.question;
  const remaining = useCountdown(question?.endsAt ?? null, socket.clock);

  if (question === null) return null;

  const me = view.players.find((p) => p.id === view.you);
  const spectating = me?.spectating ?? false;
  const chosen = question.yourAnswer ?? view.pendingAnswer;
  const locked = chosen !== null;
  const expired = remaining <= 0;
  const canAnswer = !locked && !expired && !spectating;

  const pick = (optionId: number) => {
    if (!canAnswer) return;
    setPendingAnswer(optionId);
    socket.submitAnswer(question.roundSeq, optionId);
  };

  const status = spectating
    ? "you join next round — watching this one"
    : locked
      ? "locked in"
      : expired
        ? "time!"
        : "pick an answer";

  return (
    <>
      <RoundBar view={view} remaining={remaining} />
      <h2 className="prompt">{question.prompt}</h2>

      <div className="tiles">
        {question.options.map((option, index) => (
          <AnswerTile
            key={option.id}
            index={index}
            label={option.label}
            state={chosen === option.id ? "picked" : "idle"}
            disabled={!canAnswer}
            onPick={() => pick(option.id)}
          />
        ))}
      </div>

      <div className="progress">
        <AnswerProgress progress={view.progress} />
        <span className="status" style={{ marginLeft: "auto" }}>
          {status}
        </span>
      </div>

      <Leaderboard view={view} />
    </>
  );
}

/** "4 of 6 locked in" — safe to show and good tension; it reveals who has
 * answered, never what they answered (spec §05). */
function AnswerProgress({ progress }: { progress: RoomView["progress"] }) {
  if (progress === null) return null;
  const { answered, total } = progress;
  return (
    <>
      <span className="dots" aria-hidden="true">
        {Array.from({ length: total }, (_, i) => (
          <i key={i} className={i < answered ? "in" : ""} />
        ))}
      </span>
      <span>
        {answered} of {total} locked in
      </span>
    </>
  );
}

function RevealBody({ view }: { view: RoomView }) {
  const question = view.question;
  const reveal = view.reveal;
  if (question === null || reveal === null) return null;

  const mine = reveal.results.find((r) => r.player_id === view.you);
  const deltas = new Map(reveal.results.map((r) => [r.player_id, r.delta]));

  const tileState = (optionId: number): TileState => {
    if (optionId === reveal.correctOptionId) return "correct";
    if (mine?.option_id === optionId) return "wrong";
    return "faded";
  };

  return (
    <>
      <RoundBar view={view} remaining={null} />
      <h2 className="prompt">{question.prompt}</h2>

      <div className="tiles">
        {question.options.map((option, index) => (
          <AnswerTile
            key={option.id}
            index={index}
            label={option.label}
            state={tileState(option.id)}
            disabled
            onPick={() => undefined}
          />
        ))}
      </div>

      <div className="progress">
        <span className="status">
          {mine === undefined
            ? "round over"
            : mine.correct
              ? `correct — +${mine.delta}${mine.streak >= 3 ? ` · ${mine.streak} in a row` : ""}`
              : mine.option_id === null
                ? "no answer"
                : "not this time"}
        </span>
      </div>

      <Leaderboard view={view} deltas={deltas} />
    </>
  );
}
