# Career Copilot — Implementation Guide

**Project:** Career Copilot
**Document version:** v0.1
**Purpose:** File-by-file, function-by-function guide for building the v0.1 system
**Last updated:** 2026-07-16

This doc is meant to be handed to an AI coding agent (OpenCode Go) or worked through manually. It specifies **what to build** (purpose, signatures, contracts, tests) without writing the implementations. Phases are ordered so each is testable in isolation.

---

## 0. Conventions and stack

### 0.1 Language and runtime
- **Python 3.11+** (match type-hint support, async improvements)
- `pyproject.toml` for project metadata and tool config
- `uv` or `poetry` for dependency management (recommend `uv` for speed)

### 0.2 Web framework + bot
- **FastAPI** for the dispatcher HTTP server (Telegram webhook + internal API)
- **python-telegram-bot v21+** for the bot
- **uvicorn** as the ASGI server

### 0.3 Database
- **PostgreSQL 16+** with the **pgvector** extension
- **SQLAlchemy 2.0** (async) for ORM
- **Alembic** for migrations
- **asyncpg** as the async driver

### 0.4 External APIs (HTTP clients)
- **httpx** (async) for all external API calls
- **voyageai** SDK or raw httpx (Voyage 3 embeddings)
- **tavily-python** SDK
- **firecrawl-py** SDK
- **notion-client** SDK

### 0.5 Observability
- **structlog** for structured logging
- **OpenTelemetry** SDK for traces
- **Azure Application Insights** as the backend (OTLP exporter)

### 0.6 Testing
- **pytest** + **pytest-asyncio**
- **respx** for mocking httpx
- **pytest-postgresql** or testcontainers-postgres for integration tests
- **faker** for test data

### 0.7 Code style
- **ruff** for linting and formatting
- **mypy --strict** for type checking
- Google-style docstrings
- 100% type coverage on public interfaces (non-`# type: ignore`)

### 0.8 Repo layout
Already specified in `paper-tracker-design.md` §2. Reuse the structure verbatim.

---

## 1. Phase 0 — Prerequisites

**Goal:** Local and cloud infrastructure ready, all API keys in hand.

**Exit criteria:**
- `career-copilot/` repo created locally and on GitHub
- Azure subscription active; resource group created
- All API keys obtained and stored in `.env` (local) and Key Vault (Azure)
- Local Python 3.11+ installed; `uv` or `poetry` installed
- PostgreSQL + pgvector available locally (Docker container or local install)

### 1.1 Tasks
- [ ] Create GitHub repo, init with `README.md` and `.gitignore`
- [ ] Create Azure resource group `rg-career-copilot-prod`
- [ ] Apply for API keys:
  - [ ] **Telegram** bot via @BotFather; record bot token + user chat_id
  - [ ] **Voyage AI** at voyage.ai
  - [ ] **Tavily** at tavily.com
  - [ ] **Firecrawl** at firecrawl.dev
  - [ ] **Semantic Scholar** at semanticscholar.org (free, no key for v0.1)
  - [ ] **Notion** integration at notion.so/my-integrations; share target DBs with it
  - [ ] **Hermes** API key (wherever you host it — Nous Research, OpenRouter, etc.)
- [ ] Set up local PostgreSQL:
  ```bash
  docker run -d --name pg-career-copilot -e POSTGRES_PASSWORD=dev -p 5432:5432 \
    pgvector/pgvector:pg16
  ```
- [ ] Create `.env.example` (committed) and `.env` (gitignored) with all keys

### 1.2 Files created
- `README.md` — project pitch
- `.gitignore` — Python + secrets
- `.env.example` — all required env vars documented
- `pyproject.toml` — project metadata, dependencies, tool config

### 1.3 Tests
None (infrastructure phase).

---

## 2. Phase 1 — Project skeleton

**Goal:** Empty-but-structured repo. `python -m career_copilot` runs and prints "OK".

**Exit criteria:**
- All directories in §2 of `paper-tracker-design.md` exist
- `pyproject.toml` has all dependencies installed
- `make dev` runs linter, type checker, and tests
- CI (GitHub Actions) runs the same on push

### 2.1 Files to create

```
career_copilot/
├── __init__.py
├── __main__.py            # entry point; prints "OK" for v0.1
config/
├── __init__.py
├── settings.py            # Pydantic Settings, loads from env
├── logging.py             # structlog config
└── paths.py               # Path constants
```

### 2.2 Key files

**`config/settings.py`**
- Pydantic `BaseSettings` subclass
- Reads from `.env` and env vars
- Sections: `telegram`, `voyage`, `tavily`, `firecrawl`, `notion`, `hermes`, `database`, `azure`
- Validates at startup; refuses to boot if required keys missing
- Tests: `test_settings.py` — load with mock env, assert validation, assert defaults

