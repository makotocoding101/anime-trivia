"""The small REST surface: room creation, the mode list, and health."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.game.economy import next_tier, rank_tier
from app.store import WalletRow

router = APIRouter(prefix="/api")

#: Cap on the rankings page. The table is unbounded and the client renders
#: every row it is given, so the ceiling belongs on this side of the wire.
MAX_RANKINGS = 100


class RoomCreated(BaseModel):
    code: str


class Mode(BaseModel):
    id: int
    slug: str
    name: str
    blurb: str | None
    question_count: int
    seconds_per_q: int


class Health(BaseModel):
    ok: bool
    rooms: int


class Stats(BaseModel):
    """The menu screen's live counter. Process-local by construction — see
    InProcessRegistry.stats and invariant 7."""

    open_rooms: int
    live_games: int
    players_online: int


class Wallet(BaseModel):
    name: str
    coins: int
    games_played: int
    wins: int
    best_score: int
    lifetime_score: int
    tier: str
    next_tier: str | None
    coins_to_next: int | None


class Ranked(BaseModel):
    rank: int
    wallet: Wallet


@router.post("/rooms", response_model=RoomCreated, status_code=201)
async def create_room(request: Request) -> RoomCreated:
    room = request.app.state.registry.create_room()
    return RoomCreated(code=room.code)


@router.get("/modes", response_model=list[Mode])
async def list_modes(request: Request) -> list[Mode]:
    """What the lobby shows. Modes are rows (spec §06): this list changes
    with an INSERT, not a deploy."""
    modes = await request.app.state.store.list_modes()
    return [
        Mode(
            id=m.id,
            slug=m.slug,
            name=m.name,
            blurb=m.blurb,
            question_count=m.question_count,
            seconds_per_q=m.seconds_per_q,
        )
        for m in modes
    ]


@router.get("/stats", response_model=Stats)
async def stats(request: Request) -> Stats:
    open_rooms, live_games, players = request.app.state.registry.stats()
    return Stats(open_rooms=open_rooms, live_games=live_games, players_online=players)


@router.get("/rankings", response_model=list[Ranked])
async def rankings(
    request: Request,
    limit: int = Query(default=25, ge=1, le=MAX_RANKINGS),
) -> list[Ranked]:
    """The global table. Empty rather than absent when no database is
    configured — the seed-file store has nowhere to keep history, and a menu
    that renders an empty leaderboard is better than one that errors."""
    rows = await request.app.state.store.list_rankings(limit)
    return [Ranked(rank=r.rank, wallet=_wallet(r.wallet)) for r in rows]


@router.get("/profile/{name}", response_model=Wallet | None)
async def profile(request: Request, name: str) -> Wallet | None:
    """One player's standing, by name — which is the whole identity model in
    v1 (see economy.wallet_key). Null for a name that has never finished a
    game; the client renders that as a fresh wallet rather than an error,
    because it is one."""
    wallet = await request.app.state.store.get_wallet(name)
    return None if wallet is None else _wallet(wallet)


def _wallet(row: WalletRow) -> Wallet:
    upcoming = next_tier(row.coins)
    return Wallet(
        name=row.name,
        coins=row.coins,
        games_played=row.games_played,
        wins=row.wins,
        best_score=row.best_score,
        lifetime_score=row.lifetime_score,
        # Tier is derived on read, never stored: moving a threshold is then an
        # edit to economy.py rather than a migration over every wallet.
        tier=rank_tier(row.coins),
        next_tier=None if upcoming is None else upcoming[0],
        coins_to_next=None if upcoming is None else upcoming[1],
    )


@router.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    return Health(ok=True, rooms=len(request.app.state.registry))
