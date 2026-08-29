/**
 * Server-rendered smoke tests. renderToStaticMarkup needs no DOM, so these
 * run in the plain node environment alongside the pure-function suites — and
 * they still catch the things that actually break screens: a crash on a null
 * question, a state variant that never renders, and above all R-12 at the
 * render layer.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RoomSocket } from "../net/socket";
import { initialView, type RoomView } from "../store/room";
import type { PlayerRow } from "../types/wire";
import { AnswerTile } from "./AnswerTile";
import { GameOverScreen } from "./GameOverScreen";
import { GameScreen } from "./GameScreen";
import { Leaderboard } from "./Leaderboard";
import { TimerRing } from "./TimerRing";

const socket = { clock: { remainingMs: () => 12_000 } } as unknown as RoomSocket;

function player(id: string, over: Partial<PlayerRow> = {}): PlayerRow {
  return {
    id,
    name: id,
    seat: 0,
    score: 0,
    streak: 0,
    conn: "LIVE",
    is_host: false,
    ready: false,
    spectating: false,
    ...over,
  };
}

const OPTIONS = [
  { id: 10, label: "Gojo Satoru" },
  { id: 11, label: "Nanami Kento" },
  { id: 12, label: "Megumi Fushiguro" },
  { id: 13, label: "Yuji Itadori" },
];

function playing(over: Partial<RoomView> = {}): RoomView {
  return {
    ...initialView,
    you: "p0",
    roomCode: "KRWC",
    phase: "QUESTION_OPEN",
    roundSeq: 1,
    roundIndex: 0,
    questionCount: 5,
    players: [player("p0", { name: "aoi", is_host: true }), player("p1", { name: "ren" })],
    question: {
      roundSeq: 1,
      roundIndex: 0,
      questionId: 7,
      kind: "text",
      prompt: "Who is the strongest sorcerer?",
      options: OPTIONS,
      mediaRef: null,
      endsAt: Date.now() + 12_000,
      seconds: 20,
      yourAnswer: null,
    },
    ...over,
  };
}

describe("GameScreen", () => {
  it("renders the prompt and every option while the question is open", () => {
    const html = renderToStaticMarkup(<GameScreen view={playing()} socket={socket} />);
    expect(html).toContain("Who is the strongest sorcerer?");
    for (const option of OPTIONS) expect(html).toContain(option.label);
    expect(html).toContain("pick an answer");
  });

  it("never renders correctness before the reveal", () => {
    // R-12 at the render layer: the open-question view has no correct answer
    // in its data, so no amount of markup can leak one.
    const html = renderToStaticMarkup(<GameScreen view={playing()} socket={socket} />);
    expect(html).not.toContain("correct");
    expect(html).not.toContain("✓");
  });

  it("marks the locked-in answer and stops offering the rest", () => {
    const view = playing({
      question: { ...playing().question!, yourAnswer: 11 },
    });
    const html = renderToStaticMarkup(<GameScreen view={view} socket={socket} />);
    expect(html).toContain("locked in");
    expect(html).toContain("picked");
    expect(html.match(/disabled/g)?.length).toBe(OPTIONS.length);
  });

  it("tells a spectator why the tiles are dead", () => {
    const view = playing({
      players: [player("p0", { name: "aoi", spectating: true }), player("p1")],
    });
    const html = renderToStaticMarkup(<GameScreen view={view} socket={socket} />);
    expect(html).toContain("watching this one");
  });

  it("shows correct and wrong at reveal, and the round delta", () => {
    const view = playing({
      phase: "REVEAL",
      reveal: {
        roundIndex: 0,
        correctOptionId: 10,
        results: [
          { player_id: "p0", option_id: 11, correct: false, delta: 0, score: 0, streak: 0 },
          { player_id: "p1", option_id: 10, correct: true, delta: 145, score: 145, streak: 1 },
        ],
      },
    });
    const html = renderToStaticMarkup(<GameScreen view={view} socket={socket} />);
    expect(html).toContain("tile correct");
    expect(html).toContain("tile wrong");
    expect(html).toContain("✓");
    expect(html).toContain("not this time");
    expect(html).toContain("+145");
  });

  it("survives the intro phase, where there is no question yet", () => {
    const view = playing({ phase: "INTRO", question: null, roundIndex: 2 });
    const html = renderToStaticMarkup(<GameScreen view={view} socket={socket} />);
    expect(html).toContain("Round 3");
    expect(html).toContain("get ready");
  });
});

describe("Leaderboard", () => {
  it("ranks by score with the viewer highlighted", () => {
    const view = playing({
      players: [
        player("p0", { name: "aoi", score: 100 }),
        player("p1", { name: "ren", score: 300 }),
      ],
    });
    const html = renderToStaticMarkup(<Leaderboard view={view} />);
    expect(html.indexOf("ren")).toBeLessThan(html.indexOf("aoi"));
    expect(html).toContain("row you"); // p0 is the viewer
  });

  it("shows ready flags and hides scores in the lobby", () => {
    const view = playing({
      phase: "LOBBY",
      players: [player("p0", { name: "aoi", ready: true, is_host: true })],
    });
    const html = renderToStaticMarkup(<Leaderboard view={view} />);
    expect(html).toContain("chip ready");
    expect(html).toContain("chip host");
    expect(html).not.toContain("class=\"score\"");
  });

  it("greys a dropped player rather than dropping the row", () => {
    const view = playing({
      players: [player("p0"), player("p1", { name: "ren", conn: "DROPPED" })],
    });
    const html = renderToStaticMarkup(<Leaderboard view={view} />);
    expect(html).toContain("ren");
    expect(html).toContain("chip away");
    expect(html).toContain("gone");
  });
});

describe("TimerRing", () => {
  it("goes urgent under a quarter and reads the ceiling of the seconds", () => {
    const calm = renderToStaticMarkup(<TimerRing remaining={18} total={20} />);
    expect(calm).not.toContain("urgent");
    expect(calm).toContain(">18<");

    const urgent = renderToStaticMarkup(<TimerRing remaining={3.2} total={20} />);
    expect(urgent).toContain("ring urgent");
    expect(urgent).toContain(">4<");
  });
});

describe("AnswerTile", () => {
  it("labels its state for screen readers, not just with colour", () => {
    const html = renderToStaticMarkup(
      <AnswerTile index={0} label="Gojo" state="correct" disabled onPick={() => undefined} />,
    );
    expect(html).toContain("A. Gojo (correct answer)");
    expect(html).toContain("✓");
  });
});

describe("GameOverScreen", () => {
  it("crowns the winner and offers a rematch to the host", () => {
    const view = playing({
      phase: "GAME_OVER",
      players: [player("p0", { name: "aoi", score: 290, is_host: true })],
      standings: [{ rank: 1, player_id: "p0", name: "aoi", score: 290, streak: 2 }],
    });
    const html = renderToStaticMarkup(<GameOverScreen view={view} socket={socket} />);
    expect(html).toContain("you win");
    expect(html).toContain("290 points");
    expect(html).toContain("play again");
  });
});
