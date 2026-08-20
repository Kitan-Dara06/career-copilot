#!/usr/bin/env bash
# Render Hermes + Career Copilot config from environment, then start the
# Hermes gateway (OpenAI-compatible API server on 0.0.0.0:8642).
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
mkdir -p "$HERMES_HOME"

# 1) Hermes config.yaml — Gemini provider + MCP server in this container.
cat > "$HERMES_HOME/config.yaml" <<'YAML'
model:
  default: gemini-2.5-flash-lite
  provider: gemini
  base_url: https://generativelanguage.googleapis.com/v1beta
memory:
  memory_enabled: false
agent:
  max_turns: 90
  disabled_toolsets:
    - browser
    - code_execution
    - computer_use
    - discord
    - image_gen
    - search
    - skills
    - stt
    - tts
    - video
    - vision
mcp_servers:
  career_copilot:
    command: "uv"
    args:
      - "--directory"
      - "/app/career-copilot"
      - "run"
      - "python"
      - "-m"
      - "backbone.mcp.server"
    enabled: true
    timeout: 120
    connect_timeout: 30
    supports_parallel_tool_calls: false
    tools:
      include:
        - career.profile.get
        - career.papers.search
        - career.professors.search
        - career.professors.web_search
        - career.jobs.search
        - career.planning.get_summary
        - career.planning.list_workspaces
        - career.planning.get_workspace
        - career.planning.list_goals
        - career.planning.list_tasks
        - career.planning.list_decisions
        - career.planning.list_notes
        - career.planning.list_artifacts
        - career.planning.create_workspace
        - career.planning.add_goal
        - career.planning.add_task
        - career.planning.record_decision
        - career.planning.supersede_decision
        - career.planning.update_task_status
        - career.planning.add_note
        - career.planning.switch_workspace
      resources: false
      prompts: false
YAML

# 2) In-container Career Copilot .env so the MCP subprocess reaches
#    Supabase / Qdrant / Tavily exactly like the bot does.
cat > /app/career-copilot/.env <<ENV
DATABASE_URL=${DATABASE_URL:-}
QDRANT_URL=${QDRANT_URL:-}
QDRANT_API_KEY=${QDRANT_API_KEY:-}
TAVILY_API_KEY=${TAVILY_API_KEY:-}
VOYAGE_API_KEY=${VOYAGE_API_KEY:-}
GEMINI_API_KEY=${GEMINI_API_KEY:-}
DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}
FIRECRAWL_API_KEY=${FIRECRAWL_API_KEY:-}
GITHUB_TOKEN=${GITHUB_TOKEN:-}
ENV

# 3) Hermes .env — the gateway reads API_SERVER_* and the provider key here.
cat > "$HERMES_HOME/.env" <<ENV
GOOGLE_API_KEY=${GOOGLE_API_KEY:-}
DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}
API_SERVER_ENABLED=${API_SERVER_ENABLED:-true}
API_SERVER_KEY=${API_SERVER_KEY:-}
API_SERVER_HOST=${API_SERVER_HOST:-0.0.0.0}
API_SERVER_PORT=${API_SERVER_PORT:-8642}
ENV

echo "Rendered Hermes config. Starting gateway (API_SERVER_HOST=${API_SERVER_HOST:-0.0.0.0}:${API_SERVER_PORT:-8642})..."
exec /opt/hermes-agent/.venv/bin/python /opt/hermes-agent/hermes gateway
