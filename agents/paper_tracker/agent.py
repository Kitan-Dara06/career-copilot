"""Paper Tracker agent — arXiv digest + professor tracking.

Implements the flow from paper-tracker-design.md §4.4, §4.5.1, §4.5.2.

LLM calls: Gemini (summarize, professor_why, filter), DeepSeek (why_relevant),
Modal/Qwen (professor_brief via Celery).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog

from backbone.model_client import ModelClient
from backbone.prompt_registry.loader import load as load_prompt
from backbone.prompt_registry.loader import render
from backbone.tools.arxiv import (
    FetchAuthorInput,
    FetchAuthorTool,
    FetchRecentInput,
    FetchRecentTool,
)
from backbone.tools.firecrawl import FirecrawlScrapeTool
from backbone.tools.memory import FeedbackInput, FeedbackTool
from backbone.tools.semantic_scholar import AuthorInput, BatchInput
from backbone.tools.semantic_scholar import SearchInput as S2SearchInput
from backbone.tools.structured import GetInput, GetTool, SetTool
from backbone.tools.tavily import SearchInput, TavilySearchTool
from backbone.tools.telegram import DigestItem, SendDigestInput, SendDigestTool
from backbone.tools.vector import (
    EmbedInput,
    EmbedTool,
    SearchTool,
    UpsertInput,
    UpsertTool,
)
from backbone.tools.vector import (
    SearchInput as VecSearchInput,
)

logger = structlog.get_logger("paper_tracker")

DEFAULT_CATEGORIES = ["cs.CL", "cs.IR", "cs.AI"]
EMBED_DIM = 1024
DISCOVER_YEARS = 2
DISCOVER_MAX_FETCH = 100  # arXiv rate limit: keep under 200 to avoid 429
DISCOVER_MIN_PAPERS = 2  # 2 papers in 2-year window — verify LLM is the real gate
# Earlier we tried 3 (per design §4.5.1) but with 5 keywords × 50 S2 papers
# most authors only appear 1-2 times, so 3 collapsed the verify pool to near
# zero. The LLM Position gate is the real noise filter — lowering the paper
# floor just gets more candidates verified.
DISCOVER_TOP_N = 10
DEDUP_JACCARD_THRESHOLD = 0.8  # collapse researchers sharing ≥80% of paper sets
# How many top-scored candidates we run through Firecrawl+LLM verification.
# Larger pool → more chance of filling every region bucket (esp. CA/EU when
# S2 returns many CN/US hits). Each candidate costs 1 Tavily + 1 Firecrawl + 1
# Gemini call. 50 keeps a single /discover under ~10 min serialized; drop to
# 20 if you need it under 5 min and don't mind thinner EU/CA coverage.
DISCOVER_VERIFY_POOL = 50
# How many CSRankings-seeded profs we merge into the verify pool. The verify
# pool itself is capped at DISCOVER_VERIFY_POOL (50) so we don't want seeds to
# dominate the pool — they should complement ranked S2 hits, not replace
# them. 25 seeds + 50 S2 hits → top 50 verifications still include the top
# S2-tier first, but seed profs from under-represented regions (CA/EU) get a
# guaranteed slot per region-balanced selection.
DISCOVER_SEED_POOL = 25
# Max concurrent verify calls (Tavily + Firecrawl + DeepSeek). Caps at 5 so:
#   - Tavily free tier (1000/mo) absorbs 50/cand × 2 calls = 100 req/discover.
#   - Firecrawl free tier (500/mo) absorbs 50/cand × 1 call    = 50 req/discover.
#   - DeepSeek rate limit (60/min) absorbs 5 concurrent × ~2s/call = ~25 req/min.
# Higher values save wall time but risk Firecrawl 408 timeouts on slow pages.
DISCOVER_VERIFY_CONCURRENCY = 5
# Minimum total citations across the window. The design doc says 1000 ("real
# profs") but in a 2-year window most genuinely-active profs land 50-700 —
# 1000 wipes the pool. 20 is a noise floor; the LLM Position gate removes
# students/postdocs, which is the real quality signal.
DISCOVER_MIN_CITATIONS = 20
# Max paper abstracts we embed per discover run (cost guardrail; ~elect Voyage).
DISCOVER_EMBED_CAP = 150
# Max queries per /discover — keep under 15 to stay under S2's 1 req/s soft cap.
# ``user-keywords + CANONICAL_QUERY_POOL`` is capped to this many queries.
DISCOVER_MAX_QUERIES = 14
# Canonical western-NLP subfield query terms, used as union with the
# user-configured keywords. The point: querying only for "NLP" / "IR" / "RAG"
# biases S2's search relevance toward the largest-volume community publishing
# on those literals, which is currently the Chinese NLP community. Adding
# canonical terms like "dense retrieval", "ColBERT", "RLHF" tilts the search
# toward the publication vocabulary of western IR/evaluation/alignment labs,
# so a much broader pool of authors per-country is surfaced for verification.
# Each term is queried independently and merged (union, not disjunction).
CANONICAL_QUERY_POOL = [
    "dense retrieval",
    "neural information retrieval",
    "learned sparse retrieval",
    "ColBERT",
    "SPLADE",
    "LLM evaluation",
    "instruction tuning",
    "RLHF",
    "DPO",
    "question answering",
    "open-domain QA",
    "multi-hop reasoning",
    "semantic search",
    "embedding models",
]
# OpenReview profile path — preferred canonical source for researchers who have
# submitted to NeurIPS / ICML / ICLR. Authors self-verify their affiliation and
# submission record there, which is much cleaner than scraping lab pages.
OPENREVIEW_PROFILE_PREFIX = "https://openreview.net/profile"
# Author-in-paper verification: for each verified candidate, look at the top
# cited papers in their cluster and confirm the candidate's name appears in the
# paper's author list. S2 sometimes returns an author whose cluster was filled
# via search relevance but whose actual top paper (highest citation_count) does
# NOT include them — a homonym/disambiguation artefact. Demote their score.
AUTHOR_IN_PAPER_TOP_K = 3
AUTHOR_IN_PAPER_DEMOTE_FACTOR = 0.5


class DigestResult:
    def __init__(
        self,
        mode: Literal["daily", "weekly"],
        interest_items: list[dict[str, Any]],
        professor_items: list[dict[str, Any]],
    ) -> None:
        self.mode = mode
        self.interest_items = interest_items
        self.professor_items = professor_items


class PaperTrackerAgent:
    def __init__(self, task_ctx: Any = None) -> None:
        self.ctx = task_ctx
        self._fetch = FetchRecentTool()
        self._author = FetchAuthorTool()
        self._embed = EmbedTool()
        self._search = SearchTool()
        self._upsert = UpsertTool()
        self._struct_get = GetTool()
        self._struct_set = SetTool()
        self._digest = SendDigestTool()
        self._feedback = FeedbackTool()
        self._tavily = TavilySearchTool()
        self._firecrawl = FirecrawlScrapeTool()
        self._llm = ModelClient()

    # ── Public API ────────────────────────────────────────────

    async def run_digest(self, mode: Literal["daily", "weekly"] = "daily") -> DigestResult:
        logger.info("digest_start", mode=mode)
        since = datetime.now(UTC) - timedelta(days=3)
        interest = await self._stream_a_interest(since)
        professors = await self._stream_b_professor(since)

        # Mark papers as shown so they don't repeat
        seen_ids = [p["arxiv_id"] for p in interest + professors if "arxiv_id" in p]
        if seen_ids:
            await self._mark_seen(seen_ids)

        logger.info("digest_complete", interest=len(interest), professor=len(professors))
        return DigestResult(mode=mode, interest_items=interest, professor_items=professors)

    async def send_to_telegram(self, result: DigestResult, chat_id: str) -> str:
        n = len(result.interest_items) + len(result.professor_items)
        print(f"[digest] Step 6: Sending {n} papers to Telegram")
        items = _format_digest_items(result)
        out = await self._digest(self.ctx, SendDigestInput(chat_id=chat_id, items=items))
        print(f"[digest] Step 6 done: message_id={out.message_id}")
        return out.message_id

    async def handle_feedback(self, item_id: str, signal: str) -> None:
        await self._feedback(self.ctx, FeedbackInput(item_id=item_id, signal=signal))
        logger.info("feedback_recorded", item_id=item_id, signal=signal)

    # ── Professor Discovery (§4.5.1) ─────────────────────────

    async def run_discover(self) -> list[dict[str, Any]]:
        """Discover professors via S2 + Voyage similarity.

        Searches each keyword independently, merges results, ranks by
        combined citation + similarity score.
        """
        logger.info("discover_start")
        keywords = await self._get_user_keywords()
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()][:5]
        # Build the *search* query pool: user-configured interests + canonical
        # western-NLP subfield terms (see CANONICAL_QUERY_POOL docstring).
        # This dilutes the "NLP OR IR OR RAG" bias that surfaces only the
        # high-volume Chinese community, by adding Western publication-world
        # terms ("dense retrieval", "ColBERT", "RLHF", etc.) that bring in
        # the IR/eval/alignment labs of US/CA/EU universities.
        seen_terms: set[str] = set()
        search_pool: list[str] = []
        for kw in kw_list + list(CANONICAL_QUERY_POOL):
            norm = kw.strip().lower()
            if not norm or norm in seen_terms:
                continue
            seen_terms.add(norm)
            search_pool.append(kw[:100])
        search_pool = search_pool[:DISCOVER_MAX_QUERIES]
        logger.info(
            "discover_keywords",
            user_keywords=kw_list,
            canonical_pool_size=len(CANONICAL_QUERY_POOL),
            search_pool=search_pool,
        )
        print(f"[discover] user keywords: {kw_list}")
        print(f"[discover] search pool ({len(search_pool)} queries): {search_pool}")

        from backbone.tools.semantic_scholar import batch_lookup, lookup_author, search_papers

        # Step 1-pre — CSRankings seed (parallel with S2). The paper-based S2
        # path below surfaces prolific publishers (high-volume Chinese NLP
        # community dominates the keyword window). CSRankings tracks faculty by
        # staff listing, so assistant profs at UofT / Mila / Amii / Vector bring
        # themselves into the verify pool even when they publish <5 papers/year.
        # The fetch is one-time-cached in the tool module; concurrent calls in
        # the same process reuse the cache. See backbone/tools/csrankings.py.
        discover_regions = self._discover_regions()
        csrankings_seed: list[dict[str, Any]] = []  # filled in below, before merging with `scored`.

        async def _fetch_csrankings_seed() -> None:
            try:
                from backbone.tools.csrankings import CSRankingsInput, CSRankingsTool

                tool = CSRankingsTool()
                seed_out = await tool(
                    self.ctx,
                    CSRankingsInput(
                        areas=["nlp", "inforet", "mlmining", "ai"],
                        regions=discover_regions,
                        min_adjusted_count=1.0,
                    ),
                )
                csrankings_seed.extend(
                    {
                        "name": p.name,
                        "affiliation": p.affiliation,
                        "homepage": p.homepage,
                        "country_code": p.country_code,
                        "region": p.region,
                    }
                    for p in seed_out.profs
                )
                logger.info(
                    "discover_csrankings_seed", count=len(csrankings_seed), regions=discover_regions
                )
                print(f"[discover] CSRankings seed: {len(csrankings_seed)} profs")
            except Exception as exc:
                logger.warning("discover_csrankings_failed", error=str(exc))
                print(f"[discover] CSRankings seed failed: {exc}")

        # Step 1: Search S2 for each query (user + canonical), merge results.
        # Queries run as a UNION (each paper_id is its own result, deduplicated by
        # paper_id), so a paper that surfaces for "dense retrieval" AND
        # "ColBERT" counts once but ensures the author pool spans the union of
        # all query result sets.
        # We run the CSRankings fetch concurrently with the S2 search loop
        # (they have no data dependency), then merge seed profs into the verify
        # pool below.
        all_papers: dict[str, Any] = {}  # paper_id → paper, deduplicated

        async def _search_s2() -> None:
            for kw in search_pool:
                query = kw[:100]
                try:
                    s2_out = await search_papers(
                        self.ctx,
                        S2SearchInput(query=query, year_start=datetime.now(UTC).year - 2, limit=50),
                    )
                    for p in s2_out.papers:
                        if p.paper_id not in all_papers:
                            all_papers[p.paper_id] = p
                    logger.info("discover_s2_search", keyword=kw[:30], found=len(s2_out.papers))
                except Exception as exc:
                    logger.warning("discover_s2_search_failed", keyword=kw[:30], error=str(exc))
                    print(f"[discover] S2 search failed for '{kw[:30]}': {exc}")
                    continue
                await asyncio.sleep(1.5)  # Respect S2 rate limits (~1 req/sec safe margin)

        await asyncio.gather(_search_s2(), _fetch_csrankings_seed())

        papers = list(all_papers.values())
        print(
            f"[discover] S2: {len(papers)} unique papers from {len(search_pool)} queries "
            f"+ {len(csrankings_seed)} CSRankings seed profs"
        )
        if not papers and not csrankings_seed:
            return []

        # Step 2: Batch citation data (also fetches abstracts)
        all_ids = [p.paper_id for p in papers]
        batch_out = await batch_lookup(self.ctx, BatchInput(paper_ids=all_ids))
        batch_papers = batch_out.papers if batch_out.papers else papers

        # Post-filter: keep only papers relevant to NLP/IR/AI
        relevant_words = {
            "language",
            "retrieval",
            "rag",
            "agent",
            "neural",
            "nlp",
            "transformer",
            "llm",
            "embedding",
            "search",
            "reasoning",
            "generation",
            "evaluation",
            "knowledge",
            "graph",
            "model",
            "training",
            "inference",
            "prompt",
            "alignment",
            "benchmark",
        }
        batch_papers = [
            p
            for p in batch_papers
            if any(w in (p.title + (p.abstract or "")).lower() for w in relevant_words)
        ]
        print(f"[discover] {len(batch_papers)} papers after field filter")

        # Step 3: Embed ABSTRACTS for meaningful similarity scoring.
        # Embed EVERY surviving paper (capped at DISCOVER_EMBED_CAP) — not just
        # the top 50. The earlier top-50 slice silently assigned sim=0.0 to
        # every author whose papers fell outside that window, which let
        # low-relevance authors (e.g. Shuicheng Yan in an earlier run) rank on
        # citations alone.
        embed_papers = batch_papers[:DISCOVER_EMBED_CAP]
        texts = [(p.abstract or p.title)[:1000] for p in embed_papers]
        print(f"[discover] Embedding {len(texts)} paper abstracts...")
        embed_out = await self._embed(self.ctx, EmbedInput(texts=texts))

        essay = await self._get_user_interests()
        interest_out = await self._embed(self.ctx, EmbedInput(texts=[essay[:8000]]))
        interest_vec = interest_out.embeddings[0]

        paper_scores: dict[str, float] = {}
        for i, paper in enumerate(embed_papers):
            if i < len(embed_out.embeddings):
                paper_scores[paper.paper_id] = _cosine(interest_vec, embed_out.embeddings[i])

        # Step 4: Cluster by authorId
        author_clusters: dict[str, dict[str, Any]] = {}
        for paper in batch_papers:
            for author in paper.authors:
                if author.author_id is None:
                    continue
                aid = author.author_id
                if aid not in author_clusters:
                    author_clusters[aid] = {
                        "name": author.name,
                        "papers": [],
                        "citations": 0,
                        "similarities": [],
                    }
                author_clusters[aid]["papers"].append(paper)
                author_clusters[aid]["citations"] += paper.citation_count
                sim = paper_scores.get(paper.paper_id, 0.0)
                if sim > 0:
                    author_clusters[aid]["similarities"].append(sim)

        # Step 5: Score + filter (≥3 papers, ≥DISCOVER_MIN_CITATIONS total)
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for aid, data in author_clusters.items():
            n = len(data["papers"])
            if n < DISCOVER_MIN_PAPERS:
                continue
            cit = data["citations"]
            if cit < DISCOVER_MIN_CITATIONS:
                continue
            sims = data["similarities"]
            avg_sim = sum(sims) / len(sims) if sims else 0.0
            cit_score = min(1.0, cit / 5000.0)
            combined = 0.5 * cit_score + 0.5 * avg_sim
            scored.append((combined, aid, data))

        scored.sort(key=lambda x: x[0], reverse=True)
        logger.info("discover_scored", total_scored=len(scored))
        print(f"[discover] {len(scored)} researchers after scoring")

        # Step 5.5 — Collapse co-workers sharing most of their papers.
        # Four authors of the same paper (e.g. PoisonedRAG) would otherwise each
        # surface as a separate candidate. Two researchers whose paper sets have
        # Jaccard similarity ≥ DEDUP_JACCARD_THRESHOLD are merged into the
        # higher-ranked one, surfaced as a single entry with co_researchers.
        deduped = _collapse_co_workers(scored, threshold=DEDUP_JACCARD_THRESHOLD)
        logger.info("discover_deduped", kept=len(deduped), removed=len(scored) - len(deduped))
        print(f"[discover] {len(deduped)} after dedup")

        # Step 6: Lookup S2 author profile (affiliation + h-index), then run the
        # Tavily→Firecrawl→LLM verification gate on the top DISCOVER_VERIFY_POOL
        # candidates. Only confirmed professors with a homepage are kept; this is
        # the gate that removes PhD students, postdocs, industry researchers and
        # cross-field coincidences (e.g. an economics J. Ong matching "generation").
        from backbone.tools.firecrawl import ScrapeInput

        # 6-pre-b — Merge CSRankings seed profs into the candidate list.
        # Each seed prof has no paper cluster (we don't have S2 papers for them),
        # so we synthesize a (combined_score, aid, data) tuple matching the
        # verify pipeline's input shape. Empirical CSRankings adjustedcount
        # (~count of recent papers shared) is used as the cit_score component
        # so seed profs don't drown out genuinely prolific S2 hits in ranking.
        # We cap seed merges at DISCOVER_SEED_POOL so the verify pool still has
        # room for genuinely paper-ranked S2 candidates.
        merged_pool: list[tuple[float, str, dict[str, Any]]] = list(deduped)
        seen_seed_names = {d["name"].lower() for _, _, d in merged_pool if d.get("name")}
        seed_added = 0
        for sp in csrankings_seed:
            if seed_added >= DISCOVER_SEED_POOL:
                break
            name = sp.get("name", "")
            if not name or name.lower() in seen_seed_names:
                continue
            seen_seed_names.add(name.lower())
            # Synthetic author_id so lookup_author won't match a real S2 ID; the
            # verify path will look up S2 metadata for this "csr-<sha>" string
            # and just get None back (handled by _verify_one_candidate).
            synthetic_aid = f"csr-{abs(hash(name)) % 10**10}"
            # Use a neutral combined_score of 0.4 so seed profs land BELOW the
            # top-scored S2 candidates (which cluster 0.20-0.30) but above the
            # noise floor — region balancing still lets a top CA seed prof
            # surface ahead of a CN S2 hit, which is the desired effect.
            seed_data: dict[str, Any] = {
                "name": name,
                "papers": [],  # no S2 cluster — _author_in_top_paper returns True
                "citations": 0,
                "similarities": [],
                "co_workers": [],
                "csrankings_affiliation": sp.get("affiliation", ""),
                "csrankings_homepage": sp.get("homepage", ""),
                "csrankings_region": sp.get("region", ""),
                "csrankings_country_code": sp.get("country_code", ""),
                "seed_source": "csrankings",
            }
            merged_pool.append((0.4, synthetic_aid, seed_data))
            seed_added += 1
        logger.info(
            "discover_seed_merged",
            seed_added=seed_added,
            pool_size_before=len(deduped),
            pool_size_after=len(merged_pool),
        )
        print(
            f"[discover] merged {seed_added} CSRankings seed profs "
            f"(pool {len(deduped)} → {len(merged_pool)})"
        )

        verify_pool = merged_pool[:DISCOVER_VERIFY_POOL]
        print(f"[discover] Verifying top {len(verify_pool)} candidates (Firecrawl+LLM)...")

        # 6-pre — Fetch S2 author metadata (affiliation, h-index) for every
        # candidate up front, sequentially with a tiny delay. S2 rate-limits
        # aggressively even with the API key; gathering 50 concurrent
        # lookup_author calls would trigger an immediate 429 storm. The Tavily
        # → Firecrawl → DeepSeek verify work that follows is the heavy Half;
        # this pre-step is ~1 req/s and small.
        author_meta: list[Any] = []
        for combined_score, aid, data in verify_pool:
            try:
                author_meta.append(await lookup_author(self.ctx, AuthorInput(author_id=aid)))
            except Exception as exc:
                logger.debug("discover_lookup_author_failed", aid=aid, error=str(exc))
                author_meta.append(None)
            await asyncio.sleep(0.4)
        print(f"[discover] S2 author metadata fetched for {len(author_meta)} candidates")

        # 6-main — Run the verify gate across the pool with bounded concurrency.
        # Each verify call hits Tavily (2x), Firecrawl (1x), DeepSeek (1-3x) —
        # ~5-7s wall per candidate serialized. With Semaphore(5) the wall time
        # drops from ~22min for a 50-pool to ~5-6min, while staying under every
        # API partner's rate limit (Tavily 1000/mo free, Firecrawl 500/mo free,
        # DeepSeek 60/min). The semaphore is set in DISCOVER_VERIFY_CONCURRENCY
        # so we can tune it without touching the loop body.
        user_domain_str = ", ".join(kw_list)
        sem = asyncio.Semaphore(DISCOVER_VERIFY_CONCURRENCY)

        async def _verify_one(idx: int) -> dict[str, Any] | None:
            combined_score, aid, data = verify_pool[idx]
            async with sem:
                return await self._verify_one_candidate(
                    combined_score=combined_score,
                    aid=aid,
                    data=data,
                    author_info=author_meta[idx],
                    user_domain_str=user_domain_str,
                )

        # gather preserves submission order so we keep deterministic logging.
        results_opt = await asyncio.gather(*[_verify_one(i) for i in range(len(verify_pool))])
        verified_results = [r for r in results_opt if r is not None]

        # Step 7 — Region-balanced selection: keep US/CA/EU/CN/HK (drop UK by
        # default), then round-robin across the configured region buckets so a
        # single region cannot crowd out the others.
        results = _select_by_region(verified_results, self._discover_regions())

        logger.info("discover_complete", candidates=len(results))
        return results

    async def _verify_one_candidate(
        self,
        *,
        combined_score: float,
        aid: str,
        data: dict[str, Any],
        author_info: Any,
        user_domain_str: str,
    ) -> dict[str, Any] | None:
        """Run the full verify gate on a single candidate. Concurrent-safe.

        Returns the verified-result dict (per the schema appended by the loop
        below), or None when the candidate should be dropped. Method is
        "continue-free" because each ``continue`` in the original sequential
        loop maps to ``return None`` here.
        """
        from backbone.tools.firecrawl import ScrapeInput

        name = data["name"]
        citations = data["citations"]
        papers_count = len(data["papers"])
        sim_vals = data["similarities"]
        avg_sim = sum(sim_vals) / len(sim_vals) if sim_vals else 0.0
        co_workers = data.get("co_workers", [])
        seed_source = data.get("seed_source")  # "csrankings" for CSRankings-seed candidates

        affiliation = (
            author_info.affiliations[0] if author_info and author_info.affiliations else ""
        )
        h_index = author_info.h_index if author_info else 0
        # When this candidate is a CSRankings seed, prefer the canonical affiliation
        # and homepage that CSRankings gave us (canonical, self-verified by the prof).
        if seed_source == "csrankings":
            seed_affiliation = data.get("csrankings_affiliation", "") or ""
            if seed_affiliation:
                affiliation = seed_affiliation
            seed_conf_region = data.get("csrankings_region", "")
        else:
            seed_conf_region = ""
        if affiliation:
            affiliation = _trim_affiliation(affiliation)

        # 6a — Tavily to find a candidate homepage URL AND collect content
        # snippets. S2 affiliations are often stale; we use the matching
        # Tavily snippet as the FALLBACK verify signal when Firecrawl fails
        # (403/timeout/etc) so a candidate isn't dropped just because the
        # scraper is blocked.
        homepage = data.get("csrankings_homepage", "") if seed_source == "csrankings" else ""
        tavily_snippets: list[str] = []
        try:
            t_out = await self._tavily(
                self.ctx,
                SearchInput(
                    query=f'"{name}" professor university homepage',
                    max_results=5,
                ),
            )
            name_parts = name.lower().split()
            for r in t_out.results:
                content_lower = r.content.lower() if r.content else ""
                name_match = (
                    len(name_parts) < 2
                    or name_parts[0] in content_lower
                    or name_parts[-1] in content_lower
                )
                if not name_match:
                    continue
                if r.content:
                    tavily_snippets.append(r.content)
                if not homepage:
                    homepage = r.url
            logger.debug(
                "discover_tavily_done",
                name=name,
                homepage=homepage,
                snippets=len(tavily_snippets),
            )
        except Exception as exc:
            logger.debug("discover_tavily_failed", name=name, error=str(exc))

        # 6a' — OpenReview canonical lookup. The Tavily search-result snippet
        # from OpenReview profile URLs is STRUCTURED & self-verified by the
        # researcher ("Associate Professor, Computer Science, Ohio State
        # University, Joined October 2018"). It is more reliable than what we
        # get from S2 affiliations (often stale) or from scraping a lab page
        # where the same-name collision lives. The OpenReview URL is BEHIND a
        # Cloudflare-style captcha wall, so we can't firecrawl the profile —
        # but the Tavily search snippet is exactly the canonical info we want.
        # We collect it as a separate ``openreview_snippet`` and PREPEND it to
        # the merged verify markdown (see 6c') so the verify LLM reads it
        # first and most authoritatively. We do NOT alter the homepage slot.
        openreview_snippet = ""
        try:
            or_out = await self._tavily(
                self.ctx,
                SearchInput(
                    query=f'"{name}" site:openreview.net profile',
                    max_results=3,
                ),
            )
            name_parts = name.lower().split()
            for r in or_out.results:
                if not r.url.startswith(OPENREVIEW_PROFILE_PREFIX):
                    continue
                content_lower = (r.content or "").lower()
                if (
                    len(name_parts) >= 2
                    and (
                        name_parts[0] not in content_lower
                        and name_parts[-1] not in content_lower
                    )
                ):
                    continue
                openreview_snippet = (r.content or "")[:1500]
                break
            logger.debug(
                "discover_openreview_snippet",
                name=name,
                len=len(openreview_snippet),
            )
        except Exception as exc:
            logger.debug("discover_openreview_search_failed", name=name, error=str(exc))

        # 6b — No homepage AND no snippet → no signal at all. Skip.
        if not homepage and not tavily_snippets:
            logger.info("discover_skip_no_signal", name=name)
            print(f"[discover] skip {name}: no homepage/snippet")
            return None

        # 6c — Try Firecrawl on the homepage; ALWAYS also keep the Tavily
        # snippets. We concatenate both into the LLM verify payload (with a
        # clear separator so the LLM treats them as separate evidence) so a
        # same-name collision (e.g. a control-theory professor named "Ding
        # Chen") gets visible counter-evidence instead of an empty page.
        tavily_markdown = (
            "\n\n---\n\n".join(tavily_snippets)[:5000] if tavily_snippets else ""
        )
        firecrawl_markdown = ""
        verify_source = ""
        if homepage:
            try:
                scrape_out = await self._firecrawl(
                    self.ctx, ScrapeInput(url=homepage, formats=["markdown"])
                )
                firecrawl_markdown = (scrape_out.content.markdown or "")[:6000]
                verify_source = "firecrawl"
            except Exception as exc:
                logger.warning(
                    "discover_firecrawl_failed",
                    name=name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                firecrawl_markdown = ""  # fall through to Tavily-only

        # Merge: OpenReview snippet FIRST (most authoritative — self-verified
        # profile), then Firecrawl content (author's actual lab page), then
        # other Tavily snippets appended under a labelled separator. All parts
        # are kept under the 8000-char ceiling we pass to the LLM.
        merged_parts: list[str] = []
        has_openreview = bool(openreview_snippet)
        if openreview_snippet:
            merged_parts.append(
                f"=== OPENREVIEW PROFILE (canonical, self-verified) ===\n{openreview_snippet}"
            )
        if firecrawl_markdown and len(firecrawl_markdown) > 200:
            merged_parts.append(f"=== HOMEPAGE SCRAPE ({homepage}) ===\n{firecrawl_markdown}")
        if tavily_markdown:
            merged_parts.append(f"=== SEARCH SNIPPETS ===\n{tavily_markdown}")
        if firecrawl_markdown and len(firecrawl_markdown) <= 200 and (
            tavily_markdown or openreview_snippet
        ):
            if has_openreview:
                verify_source = "openreview+tavily"
            else:
                verify_source = "tavily-only"
        elif not firecrawl_markdown and (tavily_markdown or openreview_snippet):
            verify_source = "openreview" if has_openreview else "tavily-only"
            print(f"[discover] {verify_source}-fallback for {name}")
        elif has_openreview:
            verify_source = "openreview+firecrawl"

        if not merged_parts:
            logger.info("discover_skip_empty_signal", name=name)
            print(f"[discover] skip {name}: empty scrape + no usable snippet")
            return None

        markdown = "\n\n".join(merged_parts)[:8000]
        logger.debug(
            "discover_verify_input",
            name=name,
            openreview_len=len(openreview_snippet),
            firecrawl_len=len(firecrawl_markdown),
            tavily_len=len(tavily_markdown),
            merged_len=len(markdown),
            verify_source=verify_source,
        )
        # 150 chars ≈ one short Tavily snippet, not enough to confirm role.
        if len(markdown) < 150:
            logger.info("discover_skip_thin_signal", name=name, merged_len=len(markdown))
            print(f"[discover] skip {name}: signal too thin ({len(markdown)} chars)")
            return None

        # 6d — Run the LLM verify on the merged payload, passing the user's
        # research keywords so it can reject same-name collisions against
        # research_area (e.g. economics for NLP, control-theory for IR).
        position = ""
        department = ""
        university = ""
        country = ""
        research_area = ""
        is_professor = False
        domain_match = False
        try:
            verify = await self._llm_verify_professor(
                name, markdown, user_domain=user_domain_str
            )
            if verify:
                is_professor = bool(verify.get("is_professor"))
                position = verify.get("position", "") or ""
                department = verify.get("department", "") or ""
                university = verify.get("university", "") or university
                country = verify.get("country", "") or ""
                research_area = verify.get("research_area", "") or ""
                domain_match = bool(verify.get("domain_match"))
            logger.info(
                "discover_verified",
                name=name,
                is_professor=is_professor,
                position=position,
                country=country,
                research_area=research_area,
                domain_match=domain_match,
                verify_source=verify_source,
            )
        except Exception as exc:
            logger.warning("discover_verify_failed", name=name, error=str(exc))
            logger.exception("discover_verify_trace")

        if not is_professor:
            print(f"[discover] skip {name}: not a professor (pos={position!r})")
            return None
        if not domain_match:
            print(
                f"[discover] skip {name}: domain mismatch "
                f"(area={research_area!r})"
            )
            return None

        # 6d' — Author-in-paper check. S2 occasionally returns candidates whose
        # cluster was filled via search relevance + homonym collisions, where
        # the candidate's actual highest-cited paper does NOT list them.
        # Demote (not skip) such candidates so they rank below genuine
        # professors in the region-balanced selection.
        author_in_top_paper = _author_in_top_paper(name, data["papers"])
        if not author_in_top_paper:
            combined_score = combined_score * AUTHOR_IN_PAPER_DEMOTE_FACTOR
            logger.info(
                "discover_demoted_author_in_paper",
                name=name,
                demote_factor=AUTHOR_IN_PAPER_DEMOTE_FACTOR,
            )
            print(
                f"[discover] demote {name}: author name not in top "
                f"{AUTHOR_IN_PAPER_TOP_K} paper authors"
            )

        # Prefer the LLM's university if found; fall back to S2 affiliation.
        display_affiliation = university or affiliation
        focus = _derive_focus(data["papers"])
        # Region: prefer the LLM-detected country (most authoritative) when known;
        # otherwise fall back to the CSRankings seed's canonical country when this
        # candidate came through the seed path. ``seed_conf_region`` is a region
        # CODE (US/CA/EU/CN/HK) for seeds, '' for S2-derived candidates.
        region = _country_to_region(country)
        if region == "OTHER" and seed_source == "csrankings" and seed_conf_region:
            region = seed_conf_region

        return {
            "name": name,
            "position": position,
            "affiliation": display_affiliation,
            "university": university or display_affiliation,
            "department": department,
            "homepage": homepage,
            "country": country,
            "region": region,
            "research_area": research_area,
            "focus": focus,
            "papers_count": papers_count,
            "citations": citations,
            "h_index": h_index,
            "similarity": round(avg_sim, 2),
            "combined_score": round(combined_score, 2),
            "verified": True,
            "verify_source": verify_source,
            "author_in_top_paper": author_in_top_paper,
            "co_workers": co_workers,
            "sample_titles": [p.title[:100] for p in data["papers"][:3]],
        }

    async def _llm_verify_professor(
        self, name: str, homepage_markdown: str, user_domain: str = ""
    ) -> dict[str, Any] | None:
        """Run the professor_verify v2 prompt (DeepSeek JSON) on a scraped homepage.

        ``user_domain`` is a comma-separated keyword string the LLM uses to
        reject same-name collisions (a control-theory professor named "Ding Chen"
        is rejected if the user_domain is NLP/IR).

        Routes to ``deepseek-chat`` via its OpenAI-compatible
        ``response_format=json_object`` mode. Empirically that mode is reliable
        (Gemini 2.5-flash's responseSchema was returning preambles / truncated
        JSON ~50% of the time, which is why we moved this call to DeepSeek).
        ``parse_loose_json`` is still applied as a belt-and-suspenders for any
        residual prose-wrapped JSON.

        Returns the parsed JSON dict, or None on failure (no raise, logs any).
        """
        template = load_prompt("paper_tracker", "professor_verify")
        rendered, _ = render(
            template,
            {
                "prof_name": name,
                "user_domain": user_domain or "NLP, information retrieval, AI",
                "homepage_markdown": homepage_markdown or "(empty page)",
            },
        )
        from backbone.model_client import parse_loose_json

        # DeepSeek's json_object mode is normally deterministic, but we
        # occasionally see an empty response (~5% of calls) and very rarely a
        # prose-wrapped object. 3 attempts recovers both cases at minor cost.
        # (Note: Gemini 2.5-flash's responseSchema had ~50% garbage rate,
        # which is why we moved this call to DeepSeek — 3 attempts is enough.)
        last_raw_len = 0
        for attempt in range(1, 4):  # at most 3 attempts
            try:
               raw = await self._llm.generate(
                    model=template.model.name,
                    prompt=rendered,
                    temperature=template.model.temperature,
                    max_tokens=template.model.max_tokens,
                )
            except Exception as exc:
                logger.warning(
                    "professor_verify_llm_failed",
                    name=name, attempt=attempt, error=str(exc),
                )
                return None
            if raw:
                last_raw_len = len(raw)
                data = parse_loose_json(raw)
                if isinstance(data, dict) and data:
                    return data
                logger.debug(
                    "professor_verify_retry_badjson",
                    name=name, attempt=attempt, raw_len=last_raw_len,
                )
            else:
                logger.debug(
                    "professor_verify_retry_empty",
                    name=name, attempt=attempt,
                )
            await asyncio.sleep(0.4 * attempt)

        logger.warning(
            "professor_verify_json_bad",
            name=name, attempts=3, last_raw_len=last_raw_len,
        )
        return None

    def _discover_regions(self) -> list[str]:
        """Return the configured region buckets for /discover (e.g. US, CA, EU, CN, HK)."""
        raw = (self.ctx.settings.discover_regions if self.ctx and self.ctx.settings else "")
        regions = [r.strip().upper() for r in (raw or "").split(",") if r.strip()]
        return regions or ["US", "CA", "EU", "CN", "HK"]

    # ── Watch List (§4.5.2) ──────────────────────────────────

    async def watch_add(self, name: str) -> dict[str, Any]:
        """Add a professor to the watchlist.

        1. Check if name already exists (skip if duplicate).
        2. Verify the name exists on arXiv.
        3. Search Tavily for homepage + affiliation.
        4. Fetch last 10 papers, embed them, store in Qdrant.
        5. Insert professor row + professor_interest_vectors.
        """
        logger.info("watch_add_start", name=name)

        # 0. Check for existing professor by name (case-insensitive)
        from sqlalchemy import text

        from backbone.db.session import async_session_factory

        factory = async_session_factory()
        async with factory() as session:
            existing = await session.execute(
                text("SELECT id, name, affiliation FROM professors WHERE name ILIKE :name"),
                {"name": name.strip()},
            )
            row = existing.one_or_none()
            if row is not None:
                logger.info("watch_add_duplicate", name=name, existing_id=row.id)
                return {
                    "name": row.name,
                    "affiliation": row.affiliation or "",
                    "homepage": "",
                    "duplicate": True,
                }

        # 1. Verify on arXiv
        author_out = None
        try:
            author_out = await self._author(
                self.ctx,
                FetchAuthorInput(
                    author_name=name, since=datetime.now(UTC) - timedelta(days=30), max_results=5
                ),
            )
            logger.info("watch_add_arxiv_verified", name=name, papers_found=len(author_out.papers))
        except Exception as exc:
            logger.warning("watch_add_arxiv_failed", name=name, error=str(exc))
            raise ValueError(f"Could not verify {name!r} on arXiv") from exc

        # 2. Tavily search
        homepage = ""
        affiliation = ""
        try:
            tavily_out = await self._tavily(
                self.ctx,
                SearchInput(query=f"{name} professor homepage university", max_results=3),
            )
            homepage = tavily_out.results[0].url if tavily_out.results else ""
            affiliation = _extract_affiliation(tavily_out.results)
            logger.info("watch_add_tavily_done", name=name, has_homepage=bool(homepage))
        except Exception as exc:
            logger.warning("watch_add_tavily_failed", name=name, error=str(exc))

        # 3. Embed recent papers and store in Qdrant
        if author_out is not None and author_out.papers:
            texts = [_paper_text(p) for p in author_out.papers[:10]]
            embed_out = await self._embed(self.ctx, EmbedInput(texts=texts))
            prof_vec = _avg_vector(embed_out.embeddings)

            qdrant_id = str(uuid.uuid4())
            await self._upsert(
                self.ctx,
                UpsertInput(
                    namespace="user/professors",
                    point_id=qdrant_id,
                    embedding=prof_vec,
                    payload={"name": name, "source": "watch_add"},
                ),
            )
            logger.info("watch_add_qdrant_stored", name=name, qdrant_id=qdrant_id)

        # 4. Store in professors table
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO professors"
                    " (name, affiliation, homepage_url, arxiv_author)"
                    " VALUES (:name, :aff, :home, :arxiv)"
                    " ON CONFLICT (name) DO NOTHING"
                ),
                {"name": name.strip(), "aff": affiliation, "home": homepage, "arxiv": name},
            )
            await session.commit()

        logger.info("watch_added", name=name, affiliation=affiliation[:80])
        return {"name": name, "affiliation": affiliation, "homepage": homepage}

    async def watch_list(self) -> list[dict[str, Any]]:
        """Return current watchlist from the professors table."""
        from sqlalchemy import text

        from backbone.db.session import async_session_factory

        logger.info("watch_list_start")
        factory = async_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, name, affiliation, homepage_url"
                    " FROM professors ORDER BY added_at DESC"
                )
            )
            rows = [
                {
                    "id": r.id,
                    "name": r.name,
                    "affiliation": r.affiliation or "",
                    "homepage": r.homepage_url or "",
                }
                for r in result.all()
            ]
        logger.info("watch_list_done", count=len(rows))
        return rows

    async def watch_remove(self, name: str) -> bool:
        """Remove a professor from the watchlist by name."""
        from sqlalchemy import text

        from backbone.db.session import async_session_factory

        factory = async_session_factory()
        try:
            async with factory() as session:
                result = await session.execute(
                    text("DELETE FROM professors WHERE name ILIKE :name"),
                    {"name": f"%{name}%"},
                )
                await session.commit()
                deleted = result.rowcount  # type: ignore[attr-defined]
                logger.info("watch_removed", name=name, rows_deleted=deleted)
                return deleted > 0 if deleted is not None else True
        except Exception as exc:
            logger.warning("watch_remove_failed", name=name, error=str(exc))
            return False

    # ── Professor Brief (§4.5.3) ─────────────────────────────

    async def build_prof_brief_data(self, name: str) -> dict[str, Any]:
        """Collect all structured data needed for a professor brief.

        Returns a dict with: prof_name, affiliation, homepage, recent_papers,
        user_interests, overlap_score. Designed to be passed to Celery/Modal.
        """
        from sqlalchemy import text

        from backbone.db.session import async_session_factory

        logger.info("brief_data_start", name=name)

        # 1. Look up professor in DB by name
        factory = async_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT name, affiliation, homepage_url"
                    " FROM professors WHERE name ILIKE :name LIMIT 1"
                ),
                {"name": f"%{name}%"},
            )
            row = result.one_or_none()

        if row is None:
            logger.warning("brief_data_not_found", name=name)
            return {"error": f"Professor {name!r} not in watchlist. Use /watch add first."}

        db_name = row.name
        affiliation = row.affiliation or ""
        homepage = row.homepage_url or ""

        # 2. Fetch recent papers from arXiv
        recent_titles: list[str] = []
        try:
            author_out = await self._author(
                self.ctx,
                FetchAuthorInput(
                    author_name=db_name,
                    since=datetime.now(UTC) - timedelta(days=365),
                    max_results=20,
                ),
            )
            recent_titles = [p.title for p in author_out.papers[:10]]
            logger.info("brief_data_arxiv", name=db_name, papers=len(recent_titles))
        except Exception as exc:
            logger.warning("brief_data_arxiv_failed", name=db_name, error=str(exc))

        # 3. Load user interests
        user_interests = await self._get_user_interests()

        # 4. Compute interest similarity (if we have papers)
        overlap_score = 0.0
        if recent_titles:
            try:
                paper_texts = [f"{db_name}: {t}" for t in recent_titles[:5]]
                paper_embed = await self._embed(self.ctx, EmbedInput(texts=paper_texts))
                interest_embed = await self._embed(
                    self.ctx, EmbedInput(texts=[user_interests[:8000]])
                )
                if paper_embed.embeddings and interest_embed.embeddings:
                    overlap_score = round(
                        _cosine(
                            _avg_vector(paper_embed.embeddings),
                            interest_embed.embeddings[0],
                        ),
                        2,
                    )
            except Exception:
                pass

        logger.info("brief_data_done", name=db_name, papers=len(recent_titles), sim=overlap_score)
        return {
            "prof_name": db_name,
            "affiliation": affiliation,
            "homepage": homepage,
            "recent_papers": "\n".join(f"  • {t}" for t in recent_titles),
            "user_interests": user_interests[:4000],
            "overlap_score": overlap_score,
        }

    async def run_prof_brief(self, prof_id: int) -> dict[str, Any]:
        row = await self._struct_get(self.ctx, GetInput(table="professors", key=str(prof_id)))
        if not row or not row.data:
            return {"error": f"Professor {prof_id} not found"}

        data = row.data
        name = data.get("name", "Unknown")

        # Fetch recent papers from arXiv
        author_out = None
        try:
            author_out = await self._author(
                self.ctx,
                FetchAuthorInput(
                    author_name=name, since=datetime.now(UTC) - timedelta(days=365), max_results=20
                ),
            )
            recent_titles = [p.title for p in author_out.papers[:10]]
        except Exception:
            recent_titles = []

        # Derive focus from titles
        focus = _derive_focus(author_out.papers) if author_out and author_out.papers else "Unknown"

        return {
            "name": name,
            "affiliation": data.get("affiliation", "Unknown"),
            "homepage": data.get("homepage_url", ""),
            "recent_direction": focus,
            "paper_count": len(recent_titles),
            "sample_papers": recent_titles[:5],
        }

    # ── Stream A: by interest ─────────────────────────────────

    async def _stream_a_interest(
        self, since: datetime, max_fetch: int = 50, top_k: int = 10
    ) -> list[dict[str, Any]]:
        print(f"[digest] Step 1: Fetching arXiv papers (max={max_fetch})...")
        fetch_out = await self._fetch(
            self.ctx,
            FetchRecentInput(categories=DEFAULT_CATEGORIES, since=since, max_results=max_fetch),
        )
        print(f"[digest] Step 1 done: {len(fetch_out.papers)} papers fetched")
        if not fetch_out.papers:
            print("[digest] No papers returned")
            return []

        # Load user interests from seeded profile (use keywords, not full essay)
        user_interests = await self._get_user_keywords()

        # Step 2: Filter papers by relevance (Gemini — bounded parallel).
        # We cap concurrent Gemini calls at 5 to respect the free-tier rate
        # limit (~15 req/min); without bounded concurrency, 50 papers ×
        # asyncio.gather would fire 50 simultaneous calls and Gemini
        # return 429 or timeouts on every single one.
        print("[digest] Step 2: Filtering papers by relevance (parallel, sem=5)...")
        filter_tmpl = load_prompt("paper_tracker", "filter_decision")
        filter_sem = asyncio.Semaphore(DISCOVER_VERIFY_CONCURRENCY)

        async def _filter_one(paper: Any) -> tuple[Any, bool]:
            async with filter_sem:
                try:
                    rendered, _ = render(
                        filter_tmpl,
                        {
                            "title": paper.title,
                            "abstract": paper.abstract[:1000],
                            "interests": user_interests,
                        },
                    )
                    decision = await self._llm.generate(
                        model=filter_tmpl.model.name,
                        prompt=rendered,
                        temperature=filter_tmpl.model.temperature,
                        max_tokens=filter_tmpl.model.max_tokens,
                    )
                    if "REFUSED" in decision.upper():
                        print(f"  [filter] REFUSED: {paper.title[:60]}")
                        return paper, False
                    return paper, True
                except Exception:
                    return paper, True  # Include on error

        coros = [_filter_one(p) for p in fetch_out.papers[:max_fetch]]
        filter_results = await asyncio.gather(*coros)
        filtered = [p for p, ok in filter_results if ok]
        print(f"[digest] Step 2 done: {len(filtered)}/{len(fetch_out.papers)} passed filter")

        if not filtered:
            print("[digest] No papers passed the relevance filter")
            return []

        print(f"[digest] Step 3: Embedding {len(filtered)} papers via Voyage 3...")
        texts = [_paper_text(p) for p in filtered]
        embed_out = await self._embed(self.ctx, EmbedInput(texts=texts))
        print(f"[digest] Step 3 done: {len(embed_out.embeddings)} embeddings")

        query_vec = _avg_vector(embed_out.embeddings)

        print(f"[digest] Step 4: Vector search in Qdrant (k={top_k})...")
        search_out = await self._search(
            self.ctx,
            VecSearchInput(
                namespace="paper_tracker/papers_summarized", query_embedding=query_vec, k=top_k
            ),
        )
        print(f"[digest] Step 4 done: {len(search_out.results)} search results")

        scored_lookup: dict[str, float] = {}
        for r in search_out.results:
            aid = (r.payload or {}).get("arxiv_id", r.point_id)
            scored_lookup[aid] = r.score

        if not scored_lookup:
            print("[digest] No Qdrant matches yet — first run, showing filtered papers")

        seen = await self._get_seen_ids()
        result_papers: list[tuple[float, Any]] = []
        for paper in filtered:
            if paper.arxiv_id in seen:
                continue
            score = scored_lookup.get(paper.arxiv_id, 0.0)
            result_papers.append((score, paper))

        result_papers.sort(key=lambda x: x[0], reverse=True)

        # Step 5: Generate why-lines via DeepSeek (parallel)
        print("[digest] Step 5: Generating why-lines (parallel)...")
        why_tmpl = load_prompt("paper_tracker", "why_relevant", version=1)

        async def _why_one(paper: Any) -> dict[str, Any] | None:
            rendered, _ = render(
                why_tmpl,
                {
                    "title": paper.title,
                    "abstract": paper.abstract[:500],
                    "interests": user_interests,
                },
            )
            try:
                why = await self._llm.generate(
                    model=why_tmpl.model.name,
                    prompt=rendered,
                    temperature=why_tmpl.model.temperature,
                    max_tokens=why_tmpl.model.max_tokens,
                )
                if "REFUSED" in why.upper():
                    return None
            except Exception:
                why = f"Relevant to {paper.title.split(':')[0].strip()}"
            return {
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": ", ".join(paper.authors[:3]),
                "abstract": paper.abstract[:500],
                "why": why,
                "stream": "interest",
            }

        why_coros = [_why_one(p) for _, p in result_papers[:5]]
        why_results = await asyncio.gather(*why_coros)
        results = [r for r in why_results if r is not None]

        # Step 6: Batch upsert to Qdrant (one call)
        print("[digest] Step 6: Batch upsert to Qdrant...")
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http.models import Distance, PointStruct, VectorParams

            client = QdrantClient(
                url=self.ctx.settings.qdrant_url,
                api_key=self.ctx.settings.qdrant_api_key,
            )
            collection = "career_copilot_paper_tracker_papers_summarized"
            try:
                client.get_collection(collection)
            except Exception:
                client.create_collection(
                    collection_name=collection,
                    vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
                )

            points = []
            for i, paper in enumerate(filtered[:max_fetch]):
                default = [0.0] * EMBED_DIM
                emb = embed_out.embeddings[i] if i < len(embed_out.embeddings) else default
                points.append(
                    PointStruct(
                        id=str(uuid.uuid4()),
                        vector=emb,
                        payload={
                            "arxiv_id": paper.arxiv_id,
                            "title": paper.title,
                            "abstract": paper.abstract[:500],
                            "authors": ",".join(paper.authors[:3]),
                        },
                    )
                )
            if points:
                client.upsert(collection_name=collection, points=points)
            print(f"[digest] Step 6 done: {len(points)} points upserted")
        except Exception:
            print("[digest] Qdrant upsert failed — continuing")

        return results

    # ── Stream B: by professor ────────────────────────────────

    async def _stream_b_professor(
        self, since: datetime, max_per_prof: int = 10
    ) -> list[dict[str, Any]]:
        prof_ids = await self._get_watchlist()
        all_papers: list[dict[str, Any]] = []
        for pid in prof_ids:
            try:
                prof_row = await self._struct_get(
                    self.ctx, GetInput(table="professors", key=str(pid))
                )
                if not prof_row or not prof_row.data:
                    continue
                name = prof_row.data.get("name", "")
                if not name:
                    continue
                author_out = await self._author(
                    self.ctx,
                    FetchAuthorInput(author_name=name, since=since, max_results=max_per_prof),
                )
                seen = await self._get_seen_ids()
                for paper in author_out.papers:
                    if paper.arxiv_id in seen:
                        continue
                    all_papers.append(
                        {
                            "arxiv_id": paper.arxiv_id,
                            "title": paper.title,
                            "authors": ", ".join(paper.authors[:3]),
                            "professor": name,
                            "why": f"From {name.split()[-1]}'s recent work",
                            "stream": "professor",
                        }
                    )
            except Exception:
                logger.exception("stream_b_fetch_failed", professor_id=pid)
        return all_papers

    # ── Private helpers ───────────────────────────────────────

    async def _get_watchlist(self) -> list[int]:
        """Return list of professor IDs being watched."""
        from sqlalchemy import text

        from backbone.db.session import async_session_factory

        try:
            factory = async_session_factory()
            async with factory() as session:
                result = await session.execute(
                    text("SELECT id FROM professors ORDER BY added_at DESC")
                )
                ids = [r.id for r in result.all()]
            logger.debug("get_watchlist_done", count=len(ids))
            return ids
        except Exception:
            logger.exception("get_watchlist_failed")
            return []

    async def _get_seen_ids(self) -> set[str]:
        try:
            from sqlalchemy import text

            from backbone.db.session import async_session_factory

            factory = async_session_factory()
            async with factory() as session:
                result = await session.execute(
                    text("SELECT value FROM user_facts WHERE key = 'papers_seen'"),
                )
                row = result.one_or_none()
                if row and row.value:
                    return set(row.value.get("ids", []))
        except Exception:
            pass
        return set()

    async def _mark_seen(self, arxiv_ids: list[str]) -> None:
        """Mark papers as seen so they won't appear in future digests."""
        from sqlalchemy import text

        from backbone.db.session import async_session_factory

        seen = await self._get_seen_ids()
        seen.update(arxiv_ids)
        keep = list(seen)[-500:]

        factory = async_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO user_facts (key, value)"
                    " VALUES (:key, CAST(:value AS jsonb))"
                    " ON CONFLICT (key)"
                    " DO UPDATE SET value = CAST(:value AS jsonb)"
                ),
                {"key": "papers_seen", "value": json.dumps({"ids": keep})},
            )
            await session.commit()

    async def _get_user_interests(self) -> str:
        """Load research interests from the seeded profile."""
        try:
            from sqlalchemy import text

            from backbone.db.session import async_session_factory

            factory = async_session_factory()
            async with factory() as session:
                result = await session.execute(
                    text("SELECT value FROM user_facts WHERE key = 'research_interests_essay'"),
                )
                row = result.one_or_none()
                if row and row.value:
                    return str(row.value.get("text", ""))
        except Exception:
            pass
        return "NLP, Information Retrieval, Language Models"

    async def _get_user_keywords(self) -> str:
        """Load research keywords from the seeded profile."""
        try:
            from sqlalchemy import text

            from backbone.db.session import async_session_factory

            factory = async_session_factory()
            async with factory() as session:
                result = await session.execute(
                    text("SELECT value FROM user_facts WHERE key = 'research_keywords'"),
                )
                row = result.one_or_none()
                if row and row.value:
                    kw = row.value.get("keywords", [])
                    return ", ".join(kw)
        except Exception:
            pass
        return "NLP, Information Retrieval, Language Models"

    # ── Engagement report (F-PT.14) ─────────────────────────────

    async def run_engagement_report(self) -> str:
        """Weekly engagement report: digests, papers shown, read/saved/skipped."""
        from sqlalchemy import text
        from backbone.db.session import async_session_factory
        factory = async_session_factory()
        try:
            async with factory() as session:
                result = await session.execute(text(
                    """SELECT signal, count(*) as cnt
                       FROM feedback_log
                       WHERE ts >= now() - interval '7 days'
                       GROUP BY signal"""))
                by_signal = {r.signal: r.cnt for r in result.all()}
                total = sum(by_signal.values())
                shown = by_signal.get('read', 0) + by_signal.get('save', 0) + by_signal.get('skip', 0)
                saved = by_signal.get('save', 0)
                skipped = by_signal.get('skip', 0)
                engagement = round((saved/max(shown, 1))*100, 1)
                return (
                    f"Weekly Engagement Report\n"
                    f"  Papers shown: {shown}\n"
                    f"  Saved: {saved}  |  Skipped: {skipped}\n"
                    f"  Engagement: {engagement}%\n"
                    f"  Total signals: {total}")
        except Exception:
            return "Engagement report unavailable."

    # ── Interest vector retune (F-PT.13) ──────────────────────────

    async def retune_interest_vector(self) -> tuple[bool, str]:
        """Retune the user's interest vector from last 90 days of save signals.

        Fetches all saved papers from feedback_log, extracts their titles
        + abstracts from arXiv, re-embeds them, and updates the active
        interest vector in Qdrant.
        """
        from sqlalchemy import text
        from backbone.db.session import async_session_factory
        from backbone.tools.arxiv import FetchByIdInput, FetchByIdTool
        from backbone.tools.vector import EmbedInput

        factory = async_session_factory()
        try:
            async with factory() as session:
                result = await session.execute(text(
                    """SELECT item_id FROM feedback_log
                       WHERE signal = 'save' AND ts >= now() - interval '90 days'
                       ORDER BY ts DESC LIMIT 50"""))
                saved_ids = [r.item_id for r in result.all()]
        except Exception:
            saved_ids = []
        if not saved_ids:
            return True, "No saved papers to retune from. Keep saving papers to improve recommendations."

        arxiv_tool = FetchByIdTool()
        texts = []
        for aid in saved_ids[:20]:
            try:
                arxiv_out = await arxiv_tool(self.ctx, FetchByIdInput(arxiv_id=aid))
                if arxiv_out.paper:
                    texts.append(f"TITLE: {arxiv_out.paper.title}\nABSTRACT: {arxiv_out.paper.abstract}")
            except Exception:
                continue
        if not texts:
            return False, "Could not fetch any saved papers from arXiv."

        embed_out = await self._embed(self.ctx, EmbedInput(texts=texts))
        if not embed_out.embeddings:
            return False, "Embedding failed."
        vec = _avg_vector(embed_out.embeddings)
        qdrant_id = str(uuid.uuid4())
        await self._upsert(self.ctx,
            UpsertInput(namespace="user/interests", point_id=qdrant_id, embedding=vec,
                payload={"source": "retune", "count": len(texts)}))
        return True, f"Interest vector retuned from {len(texts)} saved papers."


