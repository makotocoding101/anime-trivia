/**
 * The room store: one reducer applying server frames, mirroring the server's
 * own transition function (spec §09). No client-side game logic — the store
 * is a read-only projection of the event stream, and applyFrame's gate makes
 * the projection provably a prefix of what the server sent (R-11).
 */

import { create } from "zustand";

import type {
  OptionRow,
  PhaseName,
  PlayerRow,
  RevealResult,
  ScoreRow,
  ServerFrame,
} from "../types/wire";
import { TARGETED_TYPES } from "../types/wire";

export interface ActiveQuestion {
  roundSeq: number;
  roundIndex: number;
  questionId: number;
  kind: string;
  prompt: string;
  options: OptionRow[];
  mediaRef: string | null;
  endsAt: number | null; // wall ms, server-derived
  seconds: number | null;
  yourAnswer: number | null;
}

export interface RevealView {
  roundIndex: number;
  correctOptionId: number;
  results: RevealResult[];
}

export interface RoomView {
  lastSeq: number;
  /** A broadcast gap was observed. Cannot happen over one healthy TCP
   * connection (broadcast seq is dense); when it does, the connection is
   * suspect and the fix is a snapshot resync (wired fully in M5). */
  needsResync: boolean;
  you: string | null;
  roomCode: string | null;
  phase: PhaseName;
  roundSeq: number;
  roundIndex: number;
  questionCount: number;
  players: PlayerRow[];
  question: ActiveQuestion | null;
  pendingAnswer: number | null; // set on click, promoted on ack
  progress: { answered: number; total: number } | null;
  reveal: RevealView | null;
  scoreboard: ScoreRow[];
  standings: ScoreRow[] | null;
  lastError: string | null;
}

export const initialView: RoomView = {
  lastSeq: 0,
  needsResync: false,
  you: null,
  roomCode: null,
  phase: "LOBBY",
  roundSeq: 0,
  roundIndex: -1,
  questionCount: 0,
  players: [],
  question: null,
  pendingAnswer: null,
  progress: null,
  reveal: null,
  scoreboard: [],
  standings: null,
  lastError: null,
};

/** The three-line gate (spec §09) plus the reduction. Pure — unit-tested. */
export function applyFrame(view: RoomView, frame: ServerFrame): RoomView {
  if (!TARGETED_TYPES.has(frame.type)) {
    if (frame.seq <= view.lastSeq) return view; // stale duplicate — drop (R-11)
    const gap = frame.seq > view.lastSeq + 1;
    view = { ...view, lastSeq: frame.seq, needsResync: view.needsResync || gap };
  }
  return reduce(view, frame);
}

