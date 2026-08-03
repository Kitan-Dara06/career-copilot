#!/usr/bin/env python3
"""Probe ATS endpoints for AI-native companies likely to have RAG/agent/LLM roles.

Tests multiple slug variants per (ats, company) pair, and prints the titles of
jobs returned so we can see if any current postings match Aaliyah's profile.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from backbone.tools.base import ToolContext
from backbone.tools.jobs import FetchATSInput, FetchATSTool
from career_copilot.config import get_settings


def _make_ctx() -> ToolContext:
    return ToolContext(agent="job_hunter", task_id=f"probe_{datetime.now(UTC).timestamp():.0f}", settings=get_settings())


# AI-native companies with graduate/research intern programs.
# Each tuple: (display_name, ats, [slug_candidates])

AI_NATIVE_PROBES = [
    # ──Companies already in watchlist but possibly wrong slug ──
    ("Mistral",        "greenhouse", ["mistralai", "mistral-ai", "mistral"]),
    ("Mistral",        "ashby",      ["mistral", "mistralai", "mistral-ai"]),
    ("Mistral",        "lever",      ["mistral"]),

    # ── Top AI research labs with new-grad / co-op programs ──
    ("Cohere",         "greenhouse", ["cohere", "cohereforai"]),
    ("Cohere",         "lever",      ["cohere"]),
    ("Cohere",         "ashby",      ["cohere", "cohere-for-ai"]),
    ("Anthropic",      "greenhouse", ["anthropic", "anthropiccom"]),
    ("Anthropic",      "lever",      ["anthropic"]),
    ("Hugging Face",   "greenhouse", ["huggingface", "hf"]),
    ("Hugging Face",   "ashby",      ["hugging-face", "huggingface"]),
    ("OpenAI",         "greenhouse", ["openai"]),
    ("OpenAI",         "ashby",      ["openai"]),
    ("DeepMind",       "greenhouse", ["deepmind"]),
    ("Together AI",    "greenhouse", ["together-ai", "togetherai"]),
    ("Together AI",    "ashby",      ["together", "togetherai"]),
    ("AI21 Labs",      "greenhouse", ["ai21", "ai21-labs"]),
    ("AI21 Labs",      "ashby",      ["ai21"]),
    ("Anyscale",       "greenhouse", ["anyscale"]),
    ("LightOn",        "greenhouse", ["lighton"]),
    ("LightOn",        "ashby",      ["lighton"]),
    ("Replit",         "greenhouse", ["replit"]),
    ("Replit",         "ashby",      ["replit"]),
    ("Vercel",         "greenhouse", ["vercel"]),
    ("Modal",          "greenhouse", ["modal"]),
    ("Modal",          "ashby",      ["modal"]),
    ("Replicate",      "greenhouse", ["replicate"]),
    ("Replicate",      "ashby",      ["replicate"]),
    ("Glean",          "greenhouse", ["glean", "glean-ai"]),
    ("Glean",          "ashby",      ["glean"]),
    ("Mosaic AI",      "greenhouse", ["mosaicml", "mosaic"]),
    ("Databricks",     "greenhouse", ["databricks"]),

    # AI safety / research orgs
    ("Conjecture",     "greenhouse", ["conjecture", "conjectureai"]),
    ("Conjecture",     "ashby",      ["conjecture"]),
]


AI_KEYWORDS = (
    "llm", "rag", "agent", "retrieval", "embed", "research intern",
    "ml engineer", "ml intern", "ai engineer", "research scientist",
    "co-op", "internship", "internship", "nlp", "hugging face",
    "huggingface", "pytorch", "transformer", "tokenizer", "nlg", "genai",
)


async def probe_one(display_name: str, ats: str, slug: str) -> tuple[int, list[str]] | None:
    """Return (n_postings, sample_titles) for a successful fetch, or None on 404/error."""
    tool = FetchATSTool()
    try:
        out = await tool(_make_ctx(), FetchATSInput(
            ats=ats, company_id=slug, organization=display_name
        ))
    except Exception:
        return None
    titles = [p.title for p in out.postings]
    return (len(out.postings), titles)


async def main():
    print(f"{'Company':<20} {'ATS':<11} {'Slug':<22} {'N':>4}  Sample titles")
    print("-" * 110)
    rows = []
    for display_name, ats, slugs in AI_NATIVE_PROBES:
        for slug in slugs:
            res = await probe_one(display_name, ats, slug)
            if res is None:
                print(f"{display_name:<20} {ats:<11} {slug:<22} {'404':>4}  --")
                continue
            n, titles = res
            sample = titles[0][:40] if titles else "(empty)"
            print(f"{display_name:<20} {ats:<11} {slug:<22} {n:>4}  {sample}")
            rows.append({
                "name": display_name, "ats": ats, "slug": slug,
                "n_postings": n, "titles": titles,
            })
            # Don't try more slug variants for this (company, ats) once one works
            break

    # Report AI-relevant postings found
    print("\n" + "=" * 110)
    print("AI-RELEVANT POSTINGS (title contains AI/LLM/agent/RAG/research/ML)")
    print("=" * 110)
    for r in rows:
        for t in r["titles"]:
            tl = t.lower()
            if any(kw in tl for kw in AI_KEYWORDS):
                print(f"  {r['name']:<20} [{r['ats']}/{r['slug']}]  {t[:80]}")

    # Emit ready-to-paste YAML
    print("\n" + "=" * 110)
    print("READY-TO-PASTE YAML entries (verified working)")
    print("=" * 110)
    seen = set()
    for r in rows:
        key = (r["name"], r["ats"])
        if key in seen:
            continue
        seen.add(key)
        region = "eu" if r["name"] in ("Mistral", "LightOn") else "canada" if r["name"] == "Cohere" else "international_remote"
        print(f"  - name: \"{r['name']}\"")
        print(f"    region: \"{region}\"")
        print(f"    source_tier: 1")
        print(f"    ats: \"{r['ats']}\"")
        print(f"    ats_company_id: \"{r['slug']}\"")
        print()


if __name__ == "__main__":
    asyncio.run(main())