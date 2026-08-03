"""Quick probe: compare deepseek-v4-pro WITH and WITHOUT response_format=json_object.

Goal: determine whether the ``finish_reason=length`` empty-response bug is caused
by the response_format parameter or by something else in the prompt chain.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from career_copilot.config import get_settings  # noqa: E402
from backbone.model_client import ModelClient  # noqa: E402

PROMPT_CHARS = 8000  # simulate a real discover-sized merged markdown


async def main() -> None:
    settings = get_settings()
    client = ModelClient()

    # A prompt that mimics the verify prompt but with 8000 chars of padding.
    base = (
        "You are verifying whether 'Test Prof' is a faculty member.\n\n"
        "User domain: NLP, retrieval-augmented generation, agent architectures\n\n"
        "Respond with STRICT JSON:\n"
        '{"is_professor": false, "position": "", "department": "", '
        '"university": "", "country": "", "research_area": "", "domain_match": false}\n\n'
    )
    padding = "Page content:\n" + ("word " * (PROMPT_CHARS // 5))

    for use_json_mode in (True, False):
        for attempt in range(1, 4):
            label = "json_object" if use_json_mode else "no-format"
            raw = await client.generate(
                model="deepseek-chat",  # triggers deepseek path
                prompt=base + padding[:PROMPT_CHARS],
                temperature=0.0,
                max_tokens=2048,
                response_format="json" if use_json_mode else None,
            )
            ok = raw and len(raw) > 50 and "is_professor" in raw
            print(
                f"  {label} attempt={attempt} "
                f"len={len(raw) if raw else 0} "
                f"{'OK' if ok else 'FAIL ' + (raw or '')[:80]!r}"
            )
            if ok:
                break
        else:
            print(f"  {label}: ALL 3 ATTEMPTS FAILED")


if __name__ == "__main__":
    asyncio.run(main())