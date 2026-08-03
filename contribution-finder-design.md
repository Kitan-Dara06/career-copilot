# Contribution Finder — Design

**Project:** Career Copilot → Contribution Finder agent
**Document version:** v0.1
**Status:** Draft — design only, implementation pending
**Last updated:** 2026-07-30

---

## 0. Scope and positioning

Contribution Finder is the third of three agents in Career Copilot. It handles:

1. **Opportunity discovery** — find high-impact open issues and PRs on GitHub repos that match the user's skills and interests
2. **Impact scoring** — rank issues not by stars or popularity, but by whether a contribution would meaningfully move the needle
3. **Context analysis** — summarize the problem, why it matters, suggested first steps, estimated effort
4. **Weekly digest** — surface the top 10-20 opportunities every Sunday evening
5. **Feedback loop** — record `interested` / `pass` / `doing` signals to retune future scoring

Contribution Finder is **not** a job board (that's Job Hunter) and **not** a research paper tracker (that's Paper Tracker). The output is *contributions to open source*, not employment. The portfolio value is different: open-source PRs from a BSc student in Nigeria demonstrate exactly the skills master's admissions committees look for (technical depth, async collaboration, shipping in public, working on production codebases).

**Why this matters for Aaliyah's profile:** She has strong `agent_systems` (multi-agent architectures, agent observability) and `rag_retrieval` (graph RAG, vector databases) skills. Active repos in those areas are LangChain, LlamaIndex, AutoGen, CrewAI, vector DBs (Chroma, Weaviate, Qdrant). Contributing to any of them yields a real PR on a production codebase — which is the single strongest evidence of "I can build and ship" before she has industry experience.

---

## 1. User profile (shared with Job Hunter)

**File:** `data/user_skills.yaml` (reused — no separate profile)

The Contribution Finder reads the same skill clusters Job Hunter uses for match scoring. No new profile file.

```yaml
# Reused from user_skills.yaml — see Job Hunter design §3
# 14 skill clusters with weights:
#   agent_systems (1.5)       — multi-agent architectures, task routing
#   rag_retrieval (1.5)        — graph RAG, vector databases
#   llm_ops (1.2)              — long-term context, document parsing
#   ai_ml_frameworks (1.2)     — LangChain, LlamaIndex, Hugging Face
#   backend (1.0)              — APIs, microservices, async
#   data_engineering (0.9)     — web scraping, SQL dialect translation
#   languages (0.8)            — Python, JavaScript
#   data_parsing_libs (0.7)    — sqlglot, BeautifulSoup, Scrapy
#   devops (0.7)               — Git, Docker, CI/CD
#   multimodal (0.6)           — text-LLM ↔ visual bridging
#   computer_vision (0.5)      — OCR, document digitization
#   affective_advanced (0.5)   — sentiment-aware generation
#   creative_tech (0.3)        — 3D rendering, Three.js
#   frontend_creative (0.3)    — WebGL
```

**Topic search default** (when `/contrib` is called without args): union of all skill cluster names expanded into search terms:
- "multi-agent", "agent orchestration", "task routing"
- "RAG", "graph RAG", "vector database", "retrieval-augmented"
- "LangChain", "LlamaIndex", "Hugging Face", "AutoGen", "CrewAI"
- "LLM", "document parsing", "embedding"
- "async Python", "FastAPI", "asyncio"
- "web scraping", "SQL", "schema"

**CF-specific preferences** (stored in `data/user_profile.yaml` under `cf_prefs`):
```yaml
cf_prefs:
  preferred_effort_buckets: ["1-4 hours", "half day", "1-2 days"]
  # ^ Adjustable via /contrib efforts. See §3a.
  max_opportunities_per_digest: 60
  # ^ Aaliyah wants a large volume. Default 60.
  pass_cooldown_days: 30
  # ^ Reset-to-inbox model: 30 days then re-surface.
  digest_cadence_days: 7
  # ^ Weekly digest, default.
  digest_time: "18:00"  # 18:00 WAT (17:00 UTC) Sunday
  min_impact_score: 0.55
  language_filter: "python"  # Hard requirement.
  github_token_present: false  # Auto-detected from env.
  preferred_repos_only: false
  # ^ If true, only show issues from tracked_repos (Path B). Default false.
```