**`config/logging.py`**
- Configures structlog with JSON output
- Binds `agent`, `task_id`, `prompt_name` to every log line
- Tests: assert JSON output, assert bound context appears

**`config/paths.py`**
- Constants for `PROJECT_ROOT`, `DATA_DIR`, `PROMPTS_DIR`, `CORPUS_DIR`
- Tests: assert paths exist after init

**`pyproject.toml`**
- Project metadata
- Dependencies: list per §0.4
- Dev dependencies: pytest, pytest-asyncio, respx, ruff, mypy
- Tool configs: ruff rules, mypy strict, pytest asyncio_mode=auto

### 2.3 Tests
- `test_settings.py`
- `test_logging.py`
- `test_paths.py`
- `test_smoke.py` — `python -m career_copilot` exits 0

### 2.4 Exit checklist
- [ ] `uv sync` (or `poetry install`) succeeds
- [ ] `make lint` passes
- [ ] `make typecheck` passes
- [ ] `make test` passes
- [ ] GitHub Actions runs the same on push

---

## 3. Phase 2 — Database

**Goal:** Postgres + pgvector reachable. Migrations run. Models defined.

**Exit criteria:**
- `alembic upgrade head` runs cleanly against the local DB
- `make migrate` and `make migrate-new "msg"` work
- SQLAlchemy async session factory works
- All v0.1 tables exist with the right schema

### 3.1 Files to create

```
migrations/
├── env.py                 # Alembic env, async-aware
├── script.py.mako
└── versions/
    └── 0001_initial.py    # the v0.1 schema

backbone/db/
├── __init__.py
├── session.py             # async_session factory
├── base.py                # Declarative base
└── types.py               # VECTOR type helper for SQLAlchemy
```

### 3.2 Key files

**`migrations/env.py`**
- Async-aware (uses `async_engine_from_config`)
- Reads DB URL from settings
- Supports offline mode for SQL generation

**`migrations/versions/0001_initial.py`**
- Creates extensions: `vector`
- Creates all v0.1 tables per `paper-tracker-design.md` §3.6 + §4.5 + §4.10:
  - `user_facts`, `interest_vectors`, `short_term_memory`
  - `digests`, `digest_items`, `feedback_log`
  - `professors`, `professor_papers`, `professor_interest_vectors`
  - `prompt_runs`
- Creates indexes per the design doc
- Downgrade: drops all

**`backbone/db/session.py`**
- `async_session_factory()` — returns `async_sessionmaker[AsyncSession]`
- `get_session()` — async context manager yielding a session, commits on success, rolls back on error
- Tests: `test_session.py` — open session, execute query, commit, verify

**`backbone/db/types.py`**
- `VECTOR(1024)` custom SQLAlchemy type wrapping pgvector's `vector`
- `Vector` generic for any dimension
- Tests: assert column type is `vector(1024)` in DDL

### 3.3 Tests
- `test_migrations.py` — apply + downgrade + re-apply
- `test_session.py` — round-trip insert/select
- `test_vector_type.py` — store a 1024-dim vector, read it back, assert equality

### 3.4 Exit checklist
- [ ] `alembic upgrade head` works on empty DB
- [ ] `alembic downgrade base` works
- [ ] All tables visible via `\dt` in psql
- [ ] pgvector extension listed in `\dx`

---

## 4. Phase 3 — Memory layer

**Goal:** Three-tier memory works. Read/write with namespace enforcement.

**Exit criteria:**
- Can read/write to all three layers via a unified interface
- Namespace enforcement rejects cross-namespace reads
- Short-term memory TTLs correctly
- Long-term writes are versioned

### 4.1 Files to create

```
backbone/memory/
├── __init__.py
├── working.py             # in-process dict
├── short_term.py          # Postgres + TTL
├── long_term.py           # Postgres + pgvector, versioned
├── namespaces.py          # namespace constants + access control
└── types.py               # MemoryRecord, MemoryQuery, etc. (pydantic)
```

### 4.2 Key types (`memory/types.py`)

```python
class MemoryLayer(StrEnum):
    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"

class MemoryRecord(BaseModel):
    namespace: str
    key: str
    value: Any                          # JSON-serializable
    layer: MemoryLayer
    embedding: list[float] | None = None  # for vector layers
    metadata: dict = {}

class MemoryQuery(BaseModel):
    namespace: str
    key: str | None = None              # exact lookup if provided
    embedding: list[float] | None = None  # for similarity search
    k: int = 5
    ttl_after: datetime | None = None    # for short-term filtering
```

### 4.3 Key functions