function reduce(view: RoomView, frame: ServerFrame): RoomView {
  switch (frame.type) {
    case "snapshot": {
      const d = frame.data;
      return {
        ...initialView,
        lastSeq: frame.seq, // snapshot re-baselines the stream (R-11)
        needsResync: false,
        you: d.you,
        roomCode: d.room_code,
        phase: d.phase,
        roundSeq: d.round_seq,
        roundIndex: d.round_index,
        questionCount: d.question_count,
        players: d.players,
        question: d.question
          ? {
              roundSeq: d.round_seq,
              roundIndex: d.round_index,
              questionId: d.question.id,
              kind: d.question.kind,
              prompt: d.question.prompt,
              options: d.question.options,
              mediaRef: d.question.media_ref,
              endsAt: d.question.deadline,
              // The snapshot has no explicit round length, but it carries
              // both ends of the window — without this the timer ring would
              // render empty (and "urgent") after every reconnect.
              seconds:
                d.question.opened_at !== null && d.question.deadline !== null
                  ? (d.question.deadline - d.question.opened_at) / 1000
                  : null,
              yourAnswer: d.question.your_answer,
            }
          : null,
        reveal: d.reveal
          ? {
              roundIndex: d.reveal.round_index,
              correctOptionId: d.reveal.correct_option_id,
              results: [],
            }
          : null,
      };
    }

    case "phase_changed": {
      const d = frame.data;
      const next: RoomView = {
        ...view,
        phase: d.phase,
        roundSeq: d.round_seq,
        roundIndex: d.round_index,
      };
      if (d.phase === "INTRO") {
        // New round incoming: the old question and its progress are history.
        next.question = null;
        next.pendingAnswer = null;
        next.progress = null;
      }
      if (d.phase === "LOBBY") {
        // Rematch. A fresh snapshot follows immediately (targeted), which
        // rebuilds players and scores; clear round leftovers now.
        next.question = null;
        next.pendingAnswer = null;
        next.progress = null;
        next.reveal = null;
        next.standings = null;
      }
      return next;
    }

    case "question": {
      const d = frame.data;
      return {
        ...view,
        phase: "QUESTION_OPEN",
        roundSeq: d.round_seq,
        roundIndex: d.round_index,
        questionCount: d.question_count,
        reveal: null,
        pendingAnswer: null,
        progress: null,
        question: {
          roundSeq: d.round_seq,
          roundIndex: d.round_index,
          questionId: d.question_id,
          kind: d.kind,
          prompt: d.prompt,
          options: d.options,
          mediaRef: d.media_ref,
          endsAt: d.ends_at,
          seconds: d.seconds,
          yourAnswer: null,
        },
      };
    }

    case "answer_ack": {
      if (view.question === null || frame.data.round_seq !== view.question.roundSeq)
        return view;
      return {
        ...view,
        question: { ...view.question, yourAnswer: view.pendingAnswer },
        pendingAnswer: null,
      };
    }

    case "answer_progress":
      return { ...view, progress: frame.data };

    case "reveal": {
      const d = frame.data;
      // Merge authoritative post-round scores into the roster as we go.
      const byId = new Map(d.results.map((r) => [r.player_id, r]));
      return {
        ...view,
        reveal: {
          roundIndex: d.round_index,
          correctOptionId: d.correct_option_id,
          results: d.results,
        },
        players: view.players.map((p) => {
          const r = byId.get(p.id);
          return r ? { ...p, score: r.score, streak: r.streak } : p;
        }),
      };
    }

    case "scoreboard":
      return { ...view, scoreboard: frame.data.rows };

    case "player_joined": {
      const d = frame.data;
      if (view.players.some((p) => p.id === d.player_id)) return view;
      const row: PlayerRow = {
        id: d.player_id,
        name: d.name,
        seat: d.seat,
        score: 0,
        streak: 0,
        conn: "LIVE",
        is_host: false,
        ready: false,
        spectating: d.spectating,
      };
      return { ...view, players: [...view.players, row].sort((a, b) => a.seat - b.seat) };
    }

    case "player_left": {
      const d = frame.data;
      if (d.reason === "dropped") {
        // Seat held; the row greys out instead of vanishing (§08).
        return {
          ...view,
          players: view.players.map((p) =>
            p.id === d.player_id ? { ...p, conn: "DROPPED" as const } : p,
          ),
        };
      }
      return { ...view, players: view.players.filter((p) => p.id !== d.player_id) };
    }

    case "player_reconnected":
      return {
        ...view,
        players: view.players.map((p) =>
          p.id === frame.data.player_id ? { ...p, conn: "LIVE" as const } : p,
        ),
      };

    case "player_ready":
      return {
        ...view,
        players: view.players.map((p) =>
          p.id === frame.data.player_id ? { ...p, ready: frame.data.ready } : p,
        ),
      };

    case "host_changed":
      return {
        ...view,
        players: view.players.map((p) => ({ ...p, is_host: p.id === frame.data.player_id })),
      };

    case "game_over":
      return { ...view, standings: frame.data.standings };

    case "error":
      return { ...view, lastError: frame.data.code, pendingAnswer: null };

    case "session":
    case "pong":
      return view; // consumed by the socket layer before frames reach the store
  }
}

interface RoomStore {
  view: RoomView;
  dispatchFrame: (frame: ServerFrame) => void;
  setPendingAnswer: (optionId: number) => void;
  clearError: () => void;
  reset: () => void;
}

export const useRoomStore = create<RoomStore>((set, get) => ({
  view: initialView,
  dispatchFrame: (frame) => set({ view: applyFrame(get().view, frame) }),
  setPendingAnswer: (optionId) =>
    set({ view: { ...get().view, pendingAnswer: optionId } }),
  clearError: () => set({ view: { ...get().view, lastError: null } }),
  reset: () => set({ view: initialView }),
}));
