import { describe, expect, it } from "vitest";

import { backoffDelayMs } from "./socket";
import { clearSession, loadSession, saveSession, type StoredSession } from "./session";

describe("backoffDelayMs", () => {
  it("caps grow exponentially then flatten at 10s", () => {
    const atMax = (attempt: number) => backoffDelayMs(attempt, () => 1);
    expect(atMax(0)).toBe(500);
    expect(atMax(1)).toBe(1000);
    expect(atMax(2)).toBe(2000);
    expect(atMax(4)).toBe(8000);
    expect(atMax(5)).toBe(10_000); // capped
    expect(atMax(20)).toBe(10_000); // stays capped — no overflow silliness
  });

  it("applies full jitter across the whole window", () => {
    // Full jitter, not equal jitter: delay ranges over [0, cap), so a herd
    // of clients reconnecting after a restart spreads out (§08).
    expect(backoffDelayMs(3, () => 0)).toBe(0);
    expect(backoffDelayMs(3, () => 0.5)).toBe(2000);
    for (let i = 0; i < 50; i++) {
      const delay = backoffDelayMs(6, Math.random);
      expect(delay).toBeGreaterThanOrEqual(0);
      expect(delay).toBeLessThanOrEqual(10_000);
    }
  });
});

function fakeStorage(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: () => null,
    get length() {
      return data.size;
    },
  };
}

const SESSION: StoredSession = { playerId: "p-1", token: "p-1.abc", name: "aoi" };

describe("session persistence", () => {
  it("round-trips per room code", () => {
    const storage = fakeStorage();
    saveSession(storage, "KRWC", SESSION);
    expect(loadSession(storage, "KRWC")).toEqual(SESSION);
    expect(loadSession(storage, "XXXX")).toBeNull(); // other rooms unaffected
    clearSession(storage, "KRWC");
    expect(loadSession(storage, "KRWC")).toBeNull();
  });

  it("rejects corrupt or partial stored data", () => {
    const storage = fakeStorage();
    storage.setItem("trivia:session:KRWC", "{not json");
    expect(loadSession(storage, "KRWC")).toBeNull();
    storage.setItem("trivia:session:KRWC", JSON.stringify({ playerId: "p-1" }));
    expect(loadSession(storage, "KRWC")).toBeNull();
  });

  it("survives a missing storage entirely", () => {
    // Private windows / blocked site data: every helper is a quiet no-op.
    saveSession(null, "KRWC", SESSION);
    expect(loadSession(null, "KRWC")).toBeNull();
    clearSession(null, "KRWC");
  });

  it("survives a storage that throws", () => {
    const hostile = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    } as unknown as Storage;
    saveSession(hostile, "KRWC", SESSION);
    expect(loadSession(hostile, "KRWC")).toBeNull();
    clearSession(hostile, "KRWC");
  });
});
