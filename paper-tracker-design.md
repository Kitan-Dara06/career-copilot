# Career Copilot — Architecture & Paper Tracker Design

**Project:** Career Copilot
**Document version:** v0.3
**Scope:** Whole-system architecture + Paper Tracker deep-dive
**Status:** Draft, awaiting decisions on a small set of open questions
**Last updated:** 2026-07-15

---

## 0. Project context

Career Copilot is a single-user, multi-agent personal assistant with three jobs (job hunting, paper tracking, contribution finding) sharing one backbone and one Telegram interface. It is also a **portfolio artifact for master's applications in NLP / IR**.

That dual purpose shapes the design:
- The system must work for you daily (real utility, not a demo)
- The system must be readable, defensible, and reviewable by an admissions committee (architecture diagrams, prompt versioning, evaluation, clean engineering hygiene)
- Choices that are "fine for a personal tool" but "embarrassing in a portfolio" (e.g., SQLite, hardcoded prompts, no eval) get rejected

Constraints (confirmed this turn):
- Single user, single tenant — no auth complexity
- PostgreSQL for storage with **pgvector** for embeddings
- Microsoft Azure for hosting
- **Voyage 3** as the embedding model (1024 dim) — SOTA for IR, strong portfolio signal
- **Hermes** as the primary LLM, accessed via API; other models pluggable. Chosen specifically for tool-use strength
- **Tavily** for web research (companies, labs, professors), **Firecrawl** as backup for deep URL scraping
- 9am local time for daily digest; weekly digest also supported
- **Notion** as primary saved-papers destination (you have Pro); Zotero as v0.2 export target
- Master's goal: **IR + CL**, possibly with AI/reasoning adjacency

---

## 1. What makes this a strong portfolio piece

A working bot is table stakes. What's actually signal-bearing for an admissions committee in NLP/IR:

| Signal | How this project delivers it |
|--------|------------------------------|
| Real multi-agent system thinking | Three agents sharing one dispatcher, memory, tool registry — not three separate scripts |
| Production-grade infra choices | PostgreSQL + pgvector, containerized, deployed on Azure, with observability |
| Prompt engineering as a discipline | Versioned prompts, logged runs, engagement-based evaluation |
| Evaluation as a first-class concern | Engagement signals, weekly retune, weekly self-report |
| Research-tool thinking (vs chatbot thinking) | Professor tracking — academic reading workflows, not Q&A |
| Hallucination mitigation by design | Cover letter: structured edit plan, not generation. Paper: vector + LLM rerank, not freeform recall |
| End-to-end ownership | One person can explain every line of the design — README, architecture, deployment, all consistent |
| Real LLM operations | Cost math, model selection rationale, failure modes handled |
| Reproducibility | Every LLM call logs prompt version + model + input hash + output + engagement |

The README is the elevator pitch. The architecture is the proof. The prompts and the `prompt_runs` table are the receipts.

---

## 2. Project structure — "agent as architecture"

The top-level layout is organized by **agent**, not by feature or layer. Each agent is a first-class unit with its own prompt registry, tests, and README. The shared backbone lives at the root as a library that every agent depends on.

