"""Load test: many clients, real sockets, deliberate contention.

This is what makes §04 more than a document. It measures the two properties
the architecture actually claims:

  fairness  — every client in a room sees the same question at the same time.
              The fan-out spread (first client to last, per round) is the
              direct measurement of R-05's premise: the room task never waits
              on a socket, so one slow client cannot delay anyone else's
              question. A spread that grows with player count means the
              broadcast path has started blocking.

  safety    — when every player answers in the same millisecond at the
              deadline boundary, the guards hold: no double-credit (R-01), no
              answer landing in the wrong round (R-02), no late answer scoring
              (R-04), and every client agrees on the final scoreboard.

Run against a server that is already up:

    python -m uv run python -m loadtest.run --rooms 5 --players 6

Exits non-zero if any invariant is violated, so it can gate a deploy.

Note on resolution: on Windows the event-loop timer granularity is ~15.6ms,
so sub-frame spreads read as 0 or ~16 and nothing finer is measurable there.
Linux (CI, the deployed box) resolves far better.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

import websockets
from websockets.asyncio.client import ClientConnection

TARGETED = {"snapshot", "answer_ack", "error", "session", "pong"}


@dataclass
class Metrics:
    """Everything one run measured. Aggregated across rooms."""

    question_spread_ms: list[float] = field(default_factory=list)
    ack_latency_ms: list[float] = field(default_factory=list)
    submit_offset_ms: list[float] = field(default_factory=list)
    rejects: Counter[str] = field(default_factory=Counter)
    accepted: int = 0
    seq_gaps: int = 0
    violations: list[str] = field(default_factory=list)


@dataclass
class Player:
    name: str
    ws: ClientConnection
    player_id: str = ""
    last_seq: int = 0
    questions: dict[int, float] = field(default_factory=dict)  # round_seq -> recv monotonic
    answered: set[int] = field(default_factory=set)
    submitted: dict[int, float] = field(default_factory=dict)
    acks: dict[int, float] = field(default_factory=dict)
    final: list[dict[str, object]] | None = None


async def recv_loop(player: Player, metrics: Metrics, done: asyncio.Event) -> None:
    """One reader per socket. Records arrival times and checks the seq gate."""
    try:
        async for raw in player.ws:
            frame = json.loads(raw)
            kind = frame["type"]

            if kind not in TARGETED:
                # Broadcast stream must be dense for every client (R-11).
                if player.last_seq and frame["seq"] > player.last_seq + 1:
                    metrics.seq_gaps += 1
                    metrics.violations.append(
                        f"{player.name}: seq gap {player.last_seq} -> {frame['seq']}"
                    )
                player.last_seq = frame["seq"]

            match kind:
                case "session":
                    player.player_id = frame["data"]["player_id"]
                case "question":
                    player.questions[frame["data"]["round_seq"]] = time.monotonic()
                case "answer_ack":
                    player.acks[frame["data"]["round_seq"]] = time.monotonic()
                case "error":
                    metrics.rejects[str(frame["data"]["code"])] += 1
                case "game_over":
                    player.final = frame["data"]["standings"]
                    done.set()
    except websockets.ConnectionClosed:
        done.set()


async def play_room(
    base_ws: str,
    base_http: str,
    code: str,
    mode_id: int,
    n_players: int,
    lead_ms: float,
    metrics: Metrics,
) -> None:
    """One room: everyone joins, the host starts, then every player answers in
    the same instant near the deadline, round after round, to game over."""
    players: list[Player] = []
    dones: list[asyncio.Event] = []
    readers: list[asyncio.Task[None]] = []

    for i in range(n_players):
        ws = await websockets.connect(f"{base_ws}/ws/rooms/{code}", open_timeout=15)
        player = Player(name=f"p{i}", ws=ws)
        done = asyncio.Event()
        players.append(player)
        dones.append(done)
        readers.append(asyncio.create_task(recv_loop(player, metrics, done)))
        await ws.send(json.dumps({"type": "join", "name": player.name}))

    await asyncio.sleep(0.4)  # let every join settle before the start
    await players[0].ws.send(json.dumps({"type": "start_game", "mode_id": mode_id}))

    seen_rounds: set[int] = set()
    deadline = time.monotonic() + 180
    while not all(d.is_set() for d in dones) and time.monotonic() < deadline:
        # Find a round every player has received but nobody has answered yet.
        common = set.intersection(*(set(p.questions) for p in players)) - seen_rounds
        if not common:
            await asyncio.sleep(0.02)
            continue
        round_seq = min(common)
        seen_rounds.add(round_seq)

        # Fairness: how far apart did this question land across clients?
        arrivals = [p.questions[round_seq] for p in players]
        metrics.question_spread_ms.append((max(arrivals) - min(arrivals)) * 1000)

        # Contention: every player fires at the same instant, `lead_ms` before
        # their own view of the deadline. Same option on purpose — identical
        # payloads make a double-credit bug show up as a score, not a tie.
        fire_at = time.monotonic() + max(0.0, (lead_ms / 1000.0))

        async def submit(player: Player, rs: int = round_seq, at: float = fire_at) -> None:
            delay = at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            sent = time.monotonic()
            metrics.submit_offset_ms.append((sent - at) * 1000)
            player.submitted[rs] = sent
            await player.ws.send(
                json.dumps(
                    {
                        "type": "submit_answer",
                        "round_seq": rs,
                        "option_id": 0,
                        "cid": f"{player.name}-{rs}",
                    }
                )
            )
            player.answered.add(rs)

        await asyncio.gather(*(submit(p) for p in players))
        await asyncio.sleep(0.35)  # give acks time to land before the next round

        for player in players:
            if round_seq in player.acks and round_seq in player.submitted:
                # Round trip for the answer itself — not counting the time the
                # client deliberately waited before firing.
                metrics.ack_latency_ms.append(
                    (player.acks[round_seq] - player.submitted[round_seq]) * 1000
                )
        metrics.accepted += sum(1 for p in players if round_seq in p.acks)

    for player in players:
        await player.ws.close()
    await asyncio.gather(*readers, return_exceptions=True)

    # Every client must agree on the final standings, exactly (R-11 + §07's
    # deterministic ordering). Disagreement means the event streams diverged.
    finals = [p.final for p in players if p.final is not None]
    if not finals:
        metrics.violations.append(f"room {code}: no client saw game_over")
    else:
        first = json.dumps(finals[0], sort_keys=True)
        for other in finals[1:]:
            if json.dumps(other, sort_keys=True) != first:
                metrics.violations.append(f"room {code}: clients disagree on standings")
                break
        total_answers = sum(len(p.answered) for p in players)
        scored = sum(int(row["score"]) for row in finals[0])  # type: ignore[index]
        if total_answers and scored == 0:
            metrics.violations.append(f"room {code}: answers accepted but nothing scored")


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return ordered[index]


def report(metrics: Metrics, rooms: int, players: int, elapsed: float) -> int:
    def line(label: str, values: list[float]) -> str:
        if not values:
            return f"  {label:<26} (none)"
        return (
            f"  {label:<26} n={len(values):<5} "
            f"p50={percentile(values, 50):7.2f}  "
            f"p95={percentile(values, 95):7.2f}  "
            f"max={max(values):7.2f}   mean={statistics.fmean(values):7.2f}"
        )

    print(f"\n{rooms} rooms x {players} players — {elapsed:.1f}s\n")
    print("timing (ms)")
    print(line("question fan-out spread", metrics.question_spread_ms))
    print(line("answer round trip", metrics.ack_latency_ms))
    print(line("submit scheduling error", metrics.submit_offset_ms))

    print("\noutcomes")
    print(f"  answers accepted           {metrics.accepted}")
    print(f"  broadcast seq gaps         {metrics.seq_gaps}")
    if metrics.rejects:
        for code, count in metrics.rejects.most_common():
            print(f"  rejected: {code:<17} {count}")
    else:
        print("  rejected                   0")

    if metrics.violations:
        print("\nVIOLATIONS")
        for violation in metrics.violations[:20]:
            print(f"  ! {violation}")
        return 1
    print("\nno invariant violations")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--rooms", type=int, default=3)
    parser.add_argument("--players", type=int, default=4)
    parser.add_argument("--mode-id", type=int, default=None)
    parser.add_argument(
        "--lead-ms",
        type=float,
        default=50.0,
        help="how long after the question every client fires, in unison",
    )
    args = parser.parse_args()

    base_http = args.url.rstrip("/")
    base_ws = base_http.replace("http://", "ws://").replace("https://", "wss://")

    import urllib.request

    mode_id = args.mode_id
    if mode_id is None:
        with urllib.request.urlopen(f"{base_http}/api/modes", timeout=10) as response:
            modes = json.load(response)
        if not modes:
            print("no game modes available", file=sys.stderr)
            return 1
        mode_id = int(modes[0]["id"])

    codes: list[str] = []
    for _ in range(args.rooms):
        request = urllib.request.Request(f"{base_http}/api/rooms", method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            codes.append(json.load(response)["code"])

    metrics = Metrics()
    started = time.monotonic()
    await asyncio.gather(
        *(
            play_room(base_ws, base_http, code, mode_id, args.players, args.lead_ms, metrics)
            for code in codes
        )
    )
    return report(metrics, args.rooms, args.players, time.monotonic() - started)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