# ── Helpers ──────────────────────────────────────────────────────


def _paper_text(paper: Any) -> str:
    return f"TITLE: {paper.title}\nABSTRACT: {paper.abstract}"


def _avg_vector(embeddings: list[list[float]]) -> list[float]:
    if not embeddings:
        return [0.0] * EMBED_DIM
    dim = len(embeddings[0])
    return [sum(v[i] for v in embeddings) / len(embeddings) for i in range(dim)]


def _format_digest_items(result: DigestResult) -> list[DigestItem]:
    items: list[DigestItem] = []
    for p in result.interest_items:
        items.append(
            DigestItem(
                title=p["title"],
                authors=p["authors"],
                why=p.get("why", ""),
                arxiv_id=p["arxiv_id"],
                stream="interest",
            )
        )
    for p in result.professor_items:
        items.append(
            DigestItem(
                title=p["title"],
                authors=p["authors"],
                why=p.get("why", ""),
                arxiv_id=p["arxiv_id"],
                stream="professor",
                professor=p.get("professor", ""),
            )
        )
    return items


def _derive_focus(papers: list[Any]) -> str:
    """Build a one-line research focus from paper titles (no LLM)."""

    if not papers:
        return "Unknown"
    stop = {
        "with", "from", "this", "that", "for", "and", "the", "using", "based",
        "towards", "their", "have", "were", "been", "into", "over", "than",
        "will", "very", "also", "data", "method", "model", "approach", "system",
        "paper", "learning", "network", "neural", "deep", "multi", "new",
        "large", "language", "models",
    }
    words: Counter[str] = Counter()
    for p in papers[:20]:
        # Tokenise keeping hyphenated compounds (e.g. "retrieval-augmented")
        # together as one term.
        for match in re.finditer(r"[a-z][a-z0-9]+(?:-[a-z0-9]+)*", p.title.lower()):
            w = match.group()
            # Drop bare stopwords regardless of trailing punctuation in raw source.
            if w in stop:
                continue
            # allow short acronyms like "rag"; reject 1-2 char noise.
            if len(w) < 3:
                continue
            words[w] += 1
    top = [w for w, _ in words.most_common(3)]
    if not top:
        return "Machine Learning"
    return ", ".join(_titlecase_focused(w) for w in top)


