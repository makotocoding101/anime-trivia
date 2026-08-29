import { describe, expect, it } from "vitest";

import { ServerClock } from "./clock";

describe("ServerClock", () => {
  it("estimates offset from the RTT midpoint", () => {
    const clock = new ServerClock();
    // t0=1000, server stamped 1560, received t1=1100 -> rtt 100,
    // midpoint 1050, offset = 1560 - 1050 = 510.
    clock.onPong(1000, 1560, 1100);
    expect(clock.offsetMs).toBe(510);
  });

  it("keeps the lowest-RTT sample, not the latest", () => {
    const clock = new ServerClock();
    clock.onPong(1000, 1560, 1100); // rtt 100 -> offset 510
    clock.onPong(2000, 2540, 2040); // rtt 40  -> offset 520 (cleaner sample)
    clock.onPong(3000, 3900, 3400); // rtt 400 -> noisy; ignored
    expect(clock.offsetMs).toBe(520);
    expect(clock.sampleCount).toBe(3);
  });

  it("defaults to zero offset before any pong lands", () => {
    const clock = new ServerClock();
    const endsAt = Date.now() + 5000;
    const remaining = clock.remainingMs(endsAt);
    expect(remaining).toBeGreaterThan(4900);
    expect(remaining).toBeLessThanOrEqual(5000);
  });
});
