/**
 * The menu-screen REST surface: modes, live stats, rankings, profile.
 *
 * All of it is outside the game loop. Nothing here runs while a round is in
 * flight, which is why plain fetch is fine and none of it goes near the
 * socket.
 */

import { apiUrl } from "./base";

export interface Mode {
  id: number;
  slug: string;
  name: string;
  blurb: string | null;
  question_count: number;
  seconds_per_q: number;
}

export interface Stats {
  open_rooms: number;
  live_games: number;
  players_online: number;
}

export interface Wallet {
  name: string;
  coins: number;
  games_played: number;
  wins: number;
  best_score: number;
  lifetime_score: number;
  tier: string;
  next_tier: string | null;
  coins_to_next: number | null;
}

export interface Ranked {
  rank: number;
  wallet: Wallet;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path));
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return (await response.json()) as T;
}

export const fetchModes = (): Promise<Mode[]> => getJson<Mode[]>("/api/modes");

export const fetchStats = (): Promise<Stats> => getJson<Stats>("/api/stats");

export const fetchRankings = (limit = 25): Promise<Ranked[]> =>
  getJson<Ranked[]>(`/api/rankings?limit=${limit}`);

/** Null for a name that has never finished a game — a fresh wallet, not an
 * error, so the caller renders zeroes rather than a failure. */
export const fetchProfile = (name: string): Promise<Wallet | null> =>
  getJson<Wallet | null>(`/api/profile/${encodeURIComponent(name)}`);

/** A wallet nobody has played into yet. Lets the profile screen render the
 * same shape whether or not the server knows the name. */
export function emptyWallet(name: string): Wallet {
  return {
    name,
    coins: 0,
    games_played: 0,
    wins: 0,
    best_score: 0,
    lifetime_score: 0,
    tier: "ROOKIE",
    next_tier: "EXPLORER",
    coins_to_next: 250,
  };
}

/**
 * The remembered player name.
 *
 * localStorage, deliberately unlike the resume token in session.ts, which is
 * sessionStorage. The two are storing different things: a token is a claim on
 * one seat in one room and must not leak between tabs, while the name is a
 * preference and the thing "welcome back" is welcoming. It is also the wallet
 * key, so remembering it is what makes coins persist across visits.
 *
 * Every access is guarded — private windows and blocked site data both throw
 * on the accessor itself, not on the read.
 */
const NAME_KEY = "otakizu:name";

export function loadName(): string {
  try {
    return window.localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveName(name: string): void {
  try {
    window.localStorage.setItem(NAME_KEY, name);
  } catch {
    /* the name simply is not remembered next visit */
  }
}

/* --- name ownership ------------------------------------------------------
 *
 * A claimed name needs a token to play under. The token is minted by the
 * server and held here; it is not a password and carries no secret beyond
 * the right to use one name.
 */

export interface NameStatus {
  name: string;
  claimed: boolean;
}

export interface NameSession {
  name: string;
  token: string;
  /** True when this call is what created the account. */
  claimed: boolean;
}

/** Thrown with a machine-readable reason the UI turns into a sentence. */
export class AccountError extends Error {
  constructor(readonly reason: "wrong_passphrase" | "unavailable" | "failed") {
    super(reason);
  }
}

export const fetchNameStatus = (name: string): Promise<NameStatus> =>
  getJson<NameStatus>(`/api/account/${encodeURIComponent(name)}`);

/**
 * Claim a free name, or sign in to one already held. One call, because
 * between checking and submitting somebody else may have claimed it — the
 * server decides on the evidence in front of it.
 */
export async function claimOrSignIn(name: string, password: string): Promise<NameSession> {
  const response = await fetch(apiUrl("/api/account/session"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, password }),
  });
  if (response.status === 401) throw new AccountError("wrong_passphrase");
  if (response.status === 503) throw new AccountError("unavailable");
  if (!response.ok) throw new AccountError("failed");
  return (await response.json()) as NameSession;
}

const TOKEN_KEY = "otakizu:name-token";

export function loadNameToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveNameToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* the name will need signing in to again next visit */
  }
}

export function clearNameToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* nothing to clear as far as we can tell */
  }
}