def _titlecase_focused(word: str) -> str:
    """Light cleanup so focus keywords read like noun phrases, not raw tokens."""
    replacements = {
        "rag": "RAG",
        "llms": "LLMs",
        "llm": "LLM",
        "nlp": "NLP",
        "ir": "IR",
        "ml": "ML",
        "multi-agent": "Multi-agent",
        "retrieval-augmented": "Retrieval-augmented",
        "neural-symbolic": "Neural-symbolic",
    }
    cleaned = replacements.get(word)
    if cleaned:
        return cleaned
    # Title-case each hyphenated segment so "video-event" becomes "Video-Event".
    parts = [seg[:1].upper() + seg[1:] if seg else seg for seg in word.split("-")]
    return "-".join(parts)


def _paper_ids(data: dict[str, Any]) -> set[str]:
    """Return the set of paper_id values stored in an author cluster."""
    ids: set[str] = set()
    for p in data.get("papers", []):
        pid = getattr(p, "paper_id", None) or (p.get("paper_id") if isinstance(p, dict) else None)
        if pid:
            ids.add(str(pid))
    return ids


def _author_in_top_paper(name: str, papers: list[Any]) -> bool:
    """Verify ``name`` actually appears as an author of one of their top papers.

    S2's ``paper/search`` returns authors on papers that match the query; we
    cluster by author_id and accept anyone whose id matches one returned paper's
    author list. But S2 occasionally returns homonym collisions where the
    author_id is shared or the cluster was filled by search relevance rather
    than authorship. This check picks the top ``AUTHOR_IN_PAPER_TOP_K`` papers
    by ``citation_count`` and confirms the candidate's name token appears in at
    least one of those papers' author lists.

    Returns True when the candidate is verifiably an author of at least one top
    paper; False when they don't (caller demotes the candidate's
    ``combined_score`` by ``AUTHOR_IN_PAPER_DEMOTE_FACTOR``).
    """
    if not papers or not name:
        return True  # nothing to check against → don't demote

    # Sort by citation_count desc, take the top K.
    sorted_papers = sorted(
        papers,
        key=lambda p: getattr(p, "citation_count", 0) or 0,
        reverse=True,
    )[:AUTHOR_IN_PAPER_TOP_K]

    # Tokenize the candidate name into lowercased name fragments (longer than
    # 1 char so single-letter initials like "j" are dropped — they would not
    # disambiguate against the homonym check). We require "last-name token
    # equals the author's surname token AND at least one other name token
    # appears in the author's name".
    name_tokens = [
        tok for tok in re.split(r"[^a-z0-9]+", name.lower()) if len(tok) > 1
    ]
    if not name_tokens:
        return True
    last_name = name_tokens[-1]
    other_tokens = name_tokens[:-1]

    for paper in sorted_papers:
        authors = getattr(paper, "authors", None) or []
        for a in authors:
            author_name = (getattr(a, "name", None) or "").lower()
            if not author_name:
                continue
            # Tokenize the author's own name the same way.
            author_tokens = [
                t for t in re.split(r"[^a-z0-9]+", author_name) if t
            ]
            if not author_tokens:
                continue
            # Surname match is strict: the author's LAST token must equal the
            # candidate's last token. Stricter than ``in`` substring which lets
            # "smit" match "smith".
            if author_tokens[-1] != last_name:
                continue
            if not other_tokens:
                # Initial-form candidate (e.g. "J. Ong"). Surname match is enough
                # — we discarded the initial because "J." disambiguates worse
                # than the surname heuristically. Trust S2's surname chain here.
                return True
            other_hit = False
            for tok in other_tokens:
                if tok in author_tokens or tok in author_name:
                    other_hit = True
                    break
            if other_hit:
                return True
            # Single-token author string equal to the surname is the S2-
            # truncation edge case (older papers show surname only). Accept.
            if len(author_tokens) == 1 and author_tokens[0] == last_name:
                return True
            # Otherwise keep searching authors in this paper.
            continue
    return False


