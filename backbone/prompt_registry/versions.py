"""Prompt version management — list versions and diff/compare prompts."""

from __future__ import annotations

import yaml

from .loader import PROMPTS_ROOT


class VersionDiff:
    """Difference between two prompt versions."""

    template_diff: bool = False
    model_diff: bool = False
    schema_diff: bool = False
    changes: list[str] = []


def list_versions(agent: str, name: str) -> list[int]:
    """Return all available version numbers for a prompt.

    Args:
        agent: Agent name.
        name: Prompt name.

    Returns:
        Sorted list of version integers.
    """
    pattern = f"{name}_v*.yaml"
    prompt_dir = PROMPTS_ROOT / agent / "prompts"
    versions: list[int] = []
    for f in prompt_dir.glob(pattern):
        v_str = f.stem.split("_v")[-1]
        try:
            versions.append(int(v_str))
        except ValueError:
            continue
    return sorted(versions)


def compare(agent: str, name: str, v1: int, v2: int) -> VersionDiff:
    """Compare two versions of a prompt.

    Args:
        agent: Agent name.
        name: Prompt name.
        v1: First version.
        v2: Second version.

    Returns:
        A VersionDiff describing the differences.
    """
    p1 = _load_raw(agent, name, v1)
    p2 = _load_raw(agent, name, v2)
    diff = VersionDiff()

    if p1.get("template") != p2.get("template"):
        diff.template_diff = True
        diff.changes.append("template changed")

    if p1.get("model") != p2.get("model"):
        diff.model_diff = True
        diff.changes.append("model config changed")

    if p1.get("input_schema") != p2.get("input_schema"):
        diff.schema_diff = True
        diff.changes.append("input schema changed")

    return diff


def _load_raw(agent: str, name: str, version: int) -> dict[str, object]:
    """Load raw YAML dict for comparison without caching."""
    path = PROMPTS_ROOT / agent / "prompts" / f"{name}_v{version}.yaml"
    with open(path) as f:
        return yaml.safe_load(f) or {}
