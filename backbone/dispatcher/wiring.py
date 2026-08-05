"""Wiring — connects the Dispatcher to agent handlers for v0.1.

Import this module to register all Paper Tracker commands + callbacks
with the central Dispatcher.
"""

from __future__ import annotations

import structlog

from backbone.dispatcher.dispatcher import Dispatcher
from backbone.dispatcher.task import Task, TaskResult

logger = structlog.get_logger("wiring")


def _clean_text(text: str) -> str:
    """Strip emoji, control chars, and trailing UI chrome from text for clean output."""
    import re

    if not text:
        return ""
    # Remove common emoji ranges
    text = re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
        r"\U00002702-\U000027B0\U0000FE0F\u200d]",
        "",
        text,
    )
    # Strip trailing OpenReview/ResearchGate UI chrome.
    for cut_marker in [
        " Joined.",
        " Joined ",
        " Names |",
        " Skip slid",
        " Names",
    ]:
        idx = text.find(cut_marker.strip())
        if idx > 0:
            text = text[:idx]
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _shorten_url(url: str) -> str:
    """Trim a long URL to a readable profile link."""
    if not url:
        return ""
    # Drop tracker query strings like utm_*, fbclid, etc. but keep
    # meaningful parameters like OpenReview's profile id.
    try:
        from urllib.parse import parse_qs, urlsplit, urlunsplit

        parsed = urlsplit(url)
        keep = {
            k: v
            for k, v in parse_qs(parsed.query).items()
            if k in ("id", "user", "profile", "doi")
        }
        if keep:
            new_query = "&".join(f"{k}={v[0]}" for k, v in keep.items())
            cleaned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, ""))
        else:
            cleaned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except Exception:
        cleaned = url
    return cleaned[:140]


def _line(value: str) -> str:
    """Render a line only if non-empty after cleaning."""
    clean = _clean_text(value)
    return clean[:200] if clean else ""


