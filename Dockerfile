# Career Copilot — Dockerfile
#
# Two services (bot + worker) share this image. The entrypoint is selected
# via the CMD in the compose file, not baked into the image.
#
# Build:  docker build -t career-copilot:latest .
# Run:    docker compose up
#

FROM python:3.12-slim

WORKDIR /app

# System deps for asyncpg + uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy just the lock file first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Expose port for webhook mode (optional; polling mode doesn't need it)
EXPOSE 8080

# Entrypoint runs migrations, then starts the service.
COPY docker-entrypoint.sh /app/
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["serve", "--polling"]
