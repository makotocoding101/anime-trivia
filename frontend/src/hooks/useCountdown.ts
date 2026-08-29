/**
 * The one thing the client computes (spec §09): a countdown rendered from
 * the server's ends_at and the measured clock offset, on animation frames.
 * It is a display of server state, not a source of truth — the server
 * enforces the deadline independently, against its own clock.
 */

import { useEffect, useState } from "react";

import type { ServerClock } from "../net/clock";

export function useCountdown(endsAt: number | null, clock: ServerClock | null): number {
  const [remainingMs, setRemainingMs] = useState<number>(() =>
    endsAt !== null && clock !== null ? clock.remainingMs(endsAt) : 0,
  );

  useEffect(() => {
    if (endsAt === null || clock === null) {
      setRemainingMs(0);
      return;
    }
    let raf = 0;
    const tick = () => {
      const left = clock.remainingMs(endsAt);
      setRemainingMs(left);
      if (left > 0) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [endsAt, clock]);

  return Math.max(0, remainingMs / 1000);
}
