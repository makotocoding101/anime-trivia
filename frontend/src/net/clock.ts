/**
 * Client-side half of the clock-offset handshake (spec §03).
 *
 * The client cannot trust its own wall clock, so it measures the offset to
 * the server's: three ping/pong round trips, keep the sample with the lowest
 * RTT (least queueing noise). Everything that renders a countdown asks this
 * class what server-time "now" is.
 */

export class ServerClock {
  private best: { rtt: number; offset: number } | null = null;
  private samples = 0;

  /** offset = server_ts - midpoint(t0, t1); assumes symmetric latency. */
  onPong(t0: number, serverTs: number, t1: number = Date.now()): void {
    const rtt = t1 - t0;
    const offset = serverTs - (t0 + t1) / 2;
    this.samples += 1;
    if (this.best === null || rtt < this.best.rtt) {
      this.best = { rtt, offset };
    }
  }

  get offsetMs(): number {
    return this.best?.offset ?? 0;
  }

  get sampleCount(): number {
    return this.samples;
  }

  /** Server wall-clock now, as best we can estimate. */
  serverNow(): number {
    return Date.now() + this.offsetMs;
  }

  /** Milliseconds until a server-side wall-clock instant; can be negative. */
  remainingMs(endsAtServerMs: number): number {
    return endsAtServerMs - this.serverNow();
  }
}
