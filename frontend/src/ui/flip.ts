/**
 * FLIP (First, Last, Invert, Play) for the leaderboard.
 *
 * Rank changes are the reward moment of a round, and they are unreadable as
 * an instant jump — the eye cannot tell who overtook whom. Measuring before
 * and after, then animating the inverse, makes the movement legible.
 *
 * The measuring and animating live in the hook; this module owns the pure
 * arithmetic so the tricky part is testable without a DOM.
 */

/**
 * Per-key vertical distance to travel back to, in pixels: positive means the
 * row moved *down* the page and should animate from above.
 *
 * Only keys present in both maps move — a row that just appeared has no
 * "before" to invert from, and a row that vanished has nothing to animate.
 * Sub-pixel noise (layout rounding, scrollbar reflow) is discarded so the
 * browser is not handed a pile of no-op animations every render.
 */
export function flipDeltas(
  before: ReadonlyMap<string, number>,
  after: ReadonlyMap<string, number>,
  threshold = 1,
): Map<string, number> {
  const deltas = new Map<string, number>();
  after.forEach((top, key) => {
    const previousTop = before.get(key);
    if (previousTop === undefined) return;
    const delta = previousTop - top;
    if (Math.abs(delta) < threshold) return;
    deltas.set(key, delta);
  });
  return deltas;
}