**`backbone/memory/working.py`**
- `set(task_id: str, record: MemoryRecord) -> None`
- `get(task_id: str, key: str) -> MemoryRecord | None`
- `clear(task_id: str) -> None` — called at task end
- Tests: in-process dict, no DB

**`backbone/memory/short_term.py`**
- `set(record: MemoryRecord, ttl: timedelta) -> None` — writes to `short_term_memory`, computes `expires_at`
- `get(query: MemoryQuery) -> list[MemoryRecord]` — filters by namespace + key/TTL
- `purge_expired() -> int` — deletes expired rows, returns count
- Background task: `schedule_purge_every_5min()`
- Tests: insert, read, wait for TTL, purge, assert gone

**`backbone/memory/long_term.py`**
- `set(record: MemoryRecord) -> int` — versioned insert, returns version
- `get(query: MemoryQuery) -> list[MemoryRecord]` — vector search if `query.embedding` provided
- `rollback(namespace: str, key: str, to_version: int) -> None` — reverts to a prior version
- `history(namespace: str, key: str) -> list[MemoryRecord]` — all versions
- Tests: insert v1, v2, query latest, rollback, assert v1 returned

**`backbone/memory/namespaces.py`**
- Constants for all v0.1 namespaces (per `paper-tracker-design.md` §3.5)
- `declare_access(agent: str, namespace: str, read: bool, write: bool) -> None`
- `check_access(agent: str, namespace: str, op: Literal["read", "write"]) -> bool` — raises `NamespaceAccessError` if denied
- Used by the dispatcher to gate memory calls
- Tests: declare, attempt forbidden access, assert raises

### 4.4 Tests
- `test_working.py` — basic set/get/clear
- `test_short_term.py` — TTL behavior, namespace filter
- `test_long_term.py` — versioning, vector search, rollback
- `test_namespaces.py` — access control

---

## 5. Phase 4 — Tool registry

**Goal:** All v0.1 tools implemented and registered. The LLM can call any of them.

**Exit criteria:**
- `Tool` base class + `ToolRegistry` working
- Every tool in `paper-tracker-design.md` §3.3 implemented (or stubbed for v0.2)
- Each tool has a JSON schema for input and output
- `registry.list_tools(agent="paper_tracker")` returns the right subset
- Respx-mocked tests for each external API tool

### 5.1 Files to create

```
backbone/tools/
├── __init__.py
├── base.py                # Tool ABC, ToolContext
├── registry.py            # ToolRegistry, registration decorator
├── arxiv.py               # arxiv.fetch_recent, arxiv.fetch_author
├── vector.py              # vector.embed, vector.search, vector.upsert
├── structured.py          # structured.get/set/delete
├── telegram.py            # telegram.send_message, send_digest, send_card
├── tavily.py              # tavily.search, tavily.extract
├── firecrawl.py           # firecrawl.scrape
├── notion.py              # notion.create_page, notion.update_page
├── email.py               # email.queue_draft, email.send_now
├── http.py                # http.fetch
├── memory.py              # memory.feedback
├── scheduler.py           # scheduler.schedule
└── github.py              # stubs for v0.3
```

### 5.2 Key abstractions

**`backbone/tools/base.py`**
- `class Tool(ABC, Generic[TIn, TOut])`:
  - `name: str`
  - `description: str`
  - `input_schema: type[TIn]`
  - `output_schema: type[TOut]`
  - `cost_hint: CostHint`
  - `latency_hint: LatencyHint`
  - `owner: str` (agent name)
  - `async def __call__(self, ctx: ToolContext, input: TIn) -> TOut`
- `class ToolContext`:
  - `agent: str`
  - `task_id: str`
  - `memory: MemoryInterface`
  - `prompt_logger: PromptRunLogger`
  - `settings: Settings`

**`backbone/tools/registry.py`**
- `class ToolRegistry`:
  - `register(tool: Tool) -> None`
  - `get(name: str) -> Tool`
  - `list_for_agent(agent: str) -> list[Tool]` — filters by `owner` + `acl`
  - `schemas_for_llm(agent: str) -> list[dict]` — returns JSON schemas formatted for the LLM
- `@register_tool` decorator that auto-registers on import

### 5.3 Per-tool implementation specs

For each tool, the spec includes: name, description (for the LLM), input/output pydantic models, which external API it calls, what to do on failure, what to log.

**`arxiv.py`**
- `arxiv.fetch_recent(categories: list[str], since: datetime, max: int) -> list[Paper]`
  - Calls arXiv API (`http://export.arxiv.org/api/query`)
  - Returns parsed list of `Paper` (id, title, authors, abstract, published, categories, pdf_url)
  - Caches results in short-term memory (TTL 1 day) to avoid duplicate fetches
