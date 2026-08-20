"""Contribution Finder agent — GitHub issue discovery + impact scoring + LLM analysis.

v0.1 scope: search GitHub issues matching Aaliyah's skill clusters, score by
impact (not popularity), analyze top 20 with Gemini, send weekly Telegram digest.

Path A (topic search): derives queries from data/user_skills.yaml.
Path B (tracked repos): reads data/cf_tracked_repos.yaml for extra-scored repos.
"""
from __future__ import annotations

import asyncio
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
import yaml

from backbone.model_client import ModelClient
from backbone.prompt_registry.loader import load as load_prompt
from backbone.prompt_registry.loader import render
from backbone.tools.vector import EmbedInput, EmbedTool

logger = structlog.get_logger("contribution_finder")

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
MIN_IMPACT_SCORE = 0.35
MAX_OPPS_PER_DIGEST = 60  # Aaliyah wants large volume


def _load_yaml(name: str) -> dict[str, Any]:
    path = DATA_DIR / name
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _format_effort(bucket: str) -> str:
    """Compact effort badge for inline digest display."""
    mapping = {
        "1-4 hours": "⚡1-4h",
        "half day": "🕐½day",
        "1-2 days": "📋1-2d",
        "3-5 days": "📦3-5d",
    }
    return mapping.get(bucket, f"📌{bucket}")


def _estimate_effort(issue: dict[str, Any]) -> str:
    """Label-based effort fallback before/without Gemini analysis."""
    labels = [lbl.lower() for lbl in (issue.get("labels") or [])]
    if "good first issue" in labels or "documentation" in labels:
        return "1-2 days"
    if "help wanted" in labels or "bug" in labels:
        return "half day"
    return ""


