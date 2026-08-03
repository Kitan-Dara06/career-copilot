"""Tool registry — registration, lookup, and LLM-facing schema generation.

Tools are registered via the ``@register_tool`` decorator and looked up
by name or by agent ACLs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

logger = structlog.get_logger("tools.registry")

_registry: dict[str, Any] = {}
"""Global tool registry: {tool_name: Tool instance}."""

_agent_acls: dict[str, set[str]] = {}
"""Per-agent ACLs: {agent_name: {tool_name, ...}}."""


def _generate_schema(model: type[Any]) -> dict[str, Any]:
    """Generate a JSON-Schema-ish dict from a Pydantic model.

    The LLM receives this to understand tool parameters.
    """
    schema = model.model_json_schema()
    return {
        "type": "object",
        "properties": schema.get("properties", {}),
        "required": schema.get("required", []),
        "title": schema.get("title", model.__name__),
        "description": schema.get("description", ""),
    }


def register(tool: Any, agent: str | None = None) -> None:
    """Register a tool globally and optionally grant agent access.

    Args:
        tool: A ``Tool`` subclass instance.
        agent: Optional agent name to auto-grant ACL for this tool.
    """
    name = tool.name
    _registry[name] = tool
    if agent:
        if agent not in _agent_acls:
            _agent_acls[agent] = set()
        _agent_acls[agent].add(name)
    logger.info("tool_registered", tool=name, agent=agent)


def get(name: str) -> Any:
    """Look up a tool by name.

    Raises:
        KeyError: If the tool is not registered.
    """
    if name not in _registry:
        raise KeyError(f"Tool {name!r} not found in registry")
    return _registry[name]


def list_for_agent(agent: str) -> list[Any]:
    """Return all tools accessible to the given agent.

    If no ACL rules exist for the agent, returns all registered tools
    (open-access fallback for development).
    """
    allowed = _agent_acls.get(agent)
    if allowed is None:
        return list(_registry.values())
    return [t for name, t in _registry.items() if name in allowed]


def schemas_for_llm(agent: str) -> list[dict[str, Any]]:
    """Return LLM-compatible function schemas for the agent's tools.

    Each dict has: ``name``, ``description``, ``parameters`` (the JSON
    Schema for inputs).
    """
    tools = list_for_agent(agent)
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": _generate_schema(t.input_schema),
        }
        for t in tools
    ]


def clear() -> None:
    """Clear all registrations (used in tests)."""
    _registry.clear()
    _agent_acls.clear()


if not TYPE_CHECKING:
    # Make sure registry module doesn't create import cycles.
    # Tool modules register themselves on import via module-level calls.
    pass
