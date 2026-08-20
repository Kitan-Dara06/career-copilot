# Hermes Agent — OpenAI-compatible API server for Career Copilot (Azure Container Apps).
#
# Builds hermes-agent (pinned to the locally-tested commit) and embeds the
# Career Copilot repo so Hermes can spawn the MCP server as a subprocess,
# mirroring the local `hermes gateway` setup. config.yaml + .env are rendered
# at container start (see docker-entrypoint.sh) — no secrets in image layers.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV UV_LINK_MODE=copy \
    HERMES_HOME=/root/.hermes \
    PATH="/opt/hermes-agent/.venv/bin:/root/.local/bin:${PATH}"

# 1) Hermes Agent, pinned to the commit tested locally.
RUN git clone https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent \
    && cd /opt/hermes-agent \
    && git checkout a01d2ee \
    && uv sync --no-dev

# 2) Career Copilot — embedded so Hermes can launch the MCP server subprocess.
WORKDIR /app/career-copilot
COPY . .
RUN uv sync --no-dev

# 3) Entrypoint renders ~/.hermes + /app/career-copilot/.env from env vars.
COPY deploy/hermes/docker-entrypoint.sh /usr/local/bin/hermes-entrypoint
RUN chmod +x /usr/local/bin/hermes-entrypoint

EXPOSE 8642
ENTRYPOINT ["/usr/local/bin/hermes-entrypoint"]