- `arxiv.fetch_author(name: str, since: datetime, max: int) -> list[Paper]`
  - Uses `au:<name>` query; resolves canonical name (most results in window)
  - Returns same `Paper` shape
- Tests: respx-mock arXiv responses, assert parsing, assert caching

**`vector.py`**
- `vector.embed(texts: list[str]) -> list[list[float]]` — calls Voyage 3, returns 1024-dim vectors
  - Batches up to 128 texts per call
  - Caches by hash of input
  - Falls back to local MiniLM if Voyage is down (lower quality, logged)
- `vector.search(namespace: str, query_embedding: list[float], k: int) -> list[ScoredRecord]`
  - Uses pgvector cosine distance: `ORDER BY embedding <=> :q LIMIT :k`
  - Filters by namespace
- `vector.upsert(namespace: str, key: str, embedding: list[float], metadata: dict) -> None`
  - Upserts into the namespace's vector table
- Tests: mock Voyage API, assert embed; insert + search + assert top hit

**`structured.py`**
- Generic CRUD over typed tables. Uses SQLAlchemy models from Phase 2.
- `structured.get(table: str, key: Any) -> dict | None`
- `structured.set(table: str, key: Any, value: dict) -> None`
- `structured.delete(table: str, key: Any) -> None`
- Type-checked against the table's SQLAlchemy model
- Tests: round-trip on a test table

**`telegram.py`**
- `telegram.send_message(chat_id: str, text: str, reply_markup: InlineKeyboardMarkup | None) -> MessageId`
- `telegram.send_digest(chat_id: str, items: list[DigestItem], template: str) -> MessageId`
- `telegram.send_card(chat_id: str, card: Card) -> MessageId`
- Calls python-telegram-bot under the hood
- Tests: mock Bot, assert method called with right args

**`tavily.py`**
- `tavily.search(query: str, max_results: int, include_domains: list[str] | None) -> list[SearchResult]`
  - Uses Tavily API, returns clean results (title, url, content, score)
- `tavily.extract(url: str) -> ExtractedContent`
  - Single URL extraction
- Tests: respx-mock

**`firecrawl.py`**
- `firecrawl.scrape(url: str, formats: list[str] = ["markdown"]) -> ScrapedContent`
- Tests: respx-mock

**`notion.py`**
- `notion.create_page(database_id: str, properties: dict) -> PageId`
- `notion.update_page(page_id: str, properties: dict) -> None`
- Tests: mock Notion SDK

**`email.py`**
- `email.queue_draft(to: str, subject: str, body: str, metadata: dict) -> DraftId`
  - Writes to a `pending_drafts` table in Postgres (not sent!)
  - Sends an email to the user with the rendered body + reply instructions
  - Starts a listener for the reply (via dedicated alias)
- `email.send_now(draft_id: str) -> None`
  - Only callable by the email listener after parsing an approving reply
  - Sends via configured SMTP / SendGrid / etc.
  - Marks draft as `sent` in DB
- Tests: assert draft is created in DB, not sent; mock SMTP, assert send

**`http.py`**
- `http.fetch(url: str, cache_ttl: timedelta | None = None) -> str`
  - Returns body text
  - Optional cache in short-term memory
- Tests: respx-mock

**`memory.py`**
- `memory.feedback(item_id: str, signal: FeedbackSignal) -> None`
  - Writes to `feedback_log` table
  - Triggers weekly retune counter
- Tests: assert log written

**`scheduler.py`**
- `scheduler.schedule(job: str, cron: str, payload: dict) -> JobId`
  - Persists to a `scheduled_jobs` table
  - Background worker polls and triggers dispatcher
- Tests: schedule + advance time + assert triggered

### 5.4 Tests
- One `test_<tool>.py` per tool with respx-mocked external calls
- `test_registry.py` — register, lookup, list_for_agent, acl filtering
- Integration: `test_tool_chain.py` — embed → search → upsert

---

## 6. Phase 5 — Prompt registry + run logger

**Goal:** Prompts are versioned YAML; every LLM call is logged.

**Exit criteria:**
- `loader.load(agent, name, version) -> RenderedPrompt` works
- `run_logger.log(...)` persists to `prompt_runs`
- Versioning supports side-by-side comparison
- Cost calculation is correct (per-token pricing for the model)

### 6.1 Files to create

```
backbone/prompt_registry/
├── __init__.py
├── loader.py              # loads + renders YAML templates
├── versions.py            # version resolution, "latest" handling
└── run_logger.py          # writes to prompt_runs
```

### 6.2 Key functions

