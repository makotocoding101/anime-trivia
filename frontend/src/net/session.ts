/**
 * Resume-session persistence (spec §08). sessionStorage on purpose: a token
 * survives a reload and a network drop in the same tab, but does not leak
 * into other tabs — a second tab joining the same room is a second player,
 * not a takeover of the first.
 *
 * Storage access can throw (private windows, blocked site data), so every
 * touch is guarded and the app renders fine with no stored session.
 */

export interface StoredSession {
  playerId: string;
  token: string;
  name: string;
}

const key = (roomCode: string) => `trivia:session:${roomCode}`;

export function defaultStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function saveSession(
  storage: Storage | null,
  roomCode: string,
  session: StoredSession,
): void {
  try {
    storage?.setItem(key(roomCode), JSON.stringify(session));
  } catch {
    /* nothing persists; resume simply will not be offered */
  }
}

export function loadSession(storage: Storage | null, roomCode: string): StoredSession | null {
  try {
    const raw = storage?.getItem(key(roomCode));
    if (raw == null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof (parsed as StoredSession).playerId === "string" &&
      typeof (parsed as StoredSession).token === "string" &&
      typeof (parsed as StoredSession).name === "string"
    ) {
      return parsed as StoredSession;
    }
    return null;
  } catch {
    return null;
  }
}

export function clearSession(storage: Storage | null, roomCode: string): void {
  try {
    storage?.removeItem(key(roomCode));
  } catch {
    /* already gone as far as we can tell */
  }
}
