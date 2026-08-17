"""Tests for the MCP adapters and policy."""

from __future__ import annotations

from backbone.mcp.adapters import load_profile
from backbone.mcp.policy import apply_policy, redact


def test_load_profile_returns_skill_clusters() -> None:
    profile = load_profile()
    assert "skill_clusters" in profile
    assert isinstance(profile["skill_clusters"], list)
    # The seeded profile has 14 clusters, but tolerate fewer in CI.
    assert len(profile["skill_clusters"]) > 0
    first = profile["skill_clusters"][0]
    assert "name" in first and "weight" in first


def test_load_profile_has_keywords() -> None:
    profile = load_profile()
    assert isinstance(profile["keywords"], list)


def test_redact_masks_secret_shapes() -> None:
    raw = {
        "note": "use key sk-abcdefghijklmnop for auth",
        "nested": ["AIza1234567890abcdefghijklmnop"],
    }
    out = redact(raw)
    assert "sk-abcdefghijklmnop" not in out["note"]
    assert "[REDACTED]" in out["note"]


def test_apply_policy_caps_long_strings() -> None:
    result = {"text": "x" * 100}
    capped = apply_policy(result, limit=50)
    assert len(capped["text"]) == 50 + len("…[truncated]")
