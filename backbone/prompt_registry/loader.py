"""Prompt loader — load versioned YAML prompts and render them with inputs.

Prompts live in ``agents/<name>/prompts/<prompt_name>_v<version>.yaml``.
They are validated against the PromptTemplate schema and cached in memory.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

PROMPTS_ROOT: Path = Path(__file__).resolve().parent.parent.parent / "agents"
"""Root directory: agents/<name>/prompts/"""

_cache: dict[str, PromptTemplate] = {}
"""Cache: {fqn: PromptTemplate} where fqn = '{agent}/{name}/v{version}'"""


class ModelConfig(BaseModel):
    """Model configuration embedded in a prompt template.

    This is purely declarative metadata — no model is called during
    loading or rendering. The agent runtime reads this config to know
    which model to use when executing the prompt.
    """

    name: str = ""
    temperature: float = 0.3
    max_tokens: int = 256


class InputField(BaseModel):
    """A single field in the prompt's input schema."""

    name: str
    type: str = "str"
    description: str = ""


class InputSchema(BaseModel):
    """Input schema for a prompt template."""

    fields: list[InputField] = []


class PromptTemplate(BaseModel):
    """A single versioned prompt template loaded from YAML.

    Attributes:
        version: Template version (integer).
        agent: Agent name (e.g. ``"paper_tracker"``).
        name: Prompt name (e.g. ``"why_relevant"``).
        model: Model configuration metadata.
        input_schema: Describes expected input fields.
        template: The Jinja-like template string with ``{placeholders}``.
    """

    version: int
    agent: str
    name: str
    model: ModelConfig = ModelConfig()
    input_schema: InputSchema = InputSchema()
    template: str = ""


def load(
    agent: str,
    name: str,
    version: int | Literal["latest"] = "latest",
) -> PromptTemplate:
    """Load a prompt template from the filesystem.

    Args:
        agent: The agent name (e.g. ``"paper_tracker"``).
        name: The prompt name (e.g. ``"why_relevant"``).
        version: Version number, or ``"latest"`` for the highest version.

    Returns:
        A parsed PromptTemplate.

    Raises:
        FileNotFoundError: If the prompt file does not exist.
        ValueError: If the YAML is invalid or the version scheme is wrong.
    """
    if version == "latest":
        version = _resolve_latest(agent, name)

    fqn = f"{agent}/{name}/v{version}"
    if fqn in _cache:
        return _cache[fqn]

    path = PROMPTS_ROOT / agent / "prompts" / f"{name}_v{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt not found: {path} (agent={agent!r}, name={name!r}, version={version})"
        )

    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid prompt YAML in {path}: expected a dict")

    template = PromptTemplate(
        version=data.get("version", version),
        agent=data.get("agent", agent),
        name=data.get("name", name),
        model=ModelConfig(**data.get("model", {})),
        input_schema=InputSchema(**data.get("input_schema", {})),
        template=data.get("template", ""),
    )

    _cache[fqn] = template
    return template


def render(template: PromptTemplate, inputs: dict[str, Any]) -> tuple[str, str]:
    """Render a prompt template with the given inputs.

    Args:
        template: The prompt template to render.
        inputs: Dict of placeholder values.

    Returns:
        A tuple of ``(rendered_text, input_hash)`` where ``input_hash``
        is a SHA-256 hex digest of the serialised inputs.

    Raises:
        KeyError: If a placeholder in the template is missing from inputs.
    """
    rendered = template.template.format(**inputs)
    input_hash = hashlib.sha256(str(sorted(inputs.items())).encode()).hexdigest()
    return rendered, input_hash


def _resolve_latest(agent: str, name: str) -> int:
    """Scan the prompt directory for the highest version number."""
    pattern = f"{name}_v*.yaml"
    prompt_dir = PROMPTS_ROOT / agent / "prompts"
    if not prompt_dir.exists():
        raise FileNotFoundError(f"No prompts directory for agent {agent!r}: {prompt_dir}")

    versions: list[int] = []
    for f in prompt_dir.glob(pattern):
        version_str = f.stem.split("_v")[-1]
        try:
            versions.append(int(version_str))
        except ValueError:
            continue

    if not versions:
        raise FileNotFoundError(
            f"No prompt versions found for {agent}/{name} in {prompt_dir}/{pattern}"
        )

    return max(versions)


def clear_cache() -> None:
    """Clear the in-memory prompt cache (useful in tests)."""
    _cache.clear()
