/**
 * The client-side mirror of R-11: the store's view must be provably a prefix
 * of the server's event stream. These tests pin the gate and the reductions
 * the game depends on.
 */

import { describe, expect, it } from "vitest";

import type { ServerFrame, SnapshotData } from "../types/wire";
import { applyFrame, initialView, type RoomView } from "./room";

let nextSeq = 0;

function frame(type: string, data: object, seq?: number): ServerFrame {
  if (seq === undefined) seq = ++nextSeq;
  else nextSeq = Math.max(nextSeq, seq);
  return { v: 1, seq, ts: Date.now(), type, data } as ServerFrame;
}

function snapshotData(overrides: Partial<SnapshotData> = {}): SnapshotData {
  return {
    room_code: "TEST",
    you: "p0",
    phase: "LOBBY",
    round_seq: 0,
    round_index: -1,
    question_count: 0,
    phase_deadline: null,
    players: [
      {
        id: "p0",
        name: "aoi",
        seat: 0,
        score: 0,
        streak: 0,
        conn: "LIVE",
        is_host: true,
        ready: false,
        spectating: false,
      },
    ],
    question: null,
    reveal: null,
    ...overrides,
  };
}

function joined(view: RoomView, seq = 1): RoomView {
  return applyFrame(view, frame("snapshot", snapshotData(), seq));
}

describe("the seq gate", () => {
  it("drops stale broadcast frames", () => {
    let view = joined(initialView, 5);
    view = applyFrame(view, frame("player_ready", { player_id: "p0", ready: true }, 6));
    const before = view;
    // The same frame delivered again (or anything older) changes nothing.
    view = applyFrame(view, frame("player_ready", { player_id: "p0", ready: false }, 6));
    view = applyFrame(view, frame("player_ready", { player_id: "p0", ready: false }, 3));
    expect(view).toBe(before);
    expect(view.players[0]!.ready).toBe(true);
  });

  it("flags a broadcast gap for resync instead of guessing", () => {
    let view = joined(initialView, 5);
    view = applyFrame(view, frame("player_ready", { player_id: "p0", ready: true }, 9));
    expect(view.needsResync).toBe(true);
    expect(view.lastSeq).toBe(9);
  });

  it("exempts targeted frames from the gate entirely", () => {
    let view = joined(initialView, 5);
    // An error frame carries the current counter (5) — not stale, applies.
    view = applyFrame(view, frame("error", { code: "not_host", cid: null }, 5));
    expect(view.lastError).toBe("not_host");
  });

  it("snapshot re-baselines the stream and clears the resync flag", () => {
    let view = joined(initialView, 5);
    view = applyFrame(view, frame("player_ready", { player_id: "p0", ready: true }, 9));
    expect(view.needsResync).toBe(true);
    view = applyFrame(view, frame("snapshot", snapshotData(), 12));
    expect(view.needsResync).toBe(false);
    expect(view.lastSeq).toBe(12);
    // The stream continues densely from the snapshot's seq.
    view = applyFrame(view, frame("player_ready", { player_id: "p0", ready: true }, 13));
    expect(view.players[0]!.ready).toBe(true);
  });
});

describe("round flow reductions", () => {
  const question = () =>
    frame("question", {
      round_seq: 1,
      round_index: 0,
      question_count: 2,
      question_id: 7,
      kind: "text",
      prompt: "pick B",
      options: [
        { id: 0, label: "A" },
        { id: 1, label: "B" },
      ],
      media_ref: null,
      ends_at: Date.now() + 20_000,
      seconds: 20,
    });

  it("question opens with a clean slate", () => {
    let view = joined(initialView);
    view = applyFrame(view, question());
    expect(view.phase).toBe("QUESTION_OPEN");
    expect(view.question?.prompt).toBe("pick B");
    expect(view.question?.yourAnswer).toBeNull();
    expect(view.reveal).toBeNull();
  });

  it("ack promotes the pending answer for the matching round only", () => {
    let view = joined(initialView);
    view = applyFrame(view, question());
    view = { ...view, pendingAnswer: 1 };
    // A stray ack for an old round does nothing...
    view = applyFrame(view, frame("answer_ack", { round_seq: 0, cid: null }));
    expect(view.question?.yourAnswer).toBeNull();
    // ...the right one locks it in.
    view = applyFrame(view, frame("answer_ack", { round_seq: 1, cid: null }));
    expect(view.question?.yourAnswer).toBe(1);
    expect(view.pendingAnswer).toBeNull();
  });

  it("reveal merges authoritative scores into the roster", () => {
    let view = joined(initialView);
    view = applyFrame(view, question());
    view = applyFrame(
      view,
      frame("reveal", {
        round_seq: 1,
        round_index: 0,
        correct_option_id: 1,
        results: [
          { player_id: "p0", option_id: 1, correct: true, delta: 145, score: 145, streak: 1 },
        ],
      }),
    );
    expect(view.players[0]!.score).toBe(145);
    expect(view.reveal?.correctOptionId).toBe(1);
  });

  it("a dropped player greys out; a departed one vanishes", () => {
    let view = joined(initialView);
    view = applyFrame(
      view,
      frame("player_joined", { player_id: "p1", name: "ren", seat: 1, spectating: false }),
    );
    view = applyFrame(view, frame("player_left", { player_id: "p1", reason: "dropped" }));
    expect(view.players.find((p) => p.id === "p1")?.conn).toBe("DROPPED");
    view = applyFrame(view, frame("player_left", { player_id: "p1", reason: "kicked" }));
    expect(view.players.some((p) => p.id === "p1")).toBe(false);
  });

  it("host migration is exclusive", () => {
    let view = joined(initialView);
    view = applyFrame(
      view,
      frame("player_joined", { player_id: "p1", name: "ren", seat: 1, spectating: false }),
    );
    view = applyFrame(view, frame("host_changed", { player_id: "p1" }));
    expect(view.players.map((p) => p.is_host)).toEqual([false, true]);
  });

  it("returning to lobby clears round debris ahead of the rematch snapshot", () => {
    let view = joined(initialView);
    view = applyFrame(view, question());
    view = applyFrame(
      view,
      frame("game_over", { standings: [] }),
    );
    view = applyFrame(
      view,
      frame("phase_changed", { phase: "LOBBY", round_seq: 2, round_index: -1 }),
    );
    expect(view.question).toBeNull();
    expect(view.standings).toBeNull();
    expect(view.reveal).toBeNull();
  });
});
