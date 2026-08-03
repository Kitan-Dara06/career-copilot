# Software Requirements Specification — Career Copilot

**Project:** Career Copilot
**Document version:** v0.1
**Standard:** Adapted from IEEE Std 830-1998
**Last updated:** 2026-07-16

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Overall Description](#2-overall-description)
3. [Specific Requirements](#3-specific-requirements)
4. [Appendices](#4-appendices)

---

## 1. Introduction

### 1.1 Purpose

This Software Requirements Specification (SRS) describes the functional and non-functional requirements for **Career Copilot**, a single-user, multi-agent personal assistant system. The system automates three categories of academic-career work — paper tracking, job hunting, and contribution finding — under one Telegram interface and a shared memory and tool backbone.

This document serves four audiences:

- **The developer** (the author) as a contract for what "done" means for each feature
- **An admissions committee** evaluating the project as a portfolio artifact for master's applications in NLP/IR
- **Future contributors** (or the author's future self) needing to understand the system's behavior and constraints
- **Any AI coding assistant** (e.g. OpenCode Go) given the task of extending or implementing the system

### 1.2 Scope

Career Copilot is a personal productivity system intended for a single user — initially the author, applying to master's programs in NLP and Information Retrieval. It is not a multi-tenant SaaS product.

The system delivers three jobs, each implemented as an agent:

1. **Paper Tracker** — discovers, summarizes, and prioritizes new arXiv papers matching the user's research interests, with a special "professor watchlist" stream for tracking the recent work of specific academic advisors
2. **Job Hunter** — drafts cover letters for academic and industry positions, fact-checks company/lab research, and queues drafts for human approval via email before sending
3. **Contribution Finder** — scans GitHub for open issues and PRs in repositories matching the user's skills, and flags the ones where a contribution would create meaningful impact (not the ones with 40 active PRs)

The three agents share:
- A single Telegram interface for commands and push notifications
- A single memory layer (Postgres + pgvector) organized into namespaces per agent
- A single tool registry, used by all agents
- A single dispatcher that routes tasks (commands, schedules, callbacks) to the right agent
- A single LLM (Hermes) accessed via API; the architecture is model-pluggable
- A single embedding model (Voyage 3, 1024-dim)
- A single evaluation infrastructure that records user engagement and tunes the system weekly

Email is the only outbound channel used for *sending*; the system's only writes-to-the-world that cannot be revoked are queued as drafts and require explicit human approval before they go.

### 1.3 Definitions, Acronyms, Abbreviations

| Term | Definition |
|------|------------|
| **Agent** | A self-contained unit (system prompt + tools + memory namespaces) that performs one job. Three exist in v1.0: Paper Tracker, Job Hunter, Contribution Finder. |
| **Dispatcher** | The orchestrator that receives tasks (from commands, schedules, or callbacks) and routes them to the correct agent runtime. |
| **Memory layer** | The system's persistent state, organized into three tiers (working, short-term, long-term) and partitioned by namespace. |
| **Namespace** | A logical partition of the memory layer. Each agent declares which namespaces it reads and writes. |
| **Tool** | A typed function exposed to an agent's LLM. Each tool has a name, JSON-schema input/output, cost hint, latency hint, and owner agent. |
| **Tool registry** | The catalogue of all tools, with per-agent access control. |
| **Prompt registry** | Versioned, on-disk YAML files containing prompt templates. Every LLM call references a (name, version) pair. |
| **Run log** | The `prompt_runs` table, logging every LLM call: agent, prompt name+version, model, input hash, output, latency, cost, engagement signals. |
| **Watchlist** | The user's list of professors they are tracking for master's applications. Persisted in the `professors` table. |
| **Discovery mode** | The `/discover` command. Given the user's interests, the system scans top-cited papers, clusters by author, and proposes candidates for the watchlist. |
| **Brief** | A structured summary of a professor's recent research direction, generated on demand. |
| **Approval gate** | The mechanism by which a draft (cover letter, email opener) requires explicit human approval before it is sent. |
| **Hermes** | The LLM used for all generation. Accessed via API. |
| **Voyage 3** | The embedding model used for all vector representations. 1024 dimensions. |
| **pgvector** | The Postgres extension providing the `vector` type and similarity operators. |
| **Telegram** | The single user-facing interface. All commands and push notifications. |
| **IR** | Information Retrieval. |
| **CL** | Computation and Language (NLP). |
| **SRS** | This document. |

### 1.4 References

- IEEE Std 830-1998 — Recommended Practice for Software Requirements Specifications
- `paper-tracker-design.md` — Architecture for the whole system + Paper Tracker deep-dive
- `cover-letter-design.md` — Sub-design for the Job Hunter's cover letter pipeline
- `implementation-guide.md` — File-by-file build instructions
- arXiv API documentation — https://arxiv.org/help/api
- python-telegram-bot documentation — https://docs.python-telegram-bot.org/
- SQLAlchemy 2.0 documentation — https://docs.sqlalchemy.org/
- Voyage AI API documentation — https://docs.voyageai.com/
- Tavily API documentation — https://docs.tavily.com/
- Notion API documentation — https://developers.notion.com/

### 1.5 Overview

The remainder of this document is organized as follows:

- **Section 2** describes the system in context: its user, constraints, assumptions, and dependencies
- **Section 3** specifies the requirements: external interfaces, functional requirements (organized by feature), non-functional requirements, and design constraints
- **Section 4** provides use cases, a glossary, and a list of open questions

---

## 2. Overall Description

### 2.1 Product Perspective

Career Copilot is a new system. It does not replace any existing tool; rather, it integrates several third-party services behind a single Telegram interface:

| External dependency | Purpose | Fallback if down |
|---------------------|---------|------------------|
| Telegram Bot API | Single user interface | None (system is unusable without it) |
| arXiv API | Paper metadata | Skip digest, notify user |
| Voyage 3 API | Embeddings | Local MiniLM model (lower quality) |
| Hermes API | LLM generation | Skip generation, return error |
| Tavily API | Web research | Skip research step, log |
| Firecrawl API | Deep URL scraping | Skip, use Tavily's lighter extract |
| Notion API | Saved-papers destination | Queue save event in Postgres, retry on next run |
| PostgreSQL + pgvector | All persistent state | None (system is non-functional without DB) |
| Microsoft Azure | Hosting (Container Apps, Functions, Postgres, Blob, Key Vault) | None for production; local Docker for dev |

The system has three major subsystems:

1. **Backbone** (shared): dispatcher, memory layer, tool registry, prompt registry, evaluation, Telegram bot
2. **Agents** (three of them): Paper Tracker, Job Hunter, Contribution Finder
3. **Deployment** (Azure): Bicep templates, CI/CD, observability

### 2.2 Product Functions

The system provides the following major functions, organized by agent:

**Shared (all agents):**
- F-SH.1 — Accept and respond to Telegram commands
- F-SH.2 — Send Telegram push notifications on schedule or event
- F-SH.3 — Route tasks to the correct agent based on the command or trigger
- F-SH.4 — Persist agent state in the shared memory layer
- F-SH.5 — Enforce namespace-level read/write access between agents
- F-SH.6 — Version and log every LLM call
- F-SH.7 — Record and aggregate user engagement signals
- F-SH.8 — Queue outbound email and require approval before sending

**Paper Tracker:**
- F-PT.1 — Fetch new arXiv papers daily at 9:00 local time
- F-PT.2 — Embed new papers using Voyage 3
- F-PT.3 — Rank papers by similarity to the user's interest vector
- F-PT.4 — Summarize the top candidates using Hermes
- F-PT.5 — Send a daily Telegram digest with two sections: by interest, and by professor watchlist
- F-PT.6 — Accept feedback signals (read / save / skip / more like / less like) via inline buttons
- F-PT.7 — Maintain a watchlist of professors; fetch their new arXiv papers by author
- F-PT.8 — Generate a structured brief for any watched professor on demand
- F-PT.9 — Propose new professors for the watchlist based on the user's interests (discovery mode)
- F-PT.10 — Save a paper to the user's Notion "Papers" database on the Save signal
- F-PT.11 — Send a weekly thematic digest with embedded clustering
- F-PT.12 — Send a weekly engagement report to the user

**Job Hunter (v0.2):**
- F-JH.1 — Accept a job posting as input (URL or pasted text)
- F-JH.2 — Classify the role as academic (master's / PhD application) or industry
- F-JH.3 — Generate a structured research block (company or lab) using Tavily and arXiv
- F-JH.4 — Maintain a base letter and voice profile per user
- F-JH.5 — Produce a structured edit plan (not freeform generation) targeting the base letter
- F-JH.6 — Apply the edit plan to the base letter, preserving the user's voice
- F-JH.7 — Fact-check all company claims against the research block; flag unverifiable claims
- F-JH.8 — Queue the rendered letter for email approval
- F-JH.9 — Send the letter only after explicit "send" reply; revise on "edit" reply
- F-JH.10 — Append every approved letter to the corpus for future use

**Contribution Finder (v0.3):**
- F-CF.1 — Search GitHub for open issues and PRs matching the user's skills and interests
- F-CF.2 — Score opportunities by impact (not popularity) — flag issues where a contribution would meaningfully move the needle
- F-CF.3 — Analyze issue context using Hermes; produce a short summary
- F-CF.4 — Send a weekly digest of high-impact opportunities
- F-CF.5 — Accept feedback signals (interested / pass / already doing)

**Evaluation (shared, infrastructure not agent):**
- F-EV.1 — Record every user feedback signal with timestamp
- F-EV.2 — Compute engagement statistics per agent per week
- F-EV.3 — Send a weekly private report to the user
- F-EV.4 — Retune the user's interest vector from the last 90 days of feedback (v0.2)

### 2.3 User Characteristics

The system has exactly one user. The user:

- Is applying to master's programs in NLP and Information Retrieval
- Reads arXiv papers in cs.CL, cs.IR, and cs.AI
- Maintains a personal reading workflow in Notion
- Has Telegram installed and uses it as a primary communication tool
- Has a working email account reachable by the system's approval-gate flow
- Has a basic understanding of academic research workflows (papers, citations, conferences, labs)
- Is the developer of the system, so can debug issues that arise
- Has a budget of approximately $5-15 USD per month for cloud hosting and API calls

There is no support for multiple users, no authentication beyond Telegram chat_id allowlist, and no user management UI.

### 2.4 Constraints

**C-1. Single user.** The system is designed for one user. Adding multi-tenancy is out of scope.

**C-2. Single interface.** All interaction is via Telegram. The user does not interact with a web UI, CLI, or local app.

**C-3. Approval gate is mandatory.** Outbound email is the only write that affects the outside world, and it requires explicit human approval. There is no admin override; the system cannot send email without an "send" reply from the user.

**C-4. PostgreSQL + pgvector only.** No alternative database is supported in v1.0. The vector store is in pgvector, not a separate vector DB.

**C-5. Microsoft Azure only.** Production hosting is on Azure. Local development may use Docker but the production target is Azure Container Apps + Azure Database for PostgreSQL Flexible Server.

**C-6. Hermes as primary LLM.** Other models can be plugged in via the tool registry, but the default and the documented production choice is Hermes. The structured edit plan in Job Hunter specifically requires Hermes' tool-use strength.

**C-7. Voyage 3 for embeddings.** Other embedding models are configurable but Voyage 3 is the default and the portfolio-signaling choice.

**C-8. No real-time latency requirements.** The system is async-by-design. Digests are scheduled; commands are best-effort within a few seconds. There are no sub-second response requirements.

**C-9. Budget ceiling.** Total monthly cost (hosting + APIs) should not exceed $15 USD in v1.0. In v0.1 with single-agent scope, target is under $5.

**C-10. Honest failure modes.** The system must never silently fail. All failures must be visible (log entry, Telegram notification, or both).

**C-11. Portfolio coherence.** All engineering choices must be defensible in a portfolio review. SQLite, hardcoded prompts, untested code, missing observability are not acceptable.

### 2.5 Assumptions and Dependencies

**A-1.** The user has stable internet access and the Telegram app available on a device they check at least once daily.

**A-2.** The arXiv API will remain available and free. If arXiv changes its API, the `arxiv.fetch_*` tools must be updated; the rest of the system is insulated.

**A-3.** Voyage 3 will remain available and the cost will remain in the $0.06 / 1M tokens range. Significant price changes may force a model swap, which is a 1-day migration.

**A-4.** The user has API keys for: Telegram, Voyage, Tavily, Firecrawl, Notion, Hermes, Semantic Scholar (free).

**A-5.** The user has a Microsoft Azure subscription with permissions to create resource groups, deploy Bicep templates, and provision Postgres Flexible Server instances.

**A-6.** The Telegram bot is restricted to the user's chat_id via allowlist. No other user can interact with the bot.

**A-7.** Notion DBs ("Papers", "Professors", optional "Reading Lists") are created by the user and shared with the Notion integration.

**A-8.** The user writes and maintains a 1-2 paragraph "research interests essay" that seeds the interest vector and the discovery mode. Without this, the system produces generic results.

**A-9.** External services (Tavily, Firecrawl) are best-effort. If they are down, the system degrades gracefully and the affected feature is skipped with a clear notification.

**A-10.** The user accepts that the system's recommendations are not authoritative. The engagement-based evaluation tunes filters over time but does not guarantee that every surfaced paper is high-quality or that every professor suggestion is a good fit.

---

## 3. Specific Requirements

### 3.1 External Interface Requirements

#### 3.1.1 User interfaces

**UI-1. Telegram bot.** The user interacts with the system exclusively through a Telegram bot. Supported inputs:

- Text commands (e.g. `/digest now`, `/watch add <name>`, `/discover`, `/prof <name>`, `/interests`, `/help`)
- Inline button presses on bot-sent messages (e.g. [Read], [Save], [Skip], [📋 Brief], [📧 Opener])
- Replies to bot-sent emails (for the cover letter approval gate)

**UI-2. Email.** The user receives two types of email:

- Approval requests for outbound emails (cover letters, professor openers). These include the rendered content, the structured edit plan as an audit trail, and reply instructions.
- Weekly engagement reports (separate, lower-frequency).

**UI-3. Notion.** The user views and edits saved papers in a Notion "Papers" database. The system writes only; the user reads and edits via Notion's native UI.

There is no web UI, no mobile app beyond Telegram, no CLI.

#### 3.1.2 Software interfaces

| Interface | Protocol | Auth | Notes |
|-----------|----------|------|-------|
| Telegram Bot API | HTTPS / webhook | Bot token | Webhook URL configured via setWebhook |
| arXiv API | HTTPS / REST | None (public) | Rate limit: unauthenticated, polite use |
| Voyage 3 API | HTTPS / REST | API key | 1024-dim embeddings |
| Hermes API | HTTPS / REST | API key | Model pluggable |
| Tavily API | HTTPS / REST | API key | AI-native search + extract |
| Firecrawl API | HTTPS / REST | API key | URL → markdown |
| Notion API | HTTPS / REST | OAuth integration token | Database IDs in config |
| Postgres | TCP (TLS) | Password in connection string | pgvector extension required |
| Azure Blob Storage | HTTPS / REST | SAS token or Managed Identity | For paper PDFs |
| SMTP (for outbound email) | SMTP / TLS | Credentials in env | Provider-agnostic; SendGrid recommended |

#### 3.1.3 Communication interfaces

The system is deployed on Microsoft Azure. Inbound HTTPS (Telegram webhook) is via Azure Container Apps. Outbound HTTPS (to all third-party APIs) is over the public internet. The system does not listen on any port other than 443 for the webhook.

### 3.2 Functional Requirements

Functional requirements are organized by feature and numbered per the conventions in §2.2.

#### 3.2.1 Paper Tracker (F-PT.*)

**F-PT.1** The system shall fetch new arXiv papers from categories `[cs.CL, cs.IR, cs.AI]` daily at 9:00 local time on weekdays, with `cs.LG` as an optional throttled category.

**F-PT.2** The system shall embed the title and abstract of each new paper using Voyage 3 and store the embedding in the `paper_tracker/papers_seen` namespace.

**F-PT.3** The system shall rank new papers by cosine similarity to the user's active `interest_vector` and retain the top 30 for further processing.

**F-PT.4** The system shall produce a 1-3 sentence summary of each top-30 paper using the `summarize_paper_v*` prompt and Hermes.

**F-PT.5** The system shall produce a 1-sentence relevance reason for each top-30 paper using the `why_relevant_v*` prompt. If the prompt returns "REFUSE", the paper is dropped from the digest.

**F-PT.6** The system shall produce a daily Telegram digest with two sections: (a) "by interest" — top 5 papers, (b) "by professor" — all new papers from the user's watchlist, grouped by professor.

**F-PT.7** The system shall attach inline buttons to each digest item: [Read], [Save], [Skip], [More like this], [Less like this] for the interest section; [Read], [Save], [Skip], [📋 Brief], [📧 Opener] for the professor section.

**F-PT.8** The system shall record every button press as a `feedback_log` row containing `item_id`, `signal`, and `timestamp`.

**F-PT.9** The system shall maintain a `professors` table and a `professor_papers` table. Adding a professor via `/watch add <name>` shall:
- Resolve the canonical arXiv author name
- Extract affiliation and homepage via Tavily
- Seed a per-professor interest vector from the prof's last 10 papers

**F-PT.10** The `/prof <name>` command or the [📋 Brief] inline button shall return a structured brief including affiliation, recent direction (last 12 months), 3 suggested papers to read, fit assessment vs the user's interests, and current PhD students (when available from the lab page via Firecrawl).

**F-PT.11** The `/discover` command shall:
- Fetch the top 200 most-cited papers in `[cs.CL, cs.IR, cs.AI]` from the last 2 years via arXiv + Semantic Scholar
- Cluster by author, count papers + citations
- Rank by combined (paper count × citation score) × interest similarity
- For the top 20, extract a one-line focus summary via Hermes
- Return the top 10 to the user with [Watch] / [Brief] / [Skip] buttons

**F-PT.12** The Save signal shall create a page in the user's Notion "Papers" database with the following properties: Title, Authors, ArXiv ID, Year, Venue, Tags (multi-select), Status (multi-select), Why I saved it (auto-filled, editable), My notes (user-edited), Related profs (relation), Related papers (relation), Saved from ("Paper Tracker"), Date saved.

**F-PT.13** The system shall send a weekly thematic digest on Sunday evening, with broader paper coverage and themes surfaced by embedding clustering.

**F-PT.14** The system shall send a weekly private engagement report to the user summarizing digests sent, papers shown, read/saved/skipped breakdown, and observed patterns.

#### 3.2.2 Job Hunter (F-JH.*) — v0.2

**F-JH.1** The system shall accept a job posting as either a URL or pasted text.

**F-JH.2** The system shall classify the role as either "academic" (master's / PhD application) or "industry" using the `role_classification_v*` prompt.

**F-JH.3** For academic roles, the system shall build a structured research block containing: prof's recent papers (from arXiv by author), lab homepage content (from Tavily or Firecrawl), current PhD students, recent grants, and program structure.

**F-JH.4** For industry roles, the system shall build a structured research block containing: company mission, products, recent news, key people, required skills (from posting), and nice-to-haves.

**F-JH.5** The system shall maintain a `corpus/base_letter.yaml` (the user's hand-written base letter) and `corpus/voice_profile.yaml` (structured voice descriptors).

**F-JH.6** The system shall produce a structured edit plan (JSON) for the base letter using the `cover_letter_edit_plan_v*` prompt. The plan shall specify: opening paragraph, project mention, closing line, and any research-fit or role-fit paragraphs to add. The plan shall not include freeform generation of new paragraphs; only structured edits and additions.

**F-JH.7** The system shall apply the edit plan to the base letter, preserving the original sentence structure and voice. The output shall include inline markers for fact-check failures and major edits.

**F-JH.8** Every factual claim about the company or lab in the rendered letter shall be cross-referenced to a source in the research block. Unverifiable claims shall be flagged with `[FACT-CHECK FAILED: <claim>]` and shall not be silently corrected.

**F-JH.9** The system shall queue the rendered letter for approval via email. The email shall include: the rendered letter, the edit plan as an audit trail, the recipient address, and reply instructions ("send" / "edit: <change>" / "skip").

**F-JH.10** The system shall send the letter only upon receiving an explicit "send" reply from the user. The system shall re-queue on "edit" replies and discard on "skip" replies. There is no override mechanism.

**F-JH.11** Upon "send" confirmation, the system shall:
- Mark the draft as `sent` in the `pending_drafts` table
- Send the email via the configured SMTP provider
- Append the new letter to `corpus/approved/` with metadata for future use

#### 3.2.3 Contribution Finder (F-CF.*) — v0.3

**F-CF.1** The system shall search GitHub for open issues and PRs matching the user's skills (from `user.profile.skills`) and interests (from `interest_vector`) using the `github.search_issues` tool.

**F-CF.2** The system shall score each opportunity by impact, defined as: issue age × maintainer engagement signal × skill-match score × "not already crowded" score (low PR count, no recent activity on the issue).

**F-CF.3** The system shall retain the top 10-20 opportunities for further processing.

**F-CF.4** The system shall produce a short summary of each opportunity using the `github_analyze_v*` prompt, including: the problem, why a contribution would matter, suggested first steps, and estimated effort.

**F-CF.5** The system shall send a weekly Telegram digest of high-impact opportunities, with inline buttons for [Interested] / [Pass] / [Already doing].

#### 3.2.4 Shared (F-SH.*)

**F-SH.1** The system shall accept all commands listed in §3.1.1 and respond with a text message within 5 seconds (best-effort, depends on external API latency).

**F-SH.2** The system shall push notifications on schedule (daily 9am digest, weekly Sunday digest, weekly engagement report) and on event (draft approval request, weekly retune completion).

**F-SH.3** The dispatcher shall route each task to the correct agent based on (a) the command, (b) the schedule entry, or (c) the callback query metadata.

**F-SH.4** Each agent shall declare its read and write namespaces. The dispatcher shall reject any cross-namespace read or write that is not declared in the agent's config.

**F-SH.5** Every LLM call shall be logged to the `prompt_runs` table with: agent, prompt name, prompt version, model, input hash, input tokens, output tokens, latency, output (truncated at 4KB), cost, and metadata.

**F-SH.6** Every prompt template shall be a versioned YAML file in `agents/<name>/prompts/<prompt>_v<n>.yaml`. Older versions shall be retained for comparison and rollback.

**F-SH.7** The system shall record every feedback signal in the `feedback_log` table. The eval subsystem shall compute engagement statistics from this table weekly.

**F-SH.8** Outbound email shall be queued in a `pending_drafts` table. The system shall never call the SMTP send method directly. The email listener (parsing the user's reply) is the only path that can mark a draft as `sent`.

**F-SH.9** The system shall refuse to boot if any required API key, database connection, or critical configuration is missing. Boot-time checks shall produce clear, actionable error messages.

**F-SH.10** The system shall log every external API call with request ID, latency, status, and (where applicable) cost. Logs shall be structured (JSON) and shipped to Application Insights.

### 3.3 Non-Functional Requirements

#### 3.3.1 Performance

**NFR-P.1** The daily digest shall be delivered within 5 minutes of the scheduled 9:00 trigger, under normal conditions (no API outages, < 1000 new papers in the period).

**NFR-P.2** Commands shall respond within 5 seconds in the median case, 30 seconds in the 99th percentile.

**NFR-P.3** Vector search queries shall return top-5 results within 200ms at the v1.0 data scale (< 100K vectors).

**NFR-P.4** The system shall handle up to 500 new papers per day without performance degradation (covered by current throttling design).

#### 3.3.2 Reliability

**NFR-R.1** The system shall implement retry with exponential backoff (3 attempts, 1s/2s/4s) for all external API calls.

**NFR-R.2** The system shall degrade gracefully when external services are unavailable:
- arXiv down → skip digest, notify user
- Voyage down → fall back to local MiniLM for embeddings
- Hermes down → skip generation, return error to user
- Tavily / Firecrawl down → skip research step, log
- Notion down → queue save event, retry on next run
- Postgres down → refuse to start; do not accept commands

**NFR-R.3** The system shall persist `last_run_at` for each scheduled job, so a crash mid-run does not cause papers to be re-shown on the next run.

**NFR-R.4** The system shall detect pgvector index absence at boot and refuse to start with a clear error, rather than silently serving slow queries.

#### 3.3.3 Availability

**NFR-A.1** The system targets 99% availability, measured monthly, excluding planned maintenance.

**NFR-A.2** Scheduled jobs that are missed (e.g., bot down at 9am) shall be re-attempted on the next bot run, but only papers not previously shown shall be surfaced.

#### 3.3.4 Security

**NFR-S.1** All API keys shall be stored in environment variables for local dev and in Azure Key Vault for production. They shall never be logged, written to source code, or committed to version control.

**NFR-S.2** The Telegram bot shall accept commands only from the user's allowlisted `chat_id`. All other messages shall be rejected with a 403.

**NFR-S.3** The system shall use TLS for all network communication with external services and for the webhook endpoint.

**NFR-S.4** The Postgres connection shall use TLS. The connection string shall be stored in Key Vault and injected at runtime.

**NFR-S.5** The system shall not write to any channel (email, Notion, GitHub) without either (a) explicit user action (button press) or (b) explicit user approval (email reply).

#### 3.3.5 Maintainability

**NFR-M.1** All public functions and classes shall have Google-style docstrings describing purpose, inputs, outputs, and exceptions.

**NFR-M.2** All public interfaces shall have 100% type coverage (no `Any` except where unavoidable, no `# type: ignore` without a justification comment).

**NFR-M.3** The codebase shall pass `ruff check` with default rules, `mypy --strict` on the public surface, and `pytest` with at least 80% line coverage on the backbone.

**NFR-M.4** The architecture documents (`paper-tracker-design.md`, `cover-letter-design.md`, `implementation-guide.md`, this SRS) shall be kept in sync with the code. Changes to the architecture require corresponding doc updates in the same commit.

**NFR-M.5** The README shall be sufficient for an admissions reviewer to understand the system in 5 minutes. It shall include: the elevator pitch, a one-page architecture diagram, the three jobs in plain language, and links to the deeper design docs.

#### 3.3.6 Portability

**NFR-PT.1** The application code shall run on Python 3.11+ on Linux. macOS and Windows are not deployment targets but development should work cross-platform.

**NFR-PT.2** The local development environment shall use Docker for Postgres + pgvector. The same `docker run` command shall work on any developer machine.

**NFR-PT.3** Production deployment is on Microsoft Azure. Migration to AWS or GCP is possible by re-implementing the Bicep templates; the application code is cloud-agnostic.

#### 3.3.7 Cost

**NFR-C.1** Total monthly operating cost shall not exceed $15 USD in v1.0 (three agents), and $5 USD in v0.1 (Paper Tracker only). This includes hosting, all API calls, and storage.

**NFR-C.2** All cost-incurring operations shall be logged to the `prompt_runs` table. A weekly cost summary shall be available via a CLI command and via the weekly engagement report.

#### 3.3.8 Observability

**NFR-O.1** The system shall emit structured JSON logs to stdout, with a minimum of: timestamp, level, agent, task_id, message, and contextual fields.

**NFR-O.2** The system shall ship logs to Azure Application Insights via OpenTelemetry.

**NFR-O.3** Every LLM call shall include the prompt name + version in its log line, enabling queries like "show me all calls to why_relevant_v1 in the last 7 days and their engagement rate."

**NFR-O.4** Every external API call shall be wrapped in an OpenTelemetry span, so traces show the full path from dispatcher to tool to LLM to response.

### 3.4 Design Constraints

**DC-1.** The project shall be organized as a multi-agent system with a shared backbone, per `paper-tracker-design.md` §2. The directory structure is normative.

**DC-2.** The tool registry is the only way agents access external capabilities. Agents shall not call external APIs directly.

**DC-3.** The memory layer is the only way agents persist state. Agents shall not use files, in-process state (beyond working memory), or external databases.

**DC-4.** Prompts are versioned YAML files. The LLM shall not be called with inline string templates in production code; only with rendered PromptTemplate objects.

**DC-5.** The cover letter pipeline shall use structured edits, not freeform generation. The base letter's prose shall be preserved by construction.

**DC-6.** The professor watchlist shall be seedable by the user (manual add) or by the system (discovery mode). It shall not be populated by silent inference from other data sources.

### 3.5 Software System Attributes

**SSA-1. Correctness.** Every paper shown in a digest shall be a real arXiv paper (verifiable by arxiv_id). Every professor in a brief shall be a real person (verifiable by Google Scholar or homepage). The system shall not hallucinate these entities.

**SSA-2. Auditability.** Every action the system takes on the user's behalf shall be traceable. The `prompt_runs` table answers "what did the system see and decide?" for any historical event.

**SSA-3. Reversibility.** Long-term memory writes are versioned. The user can roll back an interest vector update that went sideways. The user can also retract a professor from the watchlist, removing them from future digests.

**SSA-4. Composability.** The backbone is a library, not a monolith. A new agent (e.g., a future "Conference Deadline Tracker") can be added by creating a new directory under `agents/`, declaring its config, and registering its prompts.

**SSA-5. Privacy.** The single user's data stays in the user's Postgres and the user's Notion. No data is sent to third-party services beyond the explicit API calls (e.g., the user's paper list is not sent to anyone; only individual papers are sent to Voyage for embedding).

---

## 4. Appendices

### Appendix A — Use Cases

#### A.1 Use case: User wants to track a new professor

**Actor:** User
**Trigger:** User sends `/watch add Maarten de Rijke`
**Preconditions:** Telegram bot is running, Tavily and arXiv APIs are reachable.
**Flow:**
1. System resolves "Maarten de Rijke" on arXiv to a canonical author id.
2. System fetches the prof's last 10 papers via arXiv.
3. System searches Tavily for the prof's homepage and affiliation.
4. System embeds the prof's last 10 papers' titles+abstracts via Voyage 3.
5. System writes a row to `professors` and a `professor_interest_vectors` row.
6. System replies in Telegram: "Added Maarten de Rijke — U Amsterdam, IRLab. Watching for new papers. Next digest will include his recent work."
7. On the next 9am digest, the new prof's papers from the last 14 days appear in section B.

**Postconditions:** The watchlist contains the new professor. Future digests include their work.

**Alternatives:**
- If the name is ambiguous: system replies with 2-3 candidates and asks the user to pick.
- If the arXiv lookup fails: system asks the user to provide the prof's homepage URL.
- If Tavily is down: system adds the prof with the arXiv data only and notes the missing homepage data.

#### A.2 Use case: User wants a cover letter drafted for a master's application

**Actor:** User
**Trigger:** User pastes a job posting or program description into Telegram and uses a Job Hunter command (v0.2)
**Preconditions:** Job Hunter agent is built. Base letter and voice profile are configured. SMTP is set up.
**Flow:**
1. System classifies the role as academic.
2. System extracts the program name, target professor (if named), and program URL from the posting.
3. System fetches the target prof's recent papers via arXiv.
4. System fetches the lab homepage and the program page via Tavily + Firecrawl.
5. System builds a structured research block.
6. System calls `cover_letter_edit_plan_v*` to produce a JSON edit plan.
7. System applies the edit plan to `corpus/base_letter.yaml`.
8. System fact-checks every claim in the rendered letter against the research block.
9. System queues the rendered letter for email approval.
10. System sends an email to the user with the rendered letter, the edit plan, and reply instructions.
11. User replies "send" or "edit: <change>" or "skip".
12. On "send": system sends the email via SMTP, marks the draft as sent, appends the letter to `corpus/approved/`.
13. On "edit": system re-applies with the user's feedback and re-queues.
14. On "skip": system discards the draft.

**Postconditions:** Either the letter is sent, revised, or discarded. The corpus grows on success.

**Alternatives:**
- If the user has no base letter: system asks the user to provide one before continuing.
- If a fact-check fails: the letter is marked with `[FACT-CHECK FAILED: ...]` markers and the user is told to verify before sending.

#### A.3 Use case: User runs `/discover` to find new professors to watch

**Actor:** User
**Trigger:** User sends `/discover`
**Preconditions:** The user has a research interests essay seeded. The arXiv, Semantic Scholar, and Tavily APIs are reachable.
**Flow:**
1. System fetches the top 200 most-cited papers in `[cs.CL, cs.IR, cs.AI]` from the last 2 years.
2. System clusters papers by author.
3. System scores authors by combined (paper count × citation score) × interest similarity.
4. System extracts a one-line focus summary for the top 20 using the `professor_discovery_v*` prompt.
5. System returns the top 10 with [Watch] / [Brief] / [Skip] buttons.
6. User clicks [Watch] on one or more.
7. For each watched professor, the system proceeds as in A.1.

**Postconditions:** The watchlist grows. The user has new professors to follow.

#### A.4 Use case: System degrades gracefully when Voyage is down

**Trigger:** Voyage 3 API is unreachable.
**Flow:**
1. The first paper tracker digest of the day attempts to call Voyage.
2. Voyage returns a 5xx after retries.
3. The system falls back to local MiniLM embeddings (lower quality but no API dependency).
4. The system logs the degradation at WARN level with a clear message.
5. The system continues the digest with MiniLM embeddings, marked in the `prompt_runs` log with `metadata.fallback = "miniLM"`.
6. The digest is sent normally.
7. The user does not see a visible failure, but a weekly report may note "Voyage was down for X hours this week, fallback embeddings were used."

**Postconditions:** The user received a digest despite the API outage. The system remained functional but with reduced embedding quality.

### Appendix B — Glossary

See §1.3 for the primary glossary. Additional terms:

- **Bicep** — Microsoft's domain-specific language for deploying Azure resources declaratively.
- **Burstable B1s** — The cheapest Azure Database for PostgreSQL Flexible Server SKU; suitable for low-traffic workloads.
- **Cold start** — The latency incurred when a serverless container (e.g., Azure Container App in Consumption plan) starts from zero instances. Targeted at < 2 seconds in the design.
- **Consumption plan** — Azure's serverless pricing tier; pay only for what you use, scale to zero.
- **Embedding** — A dense numerical vector representing text, produced by an embedding model. Used for similarity search.
- **Engagement signal** — A user action (read, save, skip, more-like, less-like) on a system-surfaced item, used to tune the system over time.
- **Fact-check marker** — An inline `[FACT-CHECK FAILED: <claim>]` marker in a draft, indicating a claim that could not be verified against the research block.
- **Inline button** — A clickable button attached to a Telegram message, returned as a `CallbackQuery` when pressed.
- **Namespacing** — The practice of partitioning the memory layer so that agents can only read/write their declared namespaces.
- **pgvector** — A Postgres extension that adds a `vector` type and similarity operators (`<=>`, `<->`, etc.) for vector search.
- **Refuse / REFUSE** — A specific output the LLM is prompted to return when it cannot produce a confident answer; the system then drops the item.
- **Tool use** — The LLM's ability to call typed external functions. Hermes is chosen specifically for its tool-use strength.
- **Voyage 3** — The current flagship embedding model from Voyage AI; 1024 dimensions, SOTA for IR.
- **Watchlist** — The user's list of professors being tracked for master's applications.

### Appendix C — Open questions

These questions are tracked in §6 of `paper-tracker-design.md`. The SRS does not resolve them; they are implementation-time decisions.

1. Postgres hosting: Azure (clean story, ~$12/mo) vs Supabase/Neon (free tier, off-Azure).
2. Initial research interests essay: when is the user writing this?
3. Initial professor watchlist: 5-10 profs from target programs.
4. Notion DBs setup: confirm "Papers" and "Professors" DBs exist with the right schema.
5. Email provider: SendGrid vs AWS SES vs SMTP relay; affects cost and deliverability.
6. Hermes host: Nous Research API vs OpenRouter vs self-hosted. Cost and latency differ.
7. Acceptance criteria for "v1.0 done": feature-complete vs polish-complete. The SRS assumes feature-complete; the user can adjust.

### Appendix D — Change log

| Version | Date | Changes |
|---------|------|---------|
| v0.1 | 2026-07-16 | Initial draft. |

---

**End of SRS.**