---

## 2. Discovery model (search-based, not watchlist-based)

**No fixed repo list.** Unlike Job Hunter's company watchlist, the Contribution Finder does not maintain a hand-curated list of repos. Reasons:

1. **Aaliyah's interests will drift.** Today's hot framework (LangChain) is tomorrow's legacy. A static list freezes what should be adaptive.
2. **The system should be self-expanding.** When Aaliyah saves a paper citing a new framework, that framework should be discoverable.
3. **GitHub search is the source of truth.** The GitHub API can find issues matching any topic, in any repo, with any label — no curation needed.

**Two discovery paths, both query-time, not list-maintenance-time:**

### 2.1 Path A: Topic search (primary)

`/contrib [topic]` or default weekly digest runs the GitHub Issues Search API with queries derived from the user's skill clusters. The skill cluster names are expanded into search terms (see §1).

**Three filters applied to every query:**

1. **`language:python`** — Aaliyah only contributes to Python repos (GitHub Issues Search supports `language:X`).
2. **`is:issue`** — Only issues, not PRs (PRs are too varied to be useful as opportunities).
3. **`is:open`** — Closed issues are not actionable.

Example queries derived from Aaliyah's profile:
```
is:issue is:open language:python label:"good first issue" "multi-agent" OR "agent orchestration"
is:issue is:open language:python label:"help wanted" RAG OR "retrieval-augmented" OR "vector database"
is:issue is:open language:python label:"good first issue" LangChain OR LlamaIndex OR "Hugging Face"
is:issue is:open language:python label:"help wanted" "document parsing" OR PDF OR "OCR"
is:issue is:open language:python label:"good first issue" Python asyncio OR FastAPI OR async
```

Each query returns up to 100 issues. Total 6 queries × 100 = 600 candidates per run. After filtering (issue age < 90 days, comments < 50, score > 0.55), typically 30-80 unique opportunities.

**Volume preference:** Aaliyah wants a *large amount* of opportunities per digest, not the top 5. Default behavior:
- Show all opportunities scoring > 0.55 (typically 30-80 per digest)
- Truncate body to 200 chars in the inline preview
- Inline buttons: [Interested] [Pass] [Doing] [Open issue] for each
- Aaliyah can set `max_opportunities_per_digest` in `user_profile.yaml` (default: 60)

### 2.2 Path B: Tracked repos (Aaliyah's manual additions)

**File:** `data/cf_tracked_repos.yaml`

Aaliyah can `add` repos she's already active in or specifically interested in. These go in a small set (5-15 repos, not 30+).

**Pre-seeded (Aaliyah confirmed):** starter list of Python-first repos. The system relies primarily on topic search (§2.1) but these repos get extra scoring weight when they appear in results.

```yaml
# Repos Aaliyah actively watches. Pre-seeded with Python-first AI/agent
# repos matching her skills. Add via /contrib repos add owner/repo.
tracked_repos:
  # AI/agent frameworks (Python)
  - full_name: "langchain-ai/langchain"
    language: "python"
    topic_hint: "agent"
  - full_name: "langchain-ai/langgraph"
    language: "python"
    topic_hint: "agent"
  - full_name: "run-llama/llama_index"
    language: "python"
    topic_hint: "rag"
  - full_name: "microsoft/autogen"
    language: "python"
    topic_hint: "agent"
  - full_name: "crewAIInc/crewAI"
    language: "python"
    topic_hint: "agent"
  - full_name: "openai/openai-python"
    language: "python"
    topic_hint: "agent"
  # Vector databases (Python clients)
  - full_name: "chroma-core/chroma"
    language: "python"
    topic_hint: "vector-db"
  - full_name: "qdrant/qdrant-client"
    language: "python"
    topic_hint: "vector-db"
  # Document parsing
  - full_name: "unstructured-io/unstructured"
    language: "python"
    topic_hint: "document-parsing"
  - full_name: "py-pdf/pypdf"
    language: "python"
    topic_hint: "document-parsing"
  # Data transformation
  - full_name: "tobymao/sqlglot"
    language: "python"
    topic_hint: "data"
```

