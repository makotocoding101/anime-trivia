# Multi-stage: uv resolves the locked environment in a throwaway layer; the
# runtime image carries only the venv, the app, and a non-root user.
#
# One process, one worker — the deployment shape IS invariant 7. uvicorn's
# default worker count is 1 and app startup asserts it stays that way.

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy

# Dependency layer first so code edits don't re-resolve the world.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY app ./app
COPY seed ./seed
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker-entrypoint.sh ./


FROM python:3.12-slim

RUN useradd --create-home --uid 1000 trivia
WORKDIR /app

COPY --from=build --chown=trivia:trivia /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER trivia
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["sh", "./docker-entrypoint.sh"]