**`backbone/prompt_registry/loader.py`**
- `class PromptTemplate`:
  - `version: int`
  - `agent: str`
  - `name: str`
  - `model: ModelConfig`
  - `template: str`
  - `input_schema: type[BaseModel]`
  - `output_schema: type[BaseModel] | None`
- `def load(agent: str, name: str, version: int | Literal["latest"]) -> PromptTemplate`
  - Reads from `agents/<agent>/prompts/<name>_v<n>.yaml`
  - Validates YAML against PromptTemplate schema
  - Caches parsed templates in memory
- `def render(template: PromptTemplate, inputs: dict) -> tuple[str, str]`
  - Returns (rendered_text, input_hash)
- Tests: load a sample, render with inputs, assert correct substitution

**`backbone/prompt_registry/versions.py`**
- `def list_versions(agent: str, name: str) -> list[int]`
- `def compare(agent: str, name: str, v1: int, v2: int) -> VersionDiff`
  - Returns diff of template, model, schema
- Tests: create two versions, compare, assert diff detected

**`backbone/prompt_registry/run_logger.py`**
- `class PromptRunLogger`:
  - `async def log(run: PromptRun) -> None`
    - Persists to `prompt_runs` table
  - `async def query(agent: str, name: str, since: datetime) -> list[PromptRun]`
  - `async def cost_summary(agent: str, since: datetime) -> CostSummary`
- `class PromptRun` (pydantic): all the columns from `paper-tracker-design.md` §3.6
- `class ModelPricing` — table of (model, $/1M input, $/1M output)
- Tests: log a run, query it back, assert cost calculation

### 6.3 Sample prompt YAML schema
The schema for the YAML files in `agents/<name>/prompts/`:

```yaml
version: 1
agent: paper_tracker
name: why_relevant
model:
  name: hermes-2-pro
  temperature: 0.3
  max_tokens: 80
input_schema:
  fields:
    - name: title
      type: str
    - name: abstract
      type: str
    - name: interests
      type: str
template: |
  Paper: {title}
  Abstract: {abstract}
  ...
```

### 6.4 Tests
- `test_loader.py` — load, render, validate
- `test_versions.py` — list, compare
- `test_run_logger.py` — log, query, cost

---

## 7. Phase 6 — Telegram bot + dispatcher

**Goal:** A running bot that responds to commands and routes them to the right agent.

**Exit criteria:**
- `python -m career_copilot serve` starts a webhook server
- `/digest now` triggers the paper tracker agent
- `/discover` triggers the discovery flow
- `/watch add Maarten de Rijke` works
- Any unhandled command returns a friendly error
- All bot interactions are logged

### 7.1 Files to create

```
backbone/dispatcher/
├── __init__.py
├── dispatcher.py          # routes tasks to agent runtimes
├── task.py                # Task, TaskResult types
└── scheduler.py           # cron-like scheduled task worker

backbone/telegram/
├── __init__.py
├── bot.py                 # bot init + handler registration
└── handlers/
    ├── __init__.py
    ├── commands.py        # /digest, /watch, /discover, /prof
    └── callbacks.py       # inline button callbacks
```

### 7.2 Key files

**`backbone/dispatcher/dispatcher.py`**
- `class Dispatcher`:
  - `async def handle_command(self, user_id: str, command: str, args: list[str]) -> str`
    - Parses command, decides which agent to invoke
    - Spawns an `AgentTask`, awaits result
    - Returns response text (may be queued for Telegram send)
  - `async def handle_callback(self, callback: CallbackQuery) -> None`
    - For inline button presses
  - `async def trigger_scheduled(self, job_id: str) -> None`
    - Called by the scheduler worker
- Tests: mock agents, assert routing, assert context passed

**`backbone/dispatcher/task.py`**
- `class Task`:
  - `id: UUID`
  - `agent: str`
  - `trigger: Literal["command", "schedule", "callback", "event"]`
  - `payload: dict`
  - `created_at: datetime`
  - `working_memory: WorkingMemory`
- `class TaskResult`:
  - `task_id: UUID`
  - `success: bool`
  - `output: Any`
  - `error: str | None`
  - `duration_ms: int`

**`backbone/dispatcher/scheduler.py`**
- `class ScheduledTaskWorker`:
  - Polls `scheduled_jobs` table every 30s
  - Fires due jobs via `dispatcher.trigger_scheduled`
  - Records last-run timestamp
- Tests: insert due job, advance time, assert triggered

**`backbone/telegram/bot.py`**
- `def build_bot(settings: Settings) -> Application`
  - Constructs python-telegram-bot `Application`
  - Registers handlers from `handlers/`
  - Sets allowed_updates to message + callback_query
- `def get_chat_id_from_update(update) -> str`
- Tests: mock update, assert handler invoked

