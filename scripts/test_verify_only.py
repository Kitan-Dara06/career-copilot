"""Probe: validate the professor_verify v2 prompt on the SAME-NAME COLLISIONS
seen in the last discover run, plus one real positive.

Goal: confirm that adding user_domain + domain_match rejects:
  - Ding Chen (Associate Prof, Defence Science)  → domain_match=False
  - Xiang-peng Xie (Asst Prof, CalState East Bay, Business/Economics) → False
  - Yiqun T. Chen (Economics at UIC)              → False
while accepting:
  - Yu Su (Distinguished Assistant Professor, OSU, AI) → True
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.paper_tracker.agent import PaperTrackerAgent  # noqa: E402
from backbone.tools.base import ToolContext  # noqa: E402
from career_copilot.config import get_settings  # noqa: E402

# Each probe: (name, fake homepage markdown that mimics the real page signal).
PROBES = [
    (
        "Ding Chen",
        "Dr. Ding Chen\nAssociate Professor, School of Defence Science & Technology, "
        "Xi'an Technological University.\nResearch interests: weapon-system "
        "reliability, fault diagnosis for armoured vehicles.\nDepartment of "
        "Mechanical Engineering.",
    ),
    (
        "Xiang-peng Xie",
        "Assistant Professor, College of Business and Economics, California State "
        "University, East Bay. Research: financial economics and data analytics. "
        "Recent papers: 'Volatility forecasting in emerging markets'.",
    ),
    (
        "Yiqun T. Chen",
        "Yiqun T. Chen — Assistant Professor of Economics, University of "
        "Illinois Chicago. Field: applied microeconomics, labour economics.\n"
        "College of Business Administration.",
    ),
    (
        "Yu Su",
        "Yu Su\nDistinguished Assistant Professor of Engineering, Department of "
        "Computer Science and Engineering, The Ohio State University.\n"
        "Research: LLM agents, retrieval-augmented generation, "
        "multi-modal foundation models. Recent paper: 'AgentBoard: An "
        "Open-Source Holistic Evaluation Engine for LLM Agents'.",
    ),
]

USER_DOMAIN = "retrieval-augmented generation, neural-symbolic IR, agent architectures, evaluation infrastructure, memory systems"


async def main() -> None:
    settings = get_settings()
    ctx = ToolContext(agent="paper_tracker", task_id="verify-probe", settings=settings)
    agent = PaperTrackerAgent(task_ctx=ctx)

    # Capture raw LLM output too so we can see WHY parsing fails.
    from backbone.prompt_registry.loader import load as load_prompt, render

    template = load_prompt("paper_tracker", "professor_verify")
    schema = {
        "type": "object",
        "properties": {
            "is_professor": {"type": "boolean"},
            "position": {"type": "string"},
            "department": {"type": "string"},
            "university": {"type": "string"},
            "country": {"type": "string"},
            "research_area": {"type": "string"},
            "domain_match": {"type": "boolean"},
        },
        "required": [
            "is_professor", "position", "department", "university",
            "country", "research_area", "domain_match",
        ],
    }

    for name, md in PROBES:
        print("=" * 80)
        print(f"Probe: {name}")
        print(f"  user_domain: {USER_DOMAIN}")
        print(f"  markdown length: {len(md)}")
        rendered, _ = render(
            template,
            {
                "prof_name": name,
                "user_domain": USER_DOMAIN,
                "homepage_markdown": md,
            },
        )
        try:
            raw = await agent._llm.generate(
                model=template.model.name,
                prompt=rendered,
                temperature=0.0,
                max_tokens=300,
                response_format="json",
                response_schema=schema,
            )
        except Exception as exc:
            print(f"  RAISED: {type(exc).__name__}: {exc}")
            continue
        print(f"  raw length: {len(raw) if raw else 0}")
        print(f"  raw repr    : {raw!r}")
        print(f"  raw hex    : {raw.encode('utf-8').hex() if raw else ''}")
        verify = await agent._llm_verify_professor(name, md, user_domain=USER_DOMAIN)
        if verify is None:
            print("  RESULT: verify returned None (LLM/JSON failure)")
            continue
        print(
            f"  RESULT: is_professor={verify.get('is_professor')} "
            f"position={verify.get('position')!r} "
            f"research_area={verify.get('research_area')!r} "
            f"country={verify.get('country')!r} "
            f"domain_match={verify.get('domain_match')}"
        )


if __name__ == "__main__":
    asyncio.run(main())