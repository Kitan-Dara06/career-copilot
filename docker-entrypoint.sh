#!/bin/sh
# Career Copilot — Docker entrypoint
# Runs alembic migrations before starting the service.
# Selected by docker-compose command (serve --polling or worker).
set -e

echo "=== Career Copilot ==="
echo "Running migrations..."
uv run alembic upgrade head
echo "Migrations complete."

# Ship the gitignored profile data into the container (base64 env secrets)
# and make the interest facts the agents read match the canonical profile.
mkdir -p /app/data
if [ -n "${USER_PROFILE_B64:-}" ]; then
  echo "$USER_PROFILE_B64" | base64 -d > /app/data/user_profile.yaml
  echo "user_profile.yaml restored ($(wc -c < /app/data/user_profile.yaml) bytes)"
fi
if [ -n "${USER_SKILLS_B64:-}" ]; then
  echo "$USER_SKILLS_B64" | base64 -d > /app/data/user_skills.yaml
  echo "user_skills.yaml restored ($(wc -c < /app/data/user_skills.yaml) bytes)"
fi
if [ -f /app/data/user_profile.yaml ]; then
  uv run python -m career_copilot.seed_user_facts || echo "[entrypoint] seed_user_facts skipped"
fi

echo "Starting: $@"
exec uv run python -m career_copilot "$@"
