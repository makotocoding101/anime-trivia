"""The small REST surface: room creation, the mode list, and health."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.auth import hash_password, mint_name_token, verify_password
from app.game.economy import next_tier, rank_tier, wallet_key
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


class NameStatus(BaseModel):
    name: str
    claimed: bool


class SessionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=24)
    # Six is a floor, not security theatre: this passphrase guards a place on
    # a leaderboard, and there is no reset path, so a long requirement would
    # mostly generate forgotten names.
    password: str = Field(min_length=6, max_length=128)


class SessionResponse(BaseModel):
    name: str
    token: str
    #: True when this request is what created the account.
    claimed: bool


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


@router.get("/account/{name}", response_model=NameStatus)
async def account_status(request: Request, name: str) -> NameStatus:
    """Whether a name is spoken for, so the form can say "claim" or "sign in"
    before anybody types a passphrase. Deliberately reveals only that — the
    same thing anyone learns by trying to claim it."""
    account = await request.app.state.store.get_account(name)
    return NameStatus(name=name, claimed=account is not None)


@router.post("/account/session", response_model=SessionResponse)
async def account_session(request: Request, body: SessionRequest) -> SessionResponse:
    """Claim a free name, or sign in to one already held.

    One endpoint rather than two because the client cannot be trusted to know
    which case it is in: between a status check and a submit, somebody else
    may have claimed the name. The server decides on the evidence in front of
    it, and the race is settled inside claim_account.
    """
    store = request.app.state.store
    secret: str = request.app.state.session_secret
    name = " ".join(body.name.split())
    if name == "":
        raise HTTPException(status_code=422, detail="empty_name")

    existing = await store.get_account(name)
    if existing is None:
        if await store.claim_account(name, hash_password(body.password)):
            return SessionResponse(
                name=name, token=mint_name_token(wallet_key(name), secret), claimed=True
            )
        # Lost the race between the read and the insert; fall through and
        # treat it as a sign-in attempt, which is what it now is.
        existing = await store.get_account(name)
        if existing is None:
            raise HTTPException(status_code=503, detail="accounts_unavailable")

    if not verify_password(body.password, existing.password_hash):
        raise HTTPException(status_code=401, detail="wrong_passphrase")
    return SessionResponse(
        name=existing.name, token=mint_name_token(wallet_key(name), secret), claimed=False
    )


@router.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    return Health(ok=True, rooms=len(request.app.state.registry))