**`backbone/telegram/handlers/commands.py`**
- `command_digest(...)` — handles `/digest now|on|off|at <time>`
- `command_watch_add(...)` — `/watch add <name>`
- `command_watch_list(...)`
- `command_watch_remove(...)`
- `command_discover(...)`
- `command_prof(...)` — `/prof <name>`
- `command_interests(...)` — show current interests
- `command_export_zotero(...)` — `/export zotero <arxiv_id>`
- `command_help(...)`
- Each parses args, calls dispatcher, formats response
- Tests: mock dispatcher, assert correct call

**`backbone/telegram/handlers/callbacks.py`**
- `callback_read(...)` — record read signal
- `callback_save(...)` — record save + create Notion page
- `callback_skip(...)` — record skip
- `callback_more_like(...)` — record + boost interest in this area
- `callback_less_like(...)` — record + dampen
- `callback_brief(...)` — trigger /prof flow
- `callback_email_opener(...)` — queue email via Job Hunter (when built)
- Each parses callback data, calls dispatcher, edits the message
- Tests: mock callback, assert correct signal recorded

### 7.3 Tests
- `test_dispatcher.py` — routing, task lifecycle
- `test_task.py` — types, validation
- `test_scheduler.py` — due-job triggering
- `test_bot.py` — handler registration
- `test_commands.py` — each command handler
- `test_callbacks.py` — each callback handler

---

## 8. Phase 7 — Paper Tracker agent

**Goal:** The first end-to-end agent. Daily digest actually works.

**Exit criteria:**
- Daily digest at 9am produces a real Telegram message with real papers
- Stream A (by interest) and Stream B (by professor) both work
- "Why" lines are present
- Inline buttons record feedback
- `prompt_runs` table gets entries

### 8.1 Files to create

```
agents/paper_tracker/
├── __init__.py
├── agent.py               # PaperTrackerAgent
├── config.yaml            # tools allowed, namespaces, schedule
├── README.md              # module-level docs
├── prompts/
│   ├── summarize_paper_v1.yaml
│   ├── why_relevant_v1.yaml
│   ├── professor_why_v1.yaml
│   ├── professor_brief_v1.yaml
│   ├── email_opener_v1.yaml
│   ├── professor_discovery_v1.yaml
│   └── filter_decision_v1.yaml
└── tests/
    ├── test_agent.py
    ├── test_prompts.py
    └── test_discovery.py
```

### 8.2 Key file: `agent.py`

- `class PaperTrackerAgent`:
  - `system_prompt_path: str = "agents/paper_tracker/prompts/system_v1.yaml"`
  - `tools: list[str]` — from config.yaml
  - `read_namespaces: list[str]`
  - `write_namespaces: list[str]`
  - `async def run_digest(self, mode: Literal["daily", "weekly"]) -> Digest`
    - Implements the flow in `paper-tracker-design.md` §4.4
    - Returns a `Digest` object (sections A + B, with items)
  - `async def run_discover(self) -> list[ProfessorCandidate]`
    - Implements `paper-tracker-design.md` §4.5.1
  - `async def run_prof_brief(self, prof_id: int) -> ProfessorBrief`
    - Implements `paper-tracker-design.md` §4.5.3
  - `async def handle_feedback(self, item_id: str, signal: FeedbackSignal) -> None`

### 8.3 `config.yaml`

```yaml
agent: paper_tracker
system_prompt: agents/paper_tracker/prompts/system_v1.yaml
tools:
  - arxiv.fetch_recent
  - arxiv.fetch_author
  - vector.embed
  - vector.search
  - vector.upsert
  - structured.get
  - structured.set
  - telegram.send_digest
  - telegram.send_card
  - memory.feedback
  - notion.create_page
  - tavily.search
  - tavily.extract
  - firecrawl.scrape
read_namespaces:
  - user/profile
  - user/activity
  - user/professors
  - paper_tracker/papers_seen
  - paper_tracker/papers_summarized
write_namespaces:
  - paper_tracker/digests
  - paper_tracker/papers_seen
  - paper_tracker/papers_summarized
  - user/activity
  - user/professors
schedule:
  daily: "0 9 * * 1-5"
  weekly: "0 20 * * 0"
```

### 8.4 Prompt files

Each prompt is a YAML file following the schema in §6.3. The full prompt text is in the file (not in this guide); the spec for each:

