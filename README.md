# OtaKizu

**A real-time multiplayer anime trivia game.** Up to eight players join a room by code, everyone
sees the same question at the same instant, a server-authoritative clock counts down, answers lock
in, and the scoreboard moves live.

### ▶ [anime-trivia.vercel.app](https://anime-trivia.vercel.app)

Open it in two browser windows to play against yourself. API: [otakizu-api.fly.dev/api/health](https://otakizu-api.fly.dev/api/health)

<!-- TODO: drop a gameplay GIF here — it is the single highest-value thing you can add to this file -->

---

## What's actually interesting about it

The content is anime trivia. The engineering is **concurrency and real-time state sync**, and every
significant decision trades horizontal scale away for provable correctness.

Eight people answer within the same few milliseconds. That is a read-then-write race: check the
round is open, check this player hasn't answered, compute points, add to their score. Run two of
those interleaved and one player's answer silently vanishes.

The usual fix is a lock around the state. This doesn't use one — anywhere.

### One writer per room

Each room is a single `asyncio.Task` that owns its state. The eight WebSocket handlers cannot touch
that state at all; the only thing they can do is put a command on the room's queue. The room task
drains it one at a time.

```python
while True:
    cmd = await self.inbox.get()               # next command
    events = transition(self.state, cmd, now)  # apply it
```

There is no second writer to race with. Not "the writers coordinate" — there is only one.

### `transition()` contains no `await`

asyncio is single-threaded: your code can only be interrupted at an `await`. So a function with no
`await` in it runs start to finish with nothing able to interleave. Check-then-write becomes atomic
by construction, for free.

That guarantee rests entirely on an absence, which is a fragile thing to rely on — so CI walks the
AST of `app/game/transition.py` and **fails the build** if an `await` ever appears in it.

### The deadline decides, not the timer

Whether an answer counts is decided by comparing *server receive time* against a stored monotonic
deadline — never by checking whether the timer task has fired. `asyncio.sleep` guarantees *at least*
the requested duration, so under load a timer-based check would let server lag quietly change the
rules of the game.

### Fencing tokens, because `cancel()` isn't enough

`Task.cancel()` is best-effort: a task already past its sleep is on the ready queue, where
cancellation arrives too late. So every deferred action captures `round_seq` when it's created and
no-ops on wake if it has moved. Applies to round timers, reveal timers, reconnect reapers and the
room sweeper.

### One slow client can't stall a room

Fan-out uses `put_nowait` into a bounded 64-message queue per connection, drained by a separate
writer task. On `QueueFull` that connection is dropped. The room task never blocks on a socket, so
one person on hotel wifi cannot freeze the round for the other seven.

### Reconnect sends a snapshot, never a replay

One code path that is correct for every gap length, including "gone for the entire game". Resume
tokens are HMAC-signed: a `player_id` appears in every scoreboard payload, so possession of one must
not be enough to steal a seat.

---

## Measured

From `loadtest/` against the running server:

| | |
|---|---|
| Concurrent WebSocket clients | **192** across 24 rooms, one process |
| Simultaneous answer submissions | **960**, all accepted |
| Rejected writes / dropped broadcasts / invariant violations | **0 / 0 / 0** |
| Question fan-out spread | p50 **0 ms** (below the platform's 16 ms timer resolution) |
| Answer round-trip under full load | p50 **47 ms** |
| Database round-trips per game | **2** |
| Automated tests | **245** (190 backend, 55 frontend) |

That last one deserves a note: a naive implementation writes once per answer per player per round —
roughly 50 queries for an 8-player, 5-round game. This does one `SELECT` at game start to fill the
deck and one batched `INSERT` at game end to record it, and touches Postgres **zero** times during a
round. Network latency is structurally absent from the path that decides who won.

---

## Architecture

```
backend/app/
  game/      pure domain — imports nothing framework-shaped, enforced by import-linter
  rooms/     the actor: inbox, run loop, timers, registry, bounded connections
  api/       websocket lifecycle, REST surface, credentials
  db/        models, repositories, migrations
frontend/src/
  store/     one reducer applying server events, mirroring transition()
```

**The `app/game/` import boundary is the load-bearing one.** Because it never imports FastAPI,
SQLAlchemy or `websockets`, the entire game — every hazard above — is testable in microseconds with
no server, socket or database running. A CI contract enforces it.

Two counters, deliberately distinct: `seq` increments on every emitted event and drives client-side
gap detection; `round_seq` increments only at round boundaries and is the fencing token. Merging
them works right up until an extra event is emitted mid-round.

All time reads go through a `Clock` protocol, so `FakeClock.advance(20.0)` tests a twenty-second
round instantly. **There is no `sleep` anywhere in the test suite** — which is why 245 tests run in
under ten seconds.

### Data model

Postgres holds question content and post-game history; memory holds the live game. Game modes are
**rows, not code** — `game_mode` stores a query specification, so adding a genre is an `INSERT`
rather than a deploy.

Player identity is a normalised display name, claimed with a passphrase (scrypt, per-hash
parameters) and proven on join with an HMAC token. Claiming is race-free via
`ON CONFLICT DO NOTHING ... RETURNING`, so two people racing for the same free name cannot both be
told they got it.

---

## Running it locally

```bash
# backend (from backend/)
python -m uv run uvicorn app.main:app --reload     # ws at /ws/rooms/{code}
python -m uv run pytest                            # full suite
python -m uv run python -m loadtest.run --rooms 8 --players 8

# frontend (from frontend/)
npm run dev                                        # :5173, proxies /api and /ws
npm test
```

Without `DATABASE_URL` the server falls back to the seed file with one synthetic mode and no
persistence, so it runs with no database at all. With it, modes come from `game_mode` and results
are recorded.

Deployment runbook, cost breakdown and the AWS alternative: [`deploy/`](deploy/).

---

## Deployed

| Piece | Where | Cost |
|---|---|---|
| React bundle | Vercel CDN | $0 |
| API + WebSockets | Fly.io, **exactly one machine** | $3.32/mo |
| Postgres | Neon | $0 |

The single machine is not cost-saving — it is invariant 7. Two machines would be two independent
room registries, so two players entering the same code would land in different games with no error
on either side. `app/config.py` refuses to boot on `WEB_CONCURRENCY > 1`, but it reads environment
variables inside one process and cannot see a peer machine, so `fly.toml` pins the count as well.

The accepted cost of that: there is nothing to drain onto, so **every deploy ends every live game.**

---

## Known limitations

Stated rather than hidden, because each one is a deliberate trade:

- **No horizontal scale.** `RoomRegistry` is a protocol and is the seam — going multi-worker means
  implementing it over Redis with room-to-worker affinity, a routing change rather than a rewrite.
- **Rooms are not durable.** A restart ends in-flight games. Room state is memory-only on purpose;
  persisting it would put the database back in the timing-critical path.
- **Name ownership is not full accounts.** No email means no password reset: lose the passphrase and
  the name is gone.
- **Coins have no sink.** They accumulate into rank tiers but there is nothing to spend them on yet,
  which makes them closer to a prestige score than a currency.
- **`ORDER BY random()`** samples the deck. Correct and fast at this bank size; it becomes a full
  scan plus a sort long before it becomes a problem worth solving.