def wire_paper_tracker(dispatcher: Dispatcher) -> None:
    """Register all Paper Tracker commands with the given dispatcher."""
    from agents.paper_tracker.agent import PaperTrackerAgent
    from backbone.tools.base import ToolContext
    from career_copilot.config import get_settings

    def _make_ctx() -> ToolContext:
        return ToolContext(agent="paper_tracker", task_id="", settings=get_settings())

    # ── Command handlers ──

    async def handle_digest(task: Task) -> TaskResult:
        import traceback as _tb
        agent = PaperTrackerAgent(task_ctx=_make_ctx())
        args = task.payload.get("args", [])
        sub = args[0] if args else "now"

        if sub == "now":
            logger.info("handle_digest_start")
            try:
                result = await agent.run_digest("daily")
            except Exception as e:
                tb = _tb.format_exc()[-500:]
                logger.exception("handle_digest_error")
                return TaskResult(task_id=task.id, success=False, error=f"{type(e).__name__}: {e}\n\n{tb}")
            await agent.send_to_telegram(result, task.payload.get("user_id", ""))
            items = len(result.interest_items) + len(result.professor_items)
            logger.info("handle_digest_done", items=items)
            return TaskResult(task_id=task.id, success=True, output=f"Digest sent ({items} papers).")

        if sub in ("on", "off", "at"):
            from backbone.db.session import async_session_factory
            from backbone.tools.scheduler import ScheduleInput, ScheduleTool

            factory = async_session_factory()
            if sub == "on":
                tool = ScheduleTool()
                await tool(_make_ctx(), ScheduleInput(
                    job_name="paper_tracker_digest",
                    cron_expression="0 9 * * *",
                    payload={"command": "digest", "args": ["now"]},
                ))
                return TaskResult(task_id=task.id, success=True, output="Daily digest enabled at 09:00.")

            elif sub == "off":
                from sqlalchemy import text
                async with factory() as session:
                    await session.execute(
                        text("DELETE FROM scheduled_jobs WHERE job_name = 'paper_tracker_digest'")
                    )
                    await session.commit()
                return TaskResult(task_id=task.id, success=True, output="Daily digest disabled.")

            elif sub == "at":
                hhmm = args[1] if len(args) > 1 else "09:00"
                tool = ScheduleTool()
                # Parse HH:MM to cron "0 HH * * *" (minute zero of that hour every day).
                await tool(_make_ctx(), ScheduleInput(
                    job_name="paper_tracker_digest",
                    cron_expression=f"0 {hhmm.split(':')[0]} * * *",
                    payload={"command": "digest", "args": ["now"]},
                ))
                return TaskResult(task_id=task.id, success=True, output=f"Daily digest set to {hhmm}.")

        return TaskResult(task_id=task.id, success=True, output=f"Unknown digest sub-command: {sub}")

    async def handle_discover(task: Task) -> TaskResult:
        logger.info("handle_discover_start")
        agent = PaperTrackerAgent(task_ctx=_make_ctx())
        candidates = await agent.run_discover()
        logger.info("handle_discover_done", candidates=len(candidates))
        if not candidates:
            return TaskResult(
                task_id=task.id,
                success=True,
                output="No professors found. Try broadening interests.",
            )

        lines = ["Professor Discovery", ""]
        for i, c in enumerate(candidates[:10], 1):
            name = c.get("name", "Unknown")
            position = _clean_text(c.get("position", ""))
            affiliation = _clean_text(c.get("university", "") or c.get("affiliation", ""))[:140]
            department = _clean_text(c.get("department", ""))[:120]
            focus = _clean_text(c.get("focus", ""))
            cit = c.get("citations", 0)
            h_index = c.get("h_index", "?")
            sim = c.get("similarity", "?")
            papers = c.get("papers_count", 0)
            homepage = _clean_text(c.get("homepage", ""))[:140]
            co_workers = c.get("co_workers", []) or []

            loc_parts = [p for p in [position, affiliation, department] if p]
            loc = " | ".join(loc_parts) if loc_parts else "Affiliation unknown"
            country = _clean_text(c.get("country", ""))
            region = _clean_text(c.get("region", ""))

            entry = [f"{i}. {_line(name) or 'Unknown'}", f"   {loc}"]
            if country:
                entry.append(f"   Location: {country}")
            if focus:
                entry.append(f"   Focus: {focus}")
            entry.append(
                f"   {papers} papers | {cit} citations" f" | h={h_index} | match {sim}"
            )
            if homepage:
                entry.append(f"   {_shorten_url(homepage)}")
            if co_workers:
                entry.append(f"   Co-researchers: {', '.join(co_workers)}")
            lines.append("\n".join(entry))
        return TaskResult(task_id=task.id, success=True, output="\n".join(lines))

    async def handle_watch(task: Task) -> TaskResult:
        agent = PaperTrackerAgent(task_ctx=_make_ctx())
        args = task.payload.get("args", [])
        sub = args[0] if args else "list"
        logger.info("handle_watch", sub=sub)

        if sub == "add":
            name = " ".join(args[1:]) if len(args) > 1 else ""
            if not name:
                return TaskResult(task_id=task.id, success=False, error="Usage: /watch add <name>")
            info = await agent.watch_add(name)
            suffix = " (already in list)" if info.get("duplicate") else ""
            aff = _clean_text(info.get("affiliation", "Unknown"))[:100]
            return TaskResult(
                task_id=task.id,
                success=True,
                output=f"Added {info['name']} ({aff}){suffix}",
            )
        elif sub == "remove":
            name = " ".join(args[1:]) if len(args) > 1 else ""
            ok = await agent.watch_remove(name)
            return TaskResult(
                task_id=task.id, success=ok, output="Removed" if ok else "Not found"
            )
        else:
            # list
            profs = await agent.watch_list()
            if not profs:
                return TaskResult(
                    task_id=task.id,
                    success=True,
                    output="Your watchlist is empty. Use /discover to find professors.",
                )
            lines = ["Watchlist", ""]
            for p in profs:
                name = _clean_text(p.get("name", ""))
                if not name:
                    continue
                aff = _clean_text(p.get("affiliation", ""))[:140]
                homepage = _clean_text(p.get("homepage", ""))[:140]
                parts = [name]
                if aff:
                    parts.append(aff)
                if homepage and homepage != aff:
                    parts.append(homepage)
                lines.append("  • " + " | ".join(parts))
            return TaskResult(task_id=task.id, success=True, output="\n".join(lines))

    async def handle_prof(task: Task) -> TaskResult:
        """Handle /prof — collect structured data, enqueue to Celery for LLM generation."""
        args = task.payload.get("args", [])
        name = " ".join(args) if args else ""
        if not name:
            return TaskResult(task_id=task.id, success=False, error="Usage: /prof <name>")

        # Collect structured data first (fast — DB + arXiv + embeddings)
        agent = PaperTrackerAgent(task_ctx=_make_ctx())
        data = await agent.build_prof_brief_data(name)

        if "error" in data:
            return TaskResult(task_id=task.id, success=False, error=data["error"])

        # Fire-and-forget to Celery: LLM generates direction/overlap/email sections
        try:
            from career_copilot.queue import generate_professor_brief

            generate_professor_brief.delay(
                prof_name=data["prof_name"],
                affiliation=data["affiliation"],
                recent_papers=data["recent_papers"],
                user_interests=data["user_interests"],
                homepage=data.get("homepage", ""),
                overlap_score=data.get("overlap_score", 0.0),
                chat_id=task.payload.get("user_id", ""),
            )
            logger.info("brief_enqueued", name=data["prof_name"])
            return TaskResult(
                task_id=task.id,
                success=True,
                output=(
                    f"Researching {data['prof_name']} — "
                    "I'll send you the brief when it's ready."
                ),
            )
        except Exception as exc:
            logger.exception("brief_enqueue_failed")
            return TaskResult(
                task_id=task.id,
                success=False,
                error=f"Could not enqueue brief: {exc}",
            )

    async def handle_interests(task: Task) -> TaskResult:
        agent = PaperTrackerAgent(task_ctx=_make_ctx())
        try:
            essay = await agent._get_user_interests()
            keywords = await agent._get_user_keywords()
        except Exception as exc:
            logger.warning("interests_load_failed", error=str(exc))
            return TaskResult(
                task_id=task.id, success=False, error=f"Could not load interests: {exc}"
            )
        kw_clean = _clean_text(keywords)
        essay_clean = _clean_text(essay)
        lines = ["Research interests"]
        if kw_clean:
            lines.append(f"Keywords: {kw_clean}")
        if essay_clean:
            lines.append("")
            lines.append(essay_clean[:1200])
        return TaskResult(task_id=task.id, success=True, output="\n".join(lines))

    async def handle_export(task: Task) -> TaskResult:
        return TaskResult(task_id=task.id, success=True, output="Zotero export coming in v0.2.")

    # ── Callback handlers (inline buttons) ──

    async def handle_read(task: Task) -> TaskResult:
        agent = PaperTrackerAgent(task_ctx=_make_ctx())
        item_id = task.payload.get("item_id", "")
        await agent.handle_feedback(item_id, "read")
        return TaskResult(task_id=task.id, success=True, output="📖 Marked as read")

    async def handle_save(task: Task) -> TaskResult:
        agent = PaperTrackerAgent(task_ctx=_make_ctx())
        item_id = task.payload.get("item_id", "")
        await agent.handle_feedback(item_id, "save")
        # Save to Notion with full metadata if configured
        try:
            from backbone.tools.notion import CreatePageInput, CreatePageTool
            from career_copilot.config import get_settings
            from backbone.tools.arxiv import FetchByIdInput, FetchByIdTool
            settings = get_settings()
            if settings.notion_api_key and settings.notion_papers_db_id:
                # Fetch paper metadata from arXiv
                arxiv = FetchByIdTool()
                try:
                    arxiv_out = await arxiv(_make_ctx(), FetchByIdInput(arxiv_id=item_id))
                    paper = arxiv_out.paper
                except Exception:
                    paper = None
                title_text = paper.title if paper else item_id
                authors_text = ', '.join(paper.authors[:5]) if paper else 'Unknown'
                notion = CreatePageTool()
                await notion(_make_ctx(), CreatePageInput(
                    database_id=settings.notion_papers_db_id,
                    properties={
                        "Title": {"title": [{"text": {"content": title_text[:2000]}}]},
                        "Authors": {"rich_text": [{"text": {"content": authors_text[:2000]}}]},
                        "ArXiv ID": {"rich_text": [{"text": {"content": item_id[:200]}}]},
                        "Year": {"number": paper.published.year if paper else None},
                        "Status": {"status": {"name": "To read"}},
                        "Saved from": {"select": {"name": "Paper Tracker"}},
                    },
                ))
                return TaskResult(task_id=task.id, success=True, output="💾 Saved to Notion")
        except Exception:
            pass
        return TaskResult(task_id=task.id, success=True, output="💾 Saved")

    async def handle_skip(task: Task) -> TaskResult:
        agent = PaperTrackerAgent(task_ctx=_make_ctx())
        item_id = task.payload.get("item_id", "")
        await agent.handle_feedback(item_id, "skip")
        return TaskResult(task_id=task.id, success=True, output="⏭️ Skipped")

    async def handle_brief(task: Task) -> TaskResult:
        """Handle 📋 Brief button — triggers same flow as /prof."""
        professor_name = task.payload.get("professor", "")
        item_id = task.payload.get("item_id", "")

        if not professor_name:
            return TaskResult(
                task_id=task.id,
                success=False,
                error="No professor name in callback data. Run /prof <name> directly.",
            )

        # Same flow as /prof — collect data, enqueue to Celery
        agent = PaperTrackerAgent(task_ctx=_make_ctx())
        data = await agent.build_prof_brief_data(professor_name)

        if "error" in data:
            return TaskResult(
                task_id=task.id,
                success=False,
                error=data["error"],
            )

        try:
            from career_copilot.queue import generate_professor_brief

            generate_professor_brief.delay(
                prof_name=data["prof_name"],
                affiliation=data["affiliation"],
                recent_papers=data["recent_papers"],
                user_interests=data["user_interests"],
                homepage=data.get("homepage", ""),
                overlap_score=data.get("overlap_score", 0.0),
                chat_id=task.payload.get("user_id", ""),
            )
            logger.info("brief_enqueued_from_button", name=data["prof_name"])
            return TaskResult(
                task_id=task.id,
                success=True,
                output=f"Researching {data['prof_name']} — brief incoming.",
            )
        except Exception as exc:
            logger.exception("brief_enqueue_failed")
            return TaskResult(
                task_id=task.id,
                success=False,
                error=f"Could not enqueue brief: {exc}",
            )

    # Register all
    dispatcher.register_command("digest", "paper_tracker", handle_digest)
    dispatcher.register_command("watch", "paper_tracker", handle_watch)
    dispatcher.register_command("discover", "paper_tracker", handle_discover)
    dispatcher.register_command("prof", "paper_tracker", handle_prof)
    dispatcher.register_command("interests", "paper_tracker", handle_interests)
    dispatcher.register_command("export", "paper_tracker", handle_export)
    dispatcher.register_command("read", "paper_tracker", handle_read)
    dispatcher.register_command("save", "paper_tracker", handle_save)
    dispatcher.register_command("skip", "paper_tracker", handle_skip)
    dispatcher.register_command("brief", "paper_tracker", handle_brief)

    logger.info("paper_tracker_wired")