**Language filter:** Aaliyah only wants Python repos. The discovery system adds `language:python` to every GitHub search query. The tracked-repos YAML also has an explicit `language: python` field; the system rejects `/contrib repos add owner/repo` for non-Python repos (with a confirmation prompt if the language is ambiguous).

**Adding new repos:** Aaliyah can add at any time via `/contrib repos add owner/repo` or `/contrib repos add <github_url>`. The system verifies the repo exists, is primarily Python (>50% of files), and has open issues. If the repo fails these checks, the system asks before adding.

### 2.3 Path C: Self-expanding (long-term)

When Aaliyah saves a paper via Paper Tracker that references a new repo, the system surfaces a suggestion: *"You saved 'X' — this paper evaluates on `chroma-core/chroma`. Add to tracked repos?"* With one click, the repo joins the tracker.

When Job Hunter finds a job posting that mentions an OSS project, same flow.

**This path is not in v0.1** — it's listed in §12 as cross-agent integration.

---

## 3. Impact score formula

The fundamental design choice: **score by whether the contribution would move the needle, not by stars or comments.**

```python
def compute_impact_score(issue: Issue, user_skills: SkillClusters) -> float:
    """0-1 normalized impact score. Higher = better opportunity."""
    
    # Freshness: log-decay. An issue open 7 days scores 1.0;
    # open 30 days scores 0.66; open 90 days scores 0.50;
    # open 365 days scores 0.33.
    age_days = (now() - issue.created_at).days
    freshness = 1.0 / math.log(age_days + 2)
    
    # Maintainer engagement: reactions show the maintainer cares
    # but hasn't had time to fix it. 0 reactions → 0; 10+ → 1.0.
    reactions = min(issue.reaction_count, 10) / 10.0
    
    # Skill match: per-cluster weighted max against user skills.
    # Reuses the Job Hunter scoring logic (Job Hunter design §3).
    skill_match = weighted_max_score(issue.title + " " + issue.body, user_skills)
    
    # Uncrowded: an issue with 0 comments is more open than one with
    # 50 comments. 1 / (1 + comment_count / 5).
    uncrowded = 1.0 / (1.0 + issue.comment_count / 5.0)
    
    # Unworked: Aaliyah wants opportunities that *nobody else* is actively
    # working on. Combines two signals:
    # - linked_pr_count: number of PRs that reference this issue.
    #   0 = nobody's tried yet, 1 = someone is in flight, 5+ = it's been taken.
    # - last_activity_days: how recently anyone touched the issue.
    #   >30 days = dormant (good); <7 days = active (someone's on it).
    if issue.linked_pr_count == 0:
        pr_unworked = 1.0
    elif issue.linked_pr_count <= 2:
        pr_unworked = 0.6
    else:
        pr_unworked = 0.2
    activity_unworked = min(1.0, issue.last_activity_days / 30.0)
    unworked = 0.6 * pr_unworked + 0.4 * activity_unworked
    
    # Label bonus: well-tagged issues are clearer.
    label_bonus = 0.0
    if "good first issue" in issue.labels: label_bonus += 0.15
    if "help wanted" in issue.labels: label_bonus += 0.10
    if "bug" in issue.labels: label_bonus += 0.05
    if "documentation" in issue.labels: label_bonus += 0.05
    label_bonus = min(label_bonus, 0.30)
    
    raw = (freshness * 0.30 + reactions * 0.10 + skill_match * 0.25
           + uncrowded * 0.05 + unworked * 0.20 + label_bonus * 0.10)
    return min(raw, 1.0)
```

**Why these weights:**
- **Freshness 30%** — recent issues are more likely to still be relevant
- **Skill match 25%** — only the user's skills count, not generic popularity
- **Unworked 20%** — Aaliyah explicitly wants opportunities no one is touching
- **Reactions 10%** — maintainer engagement without comments is a high-signal "I want this but don't have time"
- **Labels 10%** — `good first issue` is a maintainer signal that the issue is well-scoped for a new contributor
- **Uncrowded 5%** — fewer comments = more room to be the contributor (less weight than `unworked` because `unworked` is the stronger signal)

