"""Local smoke test — exercise each trigger without Telegram.

Usage:
    uv run python tests/local_trigger_test.py
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import UTC, datetime, timedelta

# Ensure env is loaded before imports
from dotenv import load_dotenv
load_dotenv()

from career_copilot.config import get_settings
from backbone.tools.base import ToolContext
from agents.paper_tracker.agent import PaperTrackerAgent


def _ctx(label: str) -> ToolContext:
    return ToolContext(agent="paper_tracker", task_id=f"local-test-{label}", settings=get_settings())


async def test_watch_add():
    """Test /watch add with a known professor."""
    print("\n" + "=" * 60)
    print("TEST 1: /watch add 'Geoffrey Hinton'")
    print("=" * 60)
    agent = PaperTrackerAgent(task_ctx=_ctx("watch-add"))
    try:
        info = await agent.watch_add("Geoffrey Hinton")
        print(f"  ✅ Result: name={info['name']}")
        print(f"     affiliation={info.get('affiliation', 'N/A')[:80]}")
        print(f"     homepage={info.get('homepage', 'N/A')[:80]}")
        print(f"     duplicate={info.get('duplicate', False)}")
    except Exception as exc:
        print(f"  ❌ Failed: {exc}")
        traceback.print_exc()


async def test_watch_list():
    """Test /watch list."""
    print("\n" + "=" * 60)
    print("TEST 2: /watch list")
    print("=" * 60)
    agent = PaperTrackerAgent(task_ctx=_ctx("watch-list"))
    try:
        profs = await agent.watch_list()
        if profs:
            for p in profs:
                print(f"  📋 {p['name']} — {p.get('affiliation', 'N/A')[:60]}")
        else:
            print("  ⚠️  Watchlist is empty")
    except Exception as exc:
        print(f"  ❌ Failed: {exc}")
        traceback.print_exc()


async def test_watch_remove():
    """Test /watch remove."""
    print("\n" + "=" * 60)
    print("TEST 3: /watch remove 'Geoffrey Hinton'")
    print("=" * 60)
    agent = PaperTrackerAgent(task_ctx=_ctx("watch-remove"))
    try:
        ok = await agent.watch_remove("Geoffrey Hinton")
        print(f"  {'✅ Removed' if ok else '⚠️  Not found'}")
    except Exception as exc:
        print(f"  ❌ Failed: {exc}")
        traceback.print_exc()


async def test_watch_duplicate():
    """Test adding duplicate professor."""
    print("\n" + "=" * 60)
    print("TEST 4: /watch add duplicate")
    print("=" * 60)
    agent = PaperTrackerAgent(task_ctx=_ctx("watch-dup"))
    # Add first
    try:
        info1 = await agent.watch_add("Yoshua Bengio")
        print(f"  1st add: {info1['name']} — duplicate={info1.get('duplicate')}")
    except Exception as exc:
        print(f"  1st add failed: {exc}")
    
    # Add again
    try:
        info2 = await agent.watch_add("Yoshua Bengio")
        print(f"  2nd add: {info2['name']} — duplicate={info2.get('duplicate')}")
    except Exception as exc:
        print(f"  2nd add failed: {exc}")


async def test_discover():
    """Test /discover — basic smoke test (calls S2 API)."""
    print("\n" + "=" * 60)
    print("TEST 5: /discover (quick smoke)")
    print("=" * 60)
    agent = PaperTrackerAgent(task_ctx=_ctx("discover"))
    try:
        candidates = await agent.run_discover()
        print(f"  Found {len(candidates)} candidates")
        for i, c in enumerate(candidates[:3]):
            print(f"  {i+1}. {c['name']} — {c.get('university', 'N/A')[:60]}")
    except Exception as exc:
        print(f"  ❌ Failed: {exc}")
        traceback.print_exc()


async def test_interests():
    """Test loading user interests."""
    print("\n" + "=" * 60)
    print("TEST 6: User interests load")
    print("=" * 60)
    agent = PaperTrackerAgent(task_ctx=_ctx("interests"))
    try:
        essay = await agent._get_user_interests()
        keywords = await agent._get_user_keywords()
        print(f"  Essay: {len(essay)} chars")
        print(f"  Keywords: {keywords[:100]}")
        print("  ✅ Loaded")
    except Exception as exc:
        print(f"  ❌ Failed: {exc}")
        traceback.print_exc()


async def main():
    print("🧪 Local Trigger Test Suite")
    print(f"   Timestamp: {datetime.now(UTC).isoformat()}")
    
    results: dict[str, bool] = {}
    
    # Run tests
    for name, test_fn in [
        ("interests", test_interests),
        ("watch_add", test_watch_add),
        ("watch_list", test_watch_list),
        ("watch_duplicate", test_watch_duplicate),
        ("watch_remove", test_watch_remove),
        ("discover", test_discover),
    ]:
        try:
            await test_fn()
            results[name] = True
        except Exception as exc:
            print(f"\n  💥 {name} crashed: {exc}")
            results[name] = False
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {name}")
    print(f"\n  {passed}/{total} passed")
    
    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