def wire_job_hunter(dispatcher: Dispatcher) -> None:
    """Register all Job Hunter commands with the given dispatcher."""
    from agents.job_hunter.agent import JobHunterAgent
    from backbone.tools.base import ToolContext
    from career_copilot.config import get_settings

    def _make_ctx() -> ToolContext:
        return ToolContext(agent="job_hunter", task_id="", settings=get_settings())

    # -- Command handlers --

    async def handle_jobs(task: Task) -> TaskResult:
        args = task.payload.get("args", [])
        region = args[0] if args else None
        agent = JobHunterAgent(task_ctx=_make_ctx())
        logger.info("jh_jobs_start", region=region)
        try:
            items = await agent.run_discovery(region=region)
        except Exception as exc:
            logger.exception("jh_jobs_failed")
            return TaskResult(task_id=task.id, success=False, error=str(exc))
        if not items:
            region_text = f" in {region}" if region else ""
            return TaskResult(
                task_id=task.id,
                success=True,
                output=f"No new postings found{region_text}. Check back in 3 days.",
            )
        await agent.send_digest(items, task.payload.get("user_id", ""))
        # Detect cross-region fallback: if the user requested a specific region
        # but the top result is from a different region, flag it.
        top_region = items[0].get("_region", "") if items else ""
        if region and top_region and top_region != region:
            logger.info("jh_jobs_done_fallback", shown=len(items), from_region=region, to=top_region)
            return TaskResult(
                task_id=task.id, success=True,
                output=f"No matches in {region.title()} — showing {top_region.replace('_',' ').title()} instead. Sent {len(items)} postings."
            )
        logger.info("jh_jobs_done", shown=len(items))
        return TaskResult(task_id=task.id, success=True, output=f"Sent {len(items)} postings.")

    async def handle_companies(task: Task) -> TaskResult:
        args = task.payload.get("args", [])
        sub = args[0] if args else "list"
        agent = JobHunterAgent(task_ctx=_make_ctx())
        companies = agent._load_watchlist()

        if sub == "add":
            if len(args) < 3:
                return TaskResult(task_id=task.id, success=False,
                    error="Usage: /companies add <name> <region>\nRegions: nigeria, africa, eu, canada, international_remote")
            name = " ".join(args[1:-1]) if len(args) > 2 else args[1]
            region = args[-1]
            ok, msg = agent.add_company_to_watchlist(name, region)
            return TaskResult(task_id=task.id, success=ok, output=msg)

        if sub == "remove":
            if len(args) < 2:
                return TaskResult(task_id=task.id, success=False,
                    error="Usage: /companies remove <name>")
            name = " ".join(args[1:])
            ok, msg = agent.remove_company_from_watchlist(name)
            return TaskResult(task_id=task.id, success=ok, output=msg)

        if sub == "region":
            region = args[1] if len(args) > 1 else None
            if not region:
                return TaskResult(task_id=task.id, success=False,
                    error="Usage: /companies region <nigeria|africa|eu|canada|international_remote>")
            companies = [c for c in companies if c.get("region") == region]
            lines = [f"Company watchlist — {region}", ""]
            for c in companies[:50]:
                tier = c.get("source_tier", "?")
                lines.append(f"  - {c['name']}  tier={tier}")
            return TaskResult(task_id=task.id, success=True, output="\n".join(lines))

        if sub == "list":
            region = args[1] if len(args) > 1 else None
            if region:
                companies = [c for c in companies if c.get("region") == region]
            lines = ["Company watchlist", ""]
            for c in companies[:50]:
                tier = c.get("source_tier", "?")
                lines.append(f"  - {c['name']}  [{c.get('region')}] tier={tier}")
            return TaskResult(task_id=task.id, success=True, output="\n".join(lines))
        return TaskResult(task_id=task.id, success=True, output=f"Companies sub-command: {sub}")

    async def handle_saved_jobs(task: Task) -> TaskResult:
        agent = JobHunterAgent(task_ctx=_make_ctx())
        rows = await agent.get_saved_postings()
        if not rows:
            return TaskResult(task_id=task.id, success=True, output="No saved postings yet. Use the [Save] button in a /jobs digest to save one.")
        lines = ["Saved postings", ""]
        for r in rows:
            region = r.get("region", "")
            org = r.get("organization", "?")
            title = r.get("title", "")[:120]
            lines.append(f"  {region} | {org}")
            lines.append(f"  {title}")
            lines.append("")
        return TaskResult(task_id=task.id, success=True, output="\n".join(lines))

    async def handle_jh_prefs(task: Task) -> TaskResult:
        args = task.payload.get("args", [])
        agent = JobHunterAgent(task_ctx=_make_ctx())

        # /prefs set <key> <value>
        if args and args[0] == "set":
            if len(args) < 3:
                return TaskResult(
                    task_id=task.id, success=False,
                    error="Usage: /prefs set <key> <value>. Try salary.canada, digest.cadence, digest.time, or match.score."
                )
            key_path = args[1]
            value = args[2]
            ok, msg = agent.set_preference(key_path, value)
            return TaskResult(task_id=task.id, success=ok, output=msg)

        # /prefs (no args) -> show current
        profile = agent._load_career_profile()
        regions = profile.get("target_regions", {})
        salary_floors = profile.get("salary_floor", {})
        lines = ["Career preferences", ""]
        lines.append(f"Regions: primary={regions.get('primary')} | secondary={regions.get('secondary')} | future={regions.get('future_relocation')}")
        lines.append(f"Role types: {profile.get('target_role_types', [])}")
        lines.append(f"Min match score: {profile.get('min_match_score', 0.55)}")
        for r, floor in (salary_floors or {}).items():
            cur = profile.get('salary_currency', {}).get(r, 'N/A')
            period = profile.get('salary_period', {}).get(r, 'N/A')
            lines.append(f"  {r}: {floor} {cur}/{period}")
        lines.append(f"Digest every {profile.get('digest_frequency_days', 3)} days at {profile.get('digest_time', '08:00')}")
        lines.append("")
        lines.append("Set prefs: /prefs set salary.canada 120000")
        lines.append("           /prefs set digest.cadence 5")
        lines.append("           /prefs set digest.time 09:00")
        lines.append("           /prefs set match.score 0.60")
        return TaskResult(task_id=task.id, success=True, output="\n".join(lines))

    async def handle_jh_help(task: Task) -> TaskResult:
        help_text = (
            "Job Hunter\n"
            "\n"
            "/jobs [region]          Run discovery\n"
            "/jobs nigeria           Nigeria only\n"
            "/jobs canada            Canada only\n"
            "/job <URL or text>      Look up a single posting\n"
            "/research <company>     Research a company\n"
            "/companies [region]     List watchlist companies\n"
            "/companies add <n> <r>  Add a company (r=region)\n"
            "/companies remove <n>   Remove a company\n"
            "/companies region <r>   Show companies by region\n"
            "/saved                   View saved postings\n"
            "/prefs                   Show career preferences\n"
            "/prefs set <k> <v>      Update a preference\n"
            "  salary.<region>        Set salary floor\n"
            "  digest.cadence         Set digest frequency in days\n"
            "  digest.time            Set digest time (HH:MM)\n"
            "  match.score            Set minimum match score\n"
            "/help_jh                Show this message"
        )
        return TaskResult(task_id=task.id, success=True, output=help_text)


    async def handle_jh_save(task: Task) -> TaskResult:
        agent = JobHunterAgent(task_ctx=_make_ctx())
        external_id = task.payload.get("external_id", "")
        if not external_id:
            return TaskResult(task_id=task.id, success=False, error="Missing external_id")
        ok = await agent.mark_saved(external_id)
        if ok:
            return TaskResult(task_id=task.id, success=True, output="Saved")
        return TaskResult(task_id=task.id, success=False, error="Could not save posting")

    async def handle_jh_skip(task: Task) -> TaskResult:
        agent = JobHunterAgent(task_ctx=_make_ctx())
        external_id = task.payload.get("external_id", "")
        if not external_id:
            return TaskResult(task_id=task.id, success=False, error="Missing external_id")
        ok = await agent.mark_skipped(external_id)
        if ok:
            return TaskResult(task_id=task.id, success=True, output="Skipped")
        return TaskResult(task_id=task.id, success=False, error="Could not skip posting")

    async def handle_job_lookup(task: Task) -> TaskResult:
        args = task.payload.get("args", [])
        if not args:
            return TaskResult(task_id=task.id, success=False, error="Usage: /job <URL or pasted text>")
        url_or_text = " ".join(args)
        agent = JobHunterAgent(task_ctx=_make_ctx())
        result = await agent.lookup_single_posting(url_or_text)
        if result is None:
            return TaskResult(task_id=task.id, success=True,
                output="Could not parse a job posting from that URL or text. Try a direct link to the job posting page.")
        lines = [
            f"{result['title']}",
            f"{result['organization']}",
            f"Match: {int(result['_score_raw'] * 100)}%  (top cluster: {result.get('_top_cluster', '?')})",
        ]
        if result.get("location"):
            lines.append(f"Location: {result['location']}")
        if result.get("remote_ok"):
            lines.append("Remote: yes")
        if result.get("role_type") and result["role_type"] != "unknown":
            lines.append(f"Role type: {result['role_type']}")
        lines.append(f"Apply: {result['application_url']}")
        return TaskResult(task_id=task.id, success=True, output="\n".join(lines))

    async def handle_pre_research(task: Task) -> TaskResult:
        args = task.payload.get("args", [])
        if not args:
            return TaskResult(task_id=task.id, success=False, error="Usage: /pre-research <company name>")
        company_name = " ".join(args)
        agent = JobHunterAgent(task_ctx=_make_ctx())
        result = await agent.pre_research(company_name)
        return TaskResult(task_id=task.id, success=True, output=result)

    dispatcher.register_command("jh_save", "job_hunter", handle_jh_save)
    dispatcher.register_command("jh_skip", "job_hunter", handle_jh_skip)

    dispatcher.register_command("jobs", "job_hunter", handle_jobs)
    dispatcher.register_command("companies", "job_hunter", handle_companies)
    dispatcher.register_command("saved", "job_hunter", handle_saved_jobs)
    dispatcher.register_command("job", "job_hunter", handle_job_lookup)
    dispatcher.register_command("research", "job_hunter", handle_pre_research)
    dispatcher.register_command("prefs", "job_hunter", handle_jh_prefs)
    dispatcher.register_command("help_jh", "job_hunter", handle_jh_help)

    logger.info("job_hunter_wired")