**Threshold:** `min_impact_score: 0.55`. Below this, the issue is not surfaced.

**Tracking unworked over time:** when an issue re-appears in a weekly digest, the system recomputes `unworked`:
- If a new PR was opened against the issue in the last 7 days, `pr_unworked` drops to 0.2 — surface with a "⚠️ Someone is working on this" badge
- If 30+ days have passed with no activity, `unworked` approaches 1.0 — clear opportunity



---

## 3a. Effort bucket preferences

The system asks the LLM to pick from 5 effort buckets when analyzing an opportunity:

| Bucket | Meaning | Good for |
|---|---|---|
| `1-4 hours` | Quick fix, doc typo, one-line change | Quick wins, batch-fixes |
| `half day` | 4-6 hours, single function or small refactor | Solid PRs in a weekend |
| `1-2 days` | 8-16 hours, multi-file change | Most master's portfolio-worthy contributions |
| `3-5 days` | 24-40 hours, significant new feature | Stretch goals, multi-week PRs |
| `1+ week` | Multi-week effort, design + implementation + tests | Long-term, commit-after-proposal |

**Default preferences:** Aaliyah wants to see **half day** and **1-2 days** opportunities first — those match her commitment level and the most useful scope for a BSc student building a portfolio. Other buckets are still shown but ranked lower.

**Adjustable via Telegram command:** `/contrib efforts <bucket1,bucket2,...>`. The system stores the preference in `user_profile.yaml` and re-ranks the digest accordingly.

Examples:
```
/contrib efforts 1-4 hours,half day,1-2 days      # default
/contrib efforts 1-2 days,3-5 days                # focus on bigger PRs
/contrib efforts 1-4 hours,half day              # only quick wins this week
```

**How it works internally:** opportunities that match the preferred buckets get a 1.15× score multiplier. Non-matching ones still appear but with a "📏 outside preferred scope" badge.

---

## 4. LLM analysis

After scoring, the top 10-20 issues get a structured analysis via `github_analyze_v1` prompt (Gemini 2.5 flash).

```yaml
version: 1
agent: contribution_finder
name: github_analyze
model:
  name: gemini-2.5-flash
  temperature: 0.2
  max_tokens: 350
input_schema:
  fields:
    - name: title
      type: str
      description: Issue title
    - name: body_snippet
      type: str
      description: First 1500 chars of the issue body
    - name: repo
      type: str
      description: "owner/repo"
    - name: comments
      type: str
      description: Top 3 comment snippets
    - name: user_skills
      type: str
      description: User's top 5 skill clusters, comma-separated
template: |
  Analyze this GitHub issue and assess it as a contribution opportunity for Aaliyah.
  
  Repo: {repo}
  Title: {title}
  Body: {body_snippet}
  Top comments: {comments}
  User skills: {user_skills}
  
  Output STRICT JSON only — no fences, no prose.
  Schema:
  {{
    "problem": "<1-2 sentence summary of what the issue asks for>",
    "why_it_matters": "<1 sentence: who benefits, what breaks without it>",
    "suggested_first_steps": "<2-3 concrete technical steps to start>",
    "estimated_effort": "<1-4 hours | half day | 1-2 days | 3-5 days | 1+ week>",
    "blocked_by": "<one of: needs-context, needs-tests, needs-arch-decision, none>"
  }}
```

**Cost per issue:** ~$0.001 (Gemini 2.5 flash, 350 max tokens, 10-20 issues per digest = $0.01-$0.02 per digest).

---

## 5. Digest cadence

**Weekly, Sunday evening 18:00 WAT (17:00 UTC).** The system fetches issues, scores them, runs the top 10-20 through Gemini, and sends the digest.

