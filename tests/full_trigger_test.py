"""Comprehensive local trigger test — exercises every command.

Usage:
    uv run python tests/full_trigger_test.py
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from career_copilot.config import get_settings
from backbone.tools.base import ToolContext
from agents.paper_tracker.agent import PaperTrackerAgent


def _ctx(label: str) -> ToolContext:
    return ToolContext(
        agent="paper_tracker", task_id=f"full-test-{label}", settings=get_settings()
    )


PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

results: dict[str, bool] = {}


def record(name: str, ok: bool) -> None:
    results[name] = ok


# ── Interest loading ──


async def test_interests():
    """Load user interests from DB."""
    label = "interests"
    print(f"\n{'='*60}")
    print(f"TEST: load research interests")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        essay = await agent._get_user_interests()
        keywords = await agent._get_user_keywords()
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
        print(f"  {PASS} Essay: {len(essay)} chars")
        print(f"  {PASS} Keywords ({len(kw_list)}): {', '.join(kw_list[:5])}")
        record(label, True)
    except Exception as exc:
        print(f"  {FAIL} {exc}")
        traceback.print_exc()
        record(label, False)


# ── Watch add ──


async def test_watch_add_arxiv():
    """Add a professor verified on arXiv."""
    label = "watch_add_arxiv"
    name = "Yann LeCun"
    print(f"\n{'='*60}")
    print(f"TEST: /watch add '{name}'")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        info = await agent.watch_add(name)
        print(f"  {PASS} name={info['name']}")
        print(f"       affiliation={info.get('affiliation', 'N/A')[:80]}")
        print(f"       homepage={info.get('homepage', 'N/A')[:80]}")
        print(f"       duplicate={info.get('duplicate', False)}")
        record(label, True)
    except Exception as exc:
        print(f"  {FAIL} {exc}")
        traceback.print_exc()
        record(label, False)


async def test_watch_add_duplicate():
    """Add same professor twice — should detect duplicate."""
    label = "watch_add_duplicate"
    name = "Yann LeCun"
    print(f"\n{'='*60}")
    print(f"TEST: /watch add '{name}' (DUPLICATE)")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        info = await agent.watch_add(name)
        is_dup = info.get("duplicate", False)
        print(f"  {PASS if is_dup else WARN} duplicate={is_dup} (expected True)")
        record(label, is_dup)
    except Exception as exc:
        print(f"  {FAIL} {exc}")
        traceback.print_exc()
        record(label, False)


async def test_watch_add_nonexistent():
    """Add a name that doesn't exist on arXiv — should fail gracefully."""
    label = "watch_add_nonexistent"
    name = "TotallyFake Professor123 XYZ"
    print(f"\n{'='*60}")
    print(f"TEST: /watch add '{name}' (should fail — fake name)")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        info = await agent.watch_add(name)
        print(f"  {WARN} Unexpectedly succeeded: {info}")
        record(label, False)
    except ValueError as exc:
        # Expected — arXiv verification should fail
        print(f"  {PASS} Correctly rejected: {exc}")
        record(label, True)
    except Exception as exc:
        print(f"  {WARN} Wrong error type: {type(exc).__name__}: {exc}")
        record(label, False)


# ── Watch list ──


async def test_watch_list():
    """List all watched professors."""
    label = "watch_list"
    print(f"\n{'='*60}")
    print(f"TEST: /watch list")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        profs = await agent.watch_list()
        print(f"  {PASS} {len(profs)} professors on watchlist:")
        for p in profs:
            print(f"       • {p['name']} — {p.get('affiliation', 'N/A')[:60]}")
        record(label, len(profs) >= 1)  # expect at least 1
    except Exception as exc:
        print(f"  {FAIL} {exc}")
        traceback.print_exc()
        record(label, False)


# ── Watch remove ──


async def test_watch_remove_existing():
    """Remove a professor that exists."""
    label = "watch_remove_existing"
    name = "Yann LeCun"
    print(f"\n{'='*60}")
    print(f"TEST: /watch remove '{name}'")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        ok = await agent.watch_remove(name)
        print(f"  {PASS if ok else FAIL} removed={ok}")
        record(label, ok)
    except Exception as exc:
        print(f"  {FAIL} {exc}")
        traceback.print_exc()
        record(label, False)


async def test_watch_remove_nonexistent():
    """Remove a professor that doesn't exist."""
    label = "watch_remove_nonexistent"
    name = "Nobody IsHere"
    print(f"\n{'='*60}")
    print(f"TEST: /watch remove '{name}' (should return False)")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        ok = await agent.watch_remove(name)
        print(f"  {PASS if not ok else WARN} removed={ok} (expected False)")
        record(label, not ok)
    except Exception as exc:
        print(f"  {FAIL} {exc}")
        traceback.print_exc()
        record(label, False)


