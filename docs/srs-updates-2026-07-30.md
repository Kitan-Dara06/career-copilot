# SRS Updates — 2026-07-30

This file documents the proposed changes to `/home/kitan/Documents/mutli-agent/srs.md` based on actual implementation state vs documented requirements.

---

## Issue 1 — Wrong LLM names

The SRS repeatedly references **"Hermes"** as the LLM. The actual implementation uses **Gemini 2.5 Flash** and **DeepSeek v4-pro/v4-flash**. Hermes was never wired up.

### Locations to update

**§1.3 Definitions** (line 69):
```
- CURRENT: Hermes | The LLM used for all generation. Accessed via API.
- REPLACE: Primary LLM | Gemini 2.5 Flash for structured JSON (classification, 
  extraction, parsing) and DeepSeek v4-pro for long-context synthesis. 
  Accessed via API. 
```

**§2.2 Product Functions** (line 167):
```
- CURRENT: F-CF.3 — Analyze issue context using Hermes; produce a short summary
- REPLACE: F-CF.3 — Analyze issue context using Gemini 2.5 Flash; produce a short summary
```

**§3.1.2 Software interfaces** (line 268):
```
- CURRENT: | Hermes API | HTTPS / REST | API key | Model pluggable |
- REPLACE: | Gemini API | HTTPS / REST | API key | Gemini 2.5 Flash, model pluggable |
           | DeepSeek API | HTTPS / REST | API key | DeepSeek v4-pro, v4-flash |
```

**§3.2.1 Paper Tracker** F-PT.4, F-PT.5:
```
- CURRENT: F-PT.4 — ... using the summarize_paper_v* prompt and Hermes.
- REPLACE: F-PT.4 — ... using the summarize_paper_v* prompt and the primary LLM.

- CURRENT: F-PT.5 — ... using the why_relevant_v* prompt. If the prompt returns "REFUSE"...
- REPLACE: (no change to text, just add: "Implemented with Gemini 2.5 Flash; 
  the why_relevant prompt returns a 0-1 relevance score instead of REFUSE.")
```

**§3.2.2 Job Hunter** F-JH.4 (referenced in the implementation but not formally specified):
```
- ADD: F-JH.4 — For industry roles, the system shall build a structured 
  research block containing: company mission, products, recent news, 
  key people, required skills, and nice-to-haves, using Tavily search + 
  Firecrawl homepage scrape + Gemini 2.5 Flash synthesis.
```

---

## Issue 2 — F-CF.3 inconsistent with §2.2 summary

§2.2 lists F-CF.1 through F-CF.5 with brief descriptions. §3.2.3 formalizes them as F-CF.1 through F-CF.5. The numbers match. Content is mostly consistent except for the Hermes reference in §2.2.

**Fix:** Apply the Hermes→Gemini replacement from Issue 1 to §2.2.

---

## Issue 3 — Missing F-CF features for data persistence + dedup

The current F-CF.1 to F-CF.5 cover search, scoring, summary, digest, feedback. Missing:

### Add to §3.2.3

```
**F-CF.6** The system shall persist every opportunity that passes the impact 
score threshold to a `contribution_opportunities` table with: id, github_repo, 
github_issue_number, title, body_snippet, labels, created_at, updated_at, 
comment_count, score, status, first_seen_at, last_seen_at.

**F-CF.7** The system shall embed the title + body of each new opportunity 
using Voyage 3 and dedupe against existing opportunities across digests. 
Re-seeing the same issue updates `last_seen_at` but does not re-add.

**F-CF.8** The system shall expose the following commands:
- /contrib [topic] — find new opportunities matching topic (default: user's 
  skill clusters from `user_skills.yaml`)
- /contrib — show this week's high-impact opportunities
- /opportunity <id> — get the full analysis of one opportunity
- callback `interested` / `pass` / `doing` — record user feedback

**F-CF.9** The system shall weight the impact score as:
  impact = (1 / log(issue_age_days + 2)) 
          × maintainer_reaction_score (reactions / 10, capped at 1.0)
          × skill_match_score (per-cluster weighted max against user_skills.yaml)
          × uncrowded_score (1.0 / (1 + comment_count / 5))
The final 0-1 score normalizes by the maximum across the candidate pool. 
Top 10-20 by score are kept for LLM analysis (F-CF.3).
```

