"""Smoke test — python -m career_copilot exits 0."""

from __future__ import annotations

import subprocess
import sys

from career_copilot import __version__


def test_version_defined() -> None:
    """Package version is a semver string."""
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_main_prints_ok() -> None:
    """`python -m career_copilot` prints 'OK'."""
    result = subprocess.run(
        [sys.executable, "-m", "career_copilot"],
        capture_output=True,
        text=True,
        cwd=str(__file__).rsplit("/", 3)[0],
    )
    assert result.returncode == 0
    assert "OK" in result.stdout