```
career-copilot/
├── README.md                       # elevator pitch, used by admissions
├── ARCHITECTURE.md                 # this doc
├── docs/
│   ├── paper-tracker-design.md     # this file
│   ├── cover-letter-design.md      # cover letter sub-problem
│   ├── sample-sessions.md          # day-in-the-life walkthroughs
│   └── diagrams/                   # PNGs/SVGs of the architecture
│
├── backbone/                       # shared library, every agent depends on
│   ├── dispatcher/
│   ├── memory/
│   │   ├── working.py
│   │   ├── short_term.py           # Postgres-backed, TTL'd
│   │   ├── long_term.py            # Postgres + pgvector, versioned
│   │   └── namespaces.py
│   ├── tools/
│   │   ├── arxiv.py
│   │   ├── vector.py
│   │   ├── structured.py
│   │   ├── telegram.py
│   │   ├── email.py
│   │   ├── http.py
│   │   ├── tavily.py               # AI-native web search for research
│   │   ├── firecrawl.py            # deep URL scraping (JS-heavy pages)
│   │   ├── notion.py               # saved-papers → Notion DB
│   │   ├── memory.py
│   │   ├── scheduler.py
│   │   ├── github.py               # Contribution Finder, later
│   │   └── registry.py
│   ├── prompt_registry/
│   │   ├── loader.py
│   │   ├── versions.py
│   │   └── run_logger.py
│   ├── eval/
│   │   ├── signals.py
│   │   ├── retune.py
│   │   └── report.py
│   └── telegram/
│       ├── bot.py
│       └── handlers/
│
├── agents/
│   ├── paper_tracker/              # v0.1
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── prompts/
│   │   │   ├── summarize_paper_v1.yaml
│   │   │   ├── why_relevant_v1.yaml
│   │   │   ├── professor_why_v1.yaml
│   │   │   ├── professor_brief_v1.yaml
│   │   │   ├── email_opener_v1.yaml
│   │   │   ├── professor_discovery_v1.yaml
│   │   │   └── filter_decision_v1.yaml
│   │   ├── tests/
│   │   ├── config.yaml
│   │   └── README.md
│   ├── job_hunter/                 # v0.2
│   └── contribution_finder/        # v0.3
│
├── deploy/
│   ├── azure/
│   │   ├── container_app.bicep
│   │   ├── postgres.bicep
│   │   ├── functions.bicep
│   │   └── storage.bicep
│   └── scripts/
│
├── migrations/                     # Alembic
└── scripts/                        # one-offs, data seeds
```

Why this matters: an admissions reviewer can `cd agents/paper_tracker/` and read a self-contained module — prompt, tests, config, README — without context-switching through the rest of the system.

---

## 3. Shared backbone

### 3.1 Components

```
┌──────────────────────────────────────────────────────────┐
│                       TELEGRAM                           │
│                  (single interface)                      │
└──────────────┬───────────────────────────────────────────┘
               │ commands / push
┌──────────────▼───────────────────────────────────────────┐
│                      DISPATCHER                          │
│  - reads user prefs (long-term memory)                   │
│  - reads recent activity (short-term memory)             │
│  - decides which agent to invoke                        │
│  - routes the task                                       │
│  - schedules re-runs                                     │
└──────────────┬───────────────────────────────────────────┘
               │ spawns task
┌──────────────▼───────────────────────────────────────────┐
│                     AGENT RUNTIME                        │
│  per-agent: system_prompt + tools + memory_namespaces    │
│  - holds working memory for the duration of the task     │
│  - invokes tools, writes results to short/long memory    │
│  - emits output via shared "deliver" tool                │
└──────┬────────────────────────────────────┬──────────────┘
       │                                    │
┌──────▼─────────┐                  ┌───────▼────────────┐
│  TOOL REGISTRY │                  │  MEMORY LAYER      │
└──────┬─────────┘                  └───────┬────────────┘
       │                                    │
       ▼                                    ▼
   arxiv, tavily, firecrawl,         Postgres + pgvector
   notion, telegram, email,          (3-tier, namespaced)
   http, github, etc.
```

### 3.2 The "one assistant" feel

All three agents read/write through the **same** tool registry and the **same** memory layer. There is no agent-local LLM, no agent-local store. When Job Hunter needs to know your research interests, it reads from the long-term memory namespace the same way Paper Tracker did.

Each agent declares, in `config.yaml`:
- its system prompt (path in prompt registry)
- the tools it can call (subset of the registry)
- the memory namespaces it can **read**
- the memory namespaces it can **write**
- its trigger(s): schedule, command, or event

The dispatcher enforces read/write boundaries. So Job Hunter can never write to `paper_tracker.digests`, and Contribution Finder can never read cover-letter drafts in flight.

### 3.3 Tool registry

A `Tool` is a typed function with:
- `name` (e.g. `arxiv.fetch_recent`)
- `description` (for the LLM to decide when to call it)
- `input_schema` / `output_schema` (JSON Schema)
- `cost_hint` (`free` | `1 LLM call` | `external API call`)
- `latency_hint` (`fast` | `~3s` | `~30s`)
- `owner` (which agent owns it)

