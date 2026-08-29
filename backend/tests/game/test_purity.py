"""Structural enforcement of invariants 1-2 and the import boundary.

These tests convert the central claims of the architecture from things you
remember into things the build enforces (spec §11).
"""

from __future__ import annotations

import ast
from pathlib import Path

GAME_DIR = Path(__file__).parents[2] / "app" / "game"

# clock.py is the one module allowed to be async and to import asyncio: it
# exists precisely to contain time-as-IO behind a protocol.
ASYNC_EXEMPT = {"clock.py"}

# Stdlib modules the domain may use. Frameworks are conspicuously absent —
# that absence is the point (import boundary).
ALLOWED_STDLIB = {
    "__future__",
    "collections",
    "collections.abc",
    "dataclasses",
    "enum",
    "functools",
    "itertools",
    "json",
    "math",
    "pathlib",
    "random",
    "typing",
}

ASYNC_NODES = (ast.Await, ast.AsyncFunctionDef, ast.AsyncWith, ast.AsyncFor)


def game_modules() -> list[Path]:
    files = sorted(GAME_DIR.glob("*.py"))
    assert files, f"no modules found under {GAME_DIR}"
    return files


def test_domain_contains_no_await() -> None:
    """Invariant 2 / R-07: no await means no interleaving point, which is what
    makes every check-then-write in transition() atomic."""
    offenders: list[str] = []
    for path in game_modules():
        if path.name in ASYNC_EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ASYNC_NODES):
                offenders.append(f"{path.name}:{node.lineno} {type(node).__name__}")
    assert not offenders, (
        "async constructs found in the synchronous domain — this reopens the "
        f"interleaving window R-07 closes: {offenders}"
    )


def test_domain_imports_no_frameworks() -> None:
    """The app/game/ boundary: pure stdlib plus app.game itself. If this test
    fails, the game just became untestable without a running server."""
    offenders: list[str] = []
    for path in game_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [node.module]
            for name in names:
                if name == "asyncio" and path.name in ASYNC_EXEMPT:
                    continue
                if name in ALLOWED_STDLIB:
                    continue
                if name == "app.game" or name.startswith("app.game."):
                    continue
                offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, f"framework-shaped imports leaked into app/game/: {offenders}"


def test_transition_is_fully_synchronous() -> None:
    """The spec's lint rule (§11), verbatim: no await in transition.py."""
    tree = ast.parse((GAME_DIR / "transition.py").read_text(encoding="utf-8"))
    assert not any(isinstance(n, ASYNC_NODES) for n in ast.walk(tree))
