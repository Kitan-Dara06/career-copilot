# Job Hunter — Design

**Project:** Career Copilot → Job Hunter agent
**Document version:** v0.5
**Status:** Active — Aaliyah's profile baked in
**Last updated:** 2026-07-27

---

## 0. Scope and positioning

Job Hunter is the second of three agents in Career Copilot. It handles:

1. **Job discovery** — find internships + new-grad + co-op openings at companies in Nigeria, Africa (remote), EU, and Canada
2. **Pre-research flow** — research a company/role before applying
3. **Cover letter flow** — draft a letter for a specific opening, queue for email approval
4. **Cross-agent watch** — surface openings posted by profs in your Paper Tracker watchlist

Academic PhD/postdoc discovery is NOT in Job Hunter's scope — that's Paper Tracker's lane. The user is a BSc student, not applying to PhD yet.

---

## 1. User profile (the canonical reference)

**File:** `data/user_profile.yaml`

```yaml
user:
  name: "Agboola Aaliyah"
  nationality: "Nigeria"
  current_location: "Nigeria"
  education_status: "bsc_student"
  degree_completion: "2027"
  masters_intake_target: "fall_2027"

target_regions:
  primary: ["nigeria"]
  secondary: ["africa_remote"]
  future_relocation: ["eu", "canada"]

target_role_types:
  - internship
  - co_op
  - new_grad
  - research_internship

visa_requirement: "need_sponsorship"
remote_preference:
  nigeria: "any"
  africa: "remote_only"
  eu: "any"
  canada: "any"

# Salary floor: ₦150,000 - ₦200,000 per month (Aaliyah's actual target)
# That's roughly ₦1.8M - ₦2.4M / year
# In USD at ~₦1,500/$ that's about $100-130 USD / month, $1,200-1,600 / year
# This is the realistic entry-level range for ML/engineering roles in Nigeria
# Below this, the system flags but doesn't filter
salary_floor:
  nigeria: 150000          # NGN per month (Aaliyah's primary target)
  africa: 150000           # NGN per month (for remote Africa roles)
  eu: 100000               # EUR per year (co-op / master's internship)
  canada: 100000           # CAD per year (co-op)
salary_currency:
  nigeria: "NGN"
  africa: "NGN"            # remote Africa roles paid in NGN or converted
  eu: "EUR"
  canada: "CAD"
salary_period:
  nigeria: "monthly"
  africa: "monthly"
  eu: "yearly"
  canada: "yearly"
salary_filter_mode: "flag"  # show below-floor postings, but mark with a ⚠️

min_match_score: 0.55
max_results_per_digest: 20
digest_frequency_days: 3
digest_time: "08:00"        # 8am WAT
digest_day_anchor: "today"  # first digest 3 days from now, then every 3 days
```

---

## 2. Master's target schools

**File:** `data/master_targets.yaml`

