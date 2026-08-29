"""The small REST surface: room creation, the mode list, and health."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api")


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


@router.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    return Health(ok=True, rooms=len(request.app.state.registry))
