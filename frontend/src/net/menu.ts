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