def _collapse_co_workers(
    scored: list[tuple[float, str, dict[str, Any]]],
    *,
    threshold: float = DEDUP_JACCARD_THRESHOLD,
) -> list[tuple[float, str, dict[str, Any]]]:
    """Group researchers whose recent paper sets nearly overlap.

    Two co-authors who only publish together (e.g. 4 authors on a single
    PoisonedRAG paper) otherwise each surface as a separate candidate.
    Returns a list where collapsed entries are dropped, with the surviving
    entry gaining a ``co_workers`` list of their names.
    """
    if not scored:
        return scored

    kept: list[tuple[float, str, dict[str, Any]]] = []
    kept_aid_sets: list[tuple[str, set[str]]] = []
    for combined, aid, data in scored:
        ids = _paper_ids(data)
        absorb: str | None = None
        for kid, kids_ids in kept_aid_sets:
            if not ids or not kids_ids:
                continue
            intersection = len(ids & kids_ids)
            union = len(ids | kids_ids)
            if union and (intersection / union) >= threshold:
                absorb = kid
                break
        if absorb is None:
            data.setdefault("co_workers", [])
            kept.append((combined, aid, data))
            kept_aid_sets.append((aid, ids))
        else:
            # Find the kept entry we are merging into.
            for i, (k_combined, k_aid, k_data) in enumerate(kept):
                if k_aid != absorb:
                    continue
                # Add the coworker name unless it is the same person.
                if data.get("name") and data["name"] != k_data.get("name"):
                    names = k_data.setdefault("co_workers", [])
                    already = set(names) | {k_data.get("name", "")}
                    if data["name"] not in already:
                        names.append(data["name"])
                # Augment the kept paper set with unique co-authored papers so
                # downstream verification sees the broader work (citations
                # already summed via clustering).
                merged_papers = list(k_data.get("papers", []))
                existing_ids = _paper_ids(k_data)
                for p in data.get("papers", []):
                    pid = getattr(p, "paper_id", None)
                    if pid and str(pid) not in existing_ids:
                        merged_papers.append(p)
                k_data["papers"] = merged_papers[:30]
                break
    return kept