- **`system_v1.yaml`** — the agent's role and constraints
- **`summarize_paper_v1.yaml`** — given a paper, produce a 2-sentence summary
- **`why_relevant_v1.yaml`** — see `paper-tracker-design.md` §4.9
- **`professor_why_v1.yaml`** — see §4.9
- **`professor_brief_v1.yaml`** — produce the §4.5.3 brief structure
- **`email_opener_v1.yaml`** — produce the email opener text
- **`professor_discovery_v1.yaml`** — given a prof's bio + recent papers, produce a one-line focus summary
- **`filter_decision_v1.yaml`** — decide if a paper passes the relevance threshold (used in §4.4 step 4)

### 8.5 Tests
- `test_agent.py` — run a digest against a fixed arXiv response, assert structure
- `test_prompts.py` — load all prompts, assert render succeeds, assert versions are valid
- `test_discovery.py` — run discover with mocked Tavily + arXiv, assert candidates returned
- Integration: `test_e2e_digest.py` — full flow from scheduler fire to Telegram message, with all external APIs mocked

### 8.6 Exit checklist
- [ ] `/digest now` produces a real-looking digest (in test mode)
- [ ] Daily 9am schedule actually fires (in dev)
- [ ] `prompt_runs` table populates with real entries
- [ ] Feedback buttons record signals correctly
- [ ] Notion page created on Save

---

## 9. Phase 8 — Seeding

**Goal:** The system has your data to work with. Not generic; *yours*.

**Exit criteria:**
- `user_profile.research_interests_essay` populated (the 1-2 paragraph essay)
- Interest vector generated and stored
- 5-10 professors in the watchlist (via `/discover` or manual)
- Notion "Papers" and "Professors" DBs connected
- Filters set (categories, daily/weekly)

### 9.1 Files to create

```
scripts/
├── seed_user_profile.py
├── seed_interest_vector.py
├── seed_professors.py
└── setup_notion.py
```

### 9.2 Specs

**`seed_user_profile.py`**
- Reads `data/user_profile.yaml` (you write this)
- Writes to `user_facts` table
- Validates against schema

**`seed_interest_vector.py`**
- Embeds the essay via Voyage
- Stores in `interest_vectors` with `source='seed'`, `is_active=true`

**`seed_professors.py`**
- Two modes:
  - `python -m scripts.seed_professors discover` — runs /discover, lets you pick
  - `python -m scripts.seed_professors from-file <yaml>` — for manual seeding
- Writes to `professors` + `professor_interest_vectors`

**`setup_notion.py`**
- Creates (or validates) the "Papers" and "Professors" Notion DBs
- Records their IDs in config

### 9.3 Tests
- `test_seed_user_profile.py` — load fixture, assert DB row
- `test_seed_interest_vector.py` — assert vector is 1024-dim
- `test_seed_professors.py` — from-file mode, assert rows
- `test_setup_notion.py` — mock Notion SDK, assert DBs created with right schema

### 9.4 Exit checklist
- [ ] You write `data/user_profile.yaml` (the essay)
- [ ] `python -m scripts.seed_user_profile` works
- [ ] `python -m scripts.seed_interest_vector` works
- [ ] Notion DBs created
- [ ] Professors discovered or seeded

---

## 10. Phase 9 — First end-to-end run

**Goal:** The system runs daily, unmonitored, and you actually use the digest.

**Exit criteria:**
- Day 1: real digest arrives at 9am
- You click Read on at least 2 papers
- You Save at least 1 (creates a Notion page)
- `/discover` produces candidates you actually want to watch
- A professor brief opens correctly
- No crashes, no infinite loops, no rate-limit issues

### 10.1 Tasks
- [ ] Run `python -m career_copilot serve` in a local process or on Azure
- [ ] Verify webhook is reachable (use ngrok for local dev)
- [ ] Manually fire `/digest now` — inspect the output
- [ ] Manually fire `/discover` — pick 3-5 profs
- [ ] Wait for next 9am, observe real run
- [ ] Click feedback buttons — verify signals recorded
- [ ] Inspect `prompt_runs` table — verify entries
- [ ] Check `feedback_log` — verify signals

### 10.2 What to watch for
- arXiv fetch failures → retry logic
- Voyage API latency → cache hits
- Telegram message size limits → split digests if needed
- Notion API rate limits → queue if hit
- Hermes cost → check `cost_summary` weekly

---

## 11. Phase 10 — Evaluation hooks

**Goal:** Engagement-based evaluation runs weekly.

**Exit criteria:**
- `eval.record_signal` called on every feedback
- `eval.weekly_report` runs Sunday night, posts to Telegram
- `eval.weekly_retune` is wired but optional in v0.1

### 11.1 Files to create

```
backbone/eval/
├── __init__.py
├── signals.py             # record + query
├── retune.py              # weekly vector retune
└── report.py              # weekly digest to user
```

### 11.2 Specs

**`eval/signals.py`**
- `async def record(item_id: str, signal: FeedbackSignal) -> None`
  - Writes to `feedback_log`