def wire_contribution_finder(dispatcher: Dispatcher) -> None:
    """Register all Contribution Finder commands with the given dispatcher."""
    from agents.contribution_finder.agent import ContributionFinderAgent
    from backbone.tools.base import ToolContext
    from career_copilot.config import get_settings

    def _make_ctx() -> ToolContext:
        return ToolContext(agent="contribution_finder", task_id="", settings=get_settings())

    async def handle_contrib(task: Task) -> TaskResult:
        args = task.payload.get("args", [])
        topic = args[0] if args else None
        agent = ContributionFinderAgent(task_ctx=_make_ctx())
        if topic == "repos":
            repos = agent._load_tracked_repos()
            lines = ["Tracked repos", ""]
            for r in repos:
                lines.append(f"  {r['full_name']} [{r.get('topic_hint', '')}]")
            return TaskResult(task_id=task.id, success=True, output="\n".join(lines))
        try:
            opps = await agent.run_discovery(topic=topic)
            if not opps:
                return TaskResult(task_id=task.id, success=True,
                    output="No new opportunities found this week. Check back Sunday.")
            lines = [f"Found {len(opps)} opportunities", ""]
            for i, o in enumerate(opps[:10]):
                title = o.get("title", "")[:70]
                repo = o.get("repo_full_name", "")
                score = o.get("_impact_score", 0)
                eff = o.get("estimated_effort", "")
                lines.append(f"{i+1}. [{eff}] {title}")
                lines.append(f"    {repo} | score={score:.2f}")
                lines.append("")
            return TaskResult(task_id=task.id, success=True, output="\n".join(lines))
        except Exception as exc:
            logger.exception("cf_discovery_failed")
            return TaskResult(task_id=task.id, success=False, error=str(exc))

    async def handle_opportunity(task: Task) -> TaskResult:
        return TaskResult(task_id=task.id, success=True, output="Use /contrib to find opportunities.")

    dispatcher.register_command("contrib", "contribution_finder", handle_contrib)
    dispatcher.register_command("opportunity", "contribution_finder", handle_opportunity)

    logger.info("contribution_finder_wired")