| Tool | Purpose | Primary agent |
|------|---------|---------------|
| `arxiv.fetch_recent` | Pull recent papers by query/category/since | Paper Tracker |
| `arxiv.fetch_author` | Pull recent papers by author name | Paper Tracker (prof mode) |
| `vector.embed` | Embed text via Voyage 3 (configurable) | All |
| `vector.search` | Top-k similarity search in a namespace | All |
| `vector.upsert` | Add/update a doc in a namespace | All |
| `structured.get` / `set` / `delete` | Read/write rows in a typed table | All |
| `telegram.send_message` | Plain text + reply markup | All |
| `telegram.send_digest` | Formatted list with inline buttons | Paper Tracker, Contribution Finder |
| `telegram.send_card` | Single-item card with action buttons | All |
| `tavily.search` | AI-native web search, returns clean structured results | Job Hunter, Paper Tracker (prof mode) |
| `tavily.extract` | Pull structured content from a URL | Job Hunter, Paper Tracker (prof mode) |
| `firecrawl.scrape` | Deep URL scrape (JS-heavy pages, returns markdown) | Paper Tracker (lab pages) |
| `notion.create_page` | Create a page in a Notion DB | Paper Tracker (save flow) |
| `notion.update_page` | Update a Notion page (status, notes) | Paper Tracker |
| `email.queue_draft` | Queue an email for approval (does NOT send) | Job Hunter |
| `email.send_now` | Send a queued draft by id (only after approval) | Job Hunter |
| `memory.feedback` | Record a user signal on an item | All |
| `scheduler.schedule` | Schedule a re-run | All |
| `http.fetch` | Generic URL fetch with cache | All |
| `github.search_issues` | Search issues/PRs by query | Contribution Finder (later) |
| `github.analyze_issue` | Read + summarize an issue with LLM | Contribution Finder (later) |

**Embedding model.** **Voyage 3** (1024 dimensions) — SOTA for IR, strong portfolio signal. $0.06 per 1M tokens. At ~100K tokens/day for paper tracking, that's ~$0.006/day, $0.18/month. The choice is documented in `backbone/tools/vector.py` config and called out in the README.

### 3.4 Three-tier memory

| Layer | Lifetime | Storage | Examples | Accessed by |
|-------|----------|---------|----------|-------------|
| **Working** | Duration of a single task run | In-process dict in agent runtime | The 5 papers being summarized right now, the current job lead being drafted | Only the running task |
| **Short-term** | 7–30 days (configurable) | Postgres + pgvector, TTL'd | Last 20 digests sent, recent feedback signals, recent commands, "what I already told the user about" | All agents, with TTL filter |
| **Long-term** | Indefinite, with explicit forget | Postgres + pgvector, versioned | Research interest vector, writing style profile, skill tags, durable preferences, "I always skip X", **professor watchlist + per-prof direction vectors** | All agents, with explicit access |

**No-bleed-through rules:**
- Each agent declares which namespaces it reads
- Cross-namespace reads are explicit and logged
- A vector search never crosses namespaces
- An LLM embedding retrieval is bounded by namespace + a max-K cap

**Update rule:** long-term writes are versioned. When the system updates your interest vector, it stores the new vector and a delta; you can roll back if the retune went sideways.

### 3.5 Namespaces

```
memory/
  user/
    profile/            # long-term: durable user facts (vector + structured)
    activity/           # short-term: recent interactions, feedback
    professors/         # long-term: watchlist + per-prof direction vectors
  paper_tracker/
    digests/            # short-term: digests sent, items, status
    papers_seen/        # short-term: arxiv ids the user has been shown
    papers_summarized/  # short-term: cached summaries
  job_hunter/           # (later)
    leads/
    drafts/
  contribution_finder/  # (later)
    opportunities/
```

### 3.6 Prompt versioning + run logger

Prompts live in `agents/<name>/prompts/` as versioned YAML files. Every LLM call is logged to `prompt_runs`:

```sql
CREATE TABLE prompt_runs (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
  agent         TEXT NOT NULL,
  prompt_name   TEXT NOT NULL,
  prompt_version INT NOT NULL,
  model         TEXT NOT NULL,
  input_hash    TEXT NOT NULL,
  input_tokens  INT,
  output_tokens INT,
  latency_ms    INT,
  output        TEXT,
  cost_usd      NUMERIC(10, 6),
  metadata      JSONB
);
CREATE INDEX prompt_runs_lookup ON prompt_runs (agent, prompt_name, ts DESC);
```

**Why this matters:**
- "Did prompt v2 actually improve over v1?" → query this table, join with engagement signals
- "What's my total spend on summarization?" → query this table
- "What did the system see when it sent this paper?" → reproducible from a digest_id

