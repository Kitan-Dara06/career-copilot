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
