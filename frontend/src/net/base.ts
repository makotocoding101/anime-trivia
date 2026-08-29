/**
 * Where the backend lives.
 *
 * Local dev and the compose stack are same-origin (vite / Caddy proxy
 * /api and /ws), so the default is relative URLs. In production the static
 * bundle is on Vercel and the backend on AWS — a different origin — so the
 * build bakes in VITE_API_BASE (e.g. https://api.example.com) and the
 * websocket scheme is derived from it.
 */

const BASE = ((import.meta.env.VITE_API_BASE as string | undefined) ?? "").replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}

export function wsUrl(path: string): string {
  if (BASE !== "") {
    return BASE.replace(/^http/, "ws") + path;
  }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}