def _dedup_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate GitHub issues across queries.

    A row is a duplicate if it repeats the same (repo, issue number) — the
    same issue surfaced by multiple queries — OR the same (repo, title), a
    common pattern in auto-generated backlogs where one task gets filed twice.
    """
    seen_ids: set[tuple[str, int]] = set()
    seen_titles: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for iss in issues:
        repo = iss.get("repo_full_name", "")
        id_key = (repo, iss.get("issue_number", 0))
        title_key = (repo, (iss.get("title") or "").strip().lower())
        if id_key in seen_ids or title_key in seen_titles:
            continue
        seen_ids.add(id_key)
        seen_titles.add(title_key)
        out.append(iss)
    return out


def _cap_per_repo(
    scored: list[dict[str, Any]], per_repo: int
) -> list[dict[str, Any]]:
    """Keep only the best ``per_repo`` issues per repo (by order = score)."""
    counts: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for s in scored:
        repo = s.get("repo_full_name", "")
        if counts.get(repo, 0) >= per_repo:
            continue
        counts[repo] = counts.get(repo, 0) + 1
        out.append(s)
    return out


class ContributionFinderAgent:
    def __init__(self, task_ctx: Any = None) -> None:
        self.ctx = task_ctx
        self._llm = ModelClient()
        self._embed = EmbedTool()
        # Pre-computed skill vectors (reuse Job Hunter logic)
        self._cluster_vecs: list[list[float]] = []
        self._cluster_names: list[str] = []
        self._cluster_weights: list[float] = []
        self._all_skill_tokens: list[str] = []

    # ── Public API ────────────────────────────────────────────────

    async def run_discovery(self, topic: str | None = None) -> list[dict[str, Any]]:
        """Full discovery: search → score → filter → analyze top 20."""
        logger.info("cf_discovery_start", topic=topic)
        await self._ensure_skill_vecs()

        # Phase 1: search GitHub
        queries = self._build_queries(topic)
        all_issues: list[dict[str, Any]] = []
        for query in queries:
            try:
                batch = await self._search_issues(query)
                all_issues.extend(batch)
            except Exception as exc:
                logger.warning("cf_search_failed", query=query[:50], error=str(exc))
                continue
        all_issues = _dedup_issues(all_issues)
        print(f"[cf] {len(all_issues)} unique issues from {len(queries)} queries")

        # Phase 2: score + filter
        profile = self._load_cf_prefs()
        min_score = profile.get("min_impact_score", MIN_IMPACT_SCORE)
        scored = await self._score_all(all_issues, profile)
        scored = [s for s in scored if s["_impact_score"] >= min_score]
        scored.sort(key=lambda s: s["_impact_score"], reverse=True)
        print(f"[cf] {len(scored)} above threshold ({min_score})")

        # Phase 3: analyze top 20 with Gemini
        top = scored[:20]
        if top:
            print(f"[cf] analyzing top {len(top)} with Gemini...")
            await self._analyze_top(top)

        # Apply effort bucket preference bonus
        preferred = profile.get("preferred_effort_buckets", ["half day", "1-2 days"])
        for s in scored:
            eff = s.get("estimated_effort", "")
            if eff in preferred:
                s["_impact_score"] = round(s["_impact_score"] * 1.15, 2)
        scored.sort(key=lambda s: s["_impact_score"], reverse=True)

        max_per = profile.get("max_opportunities_per_digest", MAX_OPPS_PER_DIGEST)
        per_repo = int(profile.get("max_per_repo", 4))
        result = _cap_per_repo(scored, per_repo)[:max_per]
        await self._persist(result)
        return result

    async def run_discovery_tracked(self) -> list[dict[str, Any]]:
        """Path B: fetch issues from tracked repos only."""
        repos = self._load_tracked_repos()
        all_issues: list[dict[str, Any]] = []
        for repo in repos:
            query = f"repo:{repo['full_name']} is:issue is:open language:{repo['language']} label:\"good first issue\",\"help wanted\""
            try:
                batch = await self._search_issues(query)
                all_issues.extend(batch)
            except Exception as exc:
                logger.warning("cf_tracked_failed", repo=repo["full_name"], error=str(exc))
                continue
        all_issues = _dedup_issues(all_issues)
        print(f"[cf] {len(all_issues)} unique issues from {len(repos)} tracked repos")
        profile = self._load_cf_prefs()
        scored = await self._score_all(all_issues, profile)
        scored = [s for s in scored if s["_impact_score"] >= profile.get("min_impact_score", MIN_IMPACT_SCORE)]
        scored.sort(key=lambda s: s["_impact_score"], reverse=True)
        top = scored[:20]
        if top:
            await self._analyze_top(top)
        per_repo = int(profile.get("max_per_repo", 4))
        result = _cap_per_repo(scored, per_repo)[:profile.get("max_opportunities_per_digest", MAX_OPPS_PER_DIGEST)]
        await self._persist(result)
        return result

    async def _persist(self, results: list[dict[str, Any]]) -> None:
        """Upsert discovered opportunities so feedback buttons have a target."""
        try:
            from backbone.tools.contribution_store import persist_opportunities

            await persist_opportunities(results)
        except Exception as exc:
            logger.warning("cf_persist_failed", error=str(exc))

    # ── Data loaders ──────────────────────────────────────────────

    def _load_skill_clusters(self) -> list[dict[str, Any]]:
        raw = _load_yaml("user_skills.yaml")
        clusters = raw.get("skills", {})
        result: list[dict[str, Any]] = []
        for name, body in clusters.items():
            result.append({
                "name": name,
                "skills": body.get("skills", []),
                "weight": body.get("weight", 1.0),
            })
        return result

    def _load_tracked_repos(self) -> list[dict[str, Any]]:
        raw = _load_yaml("cf_tracked_repos.yaml")
        return raw.get("tracked_repos", [])

    def _load_cf_prefs(self) -> dict[str, Any]:
        raw = _load_yaml("user_profile.yaml")
        prefs = raw.get("user", {})
        cf_prefs = {
            "preferred_effort_buckets": ["half day", "1-2 days"],
            "max_opportunities_per_digest": MAX_OPPS_PER_DIGEST,
            "pass_cooldown_days": 30,
            "digest_cadence_days": 7,
            "min_impact_score": MIN_IMPACT_SCORE,
            "language_filter": "python",
            "max_per_repo": 4,
        }
        # Override from user_profile if present
        user_cf = raw.get("cf_prefs", {})
        cf_prefs.update(user_cf)
        return cf_prefs

    # ── Skill vectors ─────────────────────────────────────────────

    async def _ensure_skill_vecs(self) -> None:
        if self._cluster_vecs:
            return
        clusters = self._load_skill_clusters()
        all_texts = [" ".join(c["skills"]) for c in clusters]
        for c in clusters:
            self._cluster_names.append(c["name"])
            self._cluster_weights.append(c.get("weight", 1.0))
            self._all_skill_tokens.extend(c["skills"])
        embeds = await self._embed(self.ctx, EmbedInput(texts=all_texts))
        self._cluster_vecs = [list(v) for v in (embeds.embeddings or [])]

    # ── GitHub search ─────────────────────────────────────────────

    def _build_queries(self, topic: str | None = None) -> list[str]:
        """Build 6 GitHub issue search queries from skill clusters."""
        if topic:
            return [f'{topic} is:issue is:open language:python label:"good first issue","help wanted"']

        clusters = self._load_skill_clusters()
        query_map = {
            "agent_systems": '"multi-agent" OR "agent orchestration" OR "task routing"',
            "rag_retrieval": "RAG OR \"retrieval-augmented\" OR \"vector database\"",
            "llm_ops": "\"LLM\" OR \"document parsing\" OR \"context window\"",
            "ai_ml_frameworks": "LangChain OR LlamaIndex OR \"Hugging Face\" OR AutoGen OR CrewAI",
            "backend": "\"FastAPI\" OR asyncio OR \"async Python\"",
            "data_engineering": "\"web scraping\" OR \"SQL\" OR \"schema\"",
        }
        queries = []
        for cluster_name, terms in query_map.items():
            queries.append(
                f"({terms}) is:issue is:open language:python "
                f'label:"good first issue","help wanted"'
            )
        return queries

    async def _search_issues(self, query: str) -> list[dict[str, Any]]:
        """Execute one GitHub search query, return parsed issues."""
        from backbone.tools.github import SearchIssuesInput, SearchIssuesTool
        tool = SearchIssuesTool()
        # Larger pool so a single fast-moving repo cannot monopolise the top-N.
        out = await tool(self.ctx, SearchIssuesInput(query=query, per_page=50))
        return [i.model_dump() for i in out.issues]

    # ── Scoring ───────────────────────────────────────────────────

    async def _score_all(
        self, issues: list[dict[str, Any]], profile: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Score every issue by impact formula. v0.2: batch Voyage embeddings."""
        if not issues:
            return issues
        if not self._cluster_vecs:
            await self._ensure_skill_vecs()
        texts = [(iss.get("title", "") + " " + (iss.get("body", "") or "")[:500])[:2000] for iss in issues]
        posting_vecs: list[list[float]] = []
        BATCH = 96
        for off in range(0, len(texts), BATCH):
            embeds = await self._embed(self.ctx, EmbedInput(texts=texts[off:off + BATCH]))
            posting_vecs.extend([list(v) for v in (embeds.embeddings or [])])
        now = datetime.now(UTC)
        preferred = profile.get("preferred_effort_buckets", ["half day", "1-2 days"])
        from backbone.tools.contribution_store import apply_repo_signal, repo_signals

        signals = await repo_signals()
        scored: list[dict[str, Any]] = []
        for i, iss in enumerate(issues):
            pvec = posting_vecs[i] if i < len(posting_vecs) else [0.0]
            skill_match = self._weighted_max_cosine(pvec)
            score = self._compute_impact(iss, now, preferred, skill_match)
            boost = apply_repo_signal(iss, signals)
            iss["_impact_score"] = round(score + boost, 2)
            iss["_repo_signal"] = boost
            iss["_skill_match"] = round(skill_match, 2)
            # Label-based effort so cards read well even before Gemini analysis.
            iss.setdefault("estimated_effort", _estimate_effort(iss))
            scored.append(iss)
        return scored

    def _compute_impact(
        self, issue: dict[str, Any], now: datetime, preferred_buckets: list[str],
        skill_match: float = 0.0,
    ) -> float:
        """Per-issue impact score. Skill match comes pre-computed from cosine."""
        try:
            age_days = (now - datetime.fromisoformat(issue.get("created_at", "").replace("Z", "+00:00"))).days
        except (ValueError, TypeError):
            age_days = 30
        freshness = 1.0 / math.log(max(age_days, 1) + 2)
        reactions = min(issue.get("reaction_count", 0), 10) / 10.0
        comments = issue.get("comment_count", 0)
        uncrowded = 1.0 / (1.0 + comments / 5.0)
        pr_count = issue.get("linked_pr_count", 0)
        pr_unworked = 1.0 if pr_count == 0 else 0.6 if pr_count <= 2 else 0.2
        try:
            last_activity = (now - datetime.fromisoformat(issue.get("updated_at", "").replace("Z", "+00:00"))).days
        except (ValueError, TypeError):
            last_activity = 0
        activity_unworked = min(1.0, last_activity / 30.0)
        unworked = 0.6 * pr_unworked + 0.4 * activity_unworked
        labels = [lbl.lower() for lbl in (issue.get("labels") or [])]
        label_bonus = 0.0
        if "good first issue" in labels: label_bonus += 0.15
        if "help wanted" in labels: label_bonus += 0.10
        if "bug" in labels: label_bonus += 0.05
        if "documentation" in labels: label_bonus += 0.05
        label_bonus = min(label_bonus, 0.30)
        raw = freshness * 0.30 + reactions * 0.10 + skill_match * 0.25 + uncrowded * 0.05 + unworked * 0.20 + label_bonus * 0.10
        return min(raw, 1.0)

    def _weighted_max_cosine(self, posting_vec: list[float]) -> float:
        """Per-cluster weighted max cosine."""
        best = 0.0
        for cvec, cweight in zip(self._cluster_vecs, self._cluster_weights, strict=True):
            dot = sum(x * y for x, y in zip(cvec, posting_vec, strict=True))
            mag_a = math.sqrt(sum(x * x for x in cvec))
            mag_b = math.sqrt(sum(x * x for x in posting_vec))
            sim = dot / (mag_a * mag_b) if mag_a and mag_b else 0.0
            weighted = sim * cweight
            if weighted > best:
                best = weighted
        return best

    # ── Gemini analysis ───────────────────────────────────────────

    async def _analyze_top(self, opportunities: list[dict[str, Any]]) -> None:
        """Run Gemini analysis on top issues concurrently."""
        sem = asyncio.Semaphore(3)
        async def _analyze_one(opp: dict[str, Any]) -> None:
            async with sem:
                try:
                    result = await self._llm_analyze_issue(opp)
                    if result:
                        opp.update(result)
                except Exception:
                    pass
        await asyncio.gather(*[_analyze_one(o) for o in opportunities])

    async def _llm_analyze_issue(self, issue: dict[str, Any]) -> dict[str, Any] | None:
        """One issue → Gemini 2.5 flash → structured analysis."""
        template = load_prompt("contribution_finder", "github_analyze")
        title = issue.get("title", "")[:200]
        body = (issue.get("body", "") or "")[:1500]
        repo = issue.get("repo_full_name", "")
        rendered, _ = render(
            template,
            {
                "title": title,
                "body_snippet": body,
                "repo": repo,
                "comments": "(no comments fetched in v0.1)",
                "user_skills": ", ".join(self._cluster_names[:5]),
            },
        )
        try:
            raw = await self._llm.generate(
                model=template.model.name,
                prompt=rendered,
                temperature=template.model.temperature,
                max_tokens=template.model.max_tokens,
            )
        except Exception:
            return None
        if not raw:
            return None
        from backbone.model_client import parse_loose_json
        parsed = parse_loose_json(raw)
        if isinstance(parsed, dict):
            return parsed
        return None

    # ── Feedback persistence ──────────────────────────────────────

    async def record_feedback(self, opp_github_id: str, signal: str) -> bool:
        """Record interested/pass/doing feedback (delegates to the store)."""
        from backbone.tools.contribution_store import record_feedback

        return await record_feedback(opp_github_id, signal)