Unlike Job Hunter (3-day cadence) or Paper Tracker (daily), Contribution Finder is weekly because:
- GitHub issue volume is steady, not bursty
- Contribution opportunities take days/weeks to act on — daily would be noise
- The user's attention budget for open-source is much smaller than for jobs/papers

**First digest:** next Sunday at 18:00 WAT.

---

## 6. The full Contribution Finder architecture

```
                  ┌─────────────────────┐
                  │  /contrib or weekly │
                  │  scheduler          │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  GitHub search      │
                  │  (60 req/h free)    │
                  │  + tracked repos    │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Filter + Score     │
                  │  - language:python  │
                  │  - created < 90d    │
                  │  - unworked bonus    │
                  │  - dedupe vs DB      │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Context enrichment │
                  │  Tavily (short body)│
                  │  Firecrawl (locked) │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Gemini analysis    │
                  │  (top 20)           │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Telegram digest    │
                  │  30-80 opportunities│
                  │  [Interested]       │
                  │  [Pass] [Doing]     │
                  │  [Open issue]       │
                  └─────────────────────┘
```

---

## 7. Commands

| Command | What it does |
|---------|--------------|
| `/contrib` | Run this week's discovery (alias for `/contrib weekly`) |
| `/contrib [topic]` | Search GitHub for issues matching topic (e.g. `/contrib langchain agent`) |
| `/contrib repos` | Show the tracked repos (Aaliyah's manual additions) |
| `/contrib repos add <owner/repo>` | Add a repo to tracked list |
| `/contrib repos remove <owner/repo>` | Remove a repo from tracked list |
| `/opportunity <id>` | Get the full Gemini analysis of one opportunity |
| `/opportunities` | Show the most recent week's digest |
| `/opportunity skip <id>` | Mark an opportunity as dismissed |
| `/contrib cadence <days>` | Set digest frequency (default: 7=weekly) |
| `/contrib efforts <bucket1,bucket2,...>` | Set preferred effort buckets (default: `1-4 hours,half day,1-2 days`). See §3a. |
| `/help_cf` | Show Contribution Finder help |

### Callback buttons (inline)

| Button | Effect |
|--------|--------|
| `interested` | Record `interested` signal in `contribution_feedback`. The issue stays in the next digest as a "reminder". |
| `pass` | Record `pass` signal. Issue removed from future digests for 30 days. |
| `doing` | Record `doing` signal. Issue flagged as "in progress"; won't re-appear in digests. |

---

## 8. The digest output (weekly Sunday 18:00 WAT)

```
🔍 Contribution Finder digest — 2026-08-03
   Aaliyah · LangChain + LlamaIndex + vector DBs
   18 high-impact opportunities this week

🔥 Top 5 by impact score

1. ⭐ 0.82 — langchain-ai/langgraph
   "Agent state persistence: support for custom retry policies"
   Why it matters: blocks several users' production workflows; no current maintainer.
   Suggested first steps: read the existing retry policy code, write a failing
   test demonstrating the missing config option, draft the API.
   Estimated effort: 1-2 days
   [Interested] [Pass] [Doing] [Open issue]

2. ⭐ 0.76 — run-llama/llama_index
   "Add async support to the SimpleDirectoryReader"
   Why it matters: slows down ingestion pipelines for users with large doc sets.
   Suggested first steps: identify the sync call sites, refactor to use aiofiles.
   Estimated effort: 3-5 days
   [Interested] [Pass] [Doing] [Open issue]

3. ⭐ 0.71 — chroma-core/chroma
   "Type stubs for the Python client"
   Why it matters: many users have asked for this on Discord.
   Suggested first steps: install mypy, generate a baseline stub file, fill gaps.
   Estimated effort: 1-2 days
   [Interested] [Pass] [Doing] [Open issue]

4. ⭐ 0.68 — langchain-ai/langchain
   "Document splitter handles LaTeX equations incorrectly"
   Why it matters: scientific users (including academic researchers) hit this
   regularly; the splitter loses math content.
   Suggested first steps: write a failing test for LaTeX with embedded $...$ blocks.
   Estimated effort: half day
   [Interested] [Pass] [Doing] [Open issue]

5. ⭐ 0.64 — tobymao/sqlglot
   "ClickHouse dialect: support for ARRAY JOIN syntax"
   Why it matters: this dialect is in high demand from data engineers.
   Suggested first steps: look at the Postgres ARRAY handling as a template.
   Estimated effort: 1-2 days
   [Interested] [Pass] [Doing] [Open issue]

📋 13 more opportunities (collapsed)
[View all] [Filter by repo] [Filter by skill]

🎯 Your tracked opportunities (1)
- [from 2 weeks ago] microsoft/autogen#2451 — "Add observability hooks"
  Status: interested · Last reminder: 2026-07-20
```

---

## 9. Data model

```sql
-- Discovered opportunities (deduplicated across digests)
CREATE TABLE contribution_opportunities (
  id                  BIGSERIAL PRIMARY KEY,
  github_repo         TEXT NOT NULL,           -- "owner/repo"
  github_issue_number INTEGER NOT NULL,
  title               TEXT NOT NULL,
  body_snippet        TEXT,
  url                 TEXT NOT NULL,
  labels              TEXT[],
  created_at_gh       TIMESTAMPTZ,            -- when the issue was opened on GitHub
  updated_at_gh       TIMESTAMPTZ,            -- last activity on GitHub
  comment_count       INTEGER NOT NULL DEFAULT 0,
  reaction_count      INTEGER NOT NULL DEFAULT 0,
  age_days            INTEGER NOT NULL,
  linked_pr_count     INTEGER NOT NULL DEFAULT 0,    -- PRs that reference this issue
  last_activity_days  INTEGER NOT NULL DEFAULT 0,    -- days since any comment/edit
  score               REAL NOT NULL,
  skill_match         REAL,                           -- per-cluster weighted max
  problem             TEXT,                           -- Gemini analysis
  why_it_matters      TEXT,
  suggested_first_steps TEXT,
  estimated_effort    TEXT,                           -- '1-4 hours' | 'half day' | etc.
  blocked_by          TEXT,
  status              TEXT NOT NULL DEFAULT 'new',
                      -- new | interested | pass | doing | closed
  first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_cf_opp_repo_issue ON contribution_opportunities (github_repo, github_issue_number);
CREATE INDEX idx_cf_opp_status ON contribution_opportunities (status);
CREATE INDEX idx_cf_opp_score ON contribution_opportunities (score DESC);

-- Per-user feedback log
CREATE TABLE contribution_feedback (
  id                  BIGSERIAL PRIMARY KEY,
  opportunity_id      BIGINT NOT NULL REFERENCES contribution_opportunities(id),
  user_id             TEXT NOT NULL,
  signal              TEXT NOT NULL,           -- 'interested' | 'pass' | 'doing'
  feedback_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_cf_feedback_user_opp ON contribution_feedback (user_id, opportunity_id);

-- Tracked repos (Aaliyah's manual additions; default empty)
CREATE TABLE contribution_repos (
  id                  BIGSERIAL PRIMARY KEY,
  github_full_name    TEXT NOT NULL UNIQUE,    -- "owner/repo"
  topic_hint          TEXT,                    -- 'agent', 'rag', 'vector-db', etc.
  added_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 10. Deduplication across digests

The same issue may be visible in multiple weekly digests if it stays open. The system:
1. Fetches the issue from GitHub every week
2. Checks if `(github_repo, github_issue_number)` already exists in `contribution_opportunities`
3. If yes, updates `last_seen_at`, `comment_count`, `reaction_count`, `score` (recomputed)
4. If no, inserts as a new row

The `interested` status persists across digests. The issue is shown as a "reminder" in the bottom of the next digest until the user marks it `pass`, `doing`, or it closes on GitHub.

Issues marked `pass` are excluded from digests for **30 days** (the *reset to inbox* model), then re-surface. This is not a permanent blacklist — Aaliyah may want to reconsider. The 30-day cooldown is configurable in `user_profile.yaml`.

---

## 11. Source APIs and access

The system uses three external sources, ordered by priority.

### 11.1 GitHub REST API (primary)

**Authentication: Fine-grained personal access token, scoped to `Issues: Read-only`.**

#### Why fine-grained, not classic

Classic PATs force you to grant write scope (`public_repo` or `repo`) even when you only need to read. Fine-grained PATs let you grant exactly the minimum:

| Aspect | Fine-grained PAT | Classic PAT |
|---|---|---|
| Minimum scope for read-only | `Issues: Read-only` | `public_repo` (grants write to issues, PRs, comments) |
| Per-repo restrictions | Yes | No |
| Expiration | 90 days recommended (auto-revokes) | No expiration by default |
| Revocation blast radius | Only this token | All integrations using it |

If the token ever leaks from a log/error, fine-grained limits the blast radius to "can read issue titles" instead of "can open PRs in your name."

#### Token setup (Aaliyah)

1. GitHub -> Settings -> Developer settings -> Personal access tokens -> **Fine-grained tokens** -> **Generate new token**
2. Token name: `career-copilot-contribution-finder`
3. Expiration: 90 days (recommended) or No expiration (less safe, but more convenient)
4. Resource owner: your GitHub account
5. Repository access: **All repositories** (or Public Repositories for narrower scope)
6. Permissions -> Repository permissions: **Issues: Read-only**
7. Click **Generate token**
8. Copy the token, paste into `.env` as `GITHUB_TOKEN=<token>`

The system reads `GITHUB_TOKEN` from the environment on every API call. If unset, falls back to anonymous (60 req/h).

#### Rate limit tiers

| Auth tier | Limit | Sufficient for |
|---|---|---|
| Anonymous (no token) | 60 req/h | Weekly cadence, up to ~15 tracked repos |
| Fine-grained PAT | 5,000 req/h | 3-day cadence, 50+ tracked repos, comment fetching |

#### API budget per weekly digest

- 6 topic-search queries (Path A) × 1 call each = 6 calls
- N tracked-repos (Path B) × 1 call each = 0-15 calls (depends on user additions)
- Top 20 opportunity detail fetches = 20 calls
- `linked_pr_count` and `last_activity_days` lookups: 1 call per opportunity = 20 calls
- Top 5 comments per top opportunity = 5 calls (only on the 5 highest-scoring issues)

Total: **~70 calls per weekly digest**, well under any tier. The system runs the digest over 2-3 hours, so the rate never peaks.

### 11.2 Tavily (secondary, for issue context)

GitHub issues sometimes have very short bodies. Tavily is used to find richer context:
- "issue <issue title> discussion" — finds blog posts, forum threads, Twitter discussions
- "<repo> <feature> implementation status" — finds maintainer statements, related issues
- "<repo> roadmap 2025 2026" — finds future direction signals (helps score)

**Budget per digest:** 5-10 Tavily queries at $0.008 each = $0.04-$0.08. Runs only on the top 20 opportunities after scoring.

**When to skip:** if GitHub body is > 500 chars, skip Tavily for that issue (body is already substantive).

### 11.3 Firecrawl (tertiary, for issue body expansion)

When GitHub returns a short body AND Tavily didn't find useful context, Firecrawl scrapes the issue page directly. This handles:
- Issues where the maintainer wrote a long description in a code block
- Issues with screenshots/images (Firecrawl can OCR some)
- Issues that are locked to specific GitHub features GitHub API doesn't return

**Budget per digest:** 1-3 Firecrawl scrapes at $0.005 each = $0.005-$0.015.

### 11.4 Cost summary

| Source | Calls/digest | Cost/digest | Cost/month (weekly) |
|--------|--------------|-------------|---------------------|
| GitHub | 70 | $0 | $0 |
| Tavily | 5-10 | $0.04-$0.08 | $0.16-$0.32 |
| Firecrawl | 1-3 | $0.005-$0.015 | $0.02-$0.06 |
| Gemini 2.5 flash (analysis) | 20 | $0.01-$0.02 | $0.04-$0.08 |
| **Total** | ~100 | **$0.05-$0.12** | **$0.22-$0.46** |

Within the SRS NFR-C.1 cost budget ($5 v0.1, $15 v1.0).

---

## 12. Cross-agent integrations

| Direction | How it works |
|-----------|--------------|
| **From Paper Tracker** | When Aaliyah saves a paper that references a repo (e.g. "we evaluate on Chroma"), the repo gets auto-suggested as a CF tracked-repo addition. |
| **From Job Hunter** | When a job posting mentions specific OSS projects (e.g. "experience with LangGraph"), those projects get a `+1` skill_match boost in CF scoring. |
| **To Job Hunter** | A PR merged on a CF-tracked repo with `closes #XYZ` gets recorded as a portfolio item; Job Hunter can cite it in cover letters. |

---

## 13. What I still need from Aaliyah to ship v0.1

1. **GitHub account for the bot.** The system records `feedback_at` with the user's GitHub login so future contributions can be linked. (Optional — can default to `aaliyah` if no OAuth is set up.) **Resolved — skip if not provided.**
2. **Initial tracked-repos list.** **Resolved — pre-seeded with 11 Python-first AI/agent/vector-db/repo** (langchain, langgraph, llama_index, autogen, crewAI, openai-python, chroma, qdrant-client, unstructured, pypdf, sqlglot). Python-only enforced at the GitHub query level.
3. **Digest channel.** **Resolved — Telegram only.** No email delivery for v0.1.
4. **`pass` cooldown.** **Resolved — not permanent.** 30-day cooldown. After 30 days, the issue re-appears in digests unless `interested`/`doing` overrides. This is the *reset to inbox* model — not a blacklist.
5. **Effort estimation granularity.** **Resolved.** Five buckets retained: `1-4 hours`, `half day`, `1-2 days`, `3-5 days`, `1+ week`. Default preference is `1-4 hours, half day, 1-2 days` — adjustable via `/contrib efforts <buckets>` (added to §3a).
6. **Tavily + Firecrawl integration.** **Resolved — supported.** Use Tavily to find issues GitHub search misses (forum posts, blog posts mentioning broken repos, community discussions). Use Firecrawl to scrape issue bodies for richer context when GitHub returns short bodies. Documented in §11.

---

## 14. What this delivers for the portfolio

1. **Open-source PRs as evidence.** A merged PR on LangChain or LlamaIndex from a BSc student in Nigeria is exactly the kind of signal master's admissions look for.
2. **Multi-agent coordination made concrete.** Paper Tracker → Contribution Finder tracked repos, Job Hunter → CF skill boosts. Three agents exchanging data in production.
3. **Impact-based ranking as design choice.** Most contributors search by stars (popularity); this system scores by "would my contribution actually move the needle" — a defensible engineering trade-off.
4. **Cost discipline.** Weekly digest at $0.01-$0.02, free GitHub tier, no infrastructure beyond what's already running.
5. **Resume signal.** By the time Aaliyah applies to master's programs, she'll have 5-10 open-source PRs in the relevant repos. That's the difference between a "BSc student who took ML classes" and a "BSc student who shipped".

---

## 15. Honest about limitations

- **GitHub API rate limits** without a token cap us at 60 req/h. Aaliyah creates a fine-grained PAT (`Issues: Read-only`, 90-day expiration) and sets `GITHUB_TOKEN` in `.env` to unlock 5,000 req/h. This is recommended for v0.1 even if 60 req/h is sufficient, because classic PATs grant write scope by default.
- **GitHub issues don't cover GitLab, Bitbucket, Codeberg.** The system is GitHub-only in v0.1.
- **Issue quality varies wildly.** A `good first issue` label is no guarantee of a friendly first-contribution experience. The system surfaces the score, Aaliyah decides.
- **Aaliyah's existing GitHub profile** is not yet integrated. v0.2 will cross-reference "issues you've already commented on" and "PRs you've opened" to avoid duplicates.
- **The score formula is uncalibrated.** It weights freshness heavily (35%) because we assume recent = relevant. Aaliyah may have the opposite preference ("show me issues that have been waiting 3+ months, those are the ones maintainers actually want fixed"). The weights are configurable in `user_profile.yaml`.
