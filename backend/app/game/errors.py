"""Domain errors. The room layer converts these into wire `error` events."""

from __future__ import annotations


class GameError(Exception):
    """A rejected command. `code` is machine-readable and goes on the wire."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
