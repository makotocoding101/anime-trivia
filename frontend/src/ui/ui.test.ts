import { describe, expect, it } from "vitest";

import { flipDeltas } from "./flip";
import { displaySeconds, isUrgent, ringDashOffset, ringFraction } from "./ring";

describe("ring geometry", () => {
  it("maps remaining time onto a clamped fraction", () => {
    expect(ringFraction(20, 20)).toBe(1);
    expect(ringFraction(5, 20)).toBe(0.25);
    expect(ringFraction(0, 20)).toBe(0);
    expect(ringFraction(-3, 20)).toBe(0); // past the deadline, not negative
    expect(ringFraction(99, 20)).toBe(1); // clock skew cannot overfill it
  });

  it("treats an unknown or zero round length as empty", () => {
    // The snapshot path can hand us a deadline without a duration; an empty
    // ring is honest, a divide-by-zero is not.
    expect(ringFraction(10, null)).toBe(0);
    expect(ringFraction(10, 0)).toBe(0);
  });

  it("draws the whole ring at full time and none at zero", () => {
    const circumference = 100;
    expect(ringDashOffset(1, circumference)).toBe(0);
    expect(ringDashOffset(0, circumference)).toBe(100);
    expect(ringDashOffset(0.25, circumference)).toBe(75);
  });

  it("turns urgent once, at a quarter left", () => {
    expect(isUrgent(0.26)).toBe(false);
    expect(isUrgent(0.25)).toBe(true);
    expect(isUrgent(0)).toBe(true);
  });

  it("never shows a zero while time remains", () => {
    expect(displaySeconds(0.2)).toBe(1);
    expect(displaySeconds(19.01)).toBe(20);
    expect(displaySeconds(0)).toBe(0);
    expect(displaySeconds(-2)).toBe(0);
  });
});

describe("flipDeltas", () => {
  const before = new Map([
    ["a", 0],
    ["b", 50],
    ["c", 100],
  ]);

  it("inverts the movement of rows that changed place", () => {
    const after = new Map([
      ["c", 0],
      ["a", 50],
      ["b", 100],
    ]);
    const deltas = flipDeltas(before, after);
    expect(deltas.get("c")).toBe(100); // rose to the top: animate from below
    expect(deltas.get("a")).toBe(-50); // slipped down: animate from above
    expect(deltas.get("b")).toBe(-50);
  });

  it("ignores rows that did not move", () => {
    expect(flipDeltas(before, new Map(before)).size).toBe(0);
  });

  it("discards sub-pixel noise", () => {
    const after = new Map([
      ["a", 0.4],
      ["b", 50],
      ["c", 100],
    ]);
    expect(flipDeltas(before, after).size).toBe(0);
  });

  it("skips rows with no previous position", () => {
    // A player who just joined has nothing to animate from.
    const after = new Map([...before, ["newcomer", 150]]);
    const deltas = flipDeltas(before, after);
    expect(deltas.has("newcomer")).toBe(false);
  });

  it("skips rows that vanished", () => {
    const after = new Map([["a", 0]]);
    expect(flipDeltas(before, after).size).toBe(0);
  });
});
