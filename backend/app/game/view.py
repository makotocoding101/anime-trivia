"""Redacted, JSON-ready views of room state.

This is where R-12 lives: the correct option id appears in a view only once
the phase has made it public (REVEAL / GAME_OVER for the current round).
The snapshot a reconnecting client receives is built here too (R-11).
"""

from __future__ import annotations

from app.game.state import ConnState, Phase, PlayerId, RoomState


def public_view(state: RoomState, viewer_id: PlayerId) -> dict[str, object]:
    """Everything one client may know right now. Never deltas."""
    view: dict[str, object] = {
        "room_code": state.code,
        "you": viewer_id,
        "phase": state.phase.name,
        "round_seq": state.round_seq,
        "round_index": state.round_index,
        "question_count": len(state.deck),
        "phase_deadline": state.phase_deadline,
        "players": [
            {
                "id": p.id,
                "name": p.name,
                "seat": p.seat,
                "score": p.score,
                "streak": p.streak,
                "conn": p.conn.name,
                "is_host": p.is_host,
                "ready": p.ready,
                "spectating": p.spectating_until > state.round_seq,
            }
            for p in sorted(state.players.values(), key=lambda pl: pl.seat)
            if p.conn is not ConnState.GONE
        ],
        "question": None,
        "reveal": None,
    }

    if state.round is not None and state.phase in (Phase.QUESTION_OPEN, Phase.LOCKED):
        q = state.round.question
        answer = state.round.answers.get(viewer_id)
        view["question"] = {
            "id": q.id,
            "kind": q.kind,
            "prompt": q.prompt,
            "options": [{"id": o.id, "label": o.label} for o in q.options],
            "media_ref": q.media_ref,
            "opened_at": state.round.opened_at,
            "deadline": state.round.deadline,
            # Your own answer only — nobody else's, and no correctness (R-12).
            "your_answer": None if answer is None else answer.option_id,
        }

    if state.phase in (Phase.REVEAL, Phase.GAME_OVER) and state.history:
        last = state.history[-1]
        view["reveal"] = {
            "round_index": last.index,
            "question_id": last.question_id,
            "correct_option_id": last.correct_option_id,
            "entries": {
                pid: {"option_id": opt, "elapsed_ms": ms, "points": pts}
                for pid, (opt, ms, pts) in last.entries.items()
            },
        }

    return view