### 3.7 Evaluation infrastructure (not a separate agent)

Scheduled jobs that don't go through the dispatcher:
- `eval.record_signal(item_id, signal_type, timestamp)` — called by any agent on user feedback
- `eval.weekly_retune` — Sunday night, refresh interest vector from last 90 days of feedback, prune filters with 0% engagement
- `eval.weekly_report` — Sunday night, generates a private summary to the user

V0.1: just the recorder + report. Auto-retune in v0.2 once feedback data accumulates.

---

## 4. Paper Tracker

### 4.1 Goal

Send a daily (9am) and/or weekly digest of arXiv papers — plus a separate stream of papers from your **professor watchlist** — that match your research interests, with high signal-to-noise and a learning loop.

### 4.2 Triggers

- **Daily digest** at 9:00 local time, weekdays
- **Weekly digest** Sunday evening, broader sweep, themed
- Commands: `/digest now`, `/digest daily|weekly|off`, `/digest at 09:00`, `/interests`, `/watch add <prof>`, `/watch list`, `/watch remove <prof>`, `/discover`, `/prof <name>`, `/save zotero <arxiv_id>`

### 4.3 The two streams in the daily digest

The digest is **two sections**, not one:

1. **By interest** — top 3–5 papers matching your interest vector (the "main" feed)
2. **By professor** — any new papers from your watchlist that haven't been shown before (could be 0, could be 5)

The professor section gets a higher density cap (show all of them) because you've explicitly opted in. The interest section has the strict cap (3–5).

### 4.4 Flow (one daily run)

```
1. Scheduler fires 9:00 → Dispatcher
2. Dispatcher loads:
   - long-term: user_profile.interest_vector, user_profile.filters, user.professors
   - short-term: papers_seen in last 14 days
3. Dispatcher spawns Paper Tracker task
4. Stream A — by interest:
     arxiv.fetch_recent(categories, since=last_run, max=200)
     embed titles+abstracts via Voyage 3
     vector.search against interest_vector, k=30
     for top 30: LLM summarize (Hermes) + LLM "why" line
     drop any paper where "why" was REFUSED
     drop any paper in papers_seen
     pick top 5 by combined (similarity + LLM relevance) score
5. Stream B — by professor:
     for each professor in watchlist:
       arxiv.fetch_author(name, since=last_run, max=10)
       dedupe against papers_seen
       for each new paper: LLM summarize + LLM "professor_why"
     collect all, group by professor
6. Format digest: section A then section B, with inline buttons per item
7. Send via telegram.send_digest
8. Register all items in paper_tracker.digests + paper_tracker.papers_seen
9. On user click/save/skip → memory.feedback updates activity log;
   "save" also calls notion.create_page
10. Weekly: eval.weekly_retune refreshes interest vector from last 90 days
```

### 4.5 Professor tracking — the killer feature

**Data model:**

```sql
CREATE TABLE professors (
  id            BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  affiliation   TEXT,
  homepage_url  TEXT,
  arxiv_author  TEXT,
  added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ,
  notes         TEXT
);

CREATE TABLE professor_papers (
  id            BIGSERIAL PRIMARY KEY,
  professor_id  BIGINT NOT NULL REFERENCES professors(id),
  arxiv_id      TEXT NOT NULL,
  title         TEXT NOT NULL,
  authors       TEXT NOT NULL,
  abstract      TEXT NOT NULL,
  published_at  TIMESTAMPTZ,
  shown_at      TIMESTAMPTZ,
  why           TEXT,
  feedback      TEXT,                  -- read | saved | skipped
  UNIQUE (professor_id, arxiv_id)
);

CREATE TABLE professor_interest_vectors (
  id            BIGSERIAL PRIMARY KEY,
  professor_id  BIGINT NOT NULL REFERENCES professors(id),
  vector        VECTOR(1024) NOT NULL,  -- voyage-3
  source        TEXT NOT NULL,          -- 'seed' | 'retune'
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### 4.5.1 Professor discovery — solves the "I don't know who to apply to" problem

The watchlist can be seeded by the system itself. On `/discover`:

```
1. Look at user_profile.interest_vector and user_profile.research_interests_essay
2. Fetch top 200 cited papers from cs.CL + cs.IR + cs.AI from last 2 years
   (arXiv API + Semantic Scholar API for citation counts)