def _trim_affiliation(raw_aff: str) -> str:
    """Trim noisy suffixes Tavily sometimes appends to affiliations.

    Examples purely here for future-proofing. The goal: drop trailing
    fragments like "Joined. November 2021. Names |" that come from
    scraping OpenReview / ResearchGate UI chrome.
    """
    if not raw_aff:
        return ""
    text = raw_aff.strip()
    # Drop everything from the found UI chrome keyword onwards on its own.
    for cut_marker in [
        " Joined.",
        " Joined ",
        " Names |",
        " Skip slid",
        " Skip slid |",
    ]:
        ci = text.find(cut_marker.strip())
        if ci > 0:
            text = text[:ci].strip()
    text = re.sub(r"\s+", " ", text)
    return text


# Region mapping for /discover. UK is intentionally absent from the default
# allow-list. HK and CN are separate buckets so a Hong Kong prof does not crowd
# out mainland-China candidates (and vice versa).
_REGION_BY_COUNTRY: dict[str, str] = {
    "united states": "US",
    "usa": "US",
    "us": "US",
    "america": "US",
    "canada": "CA",
    "china": "CN",
    "prc": "CN",
    "people's republic of china": "CN",
    "hong kong": "HK",
    "hong kong sar": "HK",
    "macau": "HK",
    "macao": "HK",
    "taiwan": "HK",  # bucketed with HK for discover purposes; adjust if needed
    "united kingdom": "UK",
    "uk": "UK",
    "great britain": "UK",
    "england": "UK",
    "scotland": "UK",
    "wales": "UK",
    "northern ireland": "UK",
}
_EU_COUNTRIES: set[str] = {
    "germany", "france", "netherlands", "spain", "italy", "switzerland",
    "austria", "sweden", "norway", "denmark", "finland", "belgium",
    "ireland", "portugal", "greece", "poland", "czech republic", "czechia",
    "romania", "hungary", "slovakia", "slovenia", "croatia", "estonia",
    "latvia", "lithuania", "luxembourg", "iceland", "malta", "cyprus",
    "bulgaria",
}