- `async def engagement_rate(agent: str, since: datetime) -> EngagementStats`
  - Computes: % read, % saved, % skipped, by stream (interest vs professor)
  - Returns stats object

**`eval/retune.py`** (v0.2)
- `async def retune_interest_vector() -> None`
  - Pulls all feedback from last 90 days
  - Re-embeds positively-engaged papers, averages into new interest vector
  - Stores as new `interest_vectors` row, sets `is_active=true`
  - Old row kept (versioned) for rollback

**`eval/report.py`**
- `async def weekly_report(user_id: str) -> str`
  - Aggregates: digests sent, papers shown, read/saved/skipped breakdown
  - Patterns: "you save mostly cs.IR papers; you skip cs.AI reasoning papers"
  - Top 5 most-saved papers this week
  - Returns formatted markdown
- Sent to user via Telegram every Sunday

### 11.3 Tests
- `test_signals.py` — record, query, rate
- `test_report.py` — fixed fixture, assert format

---

## 12. Phase 11 — Deployment to Azure

**Goal:** Production-ready deployment, monitored, with CI/CD.

**Exit criteria:**
- Bicep templates deploy the full stack
- GitHub Actions builds + deploys on push to main
- Application Insights shows traces
- Logs flow to Log Analytics

### 12.1 Files to create

```
deploy/azure/
├── main.bicep                 # entry point
├── container_app.bicep        # dispatcher + agent runtimes
├── postgres.bicep             # flexible server + pgvector
├── functions.bicep            # scheduled triggers
├── storage.bicep              # blob for PDFs
├── keyvault.bicep             # secrets
└── appinsights.bicep          # observability

.github/workflows/
├── ci.yaml                    # lint + typecheck + test on every PR
└── deploy.yaml                # deploy on merge to main
```

### 12.2 Key files

**`deploy/azure/main.bicep`**
- Composes all sub-modules
- Outputs: container app URL, postgres connection string (in Key Vault), App Insights key

**`deploy/azure/container_app.bicep`**
- Container Apps Environment
- App: pulls image from GitHub Container Registry
- Env vars: from Key Vault references
- Scale: min 0, max 1 (Consumption plan)
- Ingress: HTTPS only, allows Telegram webhook

**`deploy/azure/postgres.bicep`**
- Flexible Server, Burstable B1s
- 32GB storage
- pgvector extension via `azure.extensions` config
- Firewall: allow Azure services only
- Connection string stored in Key Vault

**`deploy/azure/functions.bicep`**
- Function App (Consumption plan)
- Timer trigger: runs `dispatcher.trigger_scheduled` every 5 min
- HTTP trigger: webhook for Telegram (alternative to Container App ingress)

**`.github/workflows/ci.yaml`**
- On PR: `uv sync`, `ruff check`, `mypy`, `pytest`
- On push to main: same + build container image + push to GHCR

**`.github/workflows/deploy.yaml`**
- On push to main: `az deployment group create` with the Bicep templates
- Migrates DB before deploying new app version
- Smoke test: hit health endpoint after deploy

### 12.3 Tests
- `test_bicep.py` — validate Bicep syntax via `az bicep build`
- Manual: deploy to a test resource group, verify it works

### 12.4 Exit checklist
- [ ] All Bicep templates validate
- [ ] CI green on a sample PR
- [ ] Deploy to test resource group works
- [ ] App Insights receiving traces
- [ ] Logs visible in Log Analytics

---

## 13. What to build first — TL;DR

If you have ~3 hours, do this:

1. **Phase 0** — get all API keys (1h)
2. **Phase 1** — project skeleton + `make dev` working (30min)
3. **Phase 2** — DB up, migrations run (30min)
4. **Phase 5** — prompt registry (just enough to load one prompt) (30min)
5. **Phase 4** — `arxiv.fetch_recent` + `vector.embed` + `vector.search` (30min)

By the end of 3 hours, you can: fetch recent arXiv papers, embed them, and find the top 5 most similar to your interest vector. That's the core of Stream A.

Then build the rest in order: memory → tools → dispatcher → agent → seeding → first run → eval → deploy.

---

## 14. What NOT to build in v0.1

Be ruthless about scope. Cut anything below:

- Auto-retune of interest vector (record signals; retune in v0.2)
- Multi-source paper fetching (arXiv only)
- Citation graph
- Lab page scraping beyond what /prof returns
- Zotero export (Notion only for v0.1)
- Job Hunter, Contribution Finder (those are v0.2 and v0.3)
- cs.LG throttling UI
- Voice / audio digest
- Cross-user anything
- Authentication beyond Telegram chat_id allowlist
