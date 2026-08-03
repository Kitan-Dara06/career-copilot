#!/bin/sh
# Career Copilot — Docker entrypoint
# Runs alembic migrations before starting the service.
# Selected by docker-compose command (serve --polling or worker).
set -e

echo "=== Career Copilot ==="
echo "Running migrations..."
uv run alembic upgrade head
echo "Migrations complete."

echo "Starting: $@"
exec uv run python -m career_copilot "$@"