---

## Issue 4 — Add commands section for Contribution Finder

### Add to §3.1.1 User interfaces (or create new §3.2.5)

```
#### 3.2.5 Contribution Finder commands

| Command | What it does |
|---------|--------------|
| /contrib [topic] | Find new high-impact opportunities (default: user's skills) |
| /contrib | Show this week's top opportunities |
| /opportunity <id> | Get full analysis of one opportunity |
| interested | Callback: mark opportunity as one user wants to pursue |
| pass | Callback: dismiss opportunity from future digests |
| doing | Callback: mark as already working on |
```

---

## Issue 5 — Add data model for contribution finder

### Add to §4 (Appendices) — new appendix E

```
### Appendix E — Contribution Finder data model

```sql
-- Discovered opportunities (deduplicated across digests)
CREATE TABLE contribution_opportunities (
  id                  BIGSERIAL PRIMARY KEY,
  github_repo         TEXT NOT NULL,           -- "owner/repo"
  github_issue_number INTEGER NOT NULL,
  title               TEXT NOT NULL,
  body_snippet        TEXT,
  labels              TEXT[],
  created_at_gh       TIMESTAMPTZ,            -- when the issue was opened on GitHub
  updated_at_gh       TIMESTAMPTZ,            -- last activity on GitHub
  comment_count       INTEGER NOT NULL DEFAULT 0,
  reaction_count      INTEGER NOT NULL DEFAULT 0,
  score               REAL NOT NULL,
  skill_match         REAL,                    -- per-cluster weighted max
  status              TEXT NOT NULL DEFAULT 'new',
                      -- new | interested | pass | doing | closed
  first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_cf_repo_issue ON contribution_opportunities (github_repo, github_issue_number);

-- Per-user feedback log
CREATE TABLE contribution_feedback (
  id                  BIGSERIAL PRIMARY KEY,
  opportunity_id      BIGINT NOT NULL REFERENCES contribution_opportunities(id),
  user_id             TEXT NOT NULL,
  signal              TEXT NOT NULL,           -- 'interested' | 'pass' | 'doing'
  feedback_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_cf_feedback_user_opp ON contribution_feedback (user_id, opportunity_id);
```

---

## Issue 6 — Update NFRs to reflect actual model costs

### §3.3.7 Cost NFR-C.1
```
- CURRENT: Total monthly operating cost shall not exceed $15 USD in v1.0 
  (three agents), and $5 USD in v0.1 (Paper Tracker only).
- REPLACE: Total monthly operating cost shall not exceed $15 USD in v1.0 
  (three agents), and $8 USD in v0.2 (Paper Tracker + Job Hunter active; 
  Contribution Finder runs weekly at $0.30-$1.00 per digest).
```

---

## Issue 7 — Add explicit scheduled worker mention

The system has a `career_copilot worker` process that polls `scheduled_jobs` 
every 30s. This is documented in the implementation but not in the SRS.

### Add to §2.2 Product Functions

```
- F-INFRA.1 — A standalone worker process shall poll the `scheduled_jobs` 
  table every 30 seconds and execute due jobs via the central dispatcher.
```

---

## Summary

7 issues, all minor. Total diff: ~40 lines changed, ~50 lines added (new 
F-CF.6-9, data model appendix, commands list).

**Apply in this order:**
1. Issue 1 (LLM rename) — 4 hunks
2. Issue 3 (F-CF.6-9) — 1 section added
3. Issue 4 (commands) — 1 small section
4. Issue 5 (data model) — new appendix
5. Issue 2 (cross-reference) — depends on Issue 1
6. Issue 6 (NFR) — 1 line
7. Issue 7 (worker) — 1 bullet

**Do not apply:** anything that contradicts the implemented behavior. The SRS 
must reflect the actual system, not the original ambition. Hermes is dead, 
the v0.3 Contribution Finder exists as a registered agent shell (no commands), 
and `python -m career_copilot worker` is the scheduling mechanism.
