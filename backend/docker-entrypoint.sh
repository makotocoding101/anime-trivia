#!/bin/sh
set -e

# Single instance by design (invariant 7), so migrate-on-boot has no race to
# worry about: nothing else is applying migrations concurrently. Without a
# DATABASE_URL the app runs on the seed store and there is nothing to migrate.
if [ -n "$DATABASE_URL" ]; then
  echo "applying migrations..."
  alembic upgrade head
  if [ "$SEED_ON_START" = "1" ]; then
    echo "seeding (if empty)..."
    python -m app.db.seed --if-empty
  fi
fi

# --proxy-headers: a reverse proxy (Caddy) terminates TLS in front of us and
# forwards X-Forwarded-*, so logs and any scheme checks see the real client.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips '*'