```yaml
# 3.5/5 GPA — competitive for many EU master's, mid-tier for top schools
# Tier calibration:
#   reach:    top-10 globally, GPA 3.8+ typical
#   match:    top-50 globally, GPA 3.5+ typical
#   safety:   top-200 globally, GPA 3.2+ typical
# Default strategy: match_and_safety_first (apply broadly, maximize admit odds)

school_tier_calibration:
  gpa: 3.5
  scale: 5.0
  tier_preference: "match_and_safety_first"

target_schools:
  canada:
    tier: "match"
    schools:
      - name: "University of Toronto"
        tier: "reach"
        notes: "Vector Institute affiliate, top-tier NLP/IR"
      - name: "University of British Columbia (UBC)"
        tier: "reach"
        notes: "Strong NLP group, Vancouver"
      - name: "McGill University"
        tier: "match"
        notes: "Mila affiliated, Montreal"
      - name: "University of Waterloo"
        tier: "match"
        notes: "Strong CS, co-op heavy"
      - name: "University of Alberta"
        tier: "match"
        notes: "Amii affiliated, strong RL/NLP"
      - name: "University of Montreal"
        tier: "match"
        notes: "Mila affiliated, French-friendly"
      - name: "McMaster University"
        tier: "safety"
        notes: ""
      - name: "Queen's University"
        tier: "safety"
        notes: ""
      - name: "Western University (UWO)"
        tier: "safety"
        notes: ""
      - name: "Simon Fraser University"
        tier: "match"
        notes: "Big data / NLP group"
      - name: "York University"
        tier: "safety"
        notes: ""
      - name: "University of Ottawa"
        tier: "safety"
        notes: "Bilingual, strong CS"
      - name: "University of Calgary"
        tier: "safety"
        notes: ""
      - name: "Dalhousie University"
        tier: "safety"
        notes: ""

  france:
    tier: "match"
    schools:
      - name: "Sorbonne Université"
        tier: "match"
        notes: "ISIR lab, strong ML"
      - name: "Université Paris-Saclay"
        tier: "match"
        notes: "TAU team, LRI"
      - name: "Université Grenoble Alpes"
        tier: "match"
        notes: "LIG lab"
      - name: "École Polytechnique"
        tier: "reach"
        notes: "LIX lab"
      - name: "ENS Paris-Saclay (formerly ENS Cachan)"
        tier: "match"
        notes: "Stat. & App. lab"
      - name: "INSA Lyon / Toulouse"
        tier: "safety"
        notes: ""
      - name: "Université de Strasbourg"
        tier: "safety"
        notes: ""
      - name: "Aix-Marseille Université"
        tier: "safety"
        notes: ""

  eu_other:
    tier: "match"
    schools:
      - name: "TU Delft (Netherlands)"
        tier: "match"
        notes: ""
      - name: "KU Leuven (Belgium)"
        tier: "match"
        notes: ""
      - name: "ETH Zürich (Switzerland)"
        tier: "reach"
        notes: ""
      - name: "TU München (Germany)"
        tier: "match"
        notes: ""
      - name: "Technical University of Denmark"
        tier: "match"
        notes: ""
      - name: "KTH Royal Institute of Technology (Sweden)"
        tier: "match"
        notes: ""
      - name: "Aalto University (Finland)"
        tier: "match"
        notes: ""
      - name: "University of Amsterdam (Netherlands)"
        tier: "match"
        notes: "IRLab, de Rijke group"
      - name: "University of Edinburgh (UK)"
        tier: "reach"
        notes: ""
      - name: "University of Manchester (UK)"
        tier: "match"
        notes: ""
      - name: "University of Glasgow (UK)"
        tier: "match"
        notes: ""
```

---

## 3. Skills inventory

Parsed from the user's 38-skill message. The system embeds each cluster as a separate vector, so a posting that needs "Python + PyTorch + LLM orchestration" matches the "AI/ML Stack" cluster strongly.

```yaml
# data/user_skills.yaml
skills:
  agent_systems:
    - multi-agent architectures
    - task routing
    - agent-to-agent communication
    - agent observability
    - recursive self-correction loops
    - deterministic API execution

  rag_retrieval:
    - graph RAG
    - schema retrieval
    - semantic search
    - hybrid retrieval pipelines
    - vector databases

  llm_ops:
    - long-term context retention
    - document parsing
    - context-window optimization
    - low-hallucination pipelines
    - domain-specific safety filtering
    - output constraints
    - text-to-SQL generation

  affective_advanced:
    - sentiment-aware generation
    - dynamic tone/pacing adaptation
    - emotional context tracking

  data_engineering:
    - web scraping
    - data curation
    - CNN training data generation
    - AST parsing
    - SQL dialect translation
    - schema mapping

  languages:
    - Python (advanced)
    - JavaScript / Node.js

  backend:
    - production-grade API development
    - microservices architecture
    - asynchronous programming
    - scalable backend design
    - AI model ↔ backend integration
    - full-stack deployment

  computer_vision:
    - OCR
    - handwritten query processing
    - document digitization
    - Tesseract

  creative_tech:
    - 3D rendering
    - WebGL
    - interactive UI/UX
    - Three.js

  multimodal:
    - text-LLM ↔ visual/animation bridging

  ai_ml_frameworks:
    - LangChain
    - LlamaIndex
    - custom async frameworks
    - Hugging Face ecosystem

  data_parsing_libs:
    - sqlglot
    - Tesseract
    - BeautifulSoup
    - Scrapy

  frontend_creative:
    - Three.js
    - WebGL

  devops:
    - Git
    - GitHub
    - Docker
    - CI/CD pipelines
```

---

## 4. Company watchlist (seeded)

**File:** `data/company_watchlist.yaml`

Total: ~80 companies, refreshed weekly.

### 4.1 Nigeria (25 companies — primary, no visa issue)

**Big Tech offices:**
- Google Lagos
- Microsoft Lagos
- AWS Lagos (Cape Town hub also relevant)

