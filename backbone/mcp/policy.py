"""MCP tool policy — secret redaction and output capping.

Applied to every tool result before it is returned to Hermes. This is the
first line of defence: even if an adapter accidentally includes a credential,
it never reaches the model.
"""

from __future__ import annotations

import re
from typing import Any

# Rough output cap. Assume ~4 characters per token so a 2000-token budget
# maps to an 8000-character ceiling.
DEFAULT_OUTPUT_CHARS = 8000

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),       # OpenAI / DeepSeek keys
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),      # Gemini keys
    re.compile(r"\btvly-[A-Za-z0-9_-]{16,}\b"),     # Tavily keys
    re.compile(r"\bfc-[A-Za-z0-9]{16,}\b"),         # Firecrawl keys
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}\b"),  # GitHub PATs
    re.compile(r"\bpa-[A-Za-z0-9]{16,}\b"),         # Voyage keys
]


def redact(value: Any) -> Any:
    """Recursively redact known secret shapes from a value."""
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            value = pattern.sub("[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def cap_chars(value: Any, limit: int = DEFAULT_OUTPUT_CHARS) -> Any:
    """Truncate long strings so a single tool result cannot flood context."""
    if isinstance(value, str):
        if len(value) > limit:
            return value[:limit] + "…[truncated]"
        return value
    if isinstance(value, dict):
        return {k: cap_chars(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [cap_chars(v, limit) for v in value]
    return value


def apply_policy(result: Any, limit: int = DEFAULT_OUTPUT_CHARS) -> Any:
    """Apply redaction then capping to a tool result."""
    return cap_chars(redact(result), limit)
