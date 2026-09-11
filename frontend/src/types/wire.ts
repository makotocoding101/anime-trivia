/**
 * TypeScript mirror of the wire protocol (backend/app/schemas/wire.py).
 *
 * seq semantics: broadcasts are dense; targeted frames (TARGETED_TYPES)
 * carry the current counter without incrementing and are exempt from the
 * store's stale/gap logic.
 */

export type PhaseName =
  | "LOBBY"
  | "INTRO"
  | "QUESTION_OPEN"
  | "LOCKED"
  | "REVEAL"
  | "GAME_OVER";

export type ConnName = "LIVE" | "DROPPED" | "GONE";

export interface PlayerRow {
  id: string;
  name: string;
  seat: number;
  score: number;
  streak: number;
  conn: ConnName;
  is_host: boolean;
  ready: boolean;
  spectating: boolean;
}

export interface OptionRow {
  id: number;
  label: string;
}

export interface ScoreRow {
  rank: number;
  player_id: string;
  name: string;
  score: number;
  streak: number;
  /** Coins this game paid out. Present only on the final standings — the
   * server omits it mid-game, because nothing has been earned yet. */
  coins_earned?: number;
}

export interface RevealResult {
  player_id: string;
  option_id: number | null;
  correct: boolean;
  delta: number;
  score: number;
  streak: number;
}

export interface SnapshotQuestion {
  id: number;
  kind: string;
  prompt: string;
  options: OptionRow[];
  media_ref: string | null;
  opened_at: number | null; // wall ms
  deadline: number | null; // wall ms
  your_answer: number | null;
}

export interface SnapshotReveal {
  round_index: number;
  question_id: number;
  correct_option_id: number;
  entries: Record<string, { option_id: number | null; elapsed_ms: number; points: number }>;
}

export interface SnapshotData {
  room_code: string;
  you: string;
  phase: PhaseName;
  round_seq: number;
  round_index: number;
  question_count: number;
  phase_deadline: number | null; // wall ms
  players: PlayerRow[];
  question: SnapshotQuestion | null;
  reveal: SnapshotReveal | null;
}

interface FrameBase {
  v: 1;
  seq: number;
  ts: number;
}

export type ServerFrame = FrameBase &
  (
    | { type: "snapshot"; data: SnapshotData }
    | { type: "phase_changed"; data: { phase: PhaseName; round_seq: number; round_index: number } }
    | {
        type: "question";
        data: {
          round_seq: number;
          round_index: number;
          question_count: number;
          question_id: number;
          kind: string;
          prompt: string;
          options: OptionRow[];
          media_ref: string | null;
          ends_at: number; // wall ms
          seconds: number;
        };
      }
    | { type: "answer_ack"; data: { round_seq: number; cid: string | null } }
    | { type: "answer_progress"; data: { answered: number; total: number } }
    | {
        type: "reveal";
        data: {
          round_seq: number;
          round_index: number;
          correct_option_id: number;
          results: RevealResult[];
        };
      }
    | { type: "scoreboard"; data: { rows: ScoreRow[] } }
    | {
        type: "player_joined";
        data: { player_id: string; name: string; seat: number; spectating: boolean };
      }
    | { type: "player_left"; data: { player_id: string; reason: string } }
    | { type: "player_reconnected"; data: { player_id: string } }
    | { type: "player_ready"; data: { player_id: string; ready: boolean } }
    | { type: "host_changed"; data: { player_id: string } }
    | { type: "game_over"; data: { standings: ScoreRow[] } }
    | { type: "error"; data: { code: string; cid: string | null } }
    | { type: "session"; data: { player_id: string; resume_token: string } }
    | { type: "pong"; data: { t0: number; ts: number } }
  );

export type FrameType = ServerFrame["type"];

/** Frames exempt from the dense-seq stale/gap logic (see wire.py). */
export const TARGETED_TYPES: ReadonlySet<string> = new Set([
  "snapshot",
  "answer_ack",
  "error",
  "session",
  "pong",
]);

export type ClientFrame =
  | { type: "join"; name: string; token?: string; name_token?: string }
  | { type: "start_game"; mode_id: number }
  | { type: "submit_answer"; round_seq: number; option_id: number; cid?: string }
  | { type: "set_ready"; ready: boolean }
  | { type: "kick"; target_id: string }
  | { type: "leave" }
  | { type: "rematch" }
  | { type: "ping"; t0: number };