**Fintech (highest paying in Africa):**
- Flutterwave — Series D, $120-180k ML/Eng
- Paystack — Stripe-owned, $100-150k
- Kuda — fast-growing neobank, $80-130k
- Moniepoint — just raised Series C, $90-140k
- LemFi — cross-border payments, $100-150k
- Chipper Cash — US-funded, $130-180k
- TymeBank (SA) — $80-120k
- Stitch (Cape Town) — $100-150k

**Banks with AI teams:**
- GTBank
- Access Bank
- Zenith Bank
- First Bank
- UBA

**Telco AI:**
- MTN Nigeria AI Labs
- Airtel Nigeria
- 9mobile

**E-commerce AI:**
- Jumia
- Konga

**Healthtech AI:**
- 54gene
- Helium Health
- mPharma

**AI labs:**
- Data Science Nigeria
- InstaDeep (Lagos / Tunis / London) — acquired by BioNTech, $120-180k

**Consulting AI:**
- McKinsey Lagos (QuantumBlack)
- BCG Lagos (GAMMA)

### 4.2 Africa remote-only (15 companies)

**South Africa:**
- Takealot
- Naspers
- Prosus
- MultiChoice
- Standard Bank
- Capitec

**Kenya:**
- Safaricom
- Equity Bank
- KCB
- Branch
- Tala

**Egypt:**
- Fawry
- Vodafone Egypt

**Morocco:**
- HPS (Hightech Payment Systems)

### 4.3 EU (20 companies — for master's + co-op future)

**AI-native labs:**
- Mistral (Paris)
- Aleph Alpha (Heidelberg)
- Stability AI (London)
- LightOn (Paris)
- Silo AI (Helsinki)
- Helsing (Munich)
- DeepL (Cologne)
- Synthesia (London)
- Owkin (Paris)

**Fintech:**
- Klarna (Stockholm)
- N26 (Berlin)
- Wise (London)
- Revolut (London)
- Adyen (Amsterdam)

**Automotive AI:**
- Bosch
- Continental
- BMW
- Mercedes-Benz
- Volvo

**Pharma/Health AI:**
- BenevolentAI (London)
- Exscientia (Oxford)

**Consulting AI:**
- McKinsey QuantumBlack
- BCG GAMMA

### 4.4 Canada (20 companies — primary co-op target)

**AI labs (Toronto/Montreal/Edmonton):**
- Vector Institute members
- Mila spinoffs
- Borealis AI (RBC)
- Layer 6 AI (TD)

**Big Tech Canada:**
- Google (Toronto/Montreal/Vancouver)
- Meta (Montreal)
- Microsoft (Vancouver)
- Amazon (Toronto/Vancouver)
- NVIDIA (Toronto)
- Apple (Vancouver)
- Uber (Toronto)
- Lyft (Toronto)
- Shopify (Ottawa)

**Fintech:**
- Wealthsimple
- Koho
- Neo Financial
- Questrade
- Manulife AI

**Health AI:**
- Deep Genomics
- Cyclica
- Phenomic AI
- ProteinQure

**Quantum/AI crossover:**
- Xanadu
- D-Wave

### 4.5 International remote-friendly (US-funded, hires Africa)

These explicitly hire remote Africa and pay USD:
- GitLab — $130-180k
- Automattic (WordPress) — $130-180k
- Cloudflare — $150-200k+
- Stripe — $180-220k+
- Vercel — $150-200k
- Linear — $140-180k
- Mercury (banking) — $140-180k
- GitHub — $130-180k

### 4.6 US excluded

No US-only companies in v0.5. User said EU + Canada for master's; no US focus.

---

## 5. Salary floor logic

User specified **₦150,000-200,000 per month** as the target. The system handles this with a **flag, not a filter** — below-floor postings still appear but are marked with `⚠️ Below your ₦150k floor`.

```python
def annotate_salary(posting: JobPosting, prefs: UserPreferences) -> str:
    """Format salary annotation with region-appropriate currency and period."""
    if posting.salary_max and posting.salary_max < prefs.salary_floor[posting.region]:
        return f"⚠️ {format_money(posting.salary_max, prefs.salary_currency[posting.region])} — below your {format_money(prefs.salary_floor[posting.region], prefs.salary_currency[posting.region])} floor"
    if posting.salary_min and posting.salary_min >= prefs.salary_floor[posting.region]:
        return f"💰 {format_money(posting.salary_min, prefs.salary_currency[posting.region])}+"
    return ""  # salary not listed

def format_money(amount: int, currency: str) -> str:
    if currency == "NGN":
        return f"₦{amount // 1000}k"  # e.g. ₦150k
    if currency == "USD":
        return f"${amount // 1000}k"  # e.g. $100k
    if currency == "EUR":
        return f"€{amount // 1000}k"  # e.g. €100k
    if currency == "CAD":
        return f"CAD {amount // 1000}k"
    return f"{amount} {currency}"
```

