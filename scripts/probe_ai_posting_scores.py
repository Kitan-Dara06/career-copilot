#!/usr/bin/env python3
"""Score actual AI-relevant postings together to verify the 0.55 threshold works.

For Anthropic, Together AI, DeepMind, Vercel, Databricks — fetch postings, embed
them with the user's skill vector, and print scores. Filter to AI-relevant titles
so we see if real research/agent/LLM roles cross 0.55.
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from backbone.tools.base import ToolContext
from backbone.tools.jobs import FetchATSInput, FetchATSTool
from backbone.tools.vector import EmbedInput, EmbedTool
from career_copilot.config import get_settings


def _make_ctx() -> ToolContext:
    return ToolContext(agent="job_hunter", task_id=f"probe_{datetime.now(UTC).timestamp():.0f}", settings=get_settings())


VERIFIED = [
    ("Anthropic",  "greenhouse", "anthropic"),
    ("Together AI","greenhouse", "togetherai"),
    ("Vercel",     "greenhouse", "vercel"),
    ("DeepMind",   "greenhouse", "deepmind"),
    ("Databricks", "greenhouse", "databricks"),
]


AI_TITLE_PATTERNS = re.compile(
    r"(intern|co-?op|new grad|research.*engineer|research.*scientist|ml engineer|"
    r"ai engineer|llm|agent|rag|retrieval|nlp|genai|tokenizer|"
    r"hugging face|huggingface|pytorch|transformer|gen ai)",
    re.IGNORECASE,
)


async def main():
    from agents.job_hunter.agent import JobHunterAgent, _cosine, DEFAULT_MIN_MATCH_SCORE

    agent = JobHunterAgent(task_ctx=_make_ctx())
    skills = agent._load_skill_clusters()
    await agent._ensure_user_skill_vec(skills)

    # Build per-cluster embeddings + weights for weighted-max diagnostic
    cluster_texts = [" ".join(c["skills"]) for c in skills]
    cluster_weights = [c.get("weight", 1.0) for c in skills]
    embed_tool = EmbedTool()
    cluster_embeds = await embed_tool(_make_ctx(), EmbedInput(texts=cluster_texts))
    cluster_vecs = cluster_embeds.embeddings
    cluster_names = [c["name"] for c in skills]

    print(f"Min match threshold: {DEFAULT_MIN_MATCH_SCORE}")
    print(f"Scoring {len(VERIFIED)} companies...")

    n_above = 0
    n_ai_titles_seen = 0
    n_ai_above = 0

    for display_name, ats, slug in VERIFIED:
        print("\n" + "=" * 90)
        print(f"  {display_name} ({ats}/{slug})")
        print("=" * 90)
        try:
            out = await FetchATSTool()(_make_ctx(), FetchATSInput(
                ats=ats, company_id=slug, organization=display_name
            ))
        except Exception as exc:
            print(f"  fetch failed: {exc}")
            continue

        if not out.postings:
            print("  no postings")
            continue

        # Filter to AI-relevant titles first to keep output readable
        ai_postings = [p for p in out.postings if AI_TITLE_PATTERNS.search(p.title)]
        # Take top 15 to keep batch embed under Voyage limit (~64)
        sample = ai_postings[:15]
        if not sample:
            print(f"  no AI-relevant titles in {len(out.postings)} postings")
            continue
        print(f"  total {len(out.postings)}, AI-relevant titles: {len(ai_postings)}, scoring top {len(sample)}")

        # Batch embed
        texts = [(p.title + " " + (p.description or ""))[:2000] for p in sample]
        posting_embeds = await embed_tool(_make_ctx(), EmbedInput(texts=texts))
        posting_vecs = posting_embeds.embeddings

        print(f"\n  {'#':<3} {'avg':>5} {'max':>5} {'wmax':>5} {'flag':>20}  Title")
        print("  " + "-" * 88)
        for i, (p, pvec) in enumerate(zip(sample, posting_vecs, strict=True)):
            avg_score = _cosine(agent._user_skill_vec, pvec)
            per_cluster = [(n, _cosine(cvec, pvec), w) for n, cvec, w in zip(cluster_names, cluster_vecs, cluster_weights, strict=True)]
            per_cluster.sort(key=lambda x: x[1] * x[2], reverse=True)
            max_score = per_cluster[0][1]
            wmax_score = per_cluster[0][1] * per_cluster[0][2]
            top_cluster = per_cluster[0][0]

            flag = ""
            if wmax_score >= 0.55:
                flag = "✅ ABOVE"
                n_above += 1
            elif wmax_score >= 0.45:
                flag = "⚠️  borderline"
            else:
                flag = "❌ below"
            print(f"  {i+1:<3} {avg_score:.3f} {max_score:.3f} {wmax_score:.3f}  {flag:<20}  {p.title[:60]}")
            print(f"          top cluster: {top_cluster} (weight {per_cluster[0][2]})")
            n_ai_titles_seen += 1
            if wmax_score >= 0.55:
                n_ai_above += 1

    # Prettify cluster dict for readability
    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print(f"AI-relevant postings scored: {n_ai_titles_seen}")
    print(f"Above 0.55 threshold: {n_ai_above}")
    print(f"Below 0.55 threshold: {n_ai_titles_seen - n_ai_above}")
    if n_ai_titles_seen and n_ai_above / n_ai_titles_seen < 0.20:
        print("\n⚠️  Less than 20% of AI-relevant postings cross threshold.")
        print("   Scoring scheme may need rework: per-cluster-max + lower threshold,")
        print("   or pre-extract a 'skills signature' from the posting before embedding.")
    elif n_ai_titles_seen and n_ai_above / n_ai_titles_seen >= 0.20:
        print("\n✅ Scoring surfaces real AI postings — just need to expand the watchlist.")


if __name__ == "__main__":
    asyncio.run(main())