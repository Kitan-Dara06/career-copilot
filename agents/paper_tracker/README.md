# Paper Tracker — arXiv Digest & Professor Tracking

**v0.1** — Sends a daily 9am digest of matched arXiv papers to your Telegram.

## Architecture

```
Scheduler (9am) → Dispatcher → PaperTrackerAgent
                                     ├── Stream A: by interest
                                     │   ├── arxiv.fetch_recent
                                     │   ├── vector.embed (Voyage 3)
                                     │   ├── vector.search (Qdrant)
                                     │   └── pick top 5
                                     └── Stream B: by professor
                                         ├── arxiv.fetch_author
                                         ├── dedupe against seen
                                         └── group by professor
                                         ↓
                                   telegram.send_digest
```

## Commands

| Command | Description |
|---------|-------------|
| `/digest now` | Run digest immediately |
| `/digest on/off` | Enable/disable daily digest |
| `/digest at HH:MM` | Set custom digest time |
| `/watch add <name>` | Add a professor |
| `/watch list` | View watchlist |
| `/watch remove <name>` | Remove a professor |
| `/discover` | Discover professors matching your interests |
| `/prof <name>` | Generate a professor brief |
| `/interests` | Show current interest vector |

## Prompts

All prompts live in `prompts/` and are versioned (`_v1.yaml`, `_v2.yaml`).

| Prompt | Purpose |
|--------|---------|
| `system_v1.yaml` | Agent role and constraints |
| `summarize_paper_v1.yaml` | 2-sentence paper summary |
| `why_relevant_v1.yaml` / `v2.yaml` | Interest-stream "why" line |
| `professor_why_v1.yaml` | Professor-stream "why" line |
| `professor_brief_v1.yaml` | Full professor brief template |
| `professor_discovery_v1.yaml` | Discovery mode summary |
| `email_opener_v1.yaml` | Email opener for cold outreach |
| `filter_decision_v1.yaml` | Relevance threshold check |
