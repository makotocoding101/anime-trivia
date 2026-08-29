import { isUrgent, ringDashOffset, ringFraction, displaySeconds } from "../ui/ring";

const RADIUS = 34;
const STROKE = 6;
const SIZE = (RADIUS + STROKE) * 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

interface Props {
  /** Seconds left, already derived from the server deadline + clock offset. */
  remaining: number;
  /** Round length, for the arc's fraction. Null while it is unknown. */
  total: number | null;
}

/**
 * The countdown: the only element on screen that moves continuously, which is
 * exactly why nothing else does. Driven by rAF through useCountdown, so the
 * arc gets no CSS transition — a transition would fight the frame loop. Only
 * the colour transitions, once, when the round turns urgent.
 */
export function TimerRing({ remaining, total }: Props) {
  const fraction = ringFraction(remaining, total);
  const urgent = isUrgent(fraction);
  const seconds = displaySeconds(remaining);

  return (
    <div
      className={`ring${urgent ? " urgent" : ""}`}
      role="timer"
      aria-live="off"
      aria-label={`${seconds} seconds left`}
    >
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true" focusable="false">
        <circle
          className="track"
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          strokeWidth={STROKE}
        />
        <circle
          className="bar"
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          strokeWidth={STROKE}
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={ringDashOffset(fraction, CIRCUMFERENCE)}
        />
      </svg>
      <div className="count">{seconds}</div>
    </div>
  );
}