3. Cluster papers by author, count papers + citations per author
4. Filter to active researchers (>= 3 papers in window)
5. Rank by combined (paper count × citation score) × interest_similarity
6. For top 20:
     - Tavily.search("Professor <name> <affiliation> homepage")
     - Tavily.extract(homepage_url) for affiliation + lab info
     - Firecrawl.scrape(lab_page) if homepage is JS-heavy
     - Build a one-line focus summary via Hermes (prompt: professor_discovery_v1)
7. Return top 10-20 with [Watch] [Brief] [Skip] buttons
```

The result is a *curated shortlist* generated from your stated interests. You don't need to know the field's professor landscape — the system surfaces it for you. This is the single best portfolio story in the system: *"I didn't know where to apply, so I built the tool that finds my shortlist."*

#### 4.5.2 Adding a professor manually

```
/watch add Maarten de Rijke
```

- System looks up the name on arXiv, finds canonical author id
- Tavily extracts homepage, affiliation
- Seeds `professor_interest_vectors` with a vector built from the prof's last 10 papers (titles+abstracts via Voyage 3)
- First time a prof is added, the system fetches their last 30 days of papers and immediately shows them in the next digest

#### 4.5.3 The professor brief — on-demand query

Tapping [📋 Brief] on a professor paper, or `/prof <name>`, returns:

```
📋 Professor brief — <name>

Affiliation: <lab>, <university>
Homepage: <url>     Google Scholar: <url>
Lab page: <url>

Recent direction (last 12 months):
  <cluster summary> — e.g., "8 papers on dense retrieval, 3 on
  conversational search; shift 2024→2025 toward LLM re-ranking"

3 papers you should read if applying:
  • <title> (<venue>, <year>)
  • ...
  • ...

Connection to your interests:
  ✅ <strong overlap area>
  ✅ <moderate overlap area>
  ⚠️ <adjacent, might be worth exploring>
  ❌ <not your area>

Current PhD students (from lab page, via Firecrawl):
  • <name> (<focus>) — graduating <year>
  • ...

Suggested email opener (drafted via Hermes, structured plan):
  "I've been following your group's work on <specific paper>,
   particularly <specific result>. I'm applying to <program> with
   a focus on <your area> and would welcome the chance to discuss
   whether our interests might align."

  [📧 Queue opener]  [🔄 Regenerate]  [Save brief to Notion]
```

The brief is itself a portfolio artifact — an admissions officer can read it and immediately understand what the system does for you.

#### 4.5.4 Email opener → hands off to Job Hunter

The [📧 Queue opener] button creates a draft via the Job Hunter pipeline (same approval gate, same audit trail). The recipient is a professor, not a recruiter, but the *system* doesn't care — it just queues an email, the user approves it, it sends.

### 4.6 Saved paper storage — Notion primary, Zotero as export

**Primary destination: Notion.** You have Pro, zero new-tool friction. The Save button creates a page in a Notion "Papers" database with this schema:

```
Title            "Conversational Dense Retrieval: A Survey"
Authors          "M. de Rijke, ..."
ArXiv ID         "2607.14002"
Year             2025
Venue            (auto: SIGIR 2025 if known)
Tags             (multi-select) ["cs.IR", "conversational-search",
                                 "dense-retrieval", "maarten-de-rijke"]
