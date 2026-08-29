const KEYS = ["A", "B", "C", "D", "E", "F"];

export type TileState = "idle" | "picked" | "correct" | "wrong" | "faded";

interface Props {
  index: number;
  label: string;
  state: TileState;
  disabled: boolean;
  onPick: () => void;
}

/**
 * One answer. State is carried by three things at once — border colour, the
 * filled key badge, and a glyph — so "correct" and "wrong" survive colour
 * blindness, a greyscale screenshot, and a glance from across the room.
 */
export function AnswerTile({ index, label, state, disabled, onPick }: Props) {
  const mark = state === "correct" ? "✓" : state === "wrong" ? "✕" : null;
  const suffix =
    state === "correct"
      ? " (correct answer)"
      : state === "wrong"
        ? " (your answer, incorrect)"
        : state === "picked"
          ? " (your answer)"
          : "";

  return (
    <button
      type="button"
      className={`tile${state === "idle" ? "" : ` ${state}`}`}
      disabled={disabled}
      onClick={onPick}
      aria-label={`${KEYS[index] ?? index + 1}. ${label}${suffix}`}
    >
      <span className="key" aria-hidden="true">
        {KEYS[index] ?? index + 1}
      </span>
      <span>{label}</span>
      {mark !== null && (
        <span className="mark" aria-hidden="true">
          {mark}
        </span>
      )}
    </button>
  );
}