def _country_to_region(country: str | None) -> str:
    """Map a country name to a region code (US, CA, EU, CN, HK, UK, OTHER)."""
    if not country:
        return "OTHER"
    key = country.strip().lower()
    if not key:
        return "OTHER"
    if key in _REGION_BY_COUNTRY:
        return _REGION_BY_COUNTRY[key]
    if key in _EU_COUNTRIES:
        return "EU"
    return "OTHER"


def _select_by_region(
    candidates: list[dict[str, Any]],
    allowed_regions: list[str],
    *,
    top_n: int = DISCOVER_TOP_N,
) -> list[dict[str, Any]]:
    """Pick ``top_n`` candidates balanced across the allowed region buckets.

    Each candidate keeps its original ``combined_score`` so order within a
    bucket is by score. We round-robin across buckets (in the order the user
    configured them) so the user's region preference is respected: if the
    config is ``US,CA,EU,CN,HK`` and there are 5 US profs but only 1 CA prof,
    the CA prof shows up in slot 2 rather than getting buried at the bottom.
    """
    if not candidates:
        return []
    allowed = [r.upper() for r in allowed_regions]
    buckets: dict[str, list[dict[str, Any]]] = {r: [] for r in allowed}
    for c in candidates:
        region = c.get("region", "OTHER")
        if region in buckets:
            buckets[region].append(c)
    # Sort each bucket by combined_score desc, then similarity desc.
    for region in buckets:
        buckets[region].sort(
            key=lambda c: (c.get("combined_score", 0.0), c.get("similarity", 0.0)),
            reverse=True,
        )

    selected: list[dict[str, Any]] = []
    # Round-robin pull one candidate from each non-empty bucket until we hit
    # top_n or every bucket is exhausted.
    while len(selected) < top_n:
        progressed = False
        for region in allowed:
            if len(selected) >= top_n:
                break
            pool = buckets.get(region, [])
            if pool:
                selected.append(pool.pop(0))
                progressed = True
        if not progressed:
            break
    return selected


