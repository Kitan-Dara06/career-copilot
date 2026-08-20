# Career Copilot

**A multi-agent personal assistant for academic career development.**

Career Copilot is a single-user, multi-agent system that helps you track research papers, discover professors, hunt for jobs, and find open-source contributions — all through one Telegram bot.

## 🎯 Purpose

- **Paper Tracker** — Daily arXiv digests matched to your research interests, plus professor watchlist tracking
- **Job Hunter** (v0.2) — Structured job lead tracking
- **Contribution Finder** (v0.3) — Open-source contribution opportunities

## 🏗️ Architecture

```
Telegram → Dispatcher → Agent Runtime → Tool Registry + Memory Layer
                  ↓
       arxiv, tavily, firecrawl, notion,
       telegram, vector (Voyage 3), etc.
                  ↓
             Postgres + Qdrant Cloud
```

Key design choices:
- **PostgreSQL** for structured storage, **Qdrant Cloud** for vector search
- **Voyage 3** (1024-dim) for embeddings — SOTA for IR
- **Hermes** as the primary LLM (strong tool-use)
- Versioned prompts with full run logging
- Engagement-based evaluation
- Single-tenant, no auth complexity

## 🚀 Quick Start

```bash
# Setup
uv sync
cp .env.example .env  # Fill in your API keys

# Run
make dev               # Lint + typecheck + test
python -m career_copilot serve
```

## 🔑 Required API Keys

| Service | Purpose | Get it |
|---------|---------|--------|
| Telegram Bot | Bot interface | @BotFather |
| Voyage AI | Embeddings (Voyage 3) | voyage.ai |
| Hermes | LLM | Nous Research / OpenRouter |
| Tavily | Web research | tavily.com |
| Firecrawl | Deep URL scraping | firecrawl.dev |
| Notion | Saved papers storage | notion.so/my-integrations |
| Qdrant Cloud | Vector database | cloud.qdrant.io |

## 📐 Project Structure

```
├── backbone/          # Shared library: tools, memory, dispatcher, eval
├── agents/            # Agent modules (paper_tracker, job_hunter, ...)
├── deploy/            # Azure Bicep templates + deployment scripts
├── migrations/        # Alembic database migrations
├── scripts/           # Seeding and data scripts
└── docs/              # Design docs, architecture diagrams
```

## 📊 Current Status

**v0.1** — Paper Tracker agent: daily arXiv digest with interest matching and professor tracking.

[Architecture details →](docs/paper-tracker-design.md)
[Implementation guide →](implementation-guide.md)

## 🔥 Hermes — emergency kill switch

The agentic layer runs as a **separate Azure Container App** (`career-copilot-hermes`, see
`deploy/hermes/`). If it needs to be taken down fast (suspicious traffic, runaway cost,
security concern), scale it to zero — no code change, the bot stays up:

```bash
az containerapp update \
  --name career-copilot-hermes \
  --resource-group career-copilot \
  --min-replicas 0 --max-replicas 0
```

The bot will keep serving all slash commands; only `/ask` / bare-text (agentic) responses
goT off until the Hermes app is brought back.

> **Secrets required for the Hermes deploy**: `GOOGLEAPIKEY` (Gemini provider) and
> `HERMESAPIKEY` (bearer key the bot uses to call the public Hermes endpoint). Add both to
> the repository's GitHub Actions secrets before/with the first Hermes deploy, or the
> container boots but Gemini auth fails.
