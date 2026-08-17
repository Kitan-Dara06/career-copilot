"""Adapters that expose Career Copilot data as MCP tool results.

Each adapter reads canonical data and returns a plain JSON-serializable
structure. Adapters do not perform writes and do not leak secrets.
"""

from __future__ import annotations

from typing import Any

import yaml

from career_copilot.config.paths import DATA_DIR


def _load_yaml(name: str) -> dict[str, Any]:
    """Load a YAML file from the data directory, returning {} if missing."""
    path = DATA_DIR / name
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_profile() -> dict[str, Any]:
    """Return the canonical user profile and skill clusters.

    Sources:
        - data/user_profile.yaml  (research interests, keywords, preferences)
        - data/user_skills.yaml   (14 skill clusters with weights)
    """
    profile = _load_yaml("user_profile.yaml")
    skills_raw = _load_yaml("user_skills.yaml")
    skills = skills_raw.get("skills", {}) or {}

    return {
        "research_interests": (profile.get("research_interests") or "").strip(),
        "keywords": profile.get("keywords") or [],
        "arxiv_categories": profile.get("arxiv_categories") or [],
        "preferences": profile.get("preferences") or {},
        "skill_clusters": [
            {
                "name": name,
                "skills": body.get("skills") or [],
                "weight": body.get("weight", 1.0),
            }
            for name, body in skills.items()
        ],
    }