async def test_watch_list_after_remove():
    """Verify watchlist shrunk after removal."""
    label = "watch_list_after_remove"
    print(f"\n{'='*60}")
    print(f"TEST: /watch list (after removal)")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        profs = await agent.watch_list()
        names = [p["name"] for p in profs]
        has_yann = any("Yann LeCun" in n or "LeCun" in n for n in names)
        print(f"  {PASS} {len(profs)} remaining: {names}")
        print(f"       Yann LeCun present: {has_yann} (expected False)")
        record(label, not has_yann)
    except Exception as exc:
        print(f"  {FAIL} {exc}")
        traceback.print_exc()
        record(label, False)


# ── Discover (smoke test — limited scope) ──


async def test_discover_smoke():
    """Quick discover test — just verify it doesn't crash and returns something."""
    label = "discover_smoke"
    print(f"\n{'='*60}")
    print(f"TEST: /discover (smoke — may take ~60s)")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        candidates = await agent.run_discover()
        print(f"  {PASS if candidates else WARN} {len(candidates)} candidates found")
        for i, c in enumerate(candidates[:5]):
            uni = c.get("university", "N/A")[:50]
            pos = c.get("position", "")[:40]
            sim = c.get("similarity", "?")
            cit = c.get("citations", 0)
            print(f"       {i+1}. {c['name']} | {uni} | cit={cit} sim={sim}")
        record(label, len(candidates) > 0)
    except Exception as exc:
        print(f"  {FAIL} {exc}")
        traceback.print_exc()
        record(label, False)


# ── Prof brief (smoke — just load from DB) ──


async def test_prof_brief_smoke():
    """Test prof brief retrieval from DB (not Celery)."""
    label = "prof_brief_smoke"
    print(f"\n{'='*60}")
    print(f"TEST: run_prof_brief for known ID")
    print(f"{'='*60}")
    # First add a professor so we have data
    agent = PaperTrackerAgent(task_ctx=_ctx(label + "-setup"))
    try:
        await agent.watch_add("Andrew Ng")
    except Exception:
        pass  # may already exist

    profs = await agent.watch_list()
    if profs:
        prof_id = profs[0]["id"]
        brief = await agent.run_prof_brief(prof_id)
        if "error" in brief:
            print(f"  {WARN} {brief['error']}")
            record(label, False)
        else:
            print(f"  {PASS} {brief['name']}")
            print(f"       affiliation={brief.get('affiliation', 'N/A')[:60]}")
            print(f"       direction={brief.get('recent_direction', 'N/A')[:60]}")
            print(f"       papers={brief.get('paper_count', 0)}")
            record(label, True)
    else:
        print(f"  {WARN} No professors in DB to test with")
        record(label, False)


# ── Digest (smoke) ──


async def test_digest_smoke():
    """Run digest and verify it completes without crash."""
    label = "digest_smoke"
    print(f"\n{'='*60}")
    print(f"TEST: /digest now (smoke)")
    print(f"{'='*60}")
    agent = PaperTrackerAgent(task_ctx=_ctx(label))
    try:
        result = await agent.run_digest("daily")
        total = len(result.interest_items) + len(result.professor_items)
        print(f"  {PASS} Digest complete: {len(result.interest_items)} interest + {len(result.professor_items)} professor = {total} papers")
        record(label, True)
    except Exception as exc:
        print(f"  {FAIL} {exc}")
        traceback.print_exc()
        record(label, False)


# ── Main ──


async def main():
    print("🧪 FULL TRIGGER TEST SUITE")
    print(f"   {datetime.now(UTC).isoformat()}")

    tests = [
        ("interests", test_interests),
        ("watch_add_arxiv", test_watch_add_arxiv),
        ("watch_add_duplicate", test_watch_add_duplicate),
        ("watch_add_nonexistent", test_watch_add_nonexistent),
        ("watch_list", test_watch_list),
        ("watch_remove_existing", test_watch_remove_existing),
        ("watch_remove_nonexistent", test_watch_remove_nonexistent),
        ("watch_list_after_remove", test_watch_list_after_remove),
        ("prof_brief_smoke", test_prof_brief_smoke),
        ("digest_smoke", test_digest_smoke),
        ("discover_smoke", test_discover_smoke),
    ]

    for name, fn in tests:
        try:
            await fn()
        except Exception as exc:
            print(f"\n  💥 {name} CRASHED: {exc}")
            traceback.print_exc()
            record(name, False)

    # ── Summary ──
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed = 0
    for name, ok in results.items():
        icon = PASS if ok else FAIL
        print(f"  {icon} {name}")
        if ok:
            passed += 1
    total = len(results)
    print(f"\n  {passed}/{total} passed")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
