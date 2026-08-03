"""Namespace constants and access control for the memory layer.

Each agent declares which namespaces it can read/write in its ``config.yaml``.
The dispatcher gates all memory calls through ``check_access()``.
"""

from __future__ import annotations

from .types import NamespaceAccessError

# ── Namespace constants ──

# User profile namespace (long-term)
NAMESPACE_USER_PROFILE = "user/profile"
# User activity namespace (short-term)
NAMESPACE_USER_ACTIVITY = "user/activity"
# User professors namespace (long-term)
NAMESPACE_USER_PROFESSORS = "user/professors"

# Paper tracker namespaces
NAMESPACE_PAPER_DIGESTS = "paper_tracker/digests"
NAMESPACE_PAPER_SEEN = "paper_tracker/papers_seen"
NAMESPACE_PAPER_SUMMARIZED = "paper_tracker/papers_summarized"

# All v0.1 namespaces
ALL_NAMESPACES = frozenset(
    {
        NAMESPACE_USER_PROFILE,
        NAMESPACE_USER_ACTIVITY,
        NAMESPACE_USER_PROFESSORS,
        NAMESPACE_PAPER_DIGESTS,
        NAMESPACE_PAPER_SEEN,
        NAMESPACE_PAPER_SUMMARIZED,
    }
)

# ── Access control ──

_ACCESS_RULES: dict[str, dict[str, set[str]]] = {}
"""
In-memory registry of agent namespace permissions.
Structure: {agent_name: {"read": {ns1, ns2}, "write": {ns3, ns4}}}
"""


def declare_access(agent: str, namespace: str, *, read: bool = False, write: bool = False) -> None:
    """Declare that ``agent`` can access ``namespace``.

    Args:
        agent: The agent name (e.g. ``"paper_tracker"``).
        namespace: The namespace to grant access to.
        read: If True, grant read permission.
        write: If True, grant write permission.

    Raises:
        ValueError: If neither read nor write is True.
    """
    if not read and not write:
        raise ValueError("Must grant at least one of read or write")

    if agent not in _ACCESS_RULES:
        _ACCESS_RULES[agent] = {"read": set(), "write": set()}

    if read:
        _ACCESS_RULES[agent]["read"].add(namespace)
    if write:
        _ACCESS_RULES[agent]["write"].add(namespace)


def check_access(agent: str, namespace: str, op: str) -> bool:
    """Check if ``agent`` has ``op`` permission on ``namespace``.

    Args:
        agent: The agent name.
        namespace: The namespace to check.
        op: ``"read"`` or ``"write"``.

    Returns:
        True if access is allowed.

    Raises:
        NamespaceAccessError: If access is denied.
    """
    if op not in ("read", "write"):
        raise ValueError(f"op must be 'read' or 'write', got {op!r}")

    rules = _ACCESS_RULES.get(agent)
    if rules is None:
        raise NamespaceAccessError(f"Agent {agent!r} has no declared access rules")

    allowed = rules.get(op, set())
    if namespace not in allowed:
        raise NamespaceAccessError(
            f"Agent {agent!r} does not have {op} access to namespace {namespace!r}"
        )
    return True


def clear_access() -> None:
    """Clear all access rules (useful in tests)."""
    _ACCESS_RULES.clear()