Status           (multi-select) [To Read, Reading, Read, Cited]
Why I saved it   (auto-filled from the system's "why" line; editable)
My notes         (you fill in)
Related profs    → relation to "Professors" DB
Related papers   → relation to other saved papers
Saved from       "Paper Tracker" | "Manual"
Date saved       2026-07-17
```

The system also creates a "Professors" Notion DB and a "Reading Lists" DB (per topic). Over time, your Notion workspace *becomes* your academic reading graph — searchable, related, citable. Notion's database views (gallery, table, calendar by status) make this a usable reading tool, not just a list.

**Zotero export.** Single command: `/export zotero <arxiv_id>`. The system:
- Authenticates to your Zotero library with an API key (in Key Vault)
- Creates an item with full arXiv metadata
- Attaches the PDF (downloaded from arXiv, stored in Azure Blob)
- Tags it with the same `cs.IR / conversational-search / ...` tags
- Adds it to a `from-career-copilot` collection

For LaTeX writing, you use Zotero's BibTeX export. For everything else, Notion.

### 4.7 arXiv categories — operational defaults

| Stream | Categories | Notes |
|--------|-----------|-------|
| Daily interest | `cs.CL` + `cs.IR` + `cs.AI` (all primary) | Matches your IR + CL + AI focus |
| Daily professor | Whatever the prof publishes in | Fetched by author — covers any field |
| Weekly digest | Same categories, broader, with clustering | Themes surfaced by embedding clustering |
| Discovery mode | `cs.CL` + `cs.IR` + `cs.AI` last 2 years | For the /discover command |
| Optional throttle | `cs.LG` | Behind a config flag; off by default since cs.LG drowns the feed |

### 4.8 Telegram digest template

```
📚 arXiv digest — 2026-07-17

— by interest —

1. Title (link)
   Authors · venue
   Why: 1-line relevance reason
   [Read] [Save] [Skip] [More like this] [Less like this]

2. ...

— by professor —

🎓 Maarten de Rijke (1 new)

  1. Title (link)
     Why: 1-line reason specific to his recent direction
     [Read] [Save] [Skip] [📋 Brief] [📧 Opener]

🎓 Yiming Cui (2 new)
  ...
```

Inline buttons map to `memory.feedback` calls. Each button on a "by professor" item carries `stream=professor` metadata so the eval layer can compute "are professor items more or less engaging than interest items" — a useful signal for the watchlist itself.

### 4.9 The "why" line — two prompts, two intents

- **`why_relevant_v1.yaml`** (interest stream) — refuse if weak; one sentence linking to user interests
- **`professor_why_v1.yaml`** (professor stream) — connection to user interests is *not* required; the "why" is "what direction this paper sits in within the prof's recent work"

Both prompt versions are logged to `prompt_runs`.

### 4.10 Storage schema (PostgreSQL + pgvector)

The user_facts, interest_vectors, short_term_memory tables from v0.1 (translated to Postgres with VECTOR(1024) for Voyage 3). Plus the professor tables above. Plus `prompt_runs` from §3.6.

The vector dimension is **1024** throughout (Voyage 3's native output). If you ever switch embedding models, an Alembic migration updates all `VECTOR(N)` declarations.

### 4.11 Cost estimate (per day, single user, on Azure)

| Step | Cost |
|------|------|
| arXiv fetch | free |
| Voyage 3 embed of 200 papers (≈100K tokens) | ~$0.006 |
| LLM summary of top 30 (Hermes API) | ~$0.03 |
| LLM "why" lines for top 5 interest | ~$0.005 |
| LLM "why" lines for prof items (≤5) | ~$0.005 |
| Tavily searches in /discover (rare, ~weekly) | ~$0.02 amortized |
| Firecrawl scrapes (rare) | ~$0.01 amortized |
| Notion API writes | free |
| Telegram send | free |
| Azure: Container App (Consumption, scales to zero) | ~$0–2/month |
| Azure: Functions (cron) | <$0.10/month |
| Azure: Postgres Flexible B1s + storage | ~$12/month (see §5) |
| Azure: Blob Storage (PDFs) | <$0.10/month |
| Key Vault | <$0.10/month |
| Application Insights | <$1/month |
| **Total** | **~$0.05–0.10/day, ~$3.50–15/month** (the Postgres cost dominates) |

If you want to cut the Azure Postgres line: Supabase free tier (500MB) or Neon free tier (pgvector, branching) — not Azure, but pragmatic for a portfolio piece. Document the choice.

### 4.12 Failure modes

| Failure | Detection | Behavior |
|---------|-----------|----------|
| arXiv API down | fetch error | Retry 2x, then send "couldn't fetch today's papers" |
| Hermes timeout (>30s) | per-call timeout | Skip that paper, log, continue |
| Telegram rate limit | 429 | Back off, batch into one message |
| arXiv returns nothing (slow day) | 0 results | Skip digest silently; notify if 3 days in a row |
| Bot crash mid-run | Watchdog | Next run picks up, `last_run_at` persisted, no re-sending |
| User mutes bot | Telegram | Mark paused, dispatcher checks before sending |
| Postgres connection lost | driver error | Retry with backoff, queue short-term writes to in-memory buffer, flush on reconnect |
| pgvector index missing | boot check | Refuse to start; explicit migration required |
| Voyage 3 API down | fetch error | Retry; fall back to local MiniLM for embeddings (lower quality, but no LLM call) |
| Tavily down | fetch error | Skip /discover command, log, try again next time |
| Notion API down | fetch error | Queue the "save" event in Postgres; retry on next bot run |

### 4.13 V0.1 scope

In:
- arXiv fetch in 3 primary categories (cs.CL, cs.IR, cs.AI), cs.LG as opt-in
- Voyage 3 embeddings (1024 dim)
- Top-5 selection by similarity (interest stream)
- LLM summary + "why" via Hermes API
- Professor watchlist: add/list/remove + Stream B
- **Professor discovery** via /discover (arXiv + Semantic Scholar + Tavily)
- **Professor brief** via /prof <name> or [Brief] button (Tavily + Firecrawl + Hermes)
- Telegram digest with inline buttons
- Feedback capture (read / save / skip / more / less)
- Notion save (Papers DB + Professors DB + relations)
- Postgres + pgvector as described
- 9am daily + Sunday weekly
- Prompt versioning + `prompt_runs` logging
- Engagement-based weekly report (no auto-retune yet)

Out (for v0.1):
- Auto-retune of interest vector (record signals; retune in v0.2)
- Multi-source (only arXiv for papers; Tavily/Firecrawl for research)
- Citation following
- Lab page scraping beyond what /prof returns
- Zotero export (Notion only for v0.1)
- Voice / audio digest
- Cross-user anything
- CS.LG throttling UI (config-only in v0.1)

---

## 5. Hosting on Azure

| Component | Service | SKU | Monthly cost |
|-----------|---------|-----|--------------|
| Dispatcher + agent runtimes | Azure Container Apps | Consumption, scales to zero | ~$0–2 |
| Scheduled triggers | Azure Functions | Consumption | <$0.10 |
| Postgres + pgvector | Azure Database for PostgreSQL Flexible Server | Burstable B1s, 32GB | ~$12 |
| Paper PDFs | Azure Blob Storage (LRS, Cool) | Pay-as-you-go | <$0.10 |
| Secrets | Azure Key Vault | Standard | <$0.10 |
| Observability | Application Insights + Log Analytics | Pay-as-you-go | <$1 |

**Postgres cost flag.** B1s is the cheapest single-node option but ~$12/mo. For a portfolio piece, alternatives:
- **Supabase free tier** — pgvector, 500MB, has a UI you can show in the portfolio
- **Neon free tier** — pgvector, branching, modern DX
- **Azure Container Apps with Postgres as a sidecar container** for v0.1 dev only, never prod

Document the choice and its cost trade-off explicitly in the README. Admissions wants to see the trade-off reasoning, not the absolute cheapest option.

Hermes is API-only — no model serving on Azure. Just an API key in Key Vault.

---

## 6. Open questions (decreasing in importance)

1. **Initial research interests essay** — write 1–2 paragraphs in your own words. This seeds the interest vector and the discovery mode. **Most important thing to do before v0.1 ships.**
2. **Postgres hosting** — Azure ($12/mo, clean story) or Supabase/Neon (free tier, off-Azure)? Document the choice either way.
3. **Notion DBs setup** — create the "Papers" and "Professors" DBs in your Notion workspace before v0.1; the system needs the DB IDs in config.
4. **Semantic Scholar API key** — needed for /discover citation counts. Free tier is fine. Apply at semanticscholar.org.
5. **Voyage API key** — apply at voyage.ai. Free tier is enough for v0.1.
6. **Tavily API key** — apply at tavily.com. Free tier covers the v0.1 /discover use case.
7. **Firecrawl API key** — apply at firecrawl.dev. Free tier for the occasional lab page scrape.
8. **Telegram bot token** — create via @BotFather, get the chat_id for the user (you) so the dispatcher knows where to send.

---

## 7. Next steps

1. **You write the research interests essay** (this is the seed for everything)
2. **You create the Notion DBs** and grab the IDs
3. **You apply for API keys** (Voyage, Tavily, Firecrawl, Semantic Scholar)
4. I produce a final **"what to build first"** implementation guide, broken down by file/function, that you can hand to OpenCode Go or work through yourself
5. You build it, ship v0.1
6. Run daily for a week; tune the threshold
7. Write the README so an admissions reviewer can grok the system in 5 minutes
8. Then start Job Hunter (v0.2)
