
"""Job Hunter agent — career discovery + match scoring + salary annotation.

v0.1 scope: job discovery from the company watchlist (§4, §13 of the design
doc), vector-based match scoring (§15) with optional LLM judge in the gray
band, salary floor annotation (§5), visa classification (§6).

v0.2 deferred: the full cover-letter pipeline (§14), cross-agent prof watch
(§8), and approval gate. The agent structure is laid so those plug in.

The agent reuses the Paper Tracker's patterns: tools are bound to instance
attributes, the verify-ish fetch is concurrent per tier, and the digest
formatter is plain-text grouped-by-region inline-button messages.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

from backbone.model_client import ModelClient
from backbone.prompt_registry.loader import load as load_prompt
from backbone.prompt_registry.loader import render
from backbone.tools.firecrawl import FirecrawlScrapeTool
from backbone.tools.tavily import TavilySearchTool
from backbone.tools.vector import EmbedInput, EmbedTool, UpsertInput, UpsertTool

logger = structlog.get_logger("job_hunter")

# Max concurrent company fetches per digest run. ATS fetches are sub-second
# (simple HTTPS GET), Firecrawl is ~5s per page, Tavily ~3s.
# Cap at 6 so we don't overwhelm Firecrawl's free tier.
FETCH_CONCURRENCY = 6
# Gray band is the LLM-judge-trigger interval. Vector scores in this band
# get one extra DeepSeek-v4-pro call to confirm/dispute relevance.
# NB: scores use per-cluster weighted max, so higher-value weights (1.5 for
# agent_systems/rag_retrieval) push clearly-relevant postings above 0.55.
GRAY_BAND_LOW = 0.32
GRAY_BAND_HIGH = 0.52
# Default floor — overridden by jh_user_profile.yaml if present.
# 0.45 (weighted max scoring): real AI/agent postings score ~0.55-0.60,
# clearly irrelevant postings (HR/Marketing/DevRel) score ~0.25.
DEFAULT_MIN_MATCH_SCORE = 0.45
# Regions to silently expand to when the original region returns zero matches.
# nigeria → africa + international_remote; africa → international_remote.
_REGION_FALLBACKS: dict[str, list[str]] = {
    "nigeria": ["africa", "international_remote"],
    "africa": ["international_remote"],
}
# Score bonus applied to Tier 2 (Firecrawl) and Tier 3 (Tavily) synthetic postings.
# These embed full careers pages or search snippets — inherently noisier than
# single-job ATS descriptions — so a small bonus keeps them from being systematically suppressed.
_SOURCE_TIER_BONUS = 0.10
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _load_yaml(name: str) -> dict[str, Any]:
    path = DATA_DIR / name
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


class JobHunterAgent:
    def __init__(self, task_ctx: Any = None) -> None:
        self.ctx = task_ctx
        self._embed = EmbedTool()
        self._upsert = UpsertTool()
        self._firecrawl = FirecrawlScrapeTool()
        self._tavily = TavilySearchTool()
        self._llm = ModelClient()
        # Pre-computed once per /jobs digest run.
        self._user_skill_vec: list[float] | None = None
        # Per-cluster vectors + weights — used for weighted-max scoring.
        self._cluster_vecs: list[list[float]] = []
        self._cluster_names: list[str] = []
        self._cluster_weights: list[float] = []
        self._all_skill_tokens: list[str] = []

    # ── Public API ────────────────────────────────────────────

    async def run_discovery(self, region: str | None = None) -> list[dict[str, Any]]:
        """Run the full discovery pipeline for today's digest.

        If ``region`` is provided, limit the watchlist to that region only
        (for /jobs region flags). Quality-of-life v0.2 additions:

        **Cross-region fallback (C):** when a targeted region returns zero
        matches, the system silently expands to fallback regions. The digest
        formatter shows a "No matches in Nigeria — showing Africa +
        International Remote instead" header.

        **Tier bonus (D):** Firecrawl (Tier 2) and Tavily (Tier 3) synthetic
        postings get a +0.10 score bonus to compensate for their inherently
        noisier whole-page embeddings vs single-job ATS descriptions.
        """
        logger.info("jh_discovery_start", region=region)
        profile = self._load_career_profile()
        skills = self._load_skill_clusters()
        min_match = profile.get("min_match_score", DEFAULT_MIN_MATCH_SCORE)
        await self._ensure_user_skill_vec(skills)

        results = await self._discover_for_region(region, skills, profile, min_match)

        # Cross-region fallback: if a targeted region returns nothing, expand.
        if region and not results and region in _REGION_FALLBACKS:
            fallbacks = _REGION_FALLBACKS[region]
            logger.info("jh_fallback", from_region=region, to=fallbacks)
            merged: list[dict[str, Any]] = []
            for fb in fallbacks:
                fb_results = await self._discover_for_region(fb, skills, profile, min_match)
                if fb_results:
                    merged.extend(fb_results)
            if merged:
                merged.sort(key=lambda s: s["_score_raw"], reverse=True)
                max_per = profile.get("max_results_per_digest", 20)
                results = merged[:max_per]

        return results

    async def _discover_for_region(
        self,
        region: str | None,
        skills: list[dict[str, Any]],
        profile: dict[str, Any],
        min_match: float,
    ) -> list[dict[str, Any]]:
        """Core fetch + score + annotate for one region.

        Called by run_discovery and again by the cross-region fallback path
        when the primary region returns zero matches.
        """
        companies = self._load_watchlist(region)
        print(f"[jh] Fetching across {len(companies)} companies{f' ({region})' if region else ''}...")
        all_postings: list[dict[str, Any]] = []
        for company in companies:
            src = company.get("source_tier")
            name = company["name"]
            region_tag = company.get("region", "")
            try:
                if src == 1:
                    postings = await self._fetch_ats(company)
                elif src == 2:
                    postings = await self._fetch_careers(company)
                elif src == 3:
                    postings = await self._fetch_tavily(company)
                else:
                    continue
                for p in postings:
                    p["_region"] = region_tag
                    p["_organization"] = name
                    p["_source_tier"] = src
                all_postings.extend(postings)
                logger.info("jh_company_done", company=name, tier=src, found=len(postings))
            except Exception as exc:
                logger.warning("jh_company_failed", company=name, tier=src, error=str(exc))
                continue

        print(f"[jh] {len(all_postings)} raw postings from {len(companies)} companies")
        if not all_postings:
            return []

        # Score every posting (per-cluster weighted max + optional LLM judge).
        scored = await self._score_all(all_postings, skills, profile)

        # Apply tier bonus for Firecrawl (Tier 2) and Tavily (Tier 3).
        for s in scored:
            tier = s.get("_source_tier", 0)
            if tier in (2, 3):
                s["_score_raw"] = round(s["_score_raw"] + _SOURCE_TIER_BONUS, 2)

        scored = [s for s in scored if s["_score_raw"] >= min_match]
        scored.sort(key=lambda s: s["_score_raw"], reverse=True)

        max_per = profile.get("max_results_per_digest", 20)
        top = scored[:max_per]
        logger.info("jh_discovery_done", total=len(scored), shown=len(top), region=region)

        # LLM enrichment: role type for unknown role_type postings, then visa.
        await self._enrich_roles(top)
        await self._enrich_visas(top, profile)
        await self._enrich_remote(top)
        await self._enrich_skills(top)

        # Annotate salary + visa for digest formatter.
        for item in top:
            item["_salary"] = self._annotate_salary(item, profile)
            item["_visa"] = self._annotate_visa(item, profile)

        # Persist scored postings to DB so /saved can find them later.
        await self._persist_postings(scored)

        return top

    async def _persist_postings(self, postings: list[dict[str, Any]]) -> None:
        """Write each posting to job_hunter_openings if not already present.

        Dedup key is external_id. Only inserts — ON CONFLICT DO NOTHING — so
        re-discovery across digest cadences doesn't duplicate. Companion
        status rows are upserted separately by mark_saved / mark_skipped.
        """
        from sqlalchemy import text
        from backbone.db.session import async_session_factory

        factory = async_session_factory()
        inserted = 0
        for p in postings:
            try:
                async with factory() as session:
                    await session.execute(
                        text(
                            "INSERT INTO job_hunter_openings"
                            " (external_id, source, source_url, title, organization,"
                            "  description, role_type, region, application_url, remote_ok)"
                            " VALUES"
                            " (:ext, :src, :url, :title, :org, :desc, :role, :region,"
                            "  :app, :remote)"
                            " ON CONFLICT (external_id) DO NOTHING"
                        ),
                        {
                            "ext": p.get("external_id", ""),
                            "src": p.get("source", ""),
                            "url": p.get("source_url", ""),
                            "title": p.get("title", "")[:1000],
                            "org": p.get("_organization", p.get("organization", ""))[:500],
                            "desc": (p.get("description", "") or "")[:4000],
                            "role": p.get("role_type", "unknown"),
                            "region": p.get("_region", ""),
                            "app": p.get("application_url", ""),
                            "remote": p.get("remote_ok"),
                        },
                    )
                    await session.commit()
                inserted += 1
            except Exception:
                # Already exists (duplicate external_id) — skip silently.
                pass
        if inserted:
            logger.info("jh_persist_done", inserted=inserted)

    async def send_digest(self, items: list[dict[str, Any]], user_id: str) -> str:
        """Format each posting as a Telegram message with [Save] [Skip] buttons."""
        import httpx
        import json as _json

        settings = self.ctx.settings if self.ctx else None
        if not settings or not settings.telegram_bot_token:
            return ""
        token = settings.telegram_bot_token
        base = f"https://api.telegram.org/bot{token}"
        last_msg_id = ""

        # Group by region for section headers.
        region_order = ["nigeria", "africa", "eu", "canada", "international_remote"]
        buckets: dict[str, list[dict[str, Any]]] = {r: [] for r in region_order}
        for it in items:
            region = it.get("_region", "")
            buckets.setdefault(region, []).append(it)

        async with httpx.AsyncClient() as client:
            for region in region_order:
                bucket = buckets.get(region, [])
                if not bucket:
                    continue
                # Section header.
                header = f"{region.upper()} — {len(bucket)} postings"
                resp = await client.post(
                    f"{base}/sendMessage",
                    json={"chat_id": user_id, "text": header},
                    timeout=10,
                )
                last_msg_id = str(resp.json().get("result", {}).get("message_id", ""))

                for it in bucket:
                    title = it.get("title", "")[:150]
                    org = it.get("_organization", it.get("organization", ""))
                    match_text = self._format_match(it)
                    salary_text = it.get("_salary", "")
                    visa_text = it.get("_visa", "")
                    url = it.get("application_url", it.get("source_url", ""))
                    external_id = it.get("external_id", "")

                    body_parts = [f"{title}", f"{match_text}"]
                    if salary_text:
                        body_parts.append(salary_text)
                    if visa_text:
                        body_parts.append(visa_text)
                    africa_ok = it.get("_africa_ok")
                    if africa_ok is True:
                        body_parts.append("🌍 Africa-friendly remote")
                    if org:
                        body_parts.append(org)
                    if url:
                        body_parts.append(url)

                    open_url = it.get("application_url", it.get("source_url", ""))
                    buttons_row = [
                        {
                            "text": "Open",
                            "url": open_url,
                        } if open_url else None,
                        {
                            "text": "Save",
                            "callback_data": _json.dumps(
                                {"command": "jh_save", "external_id": external_id}
                            ),
                        },
                        {
                            "text": "Skip",
                            "callback_data": _json.dumps(
                                {"command": "jh_skip", "external_id": external_id}
                            ),
                        },
                    ]
                    keyboard = [[b for b in buttons_row if b is not None]]

                    resp = await client.post(
                        f"{base}/sendMessage",
                        json={
                            "chat_id": user_id,
                            "text": " · ".join(body_parts),
                            "reply_markup": {"inline_keyboard": keyboard},
                            "disable_web_page_preview": True,
                        },
                        timeout=10,
                    )
                    last_msg_id = str(
                        resp.json().get("result", {}).get("message_id", "")
                    )
        return last_msg_id

    async def get_saved_postings(self) -> list[dict[str, Any]]:
        """Return postings the user has saved, from the DB."""
        from sqlalchemy import text
        from backbone.db.session import async_session_factory

        factory = async_session_factory()
        rows: list[dict[str, Any]] = []
        try:
            async with factory() as session:
                result = await session.execute(
                    text(
                        "SELECT o.external_id, o.title, o.organization, o.source_url, \n"

                        "FROM job_hunter_opening_status s \n"

                        "WHERE s.status = 'saved' \n"

                        "LIMIT 50"
                    )
                )
                for r in result.all():
                    rows.append({
                        "external_id": r.external_id,
                        "title": r.title,
                        "organization": r.organization,
                        "source_url": r.source_url,
                        "region": r.region,
                        "role_type": r.role_type,
                    })
        except Exception as exc:
            logger.warning("jh_saved_fetch_failed", error=str(exc))
        return rows

    async def mark_saved(self, external_id: str) -> bool:
        """Mark a posting as saved. Persists to DB via upsert."""
        from sqlalchemy import text
        from backbone.db.session import async_session_factory

        factory = async_session_factory()
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO job_hunter_opening_status (opening_id, user_id, status, saved_at) \n"

                        "FROM job_hunter_openings o \n"

                        "ON CONFLICT (user_id, opening_id) \n"

                    ),
                    {"user_id": "aaliyah", "external_id": external_id},
                )
                await session.commit()
            return True
        except Exception as exc:
            logger.warning("jh_save_failed", error=str(exc), external_id=external_id)
            return False

    async def mark_skipped(self, external_id: str) -> bool:
        """Mark a posting as skipped."""
        from sqlalchemy import text
        from backbone.db.session import async_session_factory

        factory = async_session_factory()
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO job_hunter_opening_status (opening_id, user_id, status, skipped_at) \n"

                        "FROM job_hunter_openings o \n"

                        "ON CONFLICT (user_id, opening_id) \n"

                    ),
                    {"user_id": "aaliyah", "external_id": external_id},
                )
                await session.commit()
            return True
        except Exception as exc:
            logger.warning("jh_skip_failed", error=str(exc), external_id=external_id)
            return False

    # ── Single posting lookup (/job command) ─────────────────

    async def lookup_single_posting(self, url_or_text: str) -> dict[str, Any] | None:
        """Fetch and score a single posting from a URL or pasted text.

        F-JH.1: accept a job posting as either a URL or pasted text.
        Returns a dict with title, organization, score, and metadata,
        or None if the page couldn't be parsed as a posting.
        """
        from backbone.model_client import parse_loose_json

        # Fetch
        if url_or_text.startswith("http"):
            try:
                scrape_out = await self._firecrawl(
                    self.ctx, ScrapeInput(url=url_or_text, formats=["markdown"])
                )
                markdown = (scrape_out.content.markdown or "")[:6000]
            except Exception:
                markdown = ""
            if not markdown or len(markdown) < 100:
                return None
        else:
            markdown = url_or_text[:6000]

        # Extract structured posting data
        extraction_prompt = (
            "Extract structured job posting data from this text. \n"

            "Content:\n---\n{content}\n---\n\n"
            "Output STRICT JSON only - no fences, no prose.\n"
            "Schema:\n"
            '{{"is_job_posting": <true|false>, "title": "<job title>", '
            '"organization": "<company name>", "description": "<1-2 sentence summary>", '
            '"location": "<city, country>", "remote_ok": <true|false|null>, '
            '"role_type": "<internship|co_op|new_grad|research|experienced|unknown>", '
            '"application_url": "<direct apply link>"}}'
        )
        raw = await self._llm.generate(
            model="deepseek-v4-pro",
            prompt=extraction_prompt.format(content=markdown),
            temperature=0.1,
            max_tokens=500,
        )
        parsed = parse_loose_json(raw) if raw else None
        if not isinstance(parsed, dict) or not parsed.get("is_job_posting"):
            return None

        # Ensure skill vectors are loaded
        skills = self._load_skill_clusters()
        await self._ensure_user_skill_vec(skills)

        title = parsed.get("title", "")[:200]
        org = parsed.get("organization", "Unknown")[:100]
        desc = parsed.get("description", "") or markdown[:1000]
        text = f"{title} {desc}"[:2000]
        embeds = await self._embed(self.ctx, EmbedInput(texts=[text]))
        pvec = list(embeds.embeddings[0]) if embeds.embeddings else [0.0]
        score, top_cluster = self._weighted_max_score(pvec)

        return {
            "title": title,
            "organization": org,
            "description": desc,
            "location": parsed.get("location", ""),
            "remote_ok": parsed.get("remote_ok"),
            "role_type": parsed.get("role_type", "unknown"),
            "application_url": parsed.get("application_url", url_or_text),
            "_score_raw": round(score, 2),
            "_top_cluster": top_cluster,
            "_region": "manual",
            "_organization": org,
        }

    # ── Pre-research flow (/pre-research command) ────────────

    async def pre_research(self, company_name: str) -> str:
        """Build a research brief for a company. No email, no save.

        Architecture trigger (§9): Tavily search + Firecrawl scrape →
        Gemini 2.5 flash summary → formatted result.
        """
        from backbone.tools.tavily import SearchInput, TavilySearchTool

        # Phase 1: Tavily search for company info
        tavily = TavilySearchTool()
        try:
            t_out = await tavily(
                self.ctx,
                SearchInput(query=f"{company_name} company mission products careers team", max_results=5),
            )
            search_text = "\n".join(
                f"- {r.title}: {r.content[:200]}" for r in (t_out.results or [])
            )
        except Exception:
            search_text = "(search unavailable)"

        # Phase 2: Firecrawl scrape company homepage
        homepage_text = ""
        candidates = [
            f"https://{company_name.lower().replace(' ','')}.com/about",
            f"https://{company_name.lower().replace(' ','')}.com",
        ]
        for url in candidates:
            try:
                scrape_out = await self._firecrawl(
                    self.ctx, ScrapeInput(url=url, formats=["markdown"])
                )
                homepage_text = (scrape_out.content.markdown or "")[:3000]
                if len(homepage_text) > 200:
                    break
            except Exception:
                continue

        # Phase 3: Gemini summary
        template = load_prompt("job_hunter", "company_research")
        rendered, _ = render(
            template,
            {
                "company_name": company_name,
                "search_results": search_text[:3000],
                "homepage_content": homepage_text[:3000],
            },
        )
        raw = await self._llm.generate(
            model=template.model.name,
            prompt=rendered,
            temperature=template.model.temperature,
            max_tokens=template.model.max_tokens,
        )
        from backbone.model_client import parse_loose_json
        parsed = parse_loose_json(raw) if raw else {}
        if not isinstance(parsed, dict):
            return f"**{company_name}**\n\nCould not build research brief. Try a different company name."

        # Format result
        lines = [f"**{company_name}** — Research Brief", ""]
        if parsed.get("summary"):
            lines.append(parsed["summary"])
            lines.append("")
        if parsed.get("mission"):
            lines.append(f"🎯 Mission: {parsed['mission']}")
        if parsed.get("products"):
            lines.append(f"📦 Products: {', '.join(parsed['products'][:5])}")
        if parsed.get("recent_news"):
            lines.append("📰 Recent:")
            for n in parsed["recent_news"][:3]:
                lines.append(f"  • {n}")
        if parsed.get("key_people"):
            lines.append(f"👥 Key people: {', '.join(parsed['key_people'][:5])}")
        if parsed.get("hiring_signals"):
            lines.append(f"💼 Hiring: {parsed['hiring_signals']}")
        if parsed.get("culture_notes"):
            lines.append(f"🏢 Culture: {parsed['culture_notes']}")
        if parsed.get("fit_for_aaliyah"):
            lines.append(f"🎓 Fit: {parsed['fit_for_aaliyah']}")

        return "\n".join(lines)


    # ── Skills extraction enrichment ─────────────────────────

    async def _enrich_skills(self, postings: list[dict[str, Any]]) -> None:
        """Extract required_skills and nice_to_have via LLM for each posting.

        Populates the required_skills and nice_to_have JSONB columns in the DB.
        Runs concurrently with semaphore=4. Only touches postings that don't
        already have required_skills set.
        """
        to_extract = [p for p in postings if not p.get("required_skills")]
        if not to_extract:
            return
        sem = asyncio.Semaphore(4)
        async def _extract_one(p: dict[str, Any]) -> None:
            async with sem:
                try:
                    result = await self._llm_extract_skills(p)
                    if result:
                        p["required_skills"] = result.get("required_skills", [])
                        p["nice_to_have"] = result.get("nice_to_have", [])
                        p["_min_experience"] = result.get("min_experience_years")
                        p["_education_required"] = result.get("education_required", "none")
                except Exception:
                    pass
        await asyncio.gather(*[_extract_one(p) for p in to_extract])

    async def _llm_extract_skills(self, posting: dict[str, Any]) -> dict[str, Any] | None:
        """LLM-based skills extraction."""
        template = load_prompt("job_hunter", "extract_skills")
        title = posting.get("title", "")[:200]
        desc = (posting.get("description", "") or "")[:2000]
        rendered, _ = render(
            template,
            {"posting_title": title, "posting_description": desc},
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

    # ── Data loaders ──────────────────────────────────────────

    def _load_watchlist(self, region: str | None = None) -> list[dict[str, Any]]:
        raw = _load_yaml("company_watchlist.yaml")
        companies = raw.get("companies", [])
        if not companies:
            return []
        if region:
            companies = [c for c in companies if c.get("region") == region]
        return companies

    def add_company_to_watchlist(self, name: str, region: str) -> tuple[bool, str]:
        """Add a company to the watchlist YAML. Tier defaults to 2 (Firecrawl)."""
        raw = _load_yaml("company_watchlist.yaml")
        companies: list[dict] = raw.get("companies", [])
        for c in companies:
            if c.get("name", "").lower() == name.lower():
                return False, f"'{name}' is already in the watchlist."
        if region not in ("nigeria", "africa", "eu", "canada", "international_remote"):
            return False, f"Unknown region: {region}. Use nigeria, africa, eu, canada, or international_remote."
        companies.append({
            "name": name,
            "region": region,
            "source_tier": 2,
            "careers_url": f"https://{name.lower().replace(' ','')}.com/careers",
        })
        raw["companies"] = companies
        path_yaml = DATA_DIR / "company_watchlist.yaml"
        with open(path_yaml, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return True, f"Added '{name}' to watchlist (region: {region}, tier 2)."

    def remove_company_from_watchlist(self, name: str) -> tuple[bool, str]:
        """Remove a company by name (case-insensitive match)."""
        raw = _load_yaml("company_watchlist.yaml")
        companies: list[dict] = raw.get("companies", [])
        before = len(companies)
        raw["companies"] = [c for c in companies if c.get("name", "").lower() != name.lower()]
        after = len(raw["companies"])
        if before == after:
            return False, f"'{name}' not found in watchlist."
        path_yaml = DATA_DIR / "company_watchlist.yaml"
        with open(path_yaml, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return True, f"Removed '{name}' from watchlist."

    def _load_career_profile(self) -> dict[str, Any]:
        raw = _load_yaml("jh_user_profile.yaml")
        return raw

    def _save_career_profile(self, profile: dict[str, Any]) -> None:
        """Write the career profile back to jh_user_profile.yaml."""
        path = DATA_DIR / "jh_user_profile.yaml"
        with open(path, "w") as f:
            yaml.dump(profile, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def set_preference(self, key_path: str, value: str) -> tuple[bool, str]:
        """Update a single preference key in jh_user_profile.yaml.

        key_path is dot-separated: 'salary.canada', 'digest.cadence', 'digest.time'.
        Returns (success, message).
        """
        profile = self._load_career_profile()
        parts = key_path.split(".")

        if parts[0] == "salary" and len(parts) >= 2:
            region = parts[1]
            if region not in ("nigeria", "africa", "eu", "canada"):
                return False, f"Unknown region: {region}. Use nigeria, africa, eu, or canada."
            try:
                amount = int(value)
            except ValueError:
                return False, f"Invalid amount: {value}. Use an integer."
            floors = profile.setdefault("salary_floor", {})
            floors[region] = amount
            self._save_career_profile(profile)
            cur = profile.get("salary_currency", {}).get(region, "N/A")
            period = profile.get("salary_period", {}).get(region, "N/A")
            return True, f"Salary floor for {region} set to {amount} {cur}/{period}."

        if parts[0] == "digest" and len(parts) >= 2:
            field = parts[1]
            if field == "cadence":
                try:
                    days = int(value)
                except ValueError:
                    return False, f"Invalid cadence: {value}. Use an integer (e.g. 3 for every 3 days)."
                if days < 1 or days > 30:
                    return False, "Cadence must be between 1 and 30 days."
                profile["digest_frequency_days"] = days
                self._save_career_profile(profile)
                return True, f"Digest cadence set to every {days} days."
            if field == "time":
                if not value or ":" not in value:
                    return False, f"Invalid time: {value}. Use HH:MM format (e.g. 08:00)."
                profile["digest_time"] = value
                self._save_career_profile(profile)
                return True, f"Digest time set to {value}."
            return False, f"Unknown digest field: {field}. Use cadence or time."

        if parts[0] == "match" and len(parts) >= 2:
            if parts[1] == "score":
                try:
                    score = float(value)
                except ValueError:
                    return False, f"Invalid score: {value}. Use a float (e.g. 0.55)."
                if score < 0.0 or score > 1.0:
                    return False, "Match score must be between 0.0 and 1.0."
                profile["min_match_score"] = score
                self._save_career_profile(profile)
                return True, f"Minimum match score set to {score}."

        return False, f"Unknown preference path: {key_path}. Try salary.<region>, digest.cadence, digest.time, or match.score."

    def _load_skill_clusters(self) -> list[dict[str, Any]]:
        raw = _load_yaml("user_skills.yaml")
        clusters = raw.get("skills", {})
        result: list[dict[str, Any]] = []
        for cluster_name, body in clusters.items():
            skills_list = body.get("skills", [])
            weight = body.get("weight", 1.0)
            if skills_list:
                result.append({"name": cluster_name, "skills": skills_list, "weight": weight})
        return result

    # ── Skill vectors (cached) ────────────────────────────────

    async def _ensure_user_skill_vec(self, clusters: list[dict[str, Any]]) -> None:
        """Embed all skill clusters in a single batched call.

        Caches three things for reuse across the digest run:
          - self._user_skill_vec: weighted-average vector (legacy / logging).
          - self._cluster_vecs: one vector per cluster (for weighted-max scoring).
          - self._cluster_weights + self._cluster_names: parallel metadata.

        One embedding call produces all 14 cluster embeddings. We keep the
        weighted-average around for the LLM judge context but do the actual
        ranking with per-cluster weighted max — averaging 14 clusters across
        1024 dimensions washes out strong single-cluster matches (e.g. an
        Anthropic 'Research Engineer, Agents' posting lands at 0.41 with
        avg but 0.38 × 1.5 = 0.576 with weighted max over agent_systems).
        """
        if self._user_skill_vec is not None:
            return
        all_texts: list[str] = []
        all_weights: list[float] = []
        for c in clusters:
            txt = " ".join(c["skills"])
            w = c.get("weight", 1.0)
            all_texts.append(txt)
            all_weights.append(w)
            self._all_skill_tokens.extend(c["skills"])
            self._cluster_names.append(c["name"])
            self._cluster_weights.append(w)

        if not all_texts:
            self._user_skill_vec = [0.0]
            return

        embeds = await self._embed(self.ctx, EmbedInput(texts=all_texts))
        n = len(embeds.embeddings)
        if n == 0:
            self._user_skill_vec = [0.0]
            return
        self._cluster_vecs = [list(v) for v in embeds.embeddings]
        dim = len(embeds.embeddings[0])
        total_weight = sum(all_weights)
        weighted = [0.0] * dim
        for i in range(n):
            w = all_weights[i] / total_weight if total_weight > 0 else 1.0 / n
            vec = embeds.embeddings[i]
            for d in range(dim):
                weighted[d] += vec[d] * w
        self._user_skill_vec = weighted

    async def _embed_posting(self, title: str, desc: str) -> list[float]:
        text = f"{title} {desc}"[:2000]
        embeds = await self._embed(self.ctx, EmbedInput(texts=[text]))
        return embeds.embeddings[0] if embeds.embeddings else [0.0]

    # ── Tier fetchers ─────────────────────────────────────────

    async def _fetch_ats(self, company: dict[str, Any]) -> list[dict[str, Any]]:
        ats = company["ats"]
        cid = company["ats_company_id"]
        org = company["name"]
        from backbone.tools.jobs import FetchATSInput, FetchATSTool
        tool = FetchATSTool()
        out = await tool(self.ctx, FetchATSInput(ats=ats, company_id=cid, organization=org))
        return [p.model_dump() for p in out.postings]

    async def _fetch_careers(self, company: dict[str, Any]) -> list[dict[str, Any]]:
        url = company.get("careers_url", "")
        org = company["name"]
        if not url:
            return []
        from backbone.tools.jobs import FetchCareersPageInput, FetchCareersPageTool
        tool = FetchCareersPageTool()
        out = await tool(self.ctx, FetchCareersPageInput(careers_url=url, organization=org))
        return [p.model_dump() for p in out.postings]

    async def _fetch_tavily(self, company: dict[str, Any]) -> list[dict[str, Any]]:
        query = company.get("tavily_query", f"{company['name']} careers jobs")
        org = company["name"]
        from backbone.tools.jobs import FetchViaTavilyInput, FetchViaTavilyTool
        tool = FetchViaTavilyTool()
        out = await tool(self.ctx, FetchViaTavilyInput(tavily_query=query, organization=org))
        return [p.model_dump() for p in out.postings]

    # ── Score + annotate ──────────────────────────────────────

    async def _score_all(
        self,
        postings: list[dict[str, Any]],
        skills: list[dict[str, Any]],
        profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Score every posting with per-cluster weighted max.

        Embedding short cluster text ("multi-agent architectures task routing...")
        against long job postings produces cosines compressed to 0.20-0.45.
        Averaging across 14 clusters compresses the signal further. The right
        IR-flavored operation is **per-cluster max then weighted by cluster
        importance**: an Anthropic 'Research Engineer, Agents' posting matches
        the agent_systems cluster at 0.38, weighted by importance 1.5 → 0.576
        — clearly above threshold. A posting with no cluster signal (HR,
        Marketing, DevRel) maxes at < 0.30 unweighted → stays below.

        We also batch all posting embeddings in a single Voyage call (N→1)
        so 253 postings from EU cost one network round trip, not 253.
        """
        if not self._cluster_vecs:
            return postings

        # Batch embed all postings in one API call. If a single batch is
        # larger than Voyage's 128-input cap, chunk it.
        texts = [(p.get("title", "") + " " + (p.get("description", "") or ""))[:2000] for p in postings]
        posting_vecs: list[list[float]] = []
        BATCH = 96  # Voyage max batch is 128; we leave headroom.
        for off in range(0, len(texts), BATCH):
            chunk = texts[off:off + BATCH]
            embeds = await self._embed(self.ctx, EmbedInput(texts=chunk))
            posting_vecs.extend([list(v) for v in (embeds.embeddings or [])])

        scored: list[dict[str, Any]] = []
        for i, p in enumerate(postings):
            pvec = posting_vecs[i] if i < len(posting_vecs) else [0.0]
            vec_score, top_cluster = self._weighted_max_score(pvec)
            p["_top_cluster"] = top_cluster
            p["_vec_score_raw"] = vec_score
            scored.append(p)

        # Gray-band LLM judge — the bottleneck. Parallelize with a
        # semaphore to bound DeepSeek load (matches Paper Tracker's
        # discover-verify pattern). The judge mixes the LLM verdict
        # with the vector score: 0.6 vec / 0.4 llm.
        gray_band_postings = [p for p in scored if GRAY_BAND_LOW <= p["_vec_score_raw"] <= GRAY_BAND_HIGH]
        if gray_band_postings:
            sem = asyncio.Semaphore(5)
            async def _judge_one(p: dict[str, Any]) -> None:
                async with sem:
                    llm_conf = await self._llm_judge_score(p, skills)
                    p["_score_raw"] = round(0.6 * p["_vec_score_raw"] + 0.4 * llm_conf, 2)
            await asyncio.gather(*[_judge_one(p) for p in gray_band_postings])
        # Apply the final score for non-gray-band postings (vector score as-is).
        for p in scored:
            if "_score_raw" not in p:
                p["_score_raw"] = round(p["_vec_score_raw"], 2)
        return scored

    def _weighted_max_score(self, posting_vec: list[float]) -> tuple[float, str]:
        """Per-cluster weighted max — no LLM call, no async I/O.

        Returns (score, top_cluster_name).
        """
        best_score = 0.0
        best_cluster = ""
        for cname, cvec, cweight in zip(
            self._cluster_names, self._cluster_vecs, self._cluster_weights, strict=True
        ):
            sim = _cosine(cvec, posting_vec)
            weighted = sim * cweight
            if weighted > best_score:
                best_score = weighted
                best_cluster = cname
        return best_score, best_cluster

    async def _vector_score(self, posting: dict[str, Any]) -> float:
        """Back-compat single-posting scorer used by /prof-style flow if ever called."""
        if not self._cluster_vecs:
            return 0.0
        title = posting.get("title", "")
        desc = posting.get("description", "") or ""
        posting_vec = await self._embed_posting(title, desc)
        score, _ = self._weighted_max_score(posting_vec)
        return score

    async def _llm_judge_score(
        self, posting: dict[str, Any], skills: list[dict[str, Any]]
    ) -> float:
        template = load_prompt("job_hunter", "match_judge")
        rendered, _ = render(
            template,
            {
                "posting_title": posting.get("title", ""),
                "posting_description": (posting.get("description", "") or "")[:2000],
                "user_skills": ", ".join(self._all_skill_tokens),
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
            return 0.5  # neutral fallback
        if not raw:
            return 0.5
        from backbone.model_client import parse_loose_json
        parsed = parse_loose_json(raw)
        if isinstance(parsed, dict):
            conf = parsed.get("confidence")
            if isinstance(conf, (int, float)):
                return float(conf)
        return 0.5

    # -- LLM enrichment (role type + visa) --

    async def _enrich_roles(self, postings: list[dict[str, Any]]) -> None:
        """Classify role_type via LLM for postings with unknown role_type."""
        unknown = [p for p in postings if p.get("role_type") in ("unknown", None, "")]
        if not unknown:
            return
        sem = asyncio.Semaphore(4)
        async def _classify_one(p: dict[str, Any]) -> None:
            async with sem:
                try:
                    p["role_type"] = await self._llm_classify_role(p)
                except Exception:
                    pass
        await asyncio.gather(*[_classify_one(p) for p in unknown])

    async def _enrich_visas(
        self, postings: list[dict[str, Any]], profile: dict[str, Any]
    ) -> None:
        """Classify visa sponsorship signal via LLM for postings that need it."""
        visa_requirement = profile.get("visa_requirement", "")
        if visa_requirement != "need_sponsorship":
            return
        needs_visa = [
            p for p in postings
            if p.get("_region", "") in ("eu", "canada", "international_remote")
        ]
        if not needs_visa:
            return
        sem = asyncio.Semaphore(4)
        async def _classify_one(p: dict[str, Any]) -> None:
            async with sem:
                try:
                    p["visa_status"] = await self._llm_classify_visa(p, profile)
                except Exception:
                    p["visa_status"] = "unknown"
        await asyncio.gather(*[_classify_one(p) for p in needs_visa])

    async def _llm_classify_role(self, posting: dict[str, Any]) -> str:
        """LLM-based role type classifier."""
        template = load_prompt("job_hunter", "role_classify")
        title = posting.get("title", "")[:200]
        desc = (posting.get("description", "") or "")[:500]
        rendered, _ = render(
            template,
            {"posting_title": title, "posting_snippet": desc},
        )
        try:
            raw = await self._llm.generate(
                model=template.model.name,
                prompt=rendered,
                temperature=template.model.temperature,
                max_tokens=template.model.max_tokens,
            )
        except Exception:
            return "unknown"
        if not raw:
            return "unknown"
        from backbone.model_client import parse_loose_json
        parsed = parse_loose_json(raw)
        if isinstance(parsed, dict):
            return str(parsed.get("role_type", "unknown"))
        return "unknown"

    async def _llm_classify_visa(
        self, posting: dict[str, Any], profile: dict[str, Any]
    ) -> str:
        """LLM-based visa signal classifier."""
        template = load_prompt("job_hunter", "visa_classify")
        desc = (posting.get("description", "") or "")[:1500]
        rendered, _ = render(
            template,
            {
                "posting_text": desc,
                "user_visa_requirement": profile.get("visa_requirement", ""),
                "target_region": posting.get("_region", ""),
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
            return "unknown"
        if not raw:
            return "unknown"
        from backbone.model_client import parse_loose_json
        parsed = parse_loose_json(raw)
        if isinstance(parsed, dict):
            return str(parsed.get("visa_status", "unknown"))
        return "unknown"

    async def _enrich_remote(
        self, postings: list[dict[str, Any]]
    ) -> None:
        """Classify international_remote postings as actually Africa-friendly.

        Most "remote" AI jobs mean "remote in US/EU" -- not accessible to a
        Nigerian developer on UTC+1. This prompt checks for Deel/Remote.com
        payment infrastructure, EMEA timezone mentions, and visa-free language
        to surface the ~5% of remote postings Aaliyah can actually get.
        """
        remote_postings = [
            p for p in postings
            if p.get("_region") == "international_remote"
            and "_africa_ok" not in p
        ]
        if not remote_postings:
            return
        sem = asyncio.Semaphore(4)
        async def _classify_one(p: dict[str, Any]) -> None:
            async with sem:
                try:
                    ok, reason = await self._llm_classify_remote(p)
                    p["_africa_ok"] = ok
                    p["_africa_reason"] = reason
                except Exception:
                    p["_africa_ok"] = None
        await asyncio.gather(*[_classify_one(p) for p in remote_postings])

    async def _llm_classify_remote(
        self, posting: dict[str, Any]
    ) -> tuple[bool | None, str]:
        """LLM-based Africa-friendly remote classifier."""
        template = load_prompt("job_hunter", "remote_classify")
        desc = (posting.get("description", "") or "")[:1500]
        rendered, _ = render(
            template,
            {
                "posting_title": posting.get("title", ""),
                "posting_description": desc,
                "organization": posting.get("_organization", ""),
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
            return None, ""
        if not raw:
            return None, ""
        from backbone.model_client import parse_loose_json
        parsed = parse_loose_json(raw)
        if isinstance(parsed, dict):
            return parsed.get("africa_ok"), str(parsed.get("reason", ""))
        return None, ""

    # Known salary ranges for Big Tech Lagos offices and well-documented
    # African payers. Sourced from Glassdoor, Levels.fyi, and community data.
    # These are used only when the posting itself does not list salary.
    _KNOWN_SALARY: dict[str, dict[str, str]] = {
        # (org_name_lower, region) -> "range / period"
        ("google", "nigeria"): "$80-150k USD / year",
        ("google lagos", "nigeria"): "$80-150k USD / year",
        ("microsoft", "nigeria"): "$70-130k USD / year",
        ("microsoft lagos", "nigeria"): "$70-130k USD / year",
        ("aws", "nigeria"): "$80-140k USD / year",
        ("aws lagos", "nigeria"): "$80-140k USD / year",
        ("amazon", "nigeria"): "$80-140k USD / year",
        ("flutterwave", "nigeria"): "$120-180k USD / year",
        ("paystack", "nigeria"): "$100-150k USD / year",
        ("moniepoint", "nigeria"): "$90-140k USD / year",
        ("instadeep", "nigeria"): "$120-180k USD / year",
        ("kuda", "nigeria"): "$80-130k USD / year",
        ("interswitch", "nigeria"): "$70-110k USD / year",
        ("chipper cash", "nigeria"): "$130-180k USD / year",
        ("gitlab", "international_remote"): "$130-180k USD / year",
        ("stripe", "international_remote"): "$180-220k USD / year",
        ("vercel", "international_remote"): "$150-200k USD / year",
        ("anthropic", "international_remote"): "$180-250k USD / year",
        ("deepmind", "international_remote"): "$150-200k USD / year",
        ("borealis ai", "canada"): "CAD 80-120k / year",
        ("shopify", "canada"): "CAD 100-140k / year",
    }

    def _annotate_salary(self, posting: dict[str, Any], profile: dict[str, Any]) -> str:
        region = posting.get("_region", "nigeria")
        floor = (profile.get("salary_floor") or {}).get(region, 0)
        currency = (profile.get("salary_currency") or {}).get(region, "NGN")
        period = (profile.get("salary_period") or {}).get(region, "monthly")
        mode = profile.get("salary_filter_mode", "flag")

        # If the posting itself has salary fields, use them.
        salary_max = posting.get("salary_max")
        salary_min = posting.get("salary_min")
        salary_cur = posting.get("salary_currency") or currency

        if salary_max is not None and salary_max < floor and mode == "flag":
            return f"Below your {_format_money(floor, currency)}/{period} floor"
        if salary_min is not None and salary_min >= floor:
            return f"{_format_money(salary_min, salary_cur)}+/{period}"
        if salary_max is not None:
            return f"{_format_money(salary_max, salary_cur)}/{period}"

        # Fall back to known salary ranges for well-documented payers.
        org = (posting.get("_organization", "") or "").lower().strip()
        key = (org, region)
        if key in self._KNOWN_SALARY:
            return f"~{self._KNOWN_SALARY[key]} (est.)"
        return ""

    def _annotate_visa(self, posting: dict[str, Any], profile: dict[str, Any]) -> str:
        region = posting.get("_region", "nigeria")
        if region in ("nigeria", "africa"):
            return ""
        visa_requirement = profile.get("visa_requirement", "")
        if visa_requirement != "need_sponsorship":
            return ""
        visa_status = posting.get("visa_status") or "unknown"
        if visa_status == "yes":
            return "Sponsorship available"
        if visa_status == "no":
            return "No sponsorship"
        return "Sponsorship unknown"

    def _format_match(self, posting: dict[str, Any]) -> str:
        score = posting.get("_score_raw", 0.0)
        if score >= 0.75:
            return f"Match: {int(score * 100)}%  (High)"
        if score >= DEFAULT_MIN_MATCH_SCORE:
            return f"Match: {int(score * 100)}%"
        return f"Match: {int(score * 100)}%  (Borderline)"


def _format_money(amount: int, currency: str) -> str:
    if currency == "NGN":
        return f"N{amount // 1000}k"
    if currency == "USD":
        return f"${amount // 1000}k"
    if currency == "EUR":
        return f"E{amount // 1000}k"
    if currency == "CAD":
        return f"CAD {amount // 1000}k"
    return f"{amount} {currency}"


def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)