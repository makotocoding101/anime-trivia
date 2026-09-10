/**
 * The room socket: joins, pumps frames to the store, runs the clock
 * handshake, and — from M5 — survives its own death. On an unexpected
 * close it reconnects with exponential backoff and full jitter, presenting
 * the stored resume token so the server reattaches the same seat and sends
 * a fresh snapshot (§08, R-11). Pongs and session frames are consumed here;
 * neither reaches the reducer.
 */

import type { ClientFrame, ServerFrame } from "../types/wire";

import { apiUrl, wsUrl } from "./base";
import { ServerClock } from "./clock";
import { clearSession, defaultStorage, loadSession, saveSession } from "./session";

export type SocketStatus = "connecting" | "open" | "reconnecting" | "closed";

const HANDSHAKE_PINGS = 3;

/** Close codes that mean "do not come back": left, kicked, no such room,
 * room swept. Everything else is presumed transient and retried. */
// 4403 is a refused name: retrying cannot change the answer, so it belongs
// here with the other closes that must not be reconnected.
const FATAL_CLOSE_CODES = new Set([4000, 4002, 4004, 4403, 4404]);

/** Close reasons that mean the stored token is dead — clear it and the next
 * attempt joins fresh (new seat) instead of retrying a doomed resume. */
const TOKEN_REJECTIONS = new Set(["bad_token", "seat_expired", "unknown_player"]);

const BACKOFF_BASE_MS = 500;
const BACKOFF_CAP_MS = 10_000;

/**
 * Full jitter over an exponentially growing cap. The jitter is the point:
 * after a server restart every client in every room retries at once, and
 * the synchronized stampede is what actually takes a service down (§08).
 */
export function backoffDelayMs(attempt: number, rand: () => number = Math.random): number {
  const cap = Math.min(BACKOFF_BASE_MS * 2 ** attempt, BACKOFF_CAP_MS);
  return rand() * cap;
}

export interface RoomSocketHandlers {
  onFrame: (frame: ServerFrame) => void;
  onStatus: (status: SocketStatus) => void;
  /** A close this socket will not retry, with the server's reason — the UI
   * needs it to say why, since "closed" alone reads as a network blip. */
  onFatal?: (reason: string) => void;
}

export class RoomSocket {
  readonly clock = new ServerClock();
  private ws: WebSocket | null = null;
  private closedByUs = false;
  private attempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private storage = defaultStorage();

  constructor(
    private readonly code: string,
    private readonly name: string,
    private readonly handlers: RoomSocketHandlers,
    /** Proof of name ownership, when the name has been claimed. Sent on
     * every join, including reconnects: the server may have been restarted
     * and will re-check. */
    private readonly nameToken: string | null = null,
  ) {}

  connect(): void {
    this.open("connecting");
  }

  private open(status: SocketStatus): void {
    this.handlers.onStatus(status);
    this.ws = new WebSocket(wsUrl(`/ws/rooms/${this.code}`));

    this.ws.onopen = () => {
      this.attempt = 0;
      const saved = loadSession(this.storage, this.code);
      this.send({
        type: "join",
        name: this.name,
        ...(saved !== null ? { token: saved.token } : {}),
        ...(this.nameToken !== null ? { name_token: this.nameToken } : {}),
      });
      for (let i = 0; i < HANDSHAKE_PINGS; i++) {
        // Staggered so the samples see independent network moments; rerun on
        // every (re)connect since the offset may have changed with the path.
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
      if (frame.type === "session") {
        saveSession(this.storage, this.code, {
          playerId: frame.data.player_id,
          token: frame.data.resume_token,
          name: this.name,
        });
        return;
      }
      this.handlers.onFrame(frame);
    };

    this.ws.onclose = (event: CloseEvent) => {
      if (this.closedByUs) return;
      if (TOKEN_REJECTIONS.has(event.reason)) {
        clearSession(this.storage, this.code); // next attempt joins fresh
      }
      if (FATAL_CLOSE_CODES.has(event.code)) {
        this.handlers.onStatus("closed");
        this.handlers.onFatal?.(event.reason);
        return;
      }
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    this.handlers.onStatus("reconnecting");
    this.retryTimer = setTimeout(
      () => this.open("reconnecting"),
      backoffDelayMs(this.attempt++),
    );
  }

  /** R-11's "on a gap, ask for a snapshot": with resume tokens, reconnecting
   * IS asking. Bouncing the socket triggers the resume path. */
  resync(): void {
    this.ws?.close();
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

  /** Deliberate exit: no retry, and the seat's session is forgotten. */
  close(): void {
    this.closedByUs = true;
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    clearSession(this.storage, this.code);
    this.ws?.close();
  }
}

export async function createRoom(): Promise<string> {
  const response = await fetch(apiUrl("/api/rooms"), { method: "POST" });
  if (!response.ok) throw new Error(`room creation failed: ${response.status}`);
  const body = (await response.json()) as { code: string };
  return body.code;
}
