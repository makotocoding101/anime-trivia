/**
 * The room socket: joins, pumps frames to the store, runs the clock
 * handshake. Pongs are consumed here — they calibrate the ServerClock and
 * never reach the reducer.
 *
 * Reconnect-with-resume arrives in M5 (it needs the signed token); for now a
 * dead socket surfaces as status "closed" and the user rejoins.
 */

import type { ClientFrame, ServerFrame } from "../types/wire";
import { ServerClock } from "./clock";

export type SocketStatus = "connecting" | "open" | "closed";

const HANDSHAKE_PINGS = 3;

export interface RoomSocketHandlers {
  onFrame: (frame: ServerFrame) => void;
  onStatus: (status: SocketStatus) => void;
}

export class RoomSocket {
  readonly clock = new ServerClock();
  private ws: WebSocket | null = null;
  private closedByUs = false;

  constructor(
    private readonly code: string,
    private readonly name: string,
    private readonly handlers: RoomSocketHandlers,
  ) {}

  connect(): void {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    this.handlers.onStatus("connecting");
    this.ws = new WebSocket(`${proto}//${location.host}/ws/rooms/${this.code}`);

    this.ws.onopen = () => {
      this.send({ type: "join", name: this.name });
      for (let i = 0; i < HANDSHAKE_PINGS; i++) {
        // Slightly staggered so the samples see independent network moments.
        setTimeout(() => this.send({ type: "ping", t0: Date.now() }), i * 120);
      }
      this.handlers.onStatus("open");
    };

    this.ws.onmessage = (msg: MessageEvent<string>) => {
      const frame = JSON.parse(msg.data) as ServerFrame;
      if (frame.type === "pong") {
        this.clock.onPong(frame.data.t0, frame.data.ts);
        return;
      }
      this.handlers.onFrame(frame);
    };

    this.ws.onclose = () => {
      if (!this.closedByUs) this.handlers.onStatus("closed");
    };
  }

  send(frame: ClientFrame): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(frame));
    }
  }

  submitAnswer(roundSeq: number, optionId: number): void {
    // cid makes a retry after reconnect recognisable rather than double-counted (R-01).
    this.send({
      type: "submit_answer",
      round_seq: roundSeq,
      option_id: optionId,
      cid: `c-${roundSeq}-${optionId}`,
    });
  }

  close(): void {
    this.closedByUs = true;
    this.ws?.close();
  }
}

export async function createRoom(): Promise<string> {
  const response = await fetch("/api/rooms", { method: "POST" });
  if (!response.ok) throw new Error(`room creation failed: ${response.status}`);
  const body = (await response.json()) as { code: string };
  return body.code;
}
