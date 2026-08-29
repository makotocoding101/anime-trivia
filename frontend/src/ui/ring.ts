/**
 * Timer-ring geometry. Pure, so the arithmetic behind the one continuously
 * animating element on screen is unit-tested rather than eyeballed.
 */

/** Fraction of the round still remaining, clamped to [0, 1]. */
export function ringFraction(remainingSeconds: number, totalSeconds: number | null): number {
  if (totalSeconds === null || totalSeconds <= 0) return 0;
  return Math.min(1, Math.max(0, remainingSeconds / totalSeconds));
}

/** stroke-dashoffset for a ring drawn with dasharray === circumference.
 * Full time left draws the whole ring (offset 0); no time left erases it. */
export function ringDashOffset(fraction: number, circumference: number): number {
  return circumference * (1 - Math.min(1, Math.max(0, fraction)));
}

/** Below this share of the round, the ring shifts colour — once, not
 * continuously, so the change reads as an event rather than a gradient. */
export const URGENT_AT = 0.25;

export function isUrgent(fraction: number): boolean {
  return fraction <= URGENT_AT;
}

/** What the countdown reads. Ceil so a live round never shows "0". */
export function displaySeconds(remainingSeconds: number): number {
  return Math.max(0, Math.ceil(remainingSeconds));
}