The floor is region-specific:
- **Nigeria:** ₦150,000 / month (primary target)
- **Africa (remote):** ₦150,000 / month equivalent (or converted)
- **EU:** €100,000 / year (co-op / master's internship)
- **Canada:** CAD 100,000 / year (co-op)

The system notes that **Nigerian and African startup salary info is rarely listed publicly** — most postings just say "competitive" or omit salary entirely. When not listed, the system shows the company with a note "salary not listed" and links to:
- Glassdoor (has some Nigeria data, sparse)
- Levels.fyi (US-focused, useful for Big Tech Lagos offices)
- MySalaryScale / Salaryexplorer (has some Nigeria data)
- LinkedIn Salary Insights (when available)
- The company itself if a known high-payer

For Big Tech offices in Nigeria (Google, Microsoft, AWS), the system uses known ranges: Google Lagos ML engineer = $80-150k USD/year (₦120M-225M/year), Microsoft similar, AWS similar. These are well-documented even when not listed on the posting.

---

## 6. Visa filtering

User is a Nigerian citizen. Needs visa sponsorship everywhere except Nigeria. The system reads each posting's text for sponsorship signals:

| Signal | Interpreted as |
|--------|----------------|
| "must be authorized to work in [country]" | No sponsorship (skip if needed) |
| "sponsorship available" / "visa support" | Sponsorship (match) |
| "Global Talent Stream" / "LMIA" | Canada sponsorship (match) |
| "EU Blue Card" | EU sponsorship (match) |
| "citizenship required" | No sponsorship (skip) |
| No mention | Flag as "unknown" — show but mark uncertain |

This is fragile (LLMs miss things, postings lie) but better than a flat "all postings need sponsorship" filter.

---

## 7. Digest cadence

**Every 3 days, 8:00 AM WAT.** Configurable via `digest_frequency_days: 3` in the user profile.

First digest: 3 days from now.
Subsequent digests: every 3 days at 08:00.

The cron schedule in Azure Functions:
```yaml
schedule: "0 8 */3 * *"
# Equivalent: every 3 days at 08:00 UTC
```

User can override at runtime with `/digest-cadence 1` (daily) or `/digest-cadence 7` (weekly).

---

## 8. Cross-agent prof watch (the killer feature)

The system reads from the Paper Tracker watchlist to find:

1. **Prof's posted openings** — when a prof on the watchlist posts a new PhD / research-intern position, the system alerts Aaliyah
2. **Prof's recent papers with co-authors from target schools** — surfaces "you should look at Prof. X at McGill, they co-authored with de Rijke recently"
3. **Prof's group alumni** — surfaces "students from this group went to this school, you might too" for master's apps
4. **Lab page "open positions" section** — scraped weekly, any new posting goes into the digest

**Implementation:** new tool `profs.check_openings(prof_id) -> list[OpeningAlert]` that Job Hunter calls. The Paper Tracker watchlist becomes a primary input to Job Hunter's discovery.

```
📢 Cross-agent alert — 2026-07-30

🎓 From your watchlist:
  Maarten de Rijke's group just posted: "PhD Position in Conversational IR"
    U. Amsterdam · Deadline 2026-12-15 · Match 92%
    [Open] [Draft opener] [Save for later]
  
  Yiming Cui's group just posted: "Visiting Researcher — Multilingual LLM Eval"
    Fudan University · Open application · Match 87%
    [Open] [Draft opener] [Save for later]
```

---

## 9. The full Job Hunter architecture

```
                    ┌─────────────────────┐
                    │  Trigger            │
                    └──────────┬──────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
   ┌─────────────────┐ ┌──────────────┐ ┌─────────────────┐
   │  Job Discovery  │ │  Pre-research│ │  Cross-agent    │
   │  (every 3 days) │ │  flow        │ │  prof watch     │
   │                 │ │              │ │                 │
   │ → ~80 companies │ │ → brief      │ │ → prof's posted │
   │   in 4 regions  │ │   for a      │ │   openings      │
   │ → match score   │ │   company    │ │ → alumni paths  │
   │ → salary flag   │ │              │ │                 │
   └────────┬────────┘ └──────┬───────┘ └────────┬────────┘
            │                 │                  │
            └─────────────────┼──────────────────┘
                              ▼
                    ┌─────────────────────┐
                    │  Cover letter flow  │
                    │  (research block    │
                    │   → edit plan →     │
                    │   approval gate)    │
                    └─────────────────────┘
```

---

## 10. Commands

| Command | What it does |
|---------|--------------|
| `/companies` | List all companies in the watchlist (filterable by region) |
| `/companies add <name>` | Add a company to watch |
| `/companies remove <name>` | Stop watching |
| `/companies region <region>` | Show companies in a specific region |
| `/jobs` | Run discovery across all configured regions |
| `/jobs nigeria` | Run discovery for Nigeria only |
| `/jobs canada` | Run discovery for Canada only |
| `/jobs eu` | Run discovery for EU only |
| `/jobs africa` | Run discovery for Africa (ex-Nigeria) |
| `/job <URL or text>` | Draft a cover letter for a specific posting |
| `/pre-research <company or role>` | Pre-research flow only, no email |
| `/prefs` | Show current preferences |
| `/set-prefs` | Update preferences |
| `/digest-cadence <days>` | Change digest frequency (1=daily, 3=default, 7=weekly) |
| `/help-jh` | Show Job Hunter help |

---

## 11. The digest output (every 3 days)

```
📋 Job Hunter digest — 2026-07-28
    Aaliyah · Nigeria + Africa (remote) + EU + Canada
    Internships + Co-op + New-grad

📍 Nigeria (no visa needed) — 4 new
1. ML Engineer Intern — Flutterwave (Lagos, hybrid)
   Skills match: 78% · 💰 ₦150-200k/mo · Posted 2d ago
   [Open] [Save] [Draft letter] [Skip]

2. AI Research Intern — Data Science Nigeria (Lagos, remote)
   Skills match: 85% · ⚠️ salary not listed · Posted 1d ago
   [Open] [Save] [Draft letter] [Skip]

3. Junior ML Engineer — Carbon (Lagos, hybrid)
   Skills match: 72% · 💰 ₦100-150k/mo · Posted 3d ago
   [Open] [Save] [Draft letter] [Skip]

4. Backend Engineer (Python) — Kuda (Lagos, hybrid)
   Skills match: 80% · ⚠️ salary not listed · Posted 4d ago
   [Open] [Save] [Draft letter] [Skip]

🌍 Africa (remote only) — 2 new
5. ML Engineer (Remote, Africa) — Branch (Kenya, remote)
   Skills match: 75% · ⚠️ Below your ₦150k/mo floor · Posted 1d ago
   [Open] [Save] [Draft letter] [Skip]

6. Data Engineer (Remote, Africa) — Tala (Kenya, remote)
   Skills match: 68% · ⚠️ Below your ₦150k/mo floor · Posted 2d ago
   [Open] [Save] [Draft letter] [Skip]

🇪🇺 EU (visa needed) — 1 new
7. Research Intern, LLM Safety — Mistral (Paris, hybrid)
   Skills match: 88% · 💰 €35-45k (6mo internship) · Posted 1d ago
   Visa: sponsorship available
   [Open] [Save] [Draft letter] [Skip]

🇨🇦 Canada (visa needed) — 3 new
8. ML Research Co-op (Winter 2027) — Borealis AI (Toronto)
   Skills match: 82% · 💰 CAD 60-80k (pro-rated) · Posted 2d ago
   Co-op intake: Winter 2027 · Apply by: 2026-10-15
   Visa: Global Talent Stream
   [Open] [Save] [Draft letter] [Skip]

9. Research Engineer Intern — Layer 6 AI (Toronto)
   Skills match: 79% · ⚠️ salary not listed · Posted 3d ago
   [Open] [Save] [Draft letter] [Skip]

10. New Grad ML Engineer — Shopify (Ottawa, hybrid)
    Skills match: 71% · 💰 CAD 110-140k · Posted 5d ago
    Visa: Global Talent Stream
    [Open] [Save] [Draft letter] [Skip]

🎓 From your watchlist (cross-agent)
11. Maarten de Rijke's group posted: "PhD Position in Conversational IR"
    U. Amsterdam · Deadline 2026-12-15 · Match 92%
    [Open] [Draft opener] [Save for later]

Showing 1-11 of 11. [Filter] [Change prefs] [View all saved]
```

---

## 12. Data model

```sql
-- Discovered openings
CREATE TABLE job_hunter_discovered_openings (
  id              BIGSERIAL PRIMARY KEY,
  source          TEXT NOT NULL,           -- 'careers_page' | 'manual' | 'prof_alert'
  source_url      TEXT NOT NULL,
  title           TEXT NOT NULL,
  organization    TEXT NOT NULL,
  team            TEXT,
  role_type       TEXT NOT NULL,           -- 'internship' | 'co_op' | 'new_grad' | 'research'
  description     TEXT NOT NULL,
  required_skills JSONB NOT NULL,
  nice_to_have    JSONB,
  location        TEXT,
  remote_ok       BOOLEAN,
  deadline        DATE,
  application_url TEXT,
  posted_at       TIMESTAMPTZ,
  discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  match_score     REAL,
  salary_min_usd  INTEGER,
  salary_max_usd  INTEGER,
  salary_currency TEXT,
  visa_sponsorship TEXT,                  -- 'yes' | 'no' | 'unknown'
  region          TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'new',
                  -- new | saved | drafting | drafted | sent | discarded
  embedding       VECTOR(1024),            -- in Qdrant, not pgvector
  raw_html        TEXT                     -- for re-extraction
);

-- Cross-agent alerts from prof watchlist
CREATE TABLE job_hunter_prof_alerts (
  id              BIGSERIAL PRIMARY KEY,
  prof_id         BIGINT NOT NULL,
  alert_type      TEXT NOT NULL,           -- 'new_opening' | 'alumni_path' | 'coauthor_at_target_school'
  payload         JSONB NOT NULL,
  read_at         TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Drafts (in-flight cover letters)
CREATE TABLE job_hunter_drafts (
  id              BIGSERIAL PRIMARY KEY,
  opening_id      BIGINT REFERENCES job_hunter_discovered_openings(id),
  base_letter_id  TEXT,
  edit_plan       JSONB NOT NULL,
  rendered_text   TEXT NOT NULL,
  fact_check      JSONB NOT NULL,
  status          TEXT NOT NULL DEFAULT 'queued',
                  -- queued | sent | discarded
  recipient_email TEXT NOT NULL,
  approval_email_sent_at TIMESTAMPTZ,
  approval_reply_at TIMESTAMPTZ,
  sent_at         TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Approved letters (the corpus)
CREATE TABLE job_hunter_approved (
  id              BIGSERIAL PRIMARY KEY,
  draft_id        BIGINT NOT NULL REFERENCES job_hunter_drafts(id),
  opening_id      BIGINT REFERENCES job_hunter_discovered_openings(id),
  rendered_text   TEXT NOT NULL,
  edit_plan       JSONB NOT NULL,
  approved_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 13. What I still need from Aaliyah to ship v0.1

1. **Realistic GPA confirmation.** Is 3.5/5 the correct figure, or is it higher (3.6, 3.7) or lower (3.3)? School tier calibration depends on this.
2. **Master's field.** "Master's in Computer Science with focus on IR/NLP" or "Master's in Data Science" or "Master's in AI"? Need explicit statement.
3. **Confirm the 80-company list.** Anything to add (local companies I don't know) or remove?
4. **Master's program length.** 1 year, 1.5 years, 2 years? Affects which programs are realistic.
5. **Funding requirement.** Need fully-funded? Most Canadian MSc are funded, most European M2 are not. This filters the target list heavily.
6. **Confirm digest cadence.** Every 3 days at 8am WAT — locked in unless you want to change.

---

## 14. What this delivers for the portfolio

1. **Multi-agent system in action.** Paper Tracker prof watchlist feeds Job Hunter discovery. Cross-agent workflow with explicit handoffs.
2. **Region-aware discovery.** System respects the user's actual constraints (visa, remote preference, salary floor, GPA-appropriate schools).
3. **Flag, not filter, on soft constraints.** Salary below floor = shown with warning. You decide.
4. **Per-task model picks.** Gemini for cheap/high-volume, DeepSeek for synthesis. Locked in `config/models.yaml`.
5. **Strict pydantic schemas for every structured output.** No freeform LLM prose in the data model.
6. **Cover letter flow that doesn't ship without approval.** Email gate is the safety net.
7. **Honest about data supply.** African startup postings are sparse, salary info is sparse, visa info is sparse — the system surfaces this rather than hiding it.