def _cosine(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two same-dimension vectors."""
    import math

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _extract_affiliation(results: list[Any]) -> str:
    """Extract a university/lab name from Tavily search results."""
    import re

    if not results:
        return ""
    for r in results[:3]:
        content = r.content.lower()
        for keyword in ["university", "institute", "lab", "college", "research"]:
            idx = content.find(keyword)
            if idx >= 0:
                # Find sentence boundaries around the keyword
                # Look backwards for sentence start
                sent_start = idx
                while sent_start > 0:
                    if content[sent_start - 1] == "." and sent_start < len(content) and content[sent_start] == " ":
                        break
                    sent_start -= 1
                # Skip leading space after period
                if sent_start > 0 and content[sent_start] == " ":
                    sent_start += 1

                # Look forwards for sentence end (period + space or end of content)
                sent_end = idx
                while sent_end < len(content):
                    if content[sent_end] == ".":
                        if sent_end + 1 >= len(content) or content[sent_end + 1] == " ":
                            sent_end += 1  # include the period
                            break
                    sent_end += 1

                snippet = r.content[sent_start:sent_end].strip()

                # Truncate to 120 chars at word boundary
                if len(snippet) > 120:
                    truncated = snippet[:120]
                    last_space = truncated.rfind(" ")
                    if last_space > 0:
                        truncated = truncated[:last_space]
                    snippet = truncated.rstrip()

                # Remove emoji
                snippet = re.sub(
                    r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
                    r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
                    r"\U00002702-\U000027B0\U0000FE0F"
                    r"\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F"
                    r"\U0001FA70-\U0001FAFF]+",
                    "",
                    snippet,
                ).strip()

                return snippet
    return results[0].title if results else ""
