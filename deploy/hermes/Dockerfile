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

# Build prerequisites (the slim uv image has no git / compilers).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# 1) Hermes Agent, pinned to the commit tested locally. Fetch the commit as
#    a tarball (avoids anonymous git-clone rate limits / HTTP 429 and leaves
#    no .git or token in the image).
RUN curl -fsSL --retry 4 --retry-delay 5 \
      https://codeload.github.com/NousResearch/hermes-agent/tar.gz/a01d2ee \
      -o /tmp/hermes.tgz \
    && mkdir -p /opt/hermes-agent \
    && tar -xzf /tmp/hermes.tgz -C /opt/hermes-agent --strip-components=1 \
    && rm /tmp/hermes.tgz \
    && cd /opt/hermes-agent \
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
