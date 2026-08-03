"""Tests for namespace access control."""

from __future__ import annotations

import pytest

from backbone.memory.namespaces import (
    NAMESPACE_USER_PROFILE,
    check_access,
    clear_access,
    declare_access,
)
from backbone.memory.types import NamespaceAccessError


def setup_method() -> None:
    """Clear access rules before each test."""
    clear_access()


def test_declare_and_check_read() -> None:
    """Read access can be declared and checked."""
    declare_access("paper_tracker", NAMESPACE_USER_PROFILE, read=True)
    assert check_access("paper_tracker", NAMESPACE_USER_PROFILE, "read") is True


def test_declare_and_check_write() -> None:
    """Write access can be declared and checked."""
    declare_access("paper_tracker", NAMESPACE_USER_PROFILE, write=True)
    assert check_access("paper_tracker", NAMESPACE_USER_PROFILE, "write") is True


def test_read_does_not_imply_write() -> None:
    """Read access does not grant write access."""
    clear_access()
    declare_access("paper_tracker", NAMESPACE_USER_PROFILE, read=True)
    with pytest.raises(NamespaceAccessError):
        check_access("paper_tracker", NAMESPACE_USER_PROFILE, "write")


def test_unknown_agent_raises() -> None:
    """An agent with no declared rules raises NamespaceAccessError."""
    with pytest.raises(NamespaceAccessError):
        check_access("unknown_agent", NAMESPACE_USER_PROFILE, "read")


def test_unknown_namespace_raises() -> None:
    """An agent without access to a namespace raises."""
    declare_access("paper_tracker", NAMESPACE_USER_PROFILE, read=True)
    with pytest.raises(NamespaceAccessError):
        check_access("paper_tracker", "some/other/namespace", "read")
