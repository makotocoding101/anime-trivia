"""The small REST surface: room creation and health. Game modes join this
list in M4, read from the database."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api")


class RoomCreated(BaseModel):
    code: str


class Health(BaseModel):
    ok: bool
    rooms: int


@router.post("/rooms", response_model=RoomCreated, status_code=201)
async def create_room(request: Request) -> RoomCreated:
    room = request.app.state.registry.create_room()
    return RoomCreated(code=room.code)


@router.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    return Health(ok=True, rooms=len(request.app.state.registry))
